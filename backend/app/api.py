from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response
import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_ownership_verification import search_jobs
from app.ai_ownership_verification.workflow import authorize_return_code
from app.ai_ownership_verification.workflow_ai import GeminiWorkflowAI
from app.config import Settings, get_settings
from app.admin import require_admin
from app.kiosk_operations import operation_status, require_operational, resolve_alert
from app.db import SessionLocal, get_session
from app.dev_device import FakeDeviceGateway
from app.device_telemetry import kiosk_device_states, recent_alerts
from app.integrations import IntegrationError, PiGateway, UnconfiguredAnalyzer, make_store
from app.models import Category, FoundItem, Kiosk, Locker, Registration
from app.photo_analysis import GeminiPhotoAnalyzer
from app import ai_jobs, ai_metrics, analysis_jobs, services


def require_business_api(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    if not (settings.local_development or settings.business_api_enabled):
        raise services.BusinessError("BUSINESS_API_DISABLED", 503)


router = APIRouter(prefix="/api", dependencies=[Depends(require_business_api)])
DB = Annotated[Session, Depends(get_session)]
Config = Annotated[Settings, Depends(get_settings)]
Admin = Annotated[str, Depends(require_admin)]


def get_gateway(settings: Config) -> Generator[object, None, None]:
    # Development only: an explicit setting replaces the Pi, never a device failure.
    if settings.dev_fake_device:
        yield FakeDeviceGateway()
        return
    with httpx.Client(trust_env=False) as client:
        yield PiGateway(settings, client)


def get_store(settings: Config):
    return make_store(settings)


def get_session_factory() -> Callable[[], AbstractContextManager[Session]]:
    # A background job cannot borrow the request session: it outlives the response.
    return SessionLocal


def get_analyzer_factory(settings: Config) -> analysis_jobs.AnalyzerFactory:
    @contextmanager
    def factory(category: str | None, on_stage: analysis_jobs.StageReporter):
        if not settings.gemini_api_key or not settings.gemini_model:
            yield UnconfiguredAnalyzer()
            return
        analysis_settings = settings.model_copy(update={
            "gemini_timeout_seconds": settings.image_analysis_timeout_seconds,
        })
        with httpx.Client(trust_env=False) as client:
            yield GeminiPhotoAnalyzer(GeminiWorkflowAI(analysis_settings, client), category,
                                      on_stage=on_stage)

    return factory


Sessions = Annotated[Callable[[], AbstractContextManager[Session]], Depends(get_session_factory)]
AnalyzerFactory = Annotated[analysis_jobs.AnalyzerFactory, Depends(get_analyzer_factory)]


Gateway = Annotated[object, Depends(get_gateway)]
Store = Annotated[object, Depends(get_store)]
ShotOrder = Annotated[int, Path(ge=1, le=2)]


def get_analysis_jobs(settings: Config, analyzer_factory: AnalyzerFactory, store: Store,
                      session_factory: Sessions) -> analysis_jobs.AnalysisJobs:
    return analysis_jobs.AnalysisJobs(settings, analyzer_factory, store, session_factory,
                                      ai_jobs.pool(ai_jobs.ANALYSIS, settings.max_concurrent_analyses))


AnalysisJobs = Annotated[analysis_jobs.AnalysisJobs, Depends(get_analysis_jobs)]


class RegistrationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kiosk_id: UUID
    primary_category_id: UUID | None = None
    secondary_description: str | None = Field(default=None, max_length=2000)
    found_location: str | None = Field(default=None, max_length=200)


class RegistrationView(BaseModel):
    id: UUID
    kiosk_id: UUID
    primary_category_id: UUID | None
    secondary_description: str | None
    found_location: str | None
    status: str
    analysis_status: str
    images: list[dict]
    locker_number: int | None
    # Progress of the most recent analysis. Null until one runs.
    analysis_stage: str | None = None
    analysis_attempt: int | None = None
    analysis_elapsed_ms: int | None = None
    analysis_error_code: str | None = None
    analysis_retry_after_ms: int | None = None


def registration_view(session: Session, reg: Registration) -> RegistrationView:
    number = None
    if reg.status in ("awaiting_deposit", "stored"):
        assignment = services.assignment_for(session, reg.id)
        # Pickup ends the assignment while the registration remains stored.
        if assignment is not None:
            number = session.get(Locker, assignment.locker_id).locker_number
    progress = reg.analysis_progress or {}
    return RegistrationView(id=reg.id, kiosk_id=reg.kiosk_id, primary_category_id=reg.primary_category_id,
                            secondary_description=reg.secondary_description, found_location=reg.found_location, status=reg.status,
                            analysis_status=reg.analysis_status, locker_number=number,
                            images=[{"id": value.id, "shot_order": value.shot_order} for value in services.images(session, reg.id)],
                            analysis_stage=progress.get("stage"), analysis_attempt=progress.get("attempt") or None,
                            analysis_elapsed_ms=services.analysis_elapsed_ms(progress),
                            analysis_error_code=progress.get("error_code"),
                            analysis_retry_after_ms=(ANALYSIS_POLL_INTERVAL_MS
                                                     if reg.analysis_status == "running" else None))


@router.get("/kiosks")
def list_kiosks(session: DB, limit: Annotated[int, Query(ge=1, le=100)] = 20,
                offset: Annotated[int, Query(ge=0)] = 0):
    rows = session.scalars(select(Kiosk).order_by(Kiosk.name, Kiosk.id).limit(limit).offset(offset))
    return [{"id": row.id, "name": row.name, "location_description": row.location_description,
             "latitude": row.latitude, "longitude": row.longitude} for row in rows]


@router.get("/kiosks/{kiosk_id}/device-status")
def kiosk_device_status(kiosk_id: UUID, session: DB):
    """Latest MQTT-reported state for one kiosk. Reports observation, never a business decision."""
    services.kiosk(session, kiosk_id)
    return kiosk_device_states(session, kiosk_id)


@router.get("/alerts")
def list_alerts(session: DB, response: Response, kiosk_id: UUID | None = None,
                severity: Annotated[Literal["alarm", "warning"] | None, Query()] = None,
                since: Annotated[datetime | None, Query()] = None,
                unresolved_only: bool = False,
                limit: Annotated[int, Query(ge=1, le=200)] = 50,
                offset: Annotated[int, Query(ge=0)] = 0):
    """Device alerts, newest first, including resolution state. Alarm/audit history is retained."""
    if kiosk_id is not None:
        services.kiosk(session, kiosk_id)
    response.headers["Cache-Control"] = "no-store"
    return recent_alerts(session, kiosk_id=kiosk_id, severity=severity, since=since,
                         limit=limit, offset=offset, unresolved_only=unresolved_only)


@router.get("/kiosks/{kiosk_id}/operation-status")
def get_operation_status(kiosk_id: UUID, session: DB, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return operation_status(session, kiosk_id)


@router.get("/admin/session")
def admin_session(actor: Admin, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"username": actor}


@router.get("/admin/alerts")
def admin_alerts(actor: Admin, session: DB, response: Response, kiosk_id: UUID,
                 unresolved_only: bool = False,
                 limit: Annotated[int, Query(ge=1, le=200)] = 50,
                 offset: Annotated[int, Query(ge=0)] = 0):
    services.kiosk(session, kiosk_id)
    response.headers["Cache-Control"] = "no-store"
    return recent_alerts(session, kiosk_id=kiosk_id, unresolved_only=unresolved_only,
                         limit=limit, offset=offset, audit=True)


class AlertResolutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("note")
    @classmethod
    def nonblank_note(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A resolution note is required")
        return value.strip()


@router.post("/admin/alerts/{alert_id}/resolve")
def resolve_device_alert(alert_id: UUID, body: AlertResolutionInput, actor: Admin,
                          session: DB, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return resolve_alert(session, alert_id, actor, body.note)


@router.get("/ai/status")
def ai_status(settings: Config):
    """Measured AI call latency and the limits one search runs under. Calls no provider itself."""
    key = settings.gemini_api_key
    return {
        "configured": bool(key and key.get_secret_value().strip() and settings.gemini_model),
        "limits": {
            "call_timeout_seconds": settings.gemini_timeout_seconds,
            "search_budget_seconds": settings.ownership_search_budget_seconds,
            "sync_wait_seconds": settings.ownership_sync_wait_seconds,
            "search_concurrency": settings.ownership_search_concurrency,
            "max_concurrent_searches": settings.ownership_max_concurrent_searches,
            "analysis_timeout_seconds": settings.image_analysis_timeout_seconds,
            "analysis_sync_wait_seconds": settings.analysis_sync_wait_seconds,
            "max_concurrent_analyses": settings.max_concurrent_analyses,
        },
        "searches": search_jobs.snapshot(),
        "analyses": ai_jobs.snapshot(ai_jobs.ANALYSIS),
        "recent_calls": ai_metrics.summary(),
    }


@router.get("/categories")
def list_categories(session: DB):
    return [{"id": row.id, "code": row.code, "name": row.name} for row in session.scalars(select(Category).order_by(Category.code))]


@router.post("/registrations", response_model=RegistrationView, status_code=201)
def create_registration(body: RegistrationInput, session: DB):
    return registration_view(session, services.create_registration(
        session, body.kiosk_id, body.primary_category_id, body.secondary_description,
        found_location=(body.found_location or "").strip() or None))


ANALYSIS_POLL_INTERVAL_MS = 1000


@router.get("/registrations/{registration_id}", response_model=RegistrationView)
def get_registration(registration_id: UUID, session: DB):
    # Reading the registration is also how a client learns an analysis worker never came back.
    services.expire_stale_analysis(session, registration_id)
    return registration_view(session, services.registration(session, registration_id))


@router.post("/registrations/{registration_id}/shots/{shot_order}", response_model=RegistrationView)
def capture(registration_id: UUID, shot_order: ShotOrder, session: DB, gateway: Gateway, store: Store, settings: Config):
    if not settings.command_ttl_seconds or not settings.max_image_bytes:
        raise IntegrationError("CAPTURE_NOT_CONFIGURED")
    services.capture(session, registration_id, shot_order, gateway, store,
                     ttl=settings.command_ttl_seconds, max_bytes=settings.max_image_bytes)
    return registration_view(session, services.registration(session, registration_id))


@router.get("/registrations/{registration_id}/shots/{shot_order}/preview")
def preview(registration_id: UUID, shot_order: ShotOrder, session: DB, store: Store):
    services.registration(session, registration_id)
    value = next((value for value in services.images(session, registration_id) if value.shot_order == shot_order), None)
    if value is None:
        raise services.BusinessError("IMAGE_NOT_FOUND", 404)
    return {"url": store.preview(value.object_key)}


@router.post("/registrations/{registration_id}/analysis", response_model=RegistrationView)
def analyze(registration_id: UUID, session: DB, jobs: AnalysisJobs):
    """Start the analysis. Answers within the wait window, otherwise `running` for the client to poll."""
    require_operational(session, services.registration(session, registration_id).kiosk_id)
    return registration_view(session, jobs.start(session, registration_id))


@router.post("/registrations/{registration_id}/deposit/open", response_model=RegistrationView)
def open_deposit(registration_id: UUID, session: DB, gateway: Gateway, settings: Config):
    if not settings.command_ttl_seconds:
        raise IntegrationError("DEVICE_NOT_CONFIGURED")
    services.open_deposit(session, registration_id, gateway, ttl=settings.command_ttl_seconds)
    return registration_view(session, services.registration(session, registration_id))


@router.post("/registrations/{registration_id}/deposit/reopen", response_model=RegistrationView)
def reopen_deposit(registration_id: UUID, session: DB, gateway: Gateway, settings: Config):
    """Open the assigned locker again when a completion was refused for an empty locker."""
    if not settings.command_ttl_seconds:
        raise IntegrationError("DEVICE_NOT_CONFIGURED")
    services.reopen_deposit(session, registration_id, gateway, ttl=settings.command_ttl_seconds)
    return registration_view(session, services.registration(session, registration_id))


class CompletionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]


@router.post("/registrations/{registration_id}/deposit/complete", response_model=RegistrationView)
def complete_deposit(registration_id: UUID, body: CompletionInput, session: DB, gateway: Gateway):
    services.complete_deposit(session, registration_id, gateway)
    return registration_view(session, services.registration(session, registration_id))


class ReturnCodeView(BaseModel):
    found_item_id: UUID
    code: str
    expires_at: datetime
    kiosk_id: UUID
    kiosk_name: str


class ReturnCodeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt_id: UUID


class PickupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=4, max_length=64)
    kiosk_id: UUID


class PickupView(BaseModel):
    found_item_id: UUID
    kiosk_id: UUID
    locker_number: int
    item_status: str
    assignment_status: str


def pickup_view(session: Session, assignment) -> PickupView:
    locker = session.get(Locker, assignment.locker_id)
    item = session.get(FoundItem, assignment.found_item_id)
    return PickupView(found_item_id=item.id, kiosk_id=locker.kiosk_id, locker_number=locker.locker_number,
                      item_status=item.status, assignment_status=assignment.status)


@router.post("/found-items/{found_item_id}/return-code", response_model=ReturnCodeView, status_code=201)
def create_return_code(found_item_id: UUID, body: ReturnCodeInput, response: Response, session: DB, settings: Config):
    result = issue_ownership_return_code(found_item_id, body.attempt_id, session, settings)
    response.headers["Cache-Control"] = "no-store"
    return result


def issue_ownership_return_code(found_item_id: UUID, attempt_id: UUID, session: Session, settings: Settings) -> ReturnCodeView:
    if not settings.return_code_ttl_seconds:
        raise IntegrationError("RETURN_CODE_NOT_CONFIGURED")
    item = services.found_item(session, found_item_id)
    require_operational(session, services.registration(session, item.registration_id).kiosk_id)
    authorize_return_code(session, attempt_id, found_item_id)
    assignment = services.latest_assignment(session, found_item_id)
    if assignment is None or assignment.status != "stored" or assignment.ended_at is not None:
        raise services.BusinessError("OWNERSHIP_ITEM_NO_LONGER_AVAILABLE")
    kiosk_id, kiosk_name = session.execute(
        select(Kiosk.id, Kiosk.name).join(Locker).where(Locker.id == assignment.locker_id)
    ).one()
    code, record = services.issue_return_code(session, found_item_id,
                                              ttl=settings.return_code_ttl_seconds)
    return ReturnCodeView(found_item_id=found_item_id, code=code, expires_at=services.as_utc(record.expires_at),
                          kiosk_id=kiosk_id, kiosk_name=kiosk_name)


@router.post("/pickup/open", response_model=PickupView)
def open_pickup(body: PickupInput, session: DB, gateway: Gateway, settings: Config):
    if not settings.command_ttl_seconds:
        raise IntegrationError("DEVICE_NOT_CONFIGURED")
    assignment = services.open_pickup(session, body.code, body.kiosk_id, gateway,
                                      ttl=settings.command_ttl_seconds)
    return pickup_view(session, assignment)


@router.post("/pickup/{found_item_id}/reopen", response_model=PickupView)
def reopen_pickup(found_item_id: UUID, session: DB, gateway: Gateway, settings: Config):
    """Open the same locker again after a refused pickup. The return code stays spent."""
    if not settings.command_ttl_seconds:
        raise IntegrationError("DEVICE_NOT_CONFIGURED")
    assignment = services.reopen_pickup(session, found_item_id, gateway,
                                        ttl=settings.command_ttl_seconds)
    return pickup_view(session, assignment)


@router.post("/pickup/{found_item_id}/complete", response_model=PickupView)
def complete_pickup(found_item_id: UUID, body: CompletionInput, session: DB, gateway: Gateway):
    return pickup_view(session, services.complete_pickup(session, found_item_id, gateway))
