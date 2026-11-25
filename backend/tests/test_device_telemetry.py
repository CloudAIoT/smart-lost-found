"""The MQTT contract of docs/mqtt-protocol.md, checked without a broker.

A bad message must be ignored rather than raised: the subscriber runs on paho's thread, where an
exception stops every later message. These tests pin that, the retained-state upsert, the replay
drop, ordinary telemetry's non-authority, and ITEM_MISSING's narrow incident transition.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_ownership_verification.search import stored_items
from app.config import Settings, get_settings
from app.db import Base, get_session
from app.device_telemetry import apply_message, parse_topic, prune_alerts
from app.errors import BusinessError
from app.main import app
from app.kiosk_operations import resolve_alert
from app.models import DeviceAlert, DeviceState, FoundItem, Kiosk, Registration, ReturnCode, StorageAssignment
from app.services import issue_return_code, search_stored_items

from test_registration import env, ready  # noqa: F401  (shared integration harness)

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


@pytest.mark.parametrize("code_status", ["active", "opening"])
def test_item_missing_closes_the_assignment_and_revokes_unspent_codes(env, code_status):
    """A forced-entry loss is an incident transition, not a user-declared completion."""
    registration_id = ready(env)
    base = f"/api/registrations/{registration_id}"
    assert env.client.post(base + "/deposit/open").status_code == 200
    assert env.client.post(base + "/deposit/complete", json={"confirmed": True}).status_code == 200

    with Session(env.engine) as session:
        item = session.scalar(select(FoundItem))
        item_id = item.id
        code, record = issue_return_code(session, item_id, ttl=env.settings.return_code_ttl_seconds)
        if code_status == "opening":
            record.status = "opening"
            session.commit()

        body = ('{"code": "ITEM_MISSING", "severity": "alarm", "device": "locker",'
                ' "locker_id": 1, "message": "보관 물품이 사라졌습니다.",'
                ' "detail": {"before_g": 150.0, "after_g": 0.0}, "at": "%s"}' % AT)
        assert message(session, f"slf/{env.kiosk_id}/alerts", body) == "alert"

        assignment = session.scalar(select(StorageAssignment))
        ended_at = assignment.ended_at
        assert (session.get(FoundItem, item_id).status, assignment.status) == ("missing", "missing")
        assert ended_at is not None
        assert session.get(Registration, UUID(registration_id)).status == "stored"
        assert session.scalar(select(ReturnCode)).status == "revoked"
        assert search_stored_items(session) == []
        assert session.execute(stored_items()).all() == []
        with pytest.raises(BusinessError, match="ITEM_NOT_STORED"):
            issue_return_code(session, item_id, ttl=env.settings.return_code_ttl_seconds)

        # QoS 1 replay: the alert unique key rejects the whole transaction, including any
        # attempted incident transition. The original terminal timestamp stays untouched.
        assert message(session, f"slf/{env.kiosk_id}/alerts", body) == "duplicate"
        session.expire_all()
        assert session.scalar(select(StorageAssignment)).ended_at == ended_at
        assert session.query(DeviceAlert).count() == 1

    refused = env.client.post("/api/pickup/open", json={"code": code, "kiosk_id": env.kiosk_id})
    assert refused.status_code == 423
    assert refused.json()["detail"]["code"] == "KIOSK_ALARM_ACTIVE"
    with Session(env.engine) as session:
        resolve_alert(session, session.scalar(select(DeviceAlert.id)), "test-operator", "Incident secured; item remains missing")
        assert session.get(FoundItem, item_id).status == "missing"
    refused = env.client.post("/api/pickup/open", json={"code": code, "kiosk_id": env.kiosk_id})
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "ITEM_NOT_STORED"

    # ended_at releases the partial unique slot, so the next deposit gets locker 1 again.
    next_registration = ready(env)
    opened = env.client.post(f"/api/registrations/{next_registration}/deposit/open")
    assert opened.status_code == 200, opened.text
    assert opened.json()["locker_number"] == 1


def test_item_missing_without_a_stored_assignment_only_records_the_alert(telemetry):
    body = ('{"code": "ITEM_MISSING", "severity": "alarm", "device": "locker",'
            ' "locker_id": 2, "detail": {"before_g": 150.0, "after_g": 0.0},'
            ' "at": "%s"}' % AT)
    assert message(telemetry.session, f"slf/{KIOSK}/alerts", body) == "alert"
    assert telemetry.session.query(DeviceAlert).count() == 1
    assert telemetry.session.query(FoundItem).count() == 0
    assert telemetry.session.query(StorageAssignment).count() == 0


def test_item_missing_wins_over_an_inflight_pickup(env):
    registration_id = ready(env)
    base = f"/api/registrations/{registration_id}"
    assert env.client.post(base + "/deposit/open").status_code == 200
    assert env.client.post(base + "/deposit/complete", json={"confirmed": True}).status_code == 200
    with Session(env.engine) as session:
        item_id = session.scalar(select(FoundItem.id))
        code, _ = issue_return_code(session, item_id, ttl=env.settings.return_code_ttl_seconds)

    def report_theft_while_open_waits():
        env.gateway.on_open = None
        body = ('{"code": "ITEM_MISSING", "severity": "alarm", "device": "locker",'
                ' "locker_id": 1, "detail": {"before_g": 150.0, "after_g": 0.0},'
                ' "at": "2026-11-19T19:25:27+09:00"}')
        with Session(env.engine) as other:
            assert message(other, f"slf/{env.kiosk_id}/alerts", body) == "alert"

    env.gateway.on_open = report_theft_while_open_waits
    refused = env.client.post("/api/pickup/open", json={"code": code, "kiosk_id": env.kiosk_id})
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "ITEM_NOT_STORED"
    with Session(env.engine) as session:
        assert session.get(FoundItem, item_id).status == "missing"
        assert session.scalar(select(StorageAssignment)).status == "missing"
        assert session.scalar(select(ReturnCode)).status == "revoked"


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


def test_prune_keeps_all_alarms_and_resolution_audits(telemetry):
    session = telemetry.session
    now = datetime.now(timezone.utc)
    for age_days, code in ((40, "TAMPER"), (1, "FORCED_OPEN")):
        session.add(DeviceAlert(kiosk_id=KIOSK_ID, code=code, severity="alarm", device="locker",
                                reported_at=now - timedelta(days=age_days)))
    session.add(DeviceAlert(kiosk_id=KIOSK_ID, code="OLD_WARNING", severity="warning", device="capture",
                            reported_at=now - timedelta(days=40)))
    session.add(DeviceAlert(kiosk_id=KIOSK_ID, code="REVIEWED_WARNING", severity="warning", device="capture",
                            reported_at=now - timedelta(days=40), resolved_at=now, resolved_by="operator",
                            resolution_note="Reviewed"))
    session.commit()
    assert prune_alerts(session, 14) == 1
    assert {row.code for row in session.query(DeviceAlert)} == {"FORCED_OPEN", "TAMPER", "REVIEWED_WARNING"}


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
    """D03·D06: ordinary sensors inform the screen. They never complete a deposit or pickup.

    A locker that reports an item can hold a pickup completion back (test_pickup), but no message
    on its own moves a registration, an item or an assignment. ITEM_MISSING is separately tested
    as the forced-entry incident exception."""
    session = telemetry.session
    message(session, f"slf/{KIOSK}/alerts",
            '{"code": "ITEM_NOT_TAKEN", "severity": "warning", "device": "locker", "locker_id": 1, "at": "%s"}' % AT)
    assert session.query(DeviceAlert).count() == 1
    from app.models import FoundItem, Registration, StorageAssignment
    assert session.query(Registration).count() == 0
    assert session.query(FoundItem).count() == 0
    assert session.query(StorageAssignment).count() == 0
