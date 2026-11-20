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
    values.setdefault("fake_hardware", True)
    return Settings(_env_file=None, role=role, api_key=API_KEY, **values)


def later(seconds: float = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


class MemoryPublisher:
    """MQTT 대신 보낸 메시지를 메모리에 모아 둔다."""

    def __init__(self):
        self.messages: list[tuple[str, dict, bool]] = []

    def publish(self, path: str, payload: dict, retain: bool = False) -> None:
        self.messages.append((path, payload, retain))

    def alerts(self) -> list[dict]:
        return [payload for path, payload, _ in self.messages if path == "alerts"]

    def alert_codes(self) -> list[str]:
        return [alert["code"] for alert in self.alerts()]

    def last(self, path: str) -> tuple[dict, bool]:
        payload, retain = [(p, r) for topic, p, r in self.messages if topic == path][-1]
        return payload, retain


class ManualScheduler:
    """'몇 초 뒤에 할 일'을 바로 실행하지 않고 모아 두었다가 run()으로 실행한다."""

    def __init__(self):
        self.waiting: list[dict] = []

    def call_later(self, seconds: float, func):
        job = {"seconds": seconds, "func": func, "active": True}
        self.waiting.append(job)

        def cancel() -> None:
            job["active"] = False

        return cancel

    def run(self) -> None:
        jobs, self.waiting = self.waiting, []
        for job in jobs:
            if job["active"]:
                job["func"]()


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
