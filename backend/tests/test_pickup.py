"""Return code issuance and kiosk pickup, from a stored item to a released locker."""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations import IntegrationError
from app.models import DeviceCommand, FoundItem, ReturnCode, StorageAssignment
from app.services import hash_return_code, search_stored_items

from test_registration import env, ready  # noqa: F401  (shared harness)


def stored(env):
    """Run the deposit flow to completion and return the item waiting in a locker."""
    registration_id = ready(env)
    base = f"/api/registrations/{registration_id}"
    assert env.client.post(base + "/deposit/open").status_code == 200
    assert env.client.post(base + "/deposit/complete", json={"confirmed": True}).json()["status"] == "stored"
    with Session(env.engine) as session:
        item = session.scalar(select(FoundItem))
        return str(item.id)


def issue(env, found_item_id):
    response = env.client.post(f"/api/found-items/{found_item_id}/return-code")
    assert response.status_code == 201, response.text
    return response.json()["code"]


def test_full_pickup_releases_the_locker(env):
    item_id = stored(env)
    with Session(env.engine) as session:
        assert len(search_stored_items(session)) == 1
    code = issue(env, item_id)

    opened = env.client.post("/api/pickup/open", json={"code": code, "kiosk_id": env.kiosk_id})
    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["locker_number"] == 1
    assert (body["item_status"], body["assignment_status"]) == ("stored", "awaiting_pickup")
    assert len(env.gateway.opens) == 2  # deposit open, then pickup open
    with Session(env.engine) as session:
        # An item on its way out is no longer offered by search.
        assert search_stored_items(session) == []
        command = session.scalar(select(DeviceCommand).where(DeviceCommand.action == "pickup_open"))
        assert (command.action, command.status) == ("pickup_open", "succeeded")

    done = env.client.post(f"/api/pickup/{item_id}/complete", json={"confirmed": True})
    assert done.status_code == 200, done.text
    assert done.json()["item_status"] == "retrieved"
    assert done.json()["assignment_status"] == "retrieved"
    with Session(env.engine) as session:
        assignment = session.scalar(select(StorageAssignment))
        assert assignment.ended_at is not None
        assert search_stored_items(session) == []
    # The released locker is immediately reusable by a new registration.
    again = ready(env)
    assert env.client.post(f"/api/registrations/{again}/deposit/open").json()["locker_number"] == 1


def test_code_is_single_use_and_replays_a_finished_open(env):
    item_id = stored(env)
    code = issue(env, item_id)
    first = env.client.post("/api/pickup/open", json={"code": code, "kiosk_id": env.kiosk_id})
    assert first.status_code == 200
    # Repeating the submission replays the result instead of opening the door twice.
    for _ in range(2):
        repeat = env.client.post("/api/pickup/open", json={"code": code, "kiosk_id": env.kiosk_id})
        assert repeat.status_code == 200, repeat.text
        assert repeat.json()["locker_number"] == 1
    assert len(env.gateway.opens) == 2
    for _ in range(2):
        assert env.client.post(f"/api/pickup/{item_id}/complete", json={"confirmed": True}).json()["item_status"] == "retrieved"
    # Once the item is gone the code cannot start a new open.
    spent = env.client.post("/api/pickup/open", json={"code": code, "kiosk_id": env.kiosk_id})
    assert spent.status_code == 409
    assert spent.json()["detail"]["code"] == "RETURN_CODE_ALREADY_USED"
    assert len(env.gateway.opens) == 2


def test_plaintext_code_is_never_stored_and_reissue_revokes_the_old_one(env):
    item_id = stored(env)
    first = issue(env, item_id)
    second = issue(env, item_id)
    assert first != second
    with Session(env.engine) as session:
        codes = session.scalars(select(ReturnCode)).all()
        assert {code.status for code in codes} == {"revoked", "active"}
        assert all(first not in str(code.code_hash) and second not in str(code.code_hash) for code in codes)
        assert session.scalar(select(ReturnCode).where(ReturnCode.code_hash == hash_return_code(second))).status == "active"
    stale = env.client.post("/api/pickup/open", json={"code": first, "kiosk_id": env.kiosk_id})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "RETURN_CODE_UNUSABLE"
    assert env.client.post("/api/pickup/open", json={"code": second, "kiosk_id": env.kiosk_id}).status_code == 200


