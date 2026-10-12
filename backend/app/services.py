"""Persist intent before external effects; never retry an uncertain device command."""

from datetime import timedelta
import json
from uuid import UUID, uuid4

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Category, DeviceCommand, FoundItem, ItemImage, Kiosk, Locker, Registration, StorageAssignment, now
from app.errors import BusinessError


def registration(session: Session, registration_id: UUID) -> Registration:
    value = session.get(Registration, registration_id)
    if value is None:
        raise BusinessError("REGISTRATION_NOT_FOUND", 404)
    return value


def images(session: Session, registration_id: UUID) -> list[ItemImage]:
    return list(session.scalars(select(ItemImage).where(ItemImage.registration_id == registration_id).order_by(ItemImage.shot_order)))


def claim(session: Session, registration_id: UUID, allowed: tuple[str, ...], target: str) -> Registration:
    result = session.execute(update(Registration).where(Registration.id == registration_id,
                            Registration.status.in_(allowed)).values(status=target))
    if result.rowcount != 1:
        session.rollback()
        registration(session, registration_id)
        raise BusinessError("INVALID_REGISTRATION_STATE")
    return registration(session, registration_id)


def create_registration(session: Session, kiosk_id: UUID, category_id: UUID | None, description: str | None) -> Registration:
    if session.get(Kiosk, kiosk_id) is None:
        raise BusinessError("KIOSK_NOT_FOUND", 404)
    if category_id is not None and session.get(Category, category_id) is None:
        raise BusinessError("CATEGORY_NOT_FOUND", 404)
    value = Registration(kiosk_id=kiosk_id, primary_category_id=category_id, secondary_description=description)
    session.add(value)
    session.commit()
    return value






def assignment_for(session: Session, registration_id: UUID) -> StorageAssignment | None:
    return session.scalar(select(StorageAssignment).join(FoundItem).where(
        FoundItem.registration_id == registration_id, StorageAssignment.ended_at.is_(None)))
