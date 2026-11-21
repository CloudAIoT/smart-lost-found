"""The MQTT contract of docs/mqtt-protocol.md, checked without a broker.

A bad message must be ignored rather than raised: the subscriber runs on paho's thread, where an
exception stops every later message. These tests pin that, the retained-state upsert, the replay
drop, and that telemetry never decides a business outcome.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import Base, get_session
from app.device_telemetry import apply_message, parse_topic, prune_alerts
from app.main import app
from app.models import DeviceAlert, DeviceState, Kiosk

KIOSK = "00000000-0000-0000-0000-000000000001"
KIOSK_ID = UUID(KIOSK)
AT = "2026-11-19T19:24:27+09:00"


def message(session, topic: str, body: str) -> str:
    return apply_message(session, topic, body.encode("utf-8"))


@pytest.fixture
def telemetry(database_engine):
    Base.metadata.create_all(database_engine)
    with Session(database_engine) as session:
        session.add(Kiosk(id=KIOSK_ID, name="MQTT test kiosk"))
        session.commit()
    settings = Settings(_env_file=None, postgres_db="test", postgres_user="test",
                        postgres_password="test", local_development=True)

    def sessions():
        with Session(database_engine) as session:
            yield session

    app.dependency_overrides.update({get_session: sessions, get_settings: lambda: settings})
    try:
        with Session(database_engine) as session, TestClient(app) as client:
            yield SimpleNamespace(session=session, client=client)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("topic, device, number, kind", [
    (f"slf/{KIOSK}/alerts", "", None, "alert"),
    (f"slf/{KIOSK}/locker/status", "locker", 0, "status"),
    (f"slf/{KIOSK}/capture/status", "capture", 0, "status"),
    (f"slf/{KIOSK}/capture/state", "capture", 0, "state"),
    (f"slf/{KIOSK}/lockers/2/state", "locker", 2, "state"),
])
def test_protocol_topics_are_understood(topic, device, number, kind):
    target = parse_topic(topic)
    assert (target.device, target.locker_number, target.kind) == (device, number, kind)


@pytest.mark.parametrize("topic", [
    "slf/not-a-uuid/alerts",            # 키오스크 ID가 UUID가 아니다
    f"slf/{KIOSK}/lockers/0/state",     # 칸 번호는 1 이상이다
    f"slf/{KIOSK}/lockers/x/state",     # 칸 번호가 숫자가 아니다
    f"slf/{KIOSK}/gateway/status",      # 규약에 없는 장치
    f"other/{KIOSK}/alerts",            # 접두사가 다르다
    f"slf/{KIOSK}",                     # 토픽이 짧다
])
def test_unexpected_topics_are_dropped(topic):
    assert parse_topic(topic) is None


def test_retained_state_keeps_one_row_per_locker(telemetry):
    session = telemetry.session
    first = '{"locker_id": 1, "door": "open", "weight_g": 0.0, "item": "empty", "at": "%s"}' % AT
    assert message(session, f"slf/{KIOSK}/lockers/1/state", first) == "state"
    second = '{"locker_id": 1, "door": "closed", "weight_g": 150.0, "item": "present", "at": "%s"}' % AT
    assert message(session, f"slf/{KIOSK}/lockers/1/state", second) == "state"
    rows = session.query(DeviceState).all()
    assert len(rows) == 1
    assert (rows[0].door, rows[0].weight_g, rows[0].item) == ("closed", 150.0, "present")
    assert rows[0].reported_at is not None


def test_connection_status_does_not_erase_sensor_values(telemetry):
    """`capture/state` and `capture/status` share a row; each topic writes only its own fields."""
    session = telemetry.session
    message(session, f"slf/{KIOSK}/capture/state",
            '{"weight_g": 200.0, "item": "present", "at": "%s"}' % AT)
    message(session, f"slf/{KIOSK}/capture/status", '{"status": "offline", "at": "%s"}' % AT)
    row = session.query(DeviceState).one()
    assert (row.connection, row.weight_g, row.item) == ("offline", 200.0, "present")


def test_alert_is_stored_and_a_replay_is_dropped(telemetry):
    session = telemetry.session
    body = ('{"code": "ITEM_NOT_TAKEN", "severity": "warning", "device": "locker", "locker_id": 2,'
            ' "message": "2번 보관함에 물건이 남아 있습니다.",'
            ' "detail": {"before_g": 150.0, "after_g": 150.0}, "at": "%s"}' % AT)
    assert message(session, f"slf/{KIOSK}/alerts", body) == "alert"
    # The broker can redeliver a queued QoS 1 alert after a restart.
    assert message(session, f"slf/{KIOSK}/alerts", body) == "duplicate"
    alert = session.query(DeviceAlert).one()
    assert (alert.code, alert.severity, alert.locker_number) == ("ITEM_NOT_TAKEN", "warning", 2)
    assert alert.detail == {"before_g": 150.0, "after_g": 150.0}


def test_capture_alert_without_a_locker_number(telemetry):
    session = telemetry.session
    body = ('{"code": "CAPTURE_ITEM_LEFT", "severity": "warning", "device": "capture",'
            ' "detail": {"weight_g": 200.0}, "at": "%s"}' % AT)
    assert message(session, f"slf/{KIOSK}/alerts", body) == "alert"
    assert session.query(DeviceAlert).one().locker_number is None


@pytest.mark.parametrize("body", [
    '{"code": "TAMPER", "severity": "critical", "device": "locker", "at": "%s"}' % AT,  # 없는 severity
    '{"code": "TAMPER", "severity": "warning", "device": "gateway", "at": "%s"}' % AT,  # 없는 장치
    '{"code": "TAMPER", "severity": "warning", "device": "locker"}',                    # at 없음
    '{"severity": "warning", "device": "locker", "at": "%s"}' % AT,                     # code 없음
])
def test_alerts_missing_required_fields_are_ignored(telemetry, body):
    assert message(telemetry.session, f"slf/{KIOSK}/alerts", body) == "ignored_payload"
    assert telemetry.session.query(DeviceAlert).count() == 0


@pytest.mark.parametrize("payload", [b"", b"not json", b"[1,2,3]", b'"text"', b"{" + b"a" * 5000])
def test_malformed_payloads_never_raise(telemetry, payload):
    assert apply_message(telemetry.session, f"slf/{KIOSK}/alerts", payload) == "ignored_payload"


@pytest.mark.parametrize("topic, body", [
    (f"slf/{KIOSK}/locker/status", '{"status": "rebooting", "at": "%s"}' % AT),
    (f"slf/{KIOSK}/lockers/1/state", '{"nothing": "usable"}'),
])
def test_an_unusable_state_payload_leaves_no_row(telemetry, topic, body):
    """An all-null row is indistinguishable from a real reading, so nothing may be stored."""
    assert message(telemetry.session, topic, body) == "ignored_payload"
    assert telemetry.session.query(DeviceState).count() == 0


def test_since_filter_accepts_a_naive_timestamp(telemetry):
    """FE may send a timestamp without an offset; `reported_at` is always stored with one."""
    session = telemetry.session
    message(session, f"slf/{KIOSK}/alerts",
            '{"code": "TAMPER", "severity": "warning", "device": "locker", "at": "%s"}' % AT)
    # Anchored on the stored value: SQLite keeps no offset, PostgreSQL stores timestamptz.
    reported = session.query(DeviceAlert).one().reported_at.replace(tzinfo=None)
    after = (reported + timedelta(minutes=1)).isoformat()
    assert telemetry.client.get(f"/api/alerts?since={after}").json() == []
    before = (reported - timedelta(minutes=1)).isoformat()
    assert len(telemetry.client.get(f"/api/alerts?since={before}").json()) == 1


def test_unknown_alert_code_is_still_stored(telemetry):
    """A newer Pi release must stay visible on the operator screen instead of vanishing."""
    body = '{"code": "NEW_SITUATION", "severity": "alarm", "device": "locker", "at": "%s"}' % AT
    assert message(telemetry.session, f"slf/{KIOSK}/alerts", body) == "alert"
    assert telemetry.session.query(DeviceAlert).one().code == "NEW_SITUATION"


def test_unknown_kiosk_is_recorded_without_failing(telemetry):
    other = str(uuid4())
    assert message(telemetry.session, f"slf/{other}/locker/status",
                   '{"status": "online", "at": "%s"}' % AT) == "status"
    assert telemetry.session.query(DeviceState).one().connection == "online"


def test_prune_drops_only_old_alerts(telemetry):
    session = telemetry.session
    now = datetime.now(timezone.utc)
    for age_days, code in ((40, "TAMPER"), (1, "FORCED_OPEN")):
        session.add(DeviceAlert(kiosk_id=KIOSK_ID, code=code, severity="alarm", device="locker",
                                reported_at=now - timedelta(days=age_days)))
    session.commit()
    assert prune_alerts(session, 14) == 1
    assert session.query(DeviceAlert).one().code == "FORCED_OPEN"


def test_read_apis_expose_state_and_alerts(telemetry):
    session = telemetry.session
    message(session, f"slf/{KIOSK}/locker/status", '{"status": "online", "at": "%s"}' % AT)
    message(session, f"slf/{KIOSK}/lockers/2/state",
            '{"locker_id": 2, "door": "closed", "weight_g": 150.0, "item": "present", "at": "%s"}' % AT)
    message(session, f"slf/{KIOSK}/alerts",
            '{"code": "FORCED_OPEN", "severity": "alarm", "device": "locker", "locker_id": 2, "at": "%s"}' % AT)

    states = telemetry.client.get(f"/api/kiosks/{KIOSK}/device-status").json()
    assert [(row["device"], row["locker_number"]) for row in states] == [("locker", None), ("locker", 2)]
    assert states[0]["connection"] == "online"
    assert states[1]["item"] == "present"

    alerts = telemetry.client.get("/api/alerts").json()
    assert [alert["code"] for alert in alerts] == ["FORCED_OPEN"]
    assert telemetry.client.get("/api/alerts?severity=warning").json() == []
    assert len(telemetry.client.get(f"/api/alerts?kiosk_id={KIOSK}&severity=alarm").json()) == 1

    assert telemetry.client.get(f"/api/kiosks/{uuid4()}/device-status").status_code == 404
    assert telemetry.client.get(f"/api/alerts?kiosk_id={uuid4()}").status_code == 404


def test_telemetry_does_not_change_business_state(telemetry):
    """D03·D06: sensors inform the screen. They never complete a deposit or a pickup.

    A locker that reports an item can hold a pickup completion back (test_pickup), but no message
    on its own moves a registration, an item or an assignment."""
    session = telemetry.session
    message(session, f"slf/{KIOSK}/alerts",
            '{"code": "ITEM_NOT_TAKEN", "severity": "warning", "device": "locker", "locker_id": 1, "at": "%s"}' % AT)
    assert session.query(DeviceAlert).count() == 1
    from app.models import FoundItem, Registration, StorageAssignment
    assert session.query(Registration).count() == 0
    assert session.query(FoundItem).count() == 0
    assert session.query(StorageAssignment).count() == 0