def test_code_entry_tolerates_kiosk_keyboard_noise(env):
    code = issue(env, stored(env))
    typed = " " + "-".join([code[:4], code[4:]]).lower() + " "
    assert env.client.post("/api/pickup/open", json={"code": typed, "kiosk_id": env.kiosk_id}).status_code == 200


def test_unknown_and_wrong_target_codes_never_reach_the_device(env):
    item_id = stored(env)
    code = issue(env, item_id)
    missing = env.client.post("/api/pickup/open", json={"code": "AAAAAAAA", "kiosk_id": env.kiosk_id})
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "RETURN_CODE_NOT_FOUND"
    elsewhere = env.client.post("/api/pickup/open", json={"code": code, "kiosk_id": str(uuid4())})
    assert elsewhere.status_code == 409
    assert elsewhere.json()["detail"]["code"] == "RETURN_CODE_WRONG_KIOSK"
    assert len(env.gateway.opens) == 1  # only the deposit open
    assert env.client.post(f"/api/pickup/{item_id}/complete", json={"confirmed": True}).status_code == 409


def test_expired_code_is_refused(env):
    item_id = stored(env)
    code = issue(env, item_id)
    with Session(env.engine) as session:
        record = session.scalar(select(ReturnCode))
        record.expires_at = record.created_at
        session.commit()
    expired = env.client.post("/api/pickup/open", json={"code": code, "kiosk_id": env.kiosk_id})
    assert expired.status_code == 409
    assert expired.json()["detail"]["code"] == "RETURN_CODE_EXPIRED"
    assert len(env.gateway.opens) == 1


def test_explicit_open_failure_keeps_the_code_usable(env):
    item_id = stored(env)
    code = issue(env, item_id)
    env.gateway.error = IntegrationError("LOCK_ERROR")
    failed = env.client.post("/api/pickup/open", json={"code": code, "kiosk_id": env.kiosk_id})
    assert failed.status_code == 502
    with Session(env.engine) as session:
        record = session.scalar(select(ReturnCode))
        assert (record.status, record.failed_attempts) == ("active", 1)
        assert session.scalar(select(DeviceCommand).where(DeviceCommand.action == "pickup_open")).status == "failed"
    # 규약: 개방에 실패하면 코드를 그대로 두어 다시 시도할 수 있게 한다.
    env.gateway.error = None
    assert env.client.post("/api/pickup/open", json={"code": code, "kiosk_id": env.kiosk_id}).status_code == 200
    assert env.client.post(f"/api/pickup/{item_id}/complete", json={"confirmed": True}).json()["item_status"] == "retrieved"


def test_uncertain_open_locks_the_code_against_a_second_attempt(env):
    item_id = stored(env)
    code = issue(env, item_id)
    env.gateway.error = IntegrationError("DEVICE_UNREACHABLE", uncertain=True)
    assert env.client.post("/api/pickup/open", json={"code": code, "kiosk_id": env.kiosk_id}).status_code == 502
    with Session(env.engine) as session:
        assert session.scalar(select(ReturnCode)).status == "locked"
        assert session.scalar(select(DeviceCommand).where(DeviceCommand.action == "pickup_open")).status == "unknown"
    env.gateway.error = None
    blocked = env.client.post("/api/pickup/open", json={"code": code, "kiosk_id": env.kiosk_id})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "RETURN_CODE_UNUSABLE"
    # The uncertain attempt did reach the device once; the retry never did.
    assert len(env.gateway.opens) == 2


def test_code_is_refused_before_the_item_is_stored(env):
    registration_id = ready(env)
    assert env.client.post(f"/api/registrations/{registration_id}/deposit/open").status_code == 200
    with Session(env.engine) as session:
        item_id = str(session.scalar(select(FoundItem)).id)
    early = env.client.post(f"/api/found-items/{item_id}/return-code")
    assert early.status_code == 409
    assert early.json()["detail"]["code"] == "ITEM_NOT_STORED"
    assert env.client.post(f"/api/found-items/{uuid4()}/return-code").status_code == 404
    assert env.client.post(f"/api/pickup/{uuid4()}/complete", json={"confirmed": True}).status_code == 404
