"""Turn MQTT device messages into rows (docs/mqtt-protocol.md).

Separated from the MQTT client so the contract can be tested without a broker. Everything here
treats a message as untrusted input: a malformed topic or payload is reported as ignored, never
raised, because one bad message must not stop the subscriber from reading the next one.

What arrives is observation, not authority. Deposit and pickup completion still need the user's
confirmation and the HTTP door check (D03, D06); these rows only drive warnings and displays.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import DeviceAlert, DeviceState

log = logging.getLogger("app.mqtt")

# The subscriber asks for exactly these. Topic filters live next to the parser that reads them.
TOPIC_FILTERS = ("slf/+/alerts", "slf/+/+/status", "slf/+/lockers/+/state", "slf/+/capture/state")
MAX_PAYLOAD_BYTES = 4096
DEVICES = ("capture", "locker")
SEVERITIES = ("alarm", "warning")
# Codes the protocol defines. An unknown code is still stored so a new Pi release stays visible.
ALERT_CODES = ("FORCED_OPEN", "ITEM_MISSING", "TAMPER", "ITEM_NOT_DEPOSITED", "ITEM_NOT_TAKEN",
               "CAPTURE_ITEM_LEFT")
# Device level rows (controller connection, capture box) share this target number.
DEVICE_TARGET = 0
MAX_LOCKER_NUMBER = 9999

Kind = Literal["status", "state", "alert"]


class Target:
    """Where a message belongs: one kiosk, one device, one locker number (0 = the device)."""

    __slots__ = ("kiosk_id", "device", "locker_number", "kind")

    def __init__(self, kiosk_id: UUID, device: str, locker_number: int, kind: Kind):
        self.kiosk_id = kiosk_id
        self.device = device
        self.locker_number = locker_number
        self.kind = kind


def parse_topic(topic: str) -> Target | None:
    """`slf/{kiosk}/...` into a target, or None when the topic is not one we subscribed to."""
    parts = topic.split("/")
    if len(parts) < 3 or parts[0] != "slf":
        return None
    try:
        kiosk_id = UUID(parts[1])
    except ValueError:
        return None
    rest = parts[2:]
    if rest == ["alerts"]:
        # The device is named in the payload; alerts do not carry it in the topic.
        return Target(kiosk_id, "", None, "alert")
    if len(rest) == 2 and rest[1] == "status" and rest[0] in DEVICES:
        return Target(kiosk_id, rest[0], DEVICE_TARGET, "status")
    if rest == ["capture", "state"]:
        return Target(kiosk_id, "capture", DEVICE_TARGET, "state")
    if len(rest) == 3 and rest[0] == "lockers" and rest[2] == "state":
        number = locker_number(rest[1])
        return None if number is None else Target(kiosk_id, "locker", number, "state")
    return None


def locker_number(value) -> int | None:
    """Locker numbers are positive integers; the protocol has no other form."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        number = int(value)
    except ValueError:
        return None
    return number if 1 <= number <= MAX_LOCKER_NUMBER else None


