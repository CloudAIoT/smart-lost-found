"""Local development routes for the lost-item ownership flow."""

from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.errors import BusinessError
from . import workflow
from .workflow_ai import GeminiWorkflowAI, WorkflowAI
from .workflow_schemas import AnswerRequest, AttemptView, SearchRequest


Config = Annotated[Settings, Depends(get_settings)]
DB = Annotated[Session, Depends(get_session)]


def require_local_ownership(settings: Config) -> None:
    if not settings.local_development:
        raise BusinessError("OWNERSHIP_DEV_ONLY", 503)


def get_ownership_ai(settings: Config) -> Iterator[WorkflowAI]:
    with httpx.Client(trust_env=False) as client:
        yield GeminiWorkflowAI(settings, client)


AI = Annotated[WorkflowAI, Depends(get_ownership_ai)]
router = APIRouter(prefix="/api/ownership", tags=["Ownership (development)"],
                   dependencies=[Depends(require_local_ownership)])


@router.post("/attempts", response_model=AttemptView)
def start_attempt(body: SearchRequest, session: DB, ai: AI):
    return workflow.start(session, body.description, ai)


@router.get("/attempts/{attempt_id}", response_model=AttemptView)
def get_attempt(attempt_id: UUID, session: DB):
    return workflow.get_view(session, attempt_id)


@router.post("/attempts/{attempt_id}/answers", response_model=AttemptView)
def submit_answer(attempt_id: UUID, body: AnswerRequest, session: DB, ai: AI):
    return workflow.answer(session, attempt_id, body, ai)
