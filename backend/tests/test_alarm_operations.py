from datetime import datetime, timedelta, timezone
import json
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.device_telemetry import apply_message
from app.models import DeviceAlert, DeviceCommand, FoundItem, Kiosk, Registration, StorageAssignment
from app.services import issue_return_code
from test_registration import env, ready, start  # noqa: F401

AUTH = ("test-operator", "test-secret-only")


def configure_admin(env):
    env.settings.admin_username = AUTH[0]
    env.settings.admin_password = SecretStr(AUTH[1])


def alarm(env, *, kiosk_id=None, severity="alarm", age=0, code="TAMPER"):
    with Session(env.engine) as session:
        payload = {"code": code, "severity": severity, "device": "locker", "locker_id": 1,
                   "at": (datetime.now(timezone.utc) - timedelta(days=age)).isoformat()}
        assert apply_message(session, f"slf/{kiosk_id or env.kiosk_id}/alerts", json.dumps(payload).encode()) == "alert"
        return str(session.scalar(select(DeviceAlert.id).order_by(DeviceAlert.created_at.desc())))


def resolve(env, alert_id, **kwargs):
    return env.client.post(f"/api/admin/alerts/{alert_id}/resolve", auth=AUTH,
                           json={"confirmed": True, "note": "Inspected and repaired"}, **kwargs)


def status(env):
    response = env.client.get(f"/api/kiosks/{env.kiosk_id}/operation-status")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    return response.json()


def test_authentication_and_explicit_resolution_are_required(env):
    aid = alarm(env)
    url = f"/api/admin/alerts/{aid}/resolve"
    body = {"confirmed": True, "note": "Fixed"}
    assert env.client.post(url, json=body).status_code == 503
    configure_admin(env)
    for auth in (None, (AUTH[0], "wrong"), ("someone", AUTH[1])):
        assert env.client.post(url, json=body, auth=auth).status_code == 401
        assert env.client.get("/api/admin/session", auth=auth).status_code == 401
    for invalid in ({"note": "Fixed"}, {"confirmed": False, "note": "Fixed"},
                    {"confirmed": True, "note": "   "}, {"confirmed": True, "note": "x" * 2001},
                    {**body, "resolved_by": "spoofed"}):
        assert env.client.post(url, json=invalid, auth=AUTH).status_code == 422
    assert status(env)["blocked"]
    assert env.client.get("/api/admin/session", auth=AUTH).json() == {"username": AUTH[0]}
    assert resolve(env, str(uuid4())).status_code == 404


def test_multiple_old_alarms_require_all_resolutions_and_keep_audit(env):
    configure_admin(env)
    first, second = alarm(env, age=40), alarm(env, age=30)
    assert status(env)["active_alarm_count"] == 2
    first_result = resolve(env, first).json()
    assert first_result["resolved_by"] == AUTH[0]
    assert first_result["resolution_note"] == "Inspected and repaired"
    repeated = env.client.post(f"/api/admin/alerts/{first}/resolve", auth=AUTH,
                               json={"confirmed": True, "note": "Must not overwrite audit"}).json()
    assert repeated == first_result
    assert status(env)["blocked"]
    assert status(env)["active_alarm_count"] == 1
    assert resolve(env, second).status_code == 200
    assert not status(env)["blocked"]
    query = f"?kiosk_id={env.kiosk_id}"
    assert env.client.get("/api/alerts" + query + "&unresolved_only=true").json() == []
    public = env.client.get("/api/alerts" + query).json()
    assert len(public) == 2 and all(row["resolved_at"] for row in public)
    assert all("resolved_by" not in row and "resolution_note" not in row for row in public)
    assert env.client.get("/api/admin/alerts" + query).status_code == 401
    assert len(env.client.get("/api/admin/alerts" + query, auth=AUTH).json()) == 2
    alarm(env)
    assert status(env)["blocked"]


