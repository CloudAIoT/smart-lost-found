from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.hardware.fake import FakeCamera, FakeLockers
from app.main import create_app

API_KEY = "test-api-key"
HEADERS = {"X-API-Key": API_KEY}


def make_settings(role: str, **values) -> Settings:
    # _env_file=None: 개발자의 .env가 테스트에 섞이지 않게 한다
    return Settings(_env_file=None, role=role, api_key=API_KEY, fake_hardware=True, **values)


def later(seconds: float = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


@pytest.fixture
def camera():
    return FakeCamera(delay_seconds=0)


@pytest.fixture
def capture_client(camera):
    return TestClient(create_app(make_settings("capture"), camera=camera))


@pytest.fixture
def lockers():
    return FakeLockers(count=2, open_seconds=0.2)


@pytest.fixture
def locker_client(lockers):
    return TestClient(create_app(make_settings("locker"), lockers=lockers))
