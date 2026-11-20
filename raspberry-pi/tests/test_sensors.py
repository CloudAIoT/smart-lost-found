"""가상 센서와 상황 판단(app/sensors.py)을 MQTT·시간 대역으로 확인한다."""

import time

import pytest

from app.errors import DeviceError
from app.hardware.fake import FakeLockers
from app.sensors import CaptureMonitor, LockerMonitor, SensorStore
from conftest import ManualScheduler, MemoryPublisher


@pytest.fixture
def pub():
    return MemoryPublisher()


@pytest.fixture
def clock():
    return ManualScheduler()


def make_lockers(pub, clock, store=None):
    return LockerMonitor(2, pub, clock, store or SensorStore(None), threshold_g=10, settle_seconds=1.0)


def make_capture(pub, clock):
    return CaptureMonitor(pub, clock, SensorStore(None), threshold_g=10, leftover_seconds=120)


def test_forced_open_is_an_alarm(pub, clock):
    monitor = make_lockers(pub, clock)
    monitor.door_changed(1, "forced")
    alert = pub.alerts()[0]
    assert pub.alert_codes() == ["FORCED_OPEN"]
    assert (alert["severity"], alert["device"], alert["locker_id"]) == ("alarm", "locker", 1)


def test_deposit_without_item_warns_after_weight_settles(pub, clock):
    monitor = make_lockers(pub, clock)
    monitor.door_changed(1, "opened")
    monitor.door_changed(1, "closed")
    assert pub.alert_codes() == []  # 무게가 가라앉기 전에는 판단하지 않는다
    clock.run()
    assert pub.alert_codes() == ["ITEM_NOT_DEPOSITED"]


def test_deposit_with_item_is_fine(pub, clock):
    monitor = make_lockers(pub, clock)
    monitor.door_changed(1, "opened")
    monitor.change_item(1, "put", 150)
    monitor.door_changed(1, "closed")
    clock.run()
    closed = [p for path, p, _ in pub.messages if path == "lockers/1/events" and p["type"] == "door_closed"][0]
    assert pub.alert_codes() == []
    assert (closed["authorized"], closed["before_g"], closed["after_g"]) == (True, 0.0, 150.0)


def test_pickup_with_item_left_warns(pub, clock):
    monitor = make_lockers(pub, clock)
    monitor.change_item(1, "put", 150, preset=True)  # 이미 보관 중인 칸
    monitor.door_changed(1, "opened")
    monitor.door_changed(1, "closed")
    clock.run()
    assert pub.alert_codes() == ["ITEM_NOT_TAKEN"]


def test_pickup_with_item_taken_is_fine(pub, clock):
    monitor = make_lockers(pub, clock)
    monitor.change_item(1, "put", 150, preset=True)
    monitor.door_changed(1, "opened")
    monitor.change_item(1, "take", 150)
    monitor.door_changed(1, "closed")
    clock.run()
    assert pub.alert_codes() == []


def test_item_gone_after_forced_open_is_missing(pub, clock):
    monitor = make_lockers(pub, clock)
    monitor.change_item(2, "put", 300, preset=True)
    monitor.door_changed(2, "forced")
    monitor.change_item(2, "take", 300)
    monitor.door_changed(2, "closed")
    clock.run()
    assert pub.alert_codes() == ["FORCED_OPEN", "ITEM_MISSING"]


def test_item_cannot_change_behind_closed_door(pub, clock):
    monitor = make_lockers(pub, clock)
    with pytest.raises(DeviceError) as error:
        monitor.change_item(1, "put", 100)
    assert error.value.code == "DOOR_CLOSED"


def test_only_strong_shock_on_closed_door_warns(pub, clock):
    monitor = make_lockers(pub, clock)
    monitor.shock(1, "weak")
    monitor.door_changed(2, "opened")
    monitor.shock(2, "strong")  # 열린 칸을 다루다 생긴 충격
    assert pub.alert_codes() == []
    monitor.shock(1, "strong")
    assert pub.alert_codes() == ["TAMPER"]


def test_state_is_retained_with_item_flag(pub, clock):
    monitor = make_lockers(pub, clock)
    monitor.door_changed(1, "opened")
    monitor.change_item(1, "put", 150)
    state, retain = pub.last("lockers/1/state")
    assert retain
    assert (state["door"], state["weight_g"], state["item"]) == ("open", 150.0, "present")


def test_weights_survive_restart(pub, clock, tmp_path):
    path = tmp_path / "sensors.json"
    make_lockers(pub, clock, SensorStore(path)).change_item(2, "put", 200, preset=True)
    again = make_lockers(pub, clock, SensorStore(path))
    assert again.snapshot()["lockers"][1] == {"locker_id": 2, "door": "closed", "weight_g": 200.0, "item": "present"}


def test_broken_state_file_starts_empty(pub, clock, tmp_path):
    path = tmp_path / "sensors.json"
    path.write_text("{", encoding="utf-8")
    assert make_lockers(pub, clock, SensorStore(path)).snapshot()["lockers"][0]["weight_g"] == 0.0


def test_item_left_in_capture_box_warns(pub, clock):
    monitor = make_capture(pub, clock)
    monitor.change_item("put", 200)
    monitor.captured("reg-1")
    clock.run()
    alert = pub.alerts()[0]
    assert pub.alert_codes() == ["CAPTURE_ITEM_LEFT"]
    assert (alert["device"], alert["detail"]["registration_id"]) == ("capture", "reg-1")


def test_item_moved_out_of_capture_box_in_time_is_fine(pub, clock):
    monitor = make_capture(pub, clock)
    monitor.change_item("put", 200)
    monitor.captured("reg-1")
    monitor.change_item("take", 200)
    clock.run()
    assert pub.alert_codes() == []


def test_second_shot_restarts_the_wait(pub, clock):
    monitor = make_capture(pub, clock)
    monitor.change_item("put", 200)
    monitor.captured("reg-1")
    monitor.captured("reg-1")  # 물건을 돌려 두 번째 촬영
    clock.run()
    assert pub.alert_codes() == ["CAPTURE_ITEM_LEFT"]  # 첫 촬영의 확인은 취소되어 한 번만 경고


def test_fake_lockers_report_open_before_auto_close():
    lockers = FakeLockers(count=1, open_seconds=0.05)
    changes = []
    lockers.listener = lambda number, change: changes.append((number, change))
    lockers.open(1)
    time.sleep(0.2)
    assert changes == [(1, "opened"), (1, "closed")]


def test_env_example_reads_as_no_broker_and_no_state_file():
    from pathlib import Path

    from app.config import Settings

    example = Path(__file__).resolve().parents[1] / ".env.example"
    settings = Settings(_env_file=example)
    assert (settings.mqtt_host, settings.simulation, settings.sensor_state_file) == ("", False, None)
    assert settings.kiosk_id == "00000000-0000-0000-0000-000000000001"
