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
from app.db import get_session
from . import workflow
from .workflow_ai import GeminiWorkflowAI, WorkflowAI
from .workflow_schemas import AnswerRequest, AttemptView, PhotoConfirmationRequest, SearchRequest


Config = Annotated[Settings, Depends(get_settings)]
DB = Annotated[Session, Depends(get_session)]


def get_ownership_ai(settings: Config) -> Iterator[WorkflowAI]:
    with httpx.Client(trust_env=False) as client:
        yield GeminiWorkflowAI(settings, client)


AI = Annotated[WorkflowAI, Depends(get_ownership_ai)]
router = APIRouter(prefix="/api/ownership", tags=["Ownership"],
                   dependencies=[Depends(require_business_api)])


@router.post("/attempts", response_model=AttemptView)
def start_attempt(body: SearchRequest, session: DB, ai: AI):
    return workflow.start(session, body.description, ai)


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
