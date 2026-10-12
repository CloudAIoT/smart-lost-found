from collections.abc import Generator
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.models import Category, Kiosk, Locker, Registration
from app import services


def require_local_development(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    if not settings.local_development:
        raise services.BusinessError("BUSINESS_API_DISABLED", 503)


router = APIRouter(prefix="/api", dependencies=[Depends(require_local_development)])
DB = Annotated[Session, Depends(get_session)]
Config = Annotated[Settings, Depends(get_settings)]










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
