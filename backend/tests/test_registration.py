from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.api import get_analyzer, get_gateway, get_store
from app.config import Settings, get_settings
from app.db import Base, get_session
from app.integrations import AnalysisResult, IntegrationError
from app.main import app
from app.models import Category, DeviceCommand, FoundItem, ItemImage, Kiosk, Locker, Registration, StorageAssignment
from app.services import BusinessError, complete_deposit, open_deposit, search_stored_items


class FakeDevice:
    def __init__(self, jpeg):
        self.jpeg = jpeg
        self.captures = []
        self.opens = []
        self.door_state = "closed"
        self.error = None
        self.on_open = None

    def capture(self, kiosk_id, command_id, registration_id, expires_at):
        self.captures.append((kiosk_id, command_id, registration_id))
        if self.error:
            raise self.error
        return self.jpeg

    def open(self, kiosk_id, number, command_id, expires_at):
        self.opens.append((kiosk_id, number, command_id))
        if self.on_open:
            self.on_open()
        if self.error:
            raise self.error

    def door(self, kiosk_id, number):
        if self.error:
            raise self.error
        return self.door_state


class FakeStore:
    def __init__(self):
        self.objects = {}
        self.fail = False

    def put(self, key, jpeg):
        if self.fail:
            raise IntegrationError("IMAGE_STORAGE_FAILED")
        self.objects[key] = jpeg

    def read(self, key):
        return self.objects[key]

    def preview(self, key):
        return "https://example.invalid/temporary-preview"


class FakeAnalyzer:
    def __init__(self):
        self.error = None
        self.wrong_images = False
        self.calls = 0

    def analyze(self, registration_id, images):
        self.calls += 1
        if self.error:
            raise self.error
        ids = tuple(value.image_id for value in images)
        return AnalysisResult(image_ids=(uuid4(), uuid4()) if self.wrong_images else ids,
                              features={"test_only_private_mark": "secret-scratch"})


@pytest.fixture
def env(database_engine):
    engine = database_engine
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        kiosk = Kiosk(name="Test kiosk", location_description="Test location")
        category = Category(code="test-only", name="Test only")
        session.add_all([kiosk, category])
        session.flush()
        session.add_all([Locker(kiosk_id=kiosk.id, locker_number=n) for n in (1, 2)])
        session.commit()
        kiosk_id, category_id = str(kiosk.id), str(category.id)
    buffer = BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, format="JPEG")
    gateway, store, analyzer = FakeDevice(buffer.getvalue()), FakeStore(), FakeAnalyzer()
    settings = Settings(_env_file=None, postgres_db="test", postgres_user="test", postgres_password="test",
                        local_development=True, command_ttl_seconds=30, max_image_bytes=1024 * 1024)
    def sessions():
        with Session(engine) as session:
            yield session
    app.dependency_overrides.update({get_session: sessions, get_gateway: lambda: gateway,
        get_store: lambda: store, get_analyzer: lambda: analyzer, get_settings: lambda: settings})
    try:
        with TestClient(app) as client:
            yield SimpleNamespace(client=client, engine=engine, gateway=gateway, store=store,
                                  analyzer=analyzer, settings=settings, kiosk_id=kiosk_id, category_id=category_id)
    finally:
        app.dependency_overrides.clear()


def start(env):
    response = env.client.post("/api/registrations", json={"kiosk_id": env.kiosk_id})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def photos(env, registration_id):
    for shot in (1, 2):
        response = env.client.post(f"/api/registrations/{registration_id}/shots/{shot}")
        assert response.status_code == 200, response.text


def ready(env):
    value = start(env)
    photos(env, value)
    response = env.client.post(f"/api/registrations/{value}/analysis")
    assert response.status_code == 200, response.text
    return value


def test_full_deposit_and_duplicates(env):
    value = ready(env)
    base = f"/api/registrations/{value}"
    assert len(env.gateway.captures) == 2
    assert env.gateway.captures[0][1] != env.gateway.captures[1][1]
    assert env.gateway.captures[0][2] == env.gateway.captures[1][2]
    assert env.client.post(base + "/shots/1").status_code == 200
    assert len(env.gateway.captures) == 2
    assert env.client.post(base + "/analysis").status_code == 200
    assert env.analyzer.calls == 1
    assert env.client.post(base + "/deposit/complete", json={"confirmed": True}).status_code == 409
    for _ in range(2):
        response = env.client.post(base + "/deposit/open")
        assert response.status_code == 200, response.text
        assert response.json()["locker_number"] == 1
    assert len(env.gateway.opens) == 1
    with Session(env.engine) as session:
        assert search_stored_items(session) == []
    assert env.client.get(base).json()["status"] == "awaiting_deposit"
    assert env.client.post(base + "/deposit/complete", json={"confirmed": False}).status_code == 422
    for _ in range(2):
        assert env.client.post(base + "/deposit/complete", json={"confirmed": True}).json()["status"] == "stored"
    with Session(env.engine) as session:
        results = search_stored_items(session)
        assert len(results) == 1
        assert "secret-scratch" not in str(results)
        assert search_stored_items(session, category_id=uuid4()) == []
        assert session.scalar(select(func.count()).select_from(FoundItem)) == 1
        assert session.scalar(select(StorageAssignment)).ended_at is None
    assert "analysis_features" not in env.client.get(base).json()
    assert "object_key" not in env.client.get(base).text


