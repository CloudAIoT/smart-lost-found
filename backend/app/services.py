"""Persist intent before external effects; never retry an uncertain device command."""

from datetime import timedelta
import json
from uuid import UUID, uuid4

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.integrations import AnalysisInput, AnalysisResult, DeviceGateway, FeatureAnalyzer, ImageStore, IntegrationError, validate_jpeg
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


def capture(session: Session, registration_id: UUID, shot: int, gateway: DeviceGateway, store: ImageStore, *, ttl: int, max_bytes: int) -> ItemImage:
    if shot not in (1, 2):
        raise BusinessError("INVALID_SHOT_ORDER", 422)
    registration(session, registration_id)
    existing = session.scalar(select(ItemImage).where(ItemImage.registration_id == registration_id, ItemImage.shot_order == shot))
    if existing:
        return existing
    reg = claim(session, registration_id, ("draft",), "capturing")
    current = images(session, registration_id)
    if len(current) != shot - 1:
        session.rollback()
        raise BusinessError("SHOT_OUT_OF_ORDER")
    command = DeviceCommand(command_id=str(uuid4()), registration_id=registration_id, action="capture",
                            shot_order=shot, expires_at=now() + timedelta(seconds=ttl))
    session.add(command)
    session.commit()
    # IDs and intent have been committed before the first external call.
    try:
        jpeg = gateway.capture(reg.kiosk_id, command.command_id, registration_id, command.expires_at)
        validate_jpeg(jpeg, max_bytes)
        received = now()
        key = f"registrations/{registration_id}/{command.command_id}.jpg"
        store.put(key, jpeg)
    except IntegrationError as error:
        command.status = "unknown" if error.uncertain else "failed"
        command.error_code = error.code
        reg.status = "blocked"
        session.commit()
        raise
    value = ItemImage(registration_id=registration_id, capture_command_id=command.command_id,
                      shot_order=shot, object_key=key, byte_size=len(jpeg), received_at=received)
    session.add(value)
    command.status = "succeeded"
    reg.status = "draft"
    session.commit()
    return value


def analyze(session: Session, registration_id: UUID, analyzer: FeatureAnalyzer, store: ImageStore) -> Registration:
    reg = registration(session, registration_id)
    if reg.status == "ready" and reg.analysis_status == "succeeded":
        return reg
    reg = claim(session, registration_id, ("draft",), "analyzing")
    current = images(session, registration_id)
    if [image.shot_order for image in current] != [1, 2]:
        session.rollback()
        raise BusinessError("TWO_IMAGES_REQUIRED")
    ids = tuple(image.id for image in current)
    keys = [image.object_key for image in current]
    reg.analysis_status = "running"
    reg.analysis_input_image_ids = [str(value) for value in ids]
    session.commit()
    try:
        inputs = tuple(AnalysisInput(image_id=value, jpeg=store.read(key)) for value, key in zip(ids, keys))
        result = analyzer.analyze(registration_id, inputs)
        if not isinstance(result, AnalysisResult) or result.image_ids != ids or not isinstance(result.features, dict) or not result.features:
            raise IntegrationError("INVALID_ANALYSIS_RESULT")
        try:
            json.dumps(result.features, allow_nan=False)
        except (ValueError, TypeError):
            raise IntegrationError("INVALID_ANALYSIS_RESULT") from None
    except IntegrationError:
        reg.analysis_status = "failed"
        reg.status = "draft"
        reg.analysis_features = None
        session.commit()
        raise
    reg.analysis_features = result.features
    reg.analysis_status = "succeeded"
    reg.status = "ready"
    session.commit()
    return reg


def assignment_for(session: Session, registration_id: UUID) -> StorageAssignment | None:
    return session.scalar(select(StorageAssignment).join(FoundItem).where(
        FoundItem.registration_id == registration_id, StorageAssignment.ended_at.is_(None)))
