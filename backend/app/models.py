"""Registration/storage schema; identity and ownership verification are deferred."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, JSON, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


class Entity:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Kiosk(Entity, Base):
    __tablename__ = "kiosks"
    name: Mapped[str] = mapped_column(String(200))
    location_description: Mapped[str | None]
    latitude: Mapped[float | None]
    longitude: Mapped[float | None]
    __table_args__ = (
        CheckConstraint("(latitude IS NULL) = (longitude IS NULL)", name="coordinates_pair"),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
    )


class Category(Entity, Base):
    __tablename__ = "categories"
    code: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(200))


class Locker(Entity, Base):
    __tablename__ = "lockers"
    kiosk_id: Mapped[UUID] = mapped_column(ForeignKey("kiosks.id"))
    locker_number: Mapped[int]
    __table_args__ = (
        UniqueConstraint("kiosk_id", "locker_number"),
        CheckConstraint("locker_number > 0", name="positive_locker_number"),
    )


class Registration(Entity, Base):
    __tablename__ = "registrations"
    kiosk_id: Mapped[UUID] = mapped_column(ForeignKey("kiosks.id"))
    primary_category_id: Mapped[UUID | None] = mapped_column(ForeignKey("categories.id"))
    secondary_description: Mapped[str | None]
    status: Mapped[str] = mapped_column(default="draft")
    analysis_status: Mapped[str] = mapped_column(default="pending")
    analysis_input_image_ids: Mapped[list | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    analysis_features: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    __table_args__ = (
        CheckConstraint("status IN ('draft','capturing','analyzing','ready','opening','awaiting_deposit','stored','blocked')", name="registration_status"),
        CheckConstraint("analysis_status IN ('pending','running','succeeded','failed')", name="analysis_status"),
    )


class FoundItem(Entity, Base):
    __tablename__ = "found_items"
    registration_id: Mapped[UUID] = mapped_column(ForeignKey("registrations.id"), unique=True)
    status: Mapped[str] = mapped_column(default="awaiting_deposit")
    __table_args__ = (CheckConstraint("status IN ('awaiting_deposit','stored')", name="item_status"),)


class StorageAssignment(Entity, Base):
    __tablename__ = "storage_assignments"
    found_item_id: Mapped[UUID] = mapped_column(ForeignKey("found_items.id"))
    locker_id: Mapped[UUID] = mapped_column(ForeignKey("lockers.id"))
    status: Mapped[str] = mapped_column(default="reserved")
    deposited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("status IN ('reserved','awaiting_deposit','stored','cancelled')", name="assignment_status"),
        CheckConstraint("(status = 'cancelled') = (ended_at IS NOT NULL)", name="assignment_end"),
        Index("uq_active_locker", "locker_id", unique=True, postgresql_where=text("ended_at IS NULL"), sqlite_where=text("ended_at IS NULL")),
        Index("uq_active_item", "found_item_id", unique=True, postgresql_where=text("ended_at IS NULL"), sqlite_where=text("ended_at IS NULL")),
    )


class DeviceCommand(Base):
    __tablename__ = "device_commands"
    command_id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    registration_id: Mapped[UUID] = mapped_column(ForeignKey("registrations.id"))
    action: Mapped[str]
    shot_order: Mapped[int | None]
    storage_assignment_id: Mapped[UUID | None] = mapped_column(ForeignKey("storage_assignments.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    status: Mapped[str] = mapped_column(default="sent")
    error_code: Mapped[str | None]
    __table_args__ = (
        UniqueConstraint("registration_id", "shot_order"),
        CheckConstraint("status IN ('sent','succeeded','failed','unknown')", name="command_status"),
        CheckConstraint("(action = 'capture' AND shot_order IN (1,2) AND shot_order IS NOT NULL AND storage_assignment_id IS NULL) OR (action = 'open' AND shot_order IS NULL AND storage_assignment_id IS NOT NULL)", name="command_target"),
    )


class ItemImage(Entity, Base):
    __tablename__ = "item_images"
    registration_id: Mapped[UUID] = mapped_column(ForeignKey("registrations.id"))
    capture_command_id: Mapped[str] = mapped_column(ForeignKey("device_commands.command_id"), unique=True)
    shot_order: Mapped[int]
    object_key: Mapped[str] = mapped_column(unique=True)
    content_type: Mapped[str] = mapped_column(default="image/jpeg")
    byte_size: Mapped[int]
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        UniqueConstraint("registration_id", "shot_order"),
        CheckConstraint("shot_order IN (1,2)", name="image_order"),
        CheckConstraint("byte_size > 0", name="image_size"),
        CheckConstraint("content_type = 'image/jpeg'", name="image_type"),
    )