def test_optional_inputs_and_registration_isolation(env):
    first, second = start(env), start(env)
    photos(env, first)
    assert env.client.get(f"/api/registrations/{second}").json()["images"] == []
    assert env.client.get(f"/api/registrations/{first}").json()["primary_category_id"] is None
    assert env.client.post("/api/registrations", json={"kiosk_id": env.kiosk_id, "primary_category_id": str(uuid4())}).status_code == 404
    assert env.client.post("/api/registrations", json={"kiosk_id": str(uuid4())}).status_code == 404


def test_order_and_analysis_gates(env):
    value = start(env)
    base = f"/api/registrations/{value}"
    assert env.client.post(base + "/shots/2").status_code == 409
    assert env.client.post(base + "/shots/3").status_code == 422
    assert env.client.post(base + "/analysis").status_code == 409
    assert env.client.post(base + "/deposit/open").status_code == 409
    assert env.gateway.captures == env.gateway.opens == []
    photos(env, value)
    assert env.client.post(base + "/deposit/open").status_code == 409
    env.analyzer.wrong_images = True
    assert env.client.post(base + "/analysis").status_code == 502
    assert env.client.post(base + "/deposit/open").status_code == 409
    assert env.gateway.opens == []
    env.analyzer.wrong_images = False
    assert env.client.post(base + "/analysis").status_code == 200


@pytest.mark.parametrize("failure", ["storage", "invalid_jpeg", "device"])
def test_second_photo_failure_never_becomes_ready(env, failure):
    value = start(env)
    base = f"/api/registrations/{value}"
    assert env.client.post(base + "/shots/1").status_code == 200
    if failure == "storage":
        env.store.fail = True
    elif failure == "invalid_jpeg":
        env.gateway.jpeg = b"not JPEG"
    else:
        env.gateway.error = IntegrationError("DEVICE_UNREACHABLE", uncertain=True)
    assert env.client.post(base + "/shots/2").status_code == 502
    assert env.client.get(base).json()["status"] == "blocked"
    assert env.client.post(base + "/shots/2").status_code == 409
    assert env.client.post(base + "/deposit/open").status_code == 409
    with Session(env.engine) as session:
        assert len(session.scalars(select(ItemImage)).all()) == 1
    assert len(env.gateway.captures) == 2


@pytest.mark.parametrize("uncertain", [False, True])
def test_open_failure_reserves_locker_and_hides_number(env, uncertain):
    value = ready(env)
    base = f"/api/registrations/{value}"
    env.gateway.error = IntegrationError("DEVICE_UNREACHABLE" if uncertain else "LOCK_ERROR", uncertain=uncertain)
    assert env.client.post(base + "/deposit/open").status_code == 502
    assert env.client.get(base).json()["locker_number"] is None
    assert env.client.post(base + "/deposit/open").status_code == 409
    assert env.client.post(base + "/deposit/complete", json={"confirmed": True}).status_code == 409
    with Session(env.engine) as session:
        assert session.scalar(select(StorageAssignment)).ended_at is None
        command = session.scalar(select(DeviceCommand).where(DeviceCommand.action == "open"))
        assert command.status == ("unknown" if uncertain else "failed")
    assert len(env.gateway.opens) == 1


@pytest.mark.parametrize("door", ["open", "offline"])
def test_completion_requires_closed_sensor(env, door):
    value = ready(env)
    base = f"/api/registrations/{value}"
    assert env.client.post(base + "/deposit/open").status_code == 200
    if door == "offline":
        env.gateway.error = IntegrationError("DEVICE_UNREACHABLE", uncertain=True)
    else:
        env.gateway.door_state = door
    response = env.client.post(base + "/deposit/complete", json={"confirmed": True})
    assert response.status_code in (409, 502)
    assert env.client.get(base).json()["status"] == "awaiting_deposit"


def test_duplicate_open_while_in_flight_does_not_send_another_command(env):
    value = ready(env)
    base = f"/api/registrations/{value}"
    env.gateway.on_open = lambda: assert_busy(env.client.post(base + "/deposit/open"))
    assert env.client.post(base + "/deposit/open").status_code == 200
    assert len(env.gateway.opens) == 1