def test_warning_and_other_kiosk_alarm_do_not_suspend_this_kiosk(env):
    configure_admin(env)
    other_id = uuid4()
    with Session(env.engine) as session:
        session.add(Kiosk(id=other_id, name="Other kiosk")); session.commit()
    alarm(env, kiosk_id=other_id)
    warning = alarm(env, severity="warning")
    assert not status(env)["blocked"]
    assert start(env)
    assert resolve(env, warning).status_code == 200
    assert env.client.get(f"/api/kiosks/{other_id}/operation-status").json()["blocked"]


def test_all_customer_mutations_are_refused_without_device_or_business_changes(env):
    configure_admin(env)
    registration_id = ready(env)
    base = f"/api/registrations/{registration_id}"
    assert env.client.post(base + "/deposit/open").status_code == 200
    assert env.client.post(base + "/deposit/complete", json={"confirmed": True}).status_code == 200
    with Session(env.engine) as session:
        item_id = session.scalar(select(FoundItem.id))
        code, _ = issue_return_code(session, item_id, ttl=300)
    assert env.client.post("/api/pickup/open", json={"kiosk_id": env.kiosk_id, "code": code}).status_code == 200
    command_count = len(env.gateway.opens) + len(env.gateway.captures)
    aid = alarm(env)
    requests = [
        ("/api/registrations", {"kiosk_id": env.kiosk_id}),
        (base + "/shots/1", None), (base + "/analysis", None),
        (base + "/deposit/open", None), (base + "/deposit/reopen", None),
        (base + "/deposit/complete", {"confirmed": True}),
        ("/api/pickup/open", {"kiosk_id": env.kiosk_id, "code": code}),
        (f"/api/pickup/{item_id}/reopen", None),
        (f"/api/pickup/{item_id}/complete", {"confirmed": True}),
        (f"/api/found-items/{item_id}/return-code", {"attempt_id": str(uuid4())}),
    ]
    for path, body in requests:
        response = env.client.post(path, json=body)
        assert response.status_code == 423, (path, response.text)
        assert response.json()["detail"]["code"] == "KIOSK_ALARM_ACTIVE"
    assert len(env.gateway.opens) + len(env.gateway.captures) == command_count
    assert env.client.get(base).status_code == 200
    assert resolve(env, aid).status_code == 200
    with Session(env.engine) as session:
        assert session.query(Registration).count() == 1
        assert session.get(FoundItem, item_id).status == "stored"
        assert session.scalar(select(StorageAssignment)).status == "awaiting_pickup"
        assert session.query(DeviceCommand).count() == command_count
    assert env.client.post(f"/api/pickup/{item_id}/complete", json={"confirmed": True}).status_code == 200


@pytest.mark.parametrize("flow", ["deposit", "pickup"])
def test_alarm_arriving_during_door_check_prevents_completion(env, flow):
    configure_admin(env)
    registration_id = ready(env)
    base = f"/api/registrations/{registration_id}"
    env.client.post(base + "/deposit/open")
    url = base + "/deposit/complete"
    if flow == "pickup":
        env.client.post(url, json={"confirmed": True})
        with Session(env.engine) as session:
            item_id = session.scalar(select(FoundItem.id))
            code, _ = issue_return_code(session, item_id, ttl=300)
        env.client.post("/api/pickup/open", json={"kiosk_id": env.kiosk_id, "code": code})
        url = f"/api/pickup/{item_id}/complete"
    original_door = env.gateway.door
    def door(*args):
        alarm(env)
        return original_door(*args)
    env.gateway.door = door
    assert env.client.post(url, json={"confirmed": True}).status_code == 423
    with Session(env.engine) as session:
        assert session.scalar(select(StorageAssignment)).status == f"awaiting_{flow}"


def test_status_preview_cap_never_releases_unresolved_alarms(env):
    with Session(env.engine) as session:
        session.add_all(DeviceAlert(kiosk_id=UUID(env.kiosk_id), code="UNKNOWN", severity="alarm", device="capture",
                                   reported_at=datetime.now(timezone.utc) + timedelta(seconds=i)) for i in range(205))
        session.commit()
    result = status(env)
    assert result["blocked"] and result["active_alarm_count"] == 205
    assert len(result["alarms"]) == 200
