"""키오스크 사용 흐름을 파트 전체로 잇는 종단 테스트의 준비물.

실제 Pi 컨트롤러를 가짜 하드웨어 모드로 별도 프로세스에 띄우고, 백엔드가 평소처럼
HTTP(PiGateway)로 호출한다. AI 분석과 S3만 테스트 대역으로 바꾸며, 그 사이의
백엔드·Pi 코드는 모두 실제 코드다.
"""

import os
import socket
import subprocess
import sys
import time
from contextlib import closing
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
PI_DIR = ROOT / "raspberry-pi"
API_KEY = "pipeline-test-api-key"
HEADERS = {"X-API-Key": API_KEY}
DOOR_OPEN_SECONDS = 1.0

# 백엔드 설정은 DB 접속 정보를 요구한다. 실제 연결은 하지 않는다.
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")


def free_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Pi:
    """테스트가 Pi를 직접 들여다볼 때 쓰는 손잡이. 백엔드와 같은 API만 쓴다."""

    def __init__(self, role: str, port: int, process: subprocess.Popen, log: Path):
        self.role = role
        self.port = port
        self.process = process
        self.log = log

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def get(self, path: str) -> httpx.Response:
        return httpx.get(self.url + path, headers=HEADERS, timeout=5)

    def post(self, path: str, body: dict) -> httpx.Response:
        return httpx.post(self.url + path, json=body, headers=HEADERS, timeout=5)

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)


def start_pi(role: str, tmp_path: Path) -> Pi:
    port = free_port()
    log = tmp_path / f"pi-{role}.log"
    environment = {
        **os.environ,
        "PI_ROLE": role,
        "PI_API_KEY": API_KEY,
        "PI_FAKE_HARDWARE": "true",
        "PI_FAKE_CAPTURE_SECONDS": "0",
        "PI_FAKE_DOOR_OPEN_SECONDS": str(DOOR_OPEN_SECONDS),
        "PI_LOCKER_COUNT": "2",
        "PYTHONUNBUFFERED": "1",
    }
    with log.open("wb") as stream:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "--factory", "app.main:create_app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            cwd=PI_DIR, env=environment, stdout=stream, stderr=subprocess.STDOUT)
    pi = Pi(role, port, process, log)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pi.stop()
            pytest.fail(f"{role} Pi가 시작하지 못했습니다:\n{log.read_text(errors='replace')}")
        try:
            if pi.get("/health").json() == {"status": "ok", "role": role}:
                return pi
        except httpx.HTTPError:
            time.sleep(0.1)
    pi.stop()
    pytest.fail(f"{role} Pi가 30초 안에 응답하지 않았습니다:\n{log.read_text(errors='replace')}")


class MemoryStore:
    """S3 대역. 키와 바이트만 보관해 저장된 사진을 테스트가 확인할 수 있게 한다."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, jpeg: bytes) -> None:
        self.objects[key] = jpeg

    def read(self, key: str) -> bytes:
        return self.objects[key]

    def preview(self, key: str) -> str:
        return f"https://example.invalid/{key}"


class StubAnalyzer:
    """AI 대역. 외부 응답 계약이 정해지지 않아 입력 이미지 ID만 그대로 확인한다."""

    def __init__(self):
        self.calls = 0

    def analyze(self, registration_id, images):
        from app.integrations import AnalysisResult

        self.calls += 1
        for value in images:
            assert value.jpeg, "분석기에 빈 이미지가 전달되었다"
        return AnalysisResult(image_ids=tuple(value.image_id for value in images),
                              features={"test_only_shape": "stub"})


@pytest.fixture
def kiosk(tmp_path):
    """키오스크 한 대: 촬영 Pi, 보관함 Pi 2칸, 백엔드 API 클라이언트."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.api import get_analyzer, get_gateway, get_store
    from app.config import Settings, get_settings
    from app.db import Base, get_session
    from app.main import app
    from app.models import Category, Kiosk, Locker

    capture_pi = start_pi("capture", tmp_path)
    locker_pi = None
    engine = admin = schema = None
    try:
        locker_pi = start_pi("locker", tmp_path)
        url = os.environ.get("TEST_DATABASE_URL")
        if url:
            schema = "pipeline_" + uuid4().hex
            admin = create_engine(url)
            with admin.begin() as connection:
                from sqlalchemy import text

                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            engine = create_engine(url, connect_args={"options": f"-csearch_path={schema} -clock_timeout=5000"})
        else:
            engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

            @event.listens_for(engine, "connect")
            def enable_fks(connection, record):
                connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(engine)
        with Session(engine) as session:
            value = Kiosk(name="Pipeline kiosk", location_description="Pipeline test site")
            category = Category(code="pipeline-test", name="Pipeline test")
            session.add_all([value, category])
            session.flush()
            session.add_all([Locker(kiosk_id=value.id, locker_number=number) for number in (1, 2)])
            session.commit()
            kiosk_id, category_id = str(value.id), str(category.id)

        settings = Settings(
            _env_file=None, postgres_db="test", postgres_user="test", postgres_password="test",
            local_development=True, command_ttl_seconds=30, max_image_bytes=1024 * 1024,
            pi_timeout_seconds=10,
            pi_devices={kiosk_id: {"capture_url": capture_pi.url, "capture_api_key": API_KEY,
                                   "locker_url": locker_pi.url, "locker_api_key": API_KEY}},
        )
        store, analyzer = MemoryStore(), StubAnalyzer()

        def sessions():
            with Session(engine) as session:
                yield session

        # get_gateway는 덮지 않는다. 실제 PiGateway가 실제 HTTP로 Pi를 호출해야 한다.
        app.dependency_overrides.update({get_session: sessions, get_store: lambda: store,
                                         get_analyzer: lambda: analyzer, get_settings: lambda: settings})
        try:
            with TestClient(app) as client:
                yield SimpleNamespace(client=client, engine=engine, settings=settings, store=store,
                                      analyzer=analyzer, capture_pi=capture_pi, locker_pi=locker_pi,
                                      kiosk_id=kiosk_id, category_id=category_id, Session=Session)
        finally:
            app.dependency_overrides.clear()
    finally:
        capture_pi.stop()
        if locker_pi is not None:
            locker_pi.stop()
        if engine is not None:
            engine.dispose()
        if admin is not None:
            from sqlalchemy import text

            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def door_becomes(pi: Pi, number: int, state: str, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = pi.get(f"/lockers/{number}").json()
        if body["door"] == state:
            return
        time.sleep(0.05)
    pytest.fail(f"{number}번 보관함 문이 {timeout}초 안에 {state} 상태가 되지 않았습니다")


def jpeg_size(data: bytes) -> tuple[int, int]:
    from PIL import Image

    with Image.open(BytesIO(data)) as image:
        assert image.format == "JPEG"
        return image.size
