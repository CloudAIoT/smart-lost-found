"""키오스크 사용 흐름을 파트 전체로 잇는 종단 테스트의 준비물.

기본값은 Pi 컨트롤러를 가짜 하드웨어 모드로 이 PC의 별도 프로세스 2개에 띄운다.
`E2E_CAPTURE_URL`과 `E2E_LOCKER_URL`을 주면 그 주소의 Pi를 그대로 쓴다. Tailscale로
연결한 실제 Pi 2대를 이 방식으로 부른다.

어느 쪽이든 백엔드는 평소처럼 HTTP(PiGateway)로 Pi를 호출한다. AI 분석과 S3만 테스트
대역으로 바꾸며, 그 사이의 백엔드·Pi 코드는 모두 실제 코드다.
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

# 주소를 주면 원격 Pi 모드다. 주지 않으면 이 PC에서 가짜 하드웨어 Pi를 띄운다.
CAPTURE_URL = os.environ.get("E2E_CAPTURE_URL", "").strip().rstrip("/")
LOCKER_URL = os.environ.get("E2E_LOCKER_URL", "").strip().rstrip("/")
REMOTE = bool(CAPTURE_URL or LOCKER_URL)

LOCAL_API_KEY = "pipeline-test-api-key"
SHARED_API_KEY = os.environ.get("E2E_PI_API_KEY", "").strip()
CAPTURE_API_KEY = os.environ.get("E2E_CAPTURE_API_KEY", "").strip() or SHARED_API_KEY or LOCAL_API_KEY
LOCKER_API_KEY = os.environ.get("E2E_LOCKER_API_KEY", "").strip() or SHARED_API_KEY or LOCAL_API_KEY


def setting(name: str, default: float) -> float:
    value = os.environ.get(name, "").strip()
    return float(value) if value else default


# 가짜 하드웨어는 바로 찍고 문도 금방 닫힌다. 원격 Pi는 그 Pi의 .env 값에 맞춘다.
DOOR_OPEN_SECONDS = setting("E2E_DOOR_OPEN_SECONDS", 5.0 if REMOTE else 1.0)
# 원격 Pi는 네트워크 왕복과 실제 촬영 시간이 더해진다.
PI_TIMEOUT_SECONDS = setting("E2E_PI_TIMEOUT_SECONDS", 20.0 if REMOTE else 10.0)
# 실제 카메라 사진은 가짜 사진보다 훨씬 크다.
MAX_IMAGE_BYTES = int(setting("E2E_MAX_IMAGE_BYTES", 10 * 1024 * 1024 if REMOTE else 1024 * 1024))
HTTP_TIMEOUT = 10.0 if REMOTE else 5.0


def expected_image_size() -> tuple[int, int] | None:
    """검사할 사진 크기. 가짜 카메라는 640x480이고, 원격 Pi는 지정하지 않으면 크기를 보지 않는다."""
    value = os.environ.get("E2E_IMAGE_SIZE", "").strip()
    if value:
        width, _, height = value.partition("x")
        return int(width), int(height)
    return None if REMOTE else (640, 480)


IMAGE_SIZE = expected_image_size()


def free_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Pi:
    """테스트가 Pi를 직접 들여다볼 때 쓰는 손잡이. 백엔드와 같은 API만 쓴다."""

    def __init__(self, role: str, url: str, api_key: str):
        self.role = role
        self.url = url
        self.api_key = api_key

    @property
    def headers(self) -> dict:
        return {"X-API-Key": self.api_key}

    def get(self, path: str) -> httpx.Response:
        return httpx.get(self.url + path, headers=self.headers, timeout=HTTP_TIMEOUT)

    def post(self, path: str, body: dict) -> httpx.Response:
        return httpx.post(self.url + path, json=body, headers=self.headers, timeout=HTTP_TIMEOUT)

    def stop(self) -> None:
        """원격 Pi는 테스트가 멈추지 않는다."""


class LocalPi(Pi):
    """이 PC에서 띄운 가짜 하드웨어 Pi."""

    def __init__(self, role: str, port: int, process: subprocess.Popen, log: Path):
        super().__init__(role, f"http://127.0.0.1:{port}", LOCAL_API_KEY)
        self.port = port
        self.process = process
        self.log = log

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)


def start_pi(role: str, tmp_path: Path) -> LocalPi:
    port = free_port()
    log = tmp_path / f"pi-{role}.log"
    environment = {
        **os.environ,
        "PI_ROLE": role,
        "PI_API_KEY": LOCAL_API_KEY,
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
    pi = LocalPi(role, port, process, log)
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


def connect_pi(role: str, url: str, api_key: str) -> Pi:
    """이미 돌고 있는 Pi(Tailscale로 연결한 실제 Pi)에 붙어 역할까지 확인한다."""
    pi = Pi(role, url, api_key)
    try:
        response = pi.get("/health")
    except httpx.HTTPError as error:
        pytest.fail(f"{role} Pi({url})에 연결하지 못했습니다: {error}\n"
                    "Tailscale 연결(tailscale status)과 Pi가 Tailscale 주소로 열려 있는지 확인한다.")
    body = response.json()
    if body != {"status": "ok", "role": role}:
        pytest.fail(f"{role} Pi({url})의 응답이 {body}입니다.\n"
                    "API 키(E2E_PI_API_KEY)가 Pi의 PI_API_KEY와 같은지, 두 Pi의 주소가 바뀌지 않았는지 확인한다.")
    return pi


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


# 백엔드 설정은 DB 접속 정보를 요구한다. 실제 연결은 하지 않는다.
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")


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

    started: list[Pi] = []
    engine = admin = schema = None
    try:
        if REMOTE:
            if not (CAPTURE_URL and LOCKER_URL):
                pytest.fail("원격 Pi로 시험하려면 E2E_CAPTURE_URL과 E2E_LOCKER_URL을 둘 다 지정한다")
            capture_pi = connect_pi("capture", CAPTURE_URL, CAPTURE_API_KEY)
            locker_pi = connect_pi("locker", LOCKER_URL, LOCKER_API_KEY)
            # 원격 Pi는 테스트 사이에 상태가 남는다. 앞 테스트에서 열린 문이 닫히기를 기다린다.
            for number in (1, 2):
                door_becomes(locker_pi, number, "closed", timeout=DOOR_OPEN_SECONDS + 4)
        else:
            capture_pi = start_pi("capture", tmp_path)
            started.append(capture_pi)
            locker_pi = start_pi("locker", tmp_path)
            started.append(locker_pi)

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
            local_development=True, command_ttl_seconds=30, max_image_bytes=MAX_IMAGE_BYTES,
            pi_timeout_seconds=PI_TIMEOUT_SECONDS,
            pi_devices={kiosk_id: {"capture_url": capture_pi.url, "capture_api_key": capture_pi.api_key,
                                   "locker_url": locker_pi.url, "locker_api_key": locker_pi.api_key}},
        )
        store, analyzer = MemoryStore(), StubAnalyzer()

        def break_locker() -> None:
            """보관함 Pi가 응답하지 못하는 상황을 만든다."""
            if REMOTE:
                # 원격 Pi는 멈출 수 없으니 백엔드가 보는 주소만 받는 곳이 없는 포트로 바꾼다.
                settings.pi_devices[kiosk_id]["locker_url"] = f"http://127.0.0.1:{free_port()}"
            else:
                locker_pi.stop()

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
                                      kiosk_id=kiosk_id, category_id=category_id, Session=Session,
                                      image_size=IMAGE_SIZE, break_locker=break_locker, remote=REMOTE)
        finally:
            app.dependency_overrides.clear()
    finally:
        for pi in started:
            pi.stop()
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
        if body.get("door") == state:
            return
        time.sleep(0.05)
    pytest.fail(f"{number}번 보관함 문이 {timeout}초 안에 {state} 상태가 되지 않았습니다. "
                f"마지막 응답: {body}")


def jpeg_size(data: bytes) -> tuple[int, int]:
    from PIL import Image

    with Image.open(BytesIO(data)) as image:
        assert image.format == "JPEG"
        return image.size
