from collections.abc import Generator
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.integrations import IntegrationError, PiGateway, UnconfiguredAnalyzer, make_store
from app.models import Category, FoundItem, Kiosk, Locker, Registration
from app import services


def require_business_api(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    if not (settings.local_development or settings.business_api_enabled):
        raise services.BusinessError("BUSINESS_API_DISABLED", 503)


router = APIRouter(prefix="/api", dependencies=[Depends(require_business_api)])
DB = Annotated[Session, Depends(get_session)]
Config = Annotated[Settings, Depends(get_settings)]


def get_gateway(settings: Config) -> Generator[PiGateway, None, None]:
    with httpx.Client(trust_env=False) as client:
        yield PiGateway(settings, client)


def get_store(settings: Config):
    return make_store(settings)


def get_analyzer():
    return UnconfiguredAnalyzer()


Gateway = Annotated[object, Depends(get_gateway)]
Store = Annotated[object, Depends(get_store)]
Analyzer = Annotated[object, Depends(get_analyzer)]
ShotOrder = Annotated[int, Path(ge=1, le=2)]


class RegistrationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kiosk_id: UUID
    primary_category_id: UUID | None = None
    secondary_description: str | None = Field(default=None, max_length=2000)


class RegistrationView(BaseModel):
    id: UUID
    kiosk_id: UUID
    primary_category_id: UUID | None
    secondary_description: str | None
    status: str
    analysis_status: str
    images: list[dict]
    locker_number: int | None


def registration_view(session: Session, reg: Registration) -> RegistrationView:
    number = None
    if reg.status in ("awaiting_deposit", "stored"):
        assignment = services.assignment_for(session, reg.id)
        number = session.get(Locker, assignment.locker_id).locker_number
    return RegistrationView(id=reg.id, kiosk_id=reg.kiosk_id, primary_category_id=reg.primary_category_id,
                            secondary_description=reg.secondary_description, status=reg.status,
                            analysis_status=reg.analysis_status, locker_number=number,
                            images=[{"id": value.id, "shot_order": value.shot_order} for value in services.images(session, reg.id)])


@router.get("/kiosks")
def list_kiosks(session: DB, limit: Annotated[int, Query(ge=1, le=100)] = 20,
                offset: Annotated[int, Query(ge=0)] = 0):
    rows = session.scalars(select(Kiosk).order_by(Kiosk.name, Kiosk.id).limit(limit).offset(offset))
    return [{"id": row.id, "name": row.name, "location_description": row.location_description,
             "latitude": row.latitude, "longitude": row.longitude} for row in rows]


@router.get("/categories")
def list_categories(session: DB):
    return [{"id": row.id, "code": row.code, "name": row.name} for row in session.scalars(select(Category).order_by(Category.code))]


@router.post("/registrations", response_model=RegistrationView, status_code=201)
def create_registration(body: RegistrationInput, session: DB):
    return registration_view(session, services.create_registration(session, body.kiosk_id, body.primary_category_id, body.secondary_description))


@router.get("/registrations/{registration_id}", response_model=RegistrationView)
def get_registration(registration_id: UUID, session: DB):
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
def analyze(registration_id: UUID, session: DB, analyzer: Analyzer, store: Store):
    return registration_view(session, services.analyze(session, registration_id, analyzer, store))


@router.post("/registrations/{registration_id}/deposit/open", response_model=RegistrationView)
def open_deposit(registration_id: UUID, session: DB, gateway: Gateway, settings: Config):
    if not settings.command_ttl_seconds:
        raise IntegrationError("DEVICE_NOT_CONFIGURED")
    services.open_deposit(session, registration_id, gateway, ttl=settings.command_ttl_seconds)
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
def create_return_code(found_item_id: UUID, session: DB, settings: Config):
    if not settings.return_code_ttl_seconds:
        raise IntegrationError("RETURN_CODE_NOT_CONFIGURED")
    code, record = services.issue_return_code(session, found_item_id,
                                              ttl=settings.return_code_ttl_seconds)
    # The plaintext code is delivered once here and never stored or returned again.
    return ReturnCodeView(found_item_id=found_item_id, code=code, expires_at=services.as_utc(record.expires_at))


@router.post("/pickup/open", response_model=PickupView)
def open_pickup(body: PickupInput, session: DB, gateway: Gateway, settings: Config):
    if not settings.command_ttl_seconds:
        raise IntegrationError("DEVICE_NOT_CONFIGURED")
    assignment = services.open_pickup(session, body.code, body.kiosk_id, gateway,
                                      ttl=settings.command_ttl_seconds)
    return pickup_view(session, assignment)


@router.post("/pickup/{found_item_id}/complete", response_model=PickupView)
def complete_pickup(found_item_id: UUID, body: CompletionInput, session: DB, gateway: Gateway):
    return pickup_view(session, services.complete_pickup(session, found_item_id, gateway))
