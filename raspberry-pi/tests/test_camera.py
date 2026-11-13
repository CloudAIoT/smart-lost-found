"""실제 카메라 코드(app/hardware/camera.py)를 Picamera2 없이 확인한다.

Picamera2와 libcamera는 촬영 Pi에만 있어서, 같은 이름의 흉내 모듈을 넣고 불러온다.
"""

import importlib
import sys
import types
from enum import IntEnum

import pytest
from fastapi.testclient import TestClient

from app.main import create_app, make_lockers
from conftest import HEADERS, later, make_settings


class AfState(IntEnum):
    Idle = 0
    Scanning = 1
    Focused = 2
    Failed = 3


class FakePicamera2:
    """초점 상태를 정해진 순서대로 돌려주는 흉내 카메라. 마지막 상태는 계속 반복한다."""

    made = []          # 지금까지 만든 카메라 (다시 연 횟수를 센다)
    states = []        # 새로 만드는 카메라가 돌려줄 초점 상태 순서
    fail_next = False  # True면 다음 촬영에서 한 번 오류

    def __init__(self):
        self.states = list(FakePicamera2.states)
        self.controls = []
        self.closed = False
        FakePicamera2.made.append(self)

    def create_still_configuration(self, main):
        return main

    def configure(self, config):
        self.size = config["size"]

    def start(self):
        pass

    def set_controls(self, values):
        self.controls.append(values)

    def capture_metadata(self):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"AfState": state}

    def capture_file(self, buffer, format):
        if FakePicamera2.fail_next:
            FakePicamera2.fail_next = False
            raise OSError("camera disconnected")
        buffer.write(b"\xff\xd8 jpeg \xff\xd9")

    def close(self):
        self.closed = True


@pytest.fixture
def camera_module(monkeypatch):
    libcamera = types.ModuleType("libcamera")
    libcamera.controls = types.SimpleNamespace(
        AfModeEnum=types.SimpleNamespace(Auto="auto"),
        AfTriggerEnum=types.SimpleNamespace(Start="start", Cancel="cancel"),
        AfStateEnum=AfState,
    )
    picamera2 = types.ModuleType("picamera2")
    picamera2.Picamera2 = FakePicamera2
    monkeypatch.setitem(sys.modules, "libcamera", libcamera)
    monkeypatch.setitem(sys.modules, "picamera2", picamera2)
    monkeypatch.delitem(sys.modules, "app.hardware.camera", raising=False)
    FakePicamera2.made = []
    FakePicamera2.states = [AfState.Idle, AfState.Scanning, AfState.Focused]
    FakePicamera2.fail_next = False
    return importlib.import_module("app.hardware.camera")


def test_capture_focuses_once_then_returns_jpeg(camera_module):
    camera = camera_module.PiCamera((2304, 1296), focus_timeout=1)
    assert camera.capture().startswith(b"\xff\xd8")
    picam2 = FakePicamera2.made[0]
    assert picam2.size == (2304, 1296)
    assert {"AfMode": "auto", "AfTrigger": "start"} in picam2.controls
    assert {"AfTrigger": "cancel"} not in picam2.controls


def test_focus_timeout_cancels_and_still_captures(camera_module):
    FakePicamera2.states = [AfState.Scanning]  # 탐색이 끝나지 않는 경우
    camera = camera_module.PiCamera((2304, 1296), focus_timeout=0.05)
    assert camera.capture().startswith(b"\xff\xd8")
    assert {"AfTrigger": "cancel"} in FakePicamera2.made[0].controls


def test_camera_error_reopens_on_next_capture(camera_module):
    camera = camera_module.PiCamera((2304, 1296), focus_timeout=1)
    FakePicamera2.fail_next = True
    with pytest.raises(OSError):
        camera.capture()
    assert FakePicamera2.made[0].closed
    assert camera.capture().startswith(b"\xff\xd8")
    assert len(FakePicamera2.made) == 2


def test_real_mode_capture_pi_uses_pi_camera(camera_module):
    client = TestClient(create_app(make_settings("capture", fake_hardware=False)))
    body = {"command_id": "cmd-1", "registration_id": "reg-1", "expires_at": later()}
    response = client.post("/capture", json=body, headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert isinstance(client.app.state.camera, camera_module.PiCamera)


def test_real_mode_locker_pi_refuses_to_start():
    with pytest.raises(RuntimeError):
        make_lockers(make_settings("locker", fake_hardware=False))
