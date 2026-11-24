"""Run one ownership search off the request thread.

A single search makes clue extraction, candidate matching and option generation calls in a row,
each with model fallback, so its duration follows the provider and the number of stored items. Held
on the request thread it reaches the proxy deadline and the caller sees a dropped connection instead
of an answer. Here the attempt row is stored first, the work runs on a bounded pool, and the caller
polls the attempt. The short wait below keeps the common fast search on the immediate response.
"""

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager
import logging
from uuid import UUID, uuid4

import httpx
from sqlalchemy.orm import Session

from app import ai_jobs
from app.config import Settings
from app.db import SessionLocal
from . import workflow
from .workflow_ai import GeminiWorkflowAI, WorkflowAI
from .workflow_schemas import AttemptView


logger = logging.getLogger(__name__)

AiFactory = Callable[[], AbstractContextManager[WorkflowAI]]
SessionFactory = Callable[[], AbstractContextManager[Session]]


def gemini_factory(settings: Settings) -> AiFactory:
    @contextmanager
    def factory() -> Iterator[WorkflowAI]:
        # The request-scoped client is closed with the response, so a background search needs
        # its own. The budget starts here, inside the worker, not while the job was queued.
        with httpx.Client(trust_env=False) as client:
            yield GeminiWorkflowAI(settings, client,
                                   budget_seconds=settings.ownership_search_budget_seconds)

    return factory


def shared_executor(max_workers: int) -> ThreadPoolExecutor:
    return ai_jobs.pool(ai_jobs.SEARCH, max_workers)


def snapshot() -> dict[str, int]:
    return ai_jobs.snapshot(ai_jobs.SEARCH)


class SearchJobs:
    def __init__(self, settings: Settings, ai_factory: AiFactory,
                 session_factory: SessionFactory = SessionLocal,
                 executor: ThreadPoolExecutor | None = None):
        self.settings = settings
        self.ai_factory = ai_factory
        self.session_factory = session_factory
        self.executor = executor

    def start(self, description: str) -> AttemptView:
        attempt_id = uuid4()
        with self.session_factory() as session:
            workflow.create_pending(session, attempt_id, self.settings.ownership_search_budget_seconds)
        self._dispatch(attempt_id, description)
        with self.session_factory() as session:
            return workflow.get_view(session, attempt_id)

    def _dispatch(self, attempt_id: UUID, description: str) -> None:
        # The stored attempt carries every outcome, so a failure needs no exception here.
        ai_jobs.dispatch(ai_jobs.SEARCH, self.executor, lambda: self._run(attempt_id, description),
                         wait_seconds=self.settings.ownership_sync_wait_seconds, reraise=False)

    def _run(self, attempt_id: UUID, description: str) -> None:
        with ai_jobs.running(ai_jobs.SEARCH):
            try:
                with self.ai_factory() as ai, self.session_factory() as session:
                    workflow.run_search(session, attempt_id, description, ai,
                                        budget_seconds=self.settings.ownership_search_budget_seconds,
                                        concurrency=self.settings.ownership_search_concurrency)
            except Exception:
                # Provider failures are already stored as an outcome. Anything left here is ours,
                # and leaving the attempt pending would make the client poll a row nobody finishes.
                logger.exception("소유 확인 검색 작업이 실패했습니다 (attempt=%s)", attempt_id)
                self._fail(attempt_id)

    def _fail(self, attempt_id: UUID) -> None:
        try:
            with self.session_factory() as session:
                workflow.fail_search(session, attempt_id, "OWNERSHIP_SEARCH_FAILED")
        except Exception:
            logger.exception("검색 실패 상태를 기록하지 못했습니다 (attempt=%s)", attempt_id)
