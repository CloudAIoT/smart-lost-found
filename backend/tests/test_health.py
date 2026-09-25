import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

# Unit tests run without a real database or a local .env file.
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")

from app.db import get_session
from app.main import app


@pytest.fixture
def session():
    mocked_session = MagicMock()

    def override_session():
        yield mocked_session

    app.dependency_overrides[get_session] = override_session
    try:
        yield mocked_session
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client(session):
    with TestClient(app) as test_client:
        yield test_client


def test_health_does_not_use_database(client, session):
    session.execute.side_effect = OperationalError("SELECT 1", {}, Exception("offline"))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    session.execute.assert_not_called()


def test_ready_success(client, session):
    session.execute.return_value.scalar_one.return_value = 1
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert str(session.execute.call_args.args[0]) == "SELECT 1"


def test_ready_database_failure(client, session):
    session.execute.side_effect = OperationalError(
        "SELECT 1", {}, Exception("postgresql://user:secret@db/test")
    )
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}
    assert "secret" not in response.text
