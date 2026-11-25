"""An unresolved alarm suspends a kiosk; resolution never completes a customer workflow."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import BusinessError
from app.models import DeviceAlert, Kiosk


def active_alarms(kiosk_id: UUID):
    return select(DeviceAlert).where(DeviceAlert.kiosk_id == kiosk_id,
                                    DeviceAlert.severity == "alarm", DeviceAlert.resolved_at.is_(None))


def require_operational(session: Session, kiosk_id: UUID, *, lock: bool = True) -> None:
    # Alarm ingestion takes this same short transaction lock. Never hold it across device I/O
    # for completion: check again under the lock after the door response instead.
    query = select(Kiosk.id).where(Kiosk.id == kiosk_id)
    if session.scalar(query.with_for_update() if lock else query) is None:
        raise BusinessError("KIOSK_NOT_FOUND", 404)
    if session.scalar(active_alarms(kiosk_id).with_only_columns(DeviceAlert.id).limit(1)) is not None:
        raise BusinessError("KIOSK_ALARM_ACTIVE", 423)


def alert_view(row: DeviceAlert, *, audit: bool = False) -> dict:
    result = {"id": row.id, "kiosk_id": row.kiosk_id, "code": row.code, "severity": row.severity,
              "device": row.device, "locker_number": row.locker_number, "message": row.message,
              "detail": row.detail, "reported_at": row.reported_at, "resolved_at": row.resolved_at}
    if audit:
        result.update(resolved_by=row.resolved_by, resolution_note=row.resolution_note)
    return result


def operation_status(session: Session, kiosk_id: UUID) -> dict:
    if session.get(Kiosk, kiosk_id) is None:
        raise BusinessError("KIOSK_NOT_FOUND", 404)
    # One snapshot: no list-size limit can release the block. Only the preview is capped.
    rows = session.execute(active_alarms(kiosk_id).add_columns(func.count().over())
                           .order_by(DeviceAlert.reported_at, DeviceAlert.id).limit(200)).all()
    return {"kiosk_id": kiosk_id, "blocked": bool(rows), "active_alarm_count": rows[0][1] if rows else 0,
            "alarms": [alert_view(row) for row, _ in rows]}


def resolve_alert(session: Session, alert_id: UUID, actor: str, note: str) -> dict:
    row = session.get(DeviceAlert, alert_id)
    if row is None:
        raise BusinessError("ALERT_NOT_FOUND", 404)
    session.execute(select(Kiosk.id).where(Kiosk.id == row.kiosk_id).with_for_update())
    row = session.scalar(select(DeviceAlert).where(DeviceAlert.id == alert_id).with_for_update()
                         .execution_options(populate_existing=True))
    if row is None:
        raise BusinessError("ALERT_NOT_FOUND", 404)
    if row.resolved_at is None:
        row.resolved_at = datetime.now(timezone.utc)
        row.resolved_by = actor
        row.resolution_note = note
    session.commit()
    return alert_view(row, audit=True)
