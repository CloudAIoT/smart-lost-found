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


def open_deposit(session: Session, registration_id: UUID, gateway: DeviceGateway, *, ttl: int) -> StorageAssignment:
    reg = registration(session, registration_id)
    if reg.status in ("awaiting_deposit", "stored"):
        return assignment_for(session, registration_id)
    reg = claim(session, registration_id, ("ready",), "opening")
    current = images(session, registration_id)
    if (reg.analysis_status != "succeeded" or [image.shot_order for image in current] != [1, 2]
            or reg.analysis_input_image_ids != [str(image.id) for image in current]):
        session.rollback()
        raise BusinessError("ANALYSIS_REQUIRED")
    # Serialize allocation within a kiosk before taking a fresh availability snapshot.
    # Locking only a candidate row can see a stale NOT EXISTS result when another
    # transaction commits its reservation between the snapshot and row lock.
    session.execute(select(Kiosk.id).where(Kiosk.id == reg.kiosk_id).with_for_update()).scalar_one()
    # Partial UNIQUE indexes remain the final guard against out-of-band writers.
    locker = session.scalar(select(Locker).where(Locker.kiosk_id == reg.kiosk_id, ~exists().where(
        StorageAssignment.locker_id == Locker.id, StorageAssignment.ended_at.is_(None)))
        .order_by(Locker.locker_number).limit(1).with_for_update(skip_locked=True))
    if locker is None:
        session.rollback()
        raise BusinessError("NO_AVAILABLE_LOCKER")
    item = FoundItem(registration_id=registration_id)
    session.add(item)
    session.flush()
    assignment = StorageAssignment(found_item_id=item.id, locker_id=locker.id)
    session.add(assignment)
    try:
        session.flush()
        command = DeviceCommand(command_id=str(uuid4()), registration_id=registration_id, action="open",
                                storage_assignment_id=assignment.id, expires_at=now() + timedelta(seconds=ttl))
        session.add(command)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise BusinessError("ALLOCATION_CONFLICT") from None
    try:
        gateway.open(reg.kiosk_id, locker.locker_number, command.command_id, command.expires_at)
    except IntegrationError as error:
        command.status = "unknown" if error.uncertain else "failed"
        command.error_code = error.code
        reg.status = "blocked"
        # Keep the assignment until a recovery policy is agreed, including explicit failures.
        session.commit()
        raise
    command.status = "succeeded"
    assignment.status = "awaiting_deposit"
    reg.status = "awaiting_deposit"
    session.commit()
    return assignment


def complete_deposit(session: Session, registration_id: UUID, gateway: DeviceGateway) -> StorageAssignment:
    reg = registration(session, registration_id)
    assignment = assignment_for(session, registration_id)
    if reg.status == "stored":
        return assignment
    if reg.status != "awaiting_deposit" or assignment is None or assignment.status != "awaiting_deposit":
        raise BusinessError("DEPOSIT_NOT_OPENED")
    locker = session.get(Locker, assignment.locker_id)
    door = gateway.door(reg.kiosk_id, locker.locker_number)
    if door != "closed":
        raise BusinessError("DOOR_NOT_CLOSED")
    # The API call itself is the explicit user confirmation. A sensor poll never completes a deposit.
    # Reload under a row lock after the sensor call. Two completion requests must
    # preserve the first deposited_at rather than overwrite it from stale objects.
    reg = session.scalar(select(Registration).where(Registration.id == registration_id)
                         .with_for_update().execution_options(populate_existing=True))
    if reg.status == "stored":
        session.rollback()
        return assignment_for(session, registration_id)
    if reg.status != "awaiting_deposit":
        session.rollback()
        raise BusinessError("DEPOSIT_NOT_OPENED")
    session.refresh(assignment)
    reg.status = "stored"
    assignment.status = "stored"
    assignment.deposited_at = assignment.deposited_at or now()
    session.get(FoundItem, assignment.found_item_id).status = "stored"
    session.commit()
    return assignment


def search_stored_items(session: Session, *, category_id: UUID | None = None, kiosk_id: UUID | None = None,
                        limit: int = 20, offset: int = 0) -> list[dict]:
    """Internal exact-filter experiment; not a public search contract."""
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("Invalid pagination")
    query = (select(FoundItem, Registration, Kiosk).join(Registration, FoundItem.registration_id == Registration.id)
             .join(StorageAssignment, StorageAssignment.found_item_id == FoundItem.id)
             .join(Locker, StorageAssignment.locker_id == Locker.id).join(Kiosk, Locker.kiosk_id == Kiosk.id)
             .where(FoundItem.status == "stored", StorageAssignment.status == "stored", StorageAssignment.ended_at.is_(None)))
    if category_id is not None:
        query = query.where(Registration.primary_category_id == category_id)
    if kiosk_id is not None:
        query = query.where(Kiosk.id == kiosk_id)
    rows = session.execute(query.order_by(FoundItem.created_at, FoundItem.id).limit(limit).offset(offset))
    return [{"item_id": item.id, "category_id": reg.primary_category_id, "kiosk_id": kiosk.id,
             "kiosk_name": kiosk.name, "location_description": kiosk.location_description,
             "latitude": kiosk.latitude, "longitude": kiosk.longitude} for item, reg, kiosk in rows]