def assert_busy(response):
    assert response.status_code == 409


def test_capacity_and_unique_constraints(env):
    for expected in (1, 2):
        value = ready(env)
        assert env.client.post(f"/api/registrations/{value}/deposit/open").json()["locker_number"] == expected
    value = ready(env)
    assert env.client.post(f"/api/registrations/{value}/deposit/open").json()["detail"]["code"] == "NO_AVAILABLE_LOCKER"
    assert env.client.get(f"/api/registrations/{value}").json()["status"] == "ready"
    with Session(env.engine) as session:
        first = session.scalar(select(StorageAssignment))
        session.add(StorageAssignment(found_item_id=first.found_item_id, locker_id=first.locker_id))
        with pytest.raises(IntegrityError):
            session.commit()


def test_disabled_by_default_and_no_public_search(env):
    env.settings.local_development = False
    assert env.client.get("/api/kiosks").status_code == 503
    assert env.client.post("/api/registrations", json={"kiosk_id": env.kiosk_id}).status_code == 503
    assert env.client.get("/api/items").status_code == 404
    assert env.client.get("/health").status_code == 200


def test_unconfigured_ai_fails_closed(env):
    app.dependency_overrides.pop(get_analyzer)
    value = start(env)
    photos(env, value)
    response = env.client.post(f"/api/registrations/{value}/analysis")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "AI_NOT_CONFIGURED"
    assert env.client.post(f"/api/registrations/{value}/deposit/open").status_code == 409
    assert env.gateway.opens == []


def test_changed_analysis_snapshot_cannot_open(env):
    from uuid import UUID
    value = ready(env)
    with Session(env.engine) as session:
        session.get(Registration, UUID(value)).analysis_input_image_ids = [str(uuid4()), str(uuid4())]
        session.commit()
    response = env.client.post(f"/api/registrations/{value}/deposit/open")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ANALYSIS_REQUIRED"
    assert env.gateway.opens == []


def test_database_failure_after_physical_open_never_reopens(env, monkeypatch):
    from uuid import UUID
    value = UUID(ready(env))
    with Session(env.engine) as session:
        commit = session.commit
        commits = 0
        def fail_result_commit():
            nonlocal commits
            commits += 1
            if commits == 2:
                raise OperationalError("COMMIT", {}, Exception("database offline"))
            commit()
        monkeypatch.setattr(session, "commit", fail_result_commit)
        with pytest.raises(OperationalError):
            open_deposit(session, value, env.gateway, ttl=30)
        session.rollback()
    response = env.client.post(f"/api/registrations/{value}/deposit/open")
    assert response.status_code == 409
    result = env.client.get(f"/api/registrations/{value}").json()
    assert result["status"] == "opening"
    assert result["locker_number"] is None
    assert len(env.gateway.opens) == 1
    with Session(env.engine) as session:
        assert session.scalar(select(StorageAssignment)).ended_at is None
        assert session.scalar(select(DeviceCommand).where(DeviceCommand.action == "open")).status == "sent"


@pytest.mark.parametrize("same_registration", [False, True])
def test_postgres_concurrent_allocation(env, same_registration):
    if env.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locks require TEST_DATABASE_URL")
    from uuid import UUID
    first = ready(env)
    second = first if same_registration else ready(env)
    barrier = Barrier(2)
    def allocate(value):
        with Session(env.engine) as session:
            barrier.wait(timeout=10)
            try:
                result = open_deposit(session, UUID(value), env.gateway, ttl=30)
                return result.locker_id
            except BusinessError as error:
                return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(allocate, [first, second]))
    with Session(env.engine) as session:
        assignments = session.scalars(select(StorageAssignment)).all()
        assert len(assignments) == (1 if same_registration else 2), results
        assert len({row.locker_id for row in assignments}) == len(assignments)
    assert len(env.gateway.opens) == len(assignments)


def test_postgres_concurrent_completion_preserves_first_timestamp(env):
    if env.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locks require TEST_DATABASE_URL")
    from uuid import UUID
    value = UUID(ready(env))
    assert env.client.post(f"/api/registrations/{value}/deposit/open").status_code == 200
    barrier = Barrier(2)
    def concurrent_door(kiosk_id, number):
        barrier.wait(timeout=10)
        return "closed"
    env.gateway.door = concurrent_door
    def complete(_):
        with Session(env.engine) as session:
            return complete_deposit(session, value, env.gateway).deposited_at
    with ThreadPoolExecutor(max_workers=2) as pool:
        timestamps = list(pool.map(complete, [1, 2]))
    assert timestamps[0] == timestamps[1]
