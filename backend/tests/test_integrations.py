from datetime import datetime, timezone
from io import BytesIO
import json
from uuid import uuid4
from unittest.mock import Mock

import httpx
from PIL import Image
import pytest
from botocore.exceptions import ClientError

from app.config import Settings
from app.integrations import IntegrationError, PiGateway, S3ImageStore, validate_jpeg


def pi(handler):
    kiosk_id = uuid4()
    settings = Settings(_env_file=None, postgres_db="test", postgres_user="test", postgres_password="test",
                        pi_timeout_seconds=1, max_image_bytes=4096,
                        pi_devices={str(kiosk_id): {"capture_url": "http://capture.test", "locker_url": "http://locker.test"}})
    def route(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "role": "capture" if request.url.host == "capture.test" else "locker"})
        return handler(request)
    return kiosk_id, PiGateway(settings, httpx.Client(transport=httpx.MockTransport(route)))


def test_pi_capture_wire_contract():
    observed = []
    def handler(request):
        observed.append(request)
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"jpeg from Pi")
    kiosk, gateway = pi(handler)
    reg = uuid4()
    expiry = datetime.now(timezone.utc)
    assert gateway.capture(kiosk, "cmd-1", reg, expiry) == b"jpeg from Pi"
    assert len(observed) == 1
    assert observed[0].url == "http://capture.test/capture"
    assert json.loads(observed[0].content) == {"command_id": "cmd-1", "registration_id": str(reg), "expires_at": expiry.isoformat()}


def test_pi_open_and_door_contract():
    def handler(request):
        assert request.url.host == "locker.test"
        if request.method == "POST":
            assert request.url.path == "/lockers/7/open"
            assert json.loads(request.content)["command_id"] == "cmd-2"
            return httpx.Response(200, json={"locker_id": 7, "result": "opened"})
        return httpx.Response(200, json={"locker_id": 7, "door": "closed"})
    kiosk, gateway = pi(handler)
    gateway.open(kiosk, 7, "cmd-2", datetime.now(timezone.utc))
    assert gateway.door(kiosk, 7) == "closed"


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"locker_id": 99, "result": "opened"}),
    httpx.Response(200, content=b"not json"),
    httpx.Response(502, content=b"proxy failed: secret"),
    httpx.Response(500, json={"detail": {"code": []}}),
])
def test_invalid_open_result_is_uncertain(response):
    kiosk, gateway = pi(lambda request: response)
    with pytest.raises(IntegrationError) as caught:
        gateway.open(kiosk, 7, "cmd-2", datetime.now(timezone.utc))
    assert caught.value.uncertain
    assert "secret" not in str(caught.value)


def test_device_timeout_is_not_retried():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("secret", request=request)
    kiosk, gateway = pi(handler)
    with pytest.raises(IntegrationError) as caught:
        gateway.open(kiosk, 7, "cmd-2", datetime.now(timezone.utc))
    assert caught.value.uncertain
    assert len(calls) == 1


def test_definite_error_keeps_error_code():
    kiosk, gateway = pi(lambda request: httpx.Response(409, json={"detail": {"code": "DEVICE_BUSY", "message": "secret"}}))
    with pytest.raises(IntegrationError) as caught:
        gateway.capture(kiosk, "cmd", uuid4(), datetime.now(timezone.utc))
    assert caught.value.code == "DEVICE_BUSY"
    assert not caught.value.uncertain


def test_wrong_role_stops_before_hardware_command():
    kiosk, gateway = pi(lambda request: pytest.fail("Must not call hardware"))
    gateway.settings.pi_devices[str(kiosk)]["capture_url"] = "http://locker.test"
    with pytest.raises(IntegrationError, match="DEVICE_ROLE_MISMATCH"):
        gateway.capture(kiosk, "cmd", uuid4(), datetime.now(timezone.utc))


def test_large_capture_is_bounded():
    kiosk, gateway = pi(lambda request: httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"x" * 4097))
    with pytest.raises(IntegrationError, match="DEVICE_RESPONSE_TOO_LARGE"):
        gateway.capture(kiosk, "cmd", uuid4(), datetime.now(timezone.utc))


def test_s3_private_object_and_short_lived_preview():
    client = Mock()
    store = S3ImageStore(client, "private-bucket", 60, 4096)
    store.put("registration/photo.jpg", b"jpeg")
    client.put_object.assert_called_once_with(Bucket="private-bucket", Key="registration/photo.jpg", Body=b"jpeg", ContentType="image/jpeg")
    store.preview("registration/photo.jpg")
    client.generate_presigned_url.assert_called_once_with("get_object", Params={"Bucket": "private-bucket", "Key": "registration/photo.jpg"}, ExpiresIn=60)
    client.put_object.side_effect = ClientError({"Error": {"Code": "AccessDenied", "Message": "secret"}}, "PutObject")
    with pytest.raises(IntegrationError, match="IMAGE_STORAGE_FAILED"):
        store.put("registration/photo.jpg", b"jpeg")


def test_jpeg_validation_rejects_truncated_and_non_jpeg():
    buffer = BytesIO()
    Image.new("RGB", (10, 10)).save(buffer, "JPEG")
    jpeg = buffer.getvalue()
    validate_jpeg(jpeg, 4096)
    for invalid in (b"", b"not jpeg", jpeg[:100], jpeg[:-20]):
        with pytest.raises(IntegrationError):
            validate_jpeg(invalid, 4096)
