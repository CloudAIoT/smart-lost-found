"""Routes for the lost-item ownership flow."""

from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api import ReturnCodeView, Store, issue_ownership_return_code, require_business_api
from app.config import Settings, get_settings
from app.db import SessionLocal, get_session
from . import search_jobs, workflow
from .workflow_ai import GeminiWorkflowAI, WorkflowAI
from .workflow_schemas import AnswerRequest, AttemptView, PhotoConfirmationRequest, SearchRequest


Config = Annotated[Settings, Depends(get_settings)]
DB = Annotated[Session, Depends(get_session)]


def get_ownership_ai(settings: Config) -> Iterator[WorkflowAI]:
    with httpx.Client(trust_env=False) as client:
        # One answer makes a single call, but the budget still bounds its model fallback.
        yield GeminiWorkflowAI(settings, client, budget_seconds=settings.ownership_search_budget_seconds)


def get_search_ai(settings: Config) -> search_jobs.AiFactory:
    return search_jobs.gemini_factory(settings)


def get_session_factory() -> search_jobs.SessionFactory:
    # The background search cannot borrow the request session: it outlives the response.
    return SessionLocal


AI = Annotated[WorkflowAI, Depends(get_ownership_ai)]
SearchAI = Annotated[search_jobs.AiFactory, Depends(get_search_ai)]
Sessions = Annotated[search_jobs.SessionFactory, Depends(get_session_factory)]


def get_search_jobs(settings: Config, ai_factory: SearchAI, session_factory: Sessions) -> search_jobs.SearchJobs:
    return search_jobs.SearchJobs(
        settings, ai_factory, session_factory,
        search_jobs.shared_executor(settings.ownership_max_concurrent_searches))


Jobs = Annotated[search_jobs.SearchJobs, Depends(get_search_jobs)]
router = APIRouter(prefix="/api/ownership", tags=["Ownership"],
                   dependencies=[Depends(require_business_api)])


@router.post("/attempts", response_model=AttemptView)
def start_attempt(body: SearchRequest, jobs: Jobs):
    """Start a search. Answers within the wait window, otherwise 'pending' for the client to poll."""
    return jobs.start(body.description)


@router.get("/attempts/{attempt_id}", response_model=AttemptView)
def get_attempt(attempt_id: UUID, session: DB):
    return workflow.get_view(session, attempt_id)


@router.post("/attempts/{attempt_id}/answers", response_model=AttemptView)
def submit_answer(attempt_id: UUID, body: AnswerRequest, session: DB, ai: AI):
    return workflow.answer(session, attempt_id, body, ai)


@router.get("/attempts/{attempt_id}/photo", response_class=Response,
            responses={200: {"content": {"image/jpeg": {}}}})
def get_photo(attempt_id: UUID, session: DB, store: Store):
    _, state = workflow.pending_photo_attempt(session, attempt_id)
    key = workflow.pending_photo(session, state).object_key
    session.rollback()
    jpeg = store.read(key)
    return Response(jpeg, media_type="image/jpeg", headers={
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Ownership-Photo-Token": str(state.photo_token),
    })


class PhotoConfirmationView(BaseModel):
    attempt: AttemptView
    return_code: ReturnCodeView | None


@router.post("/attempts/{attempt_id}/photo-confirmation", response_model=PhotoConfirmationView)
def submit_photo_confirmation(attempt_id: UUID, body: PhotoConfirmationRequest,
                              response: Response, session: DB, settings: Config):
    attempt = workflow.confirm_photo(session, attempt_id, body)
    if attempt.found_item_id is None:
        session.commit()
        code = None
    else:
        # Code issuance commits the confirmation and the hashed code together.
        code = issue_ownership_return_code(attempt.found_item_id, attempt_id, session, settings)
    response.headers["Cache-Control"] = "no-store"
    return PhotoConfirmationView(attempt=attempt, return_code=code)
