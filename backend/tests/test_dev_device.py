"""The development stand-in must be chosen by setting only, never by a device failure."""

from datetime import datetime, timezone
from uuid import uuid4

from app.api import get_gateway
from app.config import Settings
from app.dev_device import FakeDeviceGateway
from app.integrations import PiGateway, validate_jpeg


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, postgres_db="test", postgres_user="test", postgres_password="test",
                    max_image_bytes=1024 * 1024, **overrides)


def gateway(config: Settings):
    produced = get_gateway(config)
    try:
        return next(produced)
    finally:
        produced.close()


def test_real_gateway_stays_default():
    assert isinstance(gateway(settings()), PiGateway)


def test_flag_selects_the_bundled_dummy_photos():
    assert isinstance(gateway(settings(dev_fake_device=True)), FakeDeviceGateway)
    device = FakeDeviceGateway()
    shots = [device.capture(uuid4(), "cmd", uuid4(), datetime.now(timezone.utc), order) for order in (1, 2)]
    for jpeg in shots:
        validate_jpeg(jpeg, 1024 * 1024)
    assert shots[0] != shots[1]
    # Both completion steps read the door, and a dummy locker is always closed.
    assert device.door(uuid4(), 1) == "closed"
    assert device.open(uuid4(), 1, "cmd", datetime.now(timezone.utc)) is None
