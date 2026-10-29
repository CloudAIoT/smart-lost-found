"""통신 규약(docs/device-protocol.md)과 백엔드 PiGateway가 기대하는 응답을 확인한다."""

import io
import time

from PIL import Image

from conftest import HEADERS, later


def capture_body(command_id="cmd-1", registration_id="reg-1", expires_at=None):
    return {"command_id": command_id, "registration_id": registration_id, "expires_at": expires_at or later()}


def open_body(command_id="open-1", expires_at=None):
    return {"command_id": command_id, "expires_at": expires_at or later()}


def assert_error(response, status, code):
    assert response.status_code == status
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert isinstance(detail["message"], str)


# 공통: 동작 확인과 인증

def test_health_reports_role(capture_client, locker_client):
    assert capture_client.get("/health", headers=HEADERS).json() == {"status": "ok", "role": "capture"}
    assert locker_client.get("/health", headers=HEADERS).json() == {"status": "ok", "role": "locker"}


def test_missing_or_wrong_api_key_is_rejected(capture_client):
    assert_error(capture_client.get("/health"), 401, "UNAUTHORIZED")
    assert_error(capture_client.get("/health", headers={"X-API-Key": "wrong-key"}), 401, "UNAUTHORIZED")
    assert_error(capture_client.post("/capture", json=capture_body()), 401, "UNAUTHORIZED")


def test_each_pi_only_serves_its_role(capture_client, locker_client):
    assert locker_client.post("/capture", json=capture_body(), headers=HEADERS).status_code == 404
    assert capture_client.get("/lockers/1", headers=HEADERS).status_code == 404


# 촬영 Pi

def test_capture_returns_jpeg(capture_client):
    response = capture_client.post("/capture", json=capture_body(), headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.format == "JPEG"


def test_same_command_returns_first_result_without_capturing_again(capture_client, camera):
    first = capture_client.post("/capture", json=capture_body(), headers=HEADERS)
    second = capture_client.post("/capture", json=capture_body(), headers=HEADERS)
    assert second.content == first.content
    assert camera.shots == 1


def test_same_command_after_expiry_still_returns_first_result(capture_client, camera):
    first = capture_client.post("/capture", json=capture_body(), headers=HEADERS)
    replay = capture_client.post("/capture", json=capture_body(expires_at=later(-10)), headers=HEADERS)
    assert replay.status_code == 200
    assert replay.content == first.content
    assert camera.shots == 1


def test_expired_command_is_not_executed(capture_client, camera):
    response = capture_client.post("/capture", json=capture_body(expires_at=later(-1)), headers=HEADERS)
    assert_error(response, 400, "COMMAND_EXPIRED")
    assert camera.shots == 0


def test_invalid_commands_are_rejected(capture_client):
    missing = {"command_id": "cmd-1", "expires_at": later()}
    no_timezone = capture_body(expires_at="2026-10-26T12:00:00")
    unknown_field = {**capture_body(), "extra": 1}
    for body in (missing, no_timezone, unknown_field):
        assert_error(capture_client.post("/capture", json=body, headers=HEADERS), 400, "INVALID_COMMAND")


def test_same_command_id_for_a_different_request_is_rejected(capture_client):
    capture_client.post("/capture", json=capture_body(registration_id="reg-1"), headers=HEADERS)
    other = capture_client.post("/capture", json=capture_body(registration_id="reg-2"), headers=HEADERS)
    assert_error(other, 400, "INVALID_COMMAND")


def test_busy_device_returns_409(capture_client):
    book = capture_client.app.state.book
    book.device_lock.acquire()
    try:
        assert_error(capture_client.post("/capture", json=capture_body(), headers=HEADERS), 409, "DEVICE_BUSY")
    finally:
        book.device_lock.release()


def test_camera_failure_is_reported_once(camera, capture_client):
    def broken():
        camera.shots += 1
        raise OSError("camera disconnected")

    camera.capture = broken
    first = capture_client.post("/capture", json=capture_body(), headers=HEADERS)
    replay = capture_client.post("/capture", json=capture_body(), headers=HEADERS)
    assert_error(first, 500, "CAMERA_ERROR")
    assert_error(replay, 500, "CAMERA_ERROR")
    assert camera.shots == 1


# 보관함 Pi

def test_open_returns_integer_locker_id(locker_client):
    response = locker_client.post("/lockers/2/open", json=open_body(), headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data == {"locker_id": 2, "result": "opened"}
    assert type(data["locker_id"]) is int


def test_unknown_locker_is_rejected(locker_client):
    for number in (0, 3):
        assert_error(locker_client.post(f"/lockers/{number}/open", json=open_body(), headers=HEADERS),
                     404, "LOCKER_NOT_FOUND")
        assert_error(locker_client.get(f"/lockers/{number}", headers=HEADERS), 404, "LOCKER_NOT_FOUND")


def test_same_open_command_does_not_open_twice(locker_client, lockers):
    locker_client.post("/lockers/1/open", json=open_body(), headers=HEADERS)
    replay = locker_client.post("/lockers/1/open", json=open_body(), headers=HEADERS)
    assert replay.json() == {"locker_id": 1, "result": "opened"}
    assert lockers.opened[1] == 1


def test_fake_door_opens_then_closes(locker_client):
    assert locker_client.get("/lockers/1", headers=HEADERS).json() == {"locker_id": 1, "door": "closed"}
    locker_client.post("/lockers/1/open", json=open_body(), headers=HEADERS)
    assert locker_client.get("/lockers/1", headers=HEADERS).json() == {"locker_id": 1, "door": "open"}
    time.sleep(0.3)
    assert locker_client.get("/lockers/1", headers=HEADERS).json() == {"locker_id": 1, "door": "closed"}