def read_payload(payload: bytes) -> dict | None:
    if not payload or len(payload) > MAX_PAYLOAD_BYTES:
        return None
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def reported_time(value) -> datetime | None:
    """`at` is ISO 8601 with an offset. Anything else is treated as missing."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def choice(value, allowed: tuple[str, ...]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def weight(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return None if value < 0 else float(value)


def text(value, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped[:limit] or None


def apply_message(session: Session, topic: str, payload: bytes) -> str:
    """Store one message. Returns what happened, which is what the tests and logs read."""
    target = parse_topic(topic)
    if target is None:
        return "ignored_topic"
    body = read_payload(payload)
    if body is None:
        return "ignored_payload"
    try:
        if target.kind == "alert":
            outcome = store_alert(session, target.kiosk_id, body)
        else:
            outcome = store_state(session, target, body)
        if outcome.startswith("ignored"):
            # store_state may have added an empty row before finding the payload unusable.
            session.rollback()
            return outcome
        session.commit()
        return outcome
    except IntegrityError:
        # A redelivered alert hits the unique report key. Dropping it is the intended outcome.
        session.rollback()
        return "duplicate"
    except SQLAlchemyError:
        session.rollback()
        log.exception("MQTT 메시지를 저장하지 못했습니다: %s", topic)
        return "failed"


def store_state(session: Session, target: Target, body: dict) -> str:
    """Upsert the one row for this target. Only the fields this topic carries are touched."""
    row = session.get(DeviceState, (target.kiosk_id, target.device, target.locker_number))
    if row is None:
        row = DeviceState(kiosk_id=target.kiosk_id, device=target.device,
                          locker_number=target.locker_number)
        session.add(row)
    if target.kind == "status":
        connection = choice(body.get("status"), ("online", "offline"))
        if connection is None:
            return "ignored_payload"
        row.connection = connection
    else:
        door = choice(body.get("door"), ("open", "closed"))
        weight_g = weight(body.get("weight_g"))
        item = choice(body.get("item"), ("present", "empty"))
        # A payload with nothing readable would otherwise store an all-null row that the
        # operator screen cannot tell apart from a real reading.
        if door is None and weight_g is None and item is None:
            return "ignored_payload"
        row.door, row.weight_g, row.item = door, weight_g, item
    row.reported_at = reported_time(body.get("at"))
    row.updated_at = datetime.now(timezone.utc)
    return target.kind


def store_alert(session: Session, kiosk_id: UUID, body: dict) -> str:
    code = text(body.get("code"), 40)
    severity = choice(body.get("severity"), SEVERITIES)
    device = choice(body.get("device"), DEVICES)
    reported_at = reported_time(body.get("at"))
    if not code or severity is None or device is None or reported_at is None:
        return "ignored_payload"
    if code not in ALERT_CODES:
        log.warning("규약에 없는 경고 코드를 받았습니다: %s", code)
    detail = body.get("detail")
    session.add(DeviceAlert(
        kiosk_id=kiosk_id, code=code, severity=severity, device=device,
        locker_number=locker_number(body.get("locker_id")),
        message=text(body.get("message"), 500),
        detail=detail if isinstance(detail, dict) else None,
        reported_at=reported_at,
    ))
    return "alert"


def prune_alerts(session: Session, retention_days: int) -> int:
    """Drop alerts older than the retention window. Called once at startup, not per message."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    try:
        removed = session.execute(delete(DeviceAlert).where(DeviceAlert.reported_at < cutoff)).rowcount
        session.commit()
        return removed or 0
    except SQLAlchemyError:
        session.rollback()
        log.exception("오래된 경고를 지우지 못했습니다")
        return 0


def kiosk_device_states(session: Session, kiosk_id: UUID) -> list[dict]:
    rows = session.scalars(select(DeviceState).where(DeviceState.kiosk_id == kiosk_id)
                           .order_by(DeviceState.device, DeviceState.locker_number))
    return [{"device": row.device,
             "locker_number": row.locker_number or None,
             "connection": row.connection, "door": row.door,
             "weight_g": row.weight_g, "item": row.item,
             "reported_at": row.reported_at, "updated_at": row.updated_at} for row in rows]


def recent_alerts(session: Session, *, kiosk_id: UUID | None = None, severity: str | None = None,
                  since: datetime | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
    query = select(DeviceAlert)
    if kiosk_id is not None:
        query = query.where(DeviceAlert.kiosk_id == kiosk_id)
    if severity is not None:
        query = query.where(DeviceAlert.severity == severity)
    if since is not None:
        # A caller may send a naive timestamp; `reported_at` is always stored with an offset.
        query = query.where(DeviceAlert.reported_at >= (since if since.tzinfo
                                                        else since.replace(tzinfo=timezone.utc)))
    rows = session.scalars(query.order_by(DeviceAlert.reported_at.desc(), DeviceAlert.id)
                           .limit(limit).offset(offset))
    return [{"id": row.id, "kiosk_id": row.kiosk_id, "code": row.code, "severity": row.severity,
             "device": row.device, "locker_number": row.locker_number, "message": row.message,
             "detail": row.detail, "reported_at": row.reported_at} for row in rows]
