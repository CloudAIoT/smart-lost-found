import os

import pytest
from fastapi.testclient import TestClient

# Unit tests run without a real database or a local .env file.
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")

from app import ai_metrics
from app.config import Settings, get_settings
from app.main import app


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, postgres_db="test", postgres_user="test", postgres_password="test",
                    **overrides)


@pytest.fixture
def client():
    configured = settings(local_development=True, gemini_api_key="test-key",
                          gemini_model="gemini-3.5-flash-lite")
    app.dependency_overrides[get_settings] = lambda: configured
    ai_metrics.clear()
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        ai_metrics.clear()


def test_status_reports_the_limits_one_search_runs_under(client):
    body = client.get("/api/ai/status").json()
    assert body["configured"] is True
    assert body["limits"] == {
        "call_timeout_seconds": 30, "search_budget_seconds": 25, "sync_wait_seconds": 3,
        "search_concurrency": 3, "max_concurrent_searches": 4,
    }
    assert body["searches"] == {"accepted": 0, "running": 0, "queued": 0}
    assert body["recent_calls"]["calls"] == 0 and body["recent_calls"]["latency_ms"] is None


def test_status_summarizes_measured_calls_without_exposing_the_key(client):
    ai_metrics.record("extract", "gemini-3.5-flash-lite", 120, "ok")
    ai_metrics.record("search", "gemini-4-hydrogen", 2400, "ok")
    ai_metrics.record("search", "gemini-4-hydrogen", 30000, "OWNERSHIP_AI_TIMEOUT")
    response = client.get("/api/ai/status")
    recent = response.json()["recent_calls"]
    assert recent["calls"] == 3
    assert recent["latency_ms"] == {"p50": 2400, "p95": 30000, "max": 30000}
    assert recent["outcomes"] == {"ok": 2, "OWNERSHIP_AI_TIMEOUT": 1}
    assert recent["operations"]["search"] == {"calls": 2, "failed": 1, "slowest_ms": 30000}
    assert recent["models"] == {"gemini-3.5-flash-lite": 1, "gemini-4-hydrogen": 2}
    assert "test-key" not in response.text


def test_status_is_part_of_the_business_api(client):
    app.dependency_overrides[get_settings] = lambda: settings()
    response = client.get("/api/ai/status")
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "BUSINESS_API_DISABLED"}}


def test_status_without_a_configured_model_says_so(client):
    app.dependency_overrides[get_settings] = lambda: settings(local_development=True, gemini_api_key="test-key")
    assert client.get("/api/ai/status").json()["configured"] is False
