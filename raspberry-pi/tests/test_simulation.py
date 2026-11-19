"""시연용 /sim 경로로 가상 센서를 움직여 상황별 경고가 나가는지 API로 확인한다."""

import pytest
from fastapi.testclient import TestClient

from app.hardware.fake import FakeCamera, FakeLockers
from app.main import create_app
from conftest import HEADERS, ManualScheduler, MemoryPublisher, later, make_settings


@pytest.fixture
def pub():
    return MemoryPublisher()


@pytest.fixture
def sim(pub):
    # 문이 저절로 닫히지 않게 길게 두고, 무게 확인은 문이 닫히자마자 한다
    lockers = FakeLockers(count=2, open_seconds=60)
    settings = make_settings("locker", simulation=True, door_settle_seconds=0)
    return TestClient(create_app(settings, lockers=lockers, publisher=pub, scheduler=ManualScheduler()))


def open_by_command(client, number: int, command_id: str = "open-1"):
    body = {"command_id": command_id, "expires_at": later()}
    return client.post(f"/lockers/{number}/open", json=body, headers=HEADERS)


def sim_post(client, path: str, body: dict):
    return client.post(f"/sim/{path}", json=body, headers=HEADERS)


def test_sim_routes_are_closed_unless_simulation_is_on(locker_client):
    assert locker_client.get("/sim/state", headers=HEADERS).status_code == 404


def test_sim_routes_need_api_key(sim):
    assert sim.get("/sim/state").status_code == 401


def test_deposit_with_item_passes(sim, pub):
    assert open_by_command(sim, 1).status_code == 200
    assert sim_post(sim, "lockers/1/item", {"action": "put", "weight_g": 150}).status_code == 200
    state = sim_post(sim, "lockers/1/door", {"action": "close"}).json()
    assert state["lockers"][0] == {"locker_id": 1, "door": "closed", "weight_g": 150.0, "item": "present"}
    assert sim.get("/lockers/1", headers=HEADERS).json() == {"locker_id": 1, "door": "closed"}
    assert pub.alert_codes() == []


def test_deposit_without_item_warns(sim, pub):
    open_by_command(sim, 1)
    sim_post(sim, "lockers/1/door", {"action": "close"})
    assert pub.alert_codes() == ["ITEM_NOT_DEPOSITED"]


def test_forced_open_is_seen_by_door_sensor_and_alarms(sim, pub):
    assert sim_post(sim, "lockers/2/door", {"action": "open"}).json()["lockers"][1]["door"] == "open"
    assert sim.get("/lockers/2", headers=HEADERS).json() == {"locker_id": 2, "door": "open"}
    assert pub.alert_codes() == ["FORCED_OPEN"]


def test_shock_on_closed_locker_warns(sim, pub):
    assert sim_post(sim, "lockers/1/shock", {"strength": "strong"}).status_code == 200
    assert pub.alert_codes() == ["TAMPER"]


def test_item_change_behind_closed_door_needs_preset(sim):
    refused = sim_post(sim, "lockers/1/item", {"action": "put"})
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "DOOR_CLOSED"
    preset = sim_post(sim, "lockers/1/item", {"action": "put", "weight_g": 120, "preset": True})
    assert preset.json()["lockers"][0]["weight_g"] == 120.0


def test_sim_checks_locker_number_and_body(sim):
    missing = sim_post(sim, "lockers/9/shock", {})
    assert (missing.status_code, missing.json()["detail"]["code"]) == (404, "LOCKER_NOT_FOUND")
    wrong = sim_post(sim, "lockers/1/item", {"action": "throw"})
    assert (wrong.status_code, wrong.json()["detail"]["code"]) == (400, "INVALID_COMMAND")


def test_capture_box_item_left_after_capture_warns(pub):
    clock = ManualScheduler()
    app = create_app(make_settings("capture", simulation=True), camera=FakeCamera(0), publisher=pub, scheduler=clock)
    client = TestClient(app)
    assert sim_post(client, "capture/item", {"action": "put", "weight_g": 200}).json() == {
        "capture": {"weight_g": 200.0, "item": "present"}}
    body = {"command_id": "cap-1", "registration_id": "reg-1", "expires_at": later()}
    assert client.post("/capture", json=body, headers=HEADERS).status_code == 200
    clock.run()  # 기다리는 시간이 지났다고 치고 확인
    assert pub.alert_codes() == ["CAPTURE_ITEM_LEFT"]
