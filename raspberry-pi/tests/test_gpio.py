"""보관함 Pi의 LED·버튼 코드(app/hardware/gpio.py)를 gpiozero의 흉내 핀으로 확인한다."""

import pytest
from fastapi.testclient import TestClient
from gpiozero import Device
from gpiozero.pins.mock import MockFactory

from app.hardware.gpio import GpioLockers
from app.main import create_app
from conftest import HEADERS, later, make_settings


@pytest.fixture
def pins():
    Device.pin_factory = MockFactory()
    yield Device.pin_factory
    Device.pin_factory.reset()


def press(pins, pin):
    # 버튼을 눌렀다 뗀다. 누르면 GPIO가 GND에 연결되어 LOW가 된다
    pins.pin(pin).drive_low()
    pins.pin(pin).drive_high()


def test_open_blinks_led_until_button_pressed(pins):
    lockers = GpioLockers([17, 27], [22, 23])
    assert lockers.door(1) == "closed"
    lockers.open(1)
    assert lockers.door(1) == "open"
    assert lockers.leds[0].is_lit and not lockers.leds[1].is_lit
    press(pins, 22)
    assert lockers.door(1) == "closed"
    assert not lockers.leds[0].is_lit


def test_button_of_closed_locker_does_nothing(pins):
    lockers = GpioLockers([17, 27], [22, 23])
    lockers.open(2)
    press(pins, 22)  # 1번 칸 버튼
    assert lockers.door(1) == "closed"
    assert lockers.door(2) == "open"


def test_pin_counts_must_match(pins):
    with pytest.raises(ValueError):
        GpioLockers([17, 27], [22])


def test_real_mode_locker_pi_uses_leds_and_buttons(pins):
    client = TestClient(create_app(make_settings("locker", fake_hardware=False)))
    body = {"command_id": "open-1", "expires_at": later()}
    assert client.post("/lockers/1/open", json=body, headers=HEADERS).json() == {"locker_id": 1, "result": "opened"}
    assert client.get("/lockers/1", headers=HEADERS).json() == {"locker_id": 1, "door": "open"}
    press(pins, 22)
    assert client.get("/lockers/1", headers=HEADERS).json() == {"locker_id": 1, "door": "closed"}
    assert client.get("/lockers/3", headers=HEADERS).status_code == 404
