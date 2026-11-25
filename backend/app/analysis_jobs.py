"""Run one photo analysis off the request thread, reporting its stage to a polling client.

Two-photo inference is the longest external call in the flow: its budget is
`IMAGE_ANALYSIS_TIMEOUT_SECONDS` (90초 기본) and a provider failure adds model retries inside it.
Held on the request thread, the kiosk shows a spinner with nothing behind it and a lost worker
leaves the registration in `analyzing/running` forever. Here the claim is stored first, the work
runs on a bounded pool, and `GET /api/registrations/{id}` reports the stage until it finishes.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ai_jobs, services
from app.config import Settings
from app.db import SessionLocal
from app.errors import IntegrationError
from app.integrations import FeatureAnalyzer, ImageStore
from app.models import Category, Registration


logger = logging.getLogger(__name__)

StageReporter = Callable[..., None]
AnalyzerFactory = Callable[[str | None, StageReporter], AbstractContextManager[FeatureAnalyzer]]
SessionFactory = Callable[[], AbstractContextManager[Session]]


class AnalysisJobs:
    def __init__(self, settings: Settings, analyzer_factory: AnalyzerFactory, store: ImageStore,
                 session_factory: SessionFactory = SessionLocal, executor=None):
        self.settings = settings
        self.analyzer_factory = analyzer_factory
        self.store = store
        self.session_factory = session_factory
        self.executor = executor

    def start(self, session: Session, registration_id: UUID) -> Registration:
        """Claim and dispatch. Returns the registration as it stands when the wait window ends."""
        claimed = services.start_analysis(session, registration_id,
                                          self.settings.image_analysis_timeout_seconds)
        if claimed is not None:
            # A failure inside the wait window reaches the caller exactly as it did when the
            # analysis ran on the request thread; a slower one is read from the registration.
            ai_jobs.dispatch(ai_jobs.ANALYSIS, self.executor, lambda: self._run(registration_id),
                             wait_seconds=self.settings.analysis_sync_wait_seconds, reraise=True)
        # The worker committed on its own session, so drop anything this one still remembers.
        session.expire_all()
        return services.registration(session, registration_id)

    def _run(self, registration_id: UUID) -> None:
        with ai_jobs.running(ai_jobs.ANALYSIS):
            try:
                self._analyze(registration_id)
            except IntegrationError:
                raise  # Already stored as the registration's failure code.
            except Exception:
                logger.exception("사진 분석 작업이 실패했습니다 (registration=%s)", registration_id)
                self._fail(registration_id)
                raise

    def _analyze(self, registration_id: UUID) -> None:
        with self.session_factory() as session:
            reg = services.registration(session, registration_id)
            category = session.scalar(select(Category.name).join(
                Registration, Registration.primary_category_id == Category.id
            ).where(Registration.id == registration_id))

            def report(stage: str, attempt: int = 0) -> None:
                services.record_analysis_stage(session, reg, stage, attempt)

            with self.analyzer_factory(category, report) as analyzer:
                services.run_analysis(session, registration_id, analyzer, self.store)

    def _fail(self, registration_id: UUID) -> None:
        try:
            with self.session_factory() as session:
                services.fail_analysis(session, services.registration(session, registration_id),
                                       "ANALYSIS_FAILED")
        except Exception:
            logger.exception("분석 실패 상태를 기록하지 못했습니다 (registration=%s)", registration_id)
