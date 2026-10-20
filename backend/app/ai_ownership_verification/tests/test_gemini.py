import json

import httpx
import pytest
from pydantic import ValidationError

from app.config import GeminiSettings, Settings
from app.ai_ownership_verification.gemini import GeminiError, GeminiQuestionGenerator, MAX_RESPONSE_BYTES
from app.ai_ownership_verification.schemas import ItemEvidence
from app.ai_ownership_verification.experiments import generate_questions as experiment


@pytest.fixture
def settings():
    return GeminiSettings(_env_file=None, gemini_api_key="test-only-key", gemini_model="test-model")


@pytest.fixture
def item():
    return experiment.load_cases()[0].item


def batch(text="기억나는 각인이나 문구를 설명해 주세요.", feature_id="mark"):
    return {"status": "ready", "questions": [{"feature_id": feature_id, "text": text}], "reason": "각인 확인"}


def envelope(content=None, finish="STOP"):
    return {
        "candidates": [{"finishReason": finish, "content": {"parts": [{"text": json.dumps(content or batch())}]}}],
        "modelVersion": "test-model-001",
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20, "totalTokenCount": 30},
    }


def generate(settings, item, response):
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as client:
        return GeminiQuestionGenerator(settings, client).generate(item)


def test_structured_request_and_valid_response(settings, item):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "generativelanguage.googleapis.com"
        assert request.url.path == "/v1beta/models/test-model:generateContent"
        assert not request.url.query
        assert request.headers["x-goog-api-key"] == "test-only-key"
        data = json.loads(request.content)
        submitted = json.loads(data["contents"][0]["parts"][0]["text"])
        assert [feature["feature_id"] for feature in submitted["features"]] == ["mark", "damage"]
        assert "test-only-key" not in request.content.decode()
        assert data["generationConfig"]["responseJsonSchema"]["type"] == "object"
        return httpx.Response(200, json=envelope())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = GeminiQuestionGenerator(settings, client).generate(item)
    assert len(requests) == 1
    assert result.batch.questions[0].feature_id == "mark"
    assert result.api_called
    assert result.usage["totalTokenCount"] == 30
    assert result.model_version == "test-model-001"


@pytest.mark.parametrize("case_id", ["umbrella_common", "uncertain_mark", "already_public"])
def test_insufficient_evidence_never_calls_provider(case_id):
    def no_network(request):
        pytest.fail("Insufficient evidence must not trigger a Gemini request")

    case = next(case for case in experiment.load_cases() if case.case_id == case_id)
    unconfigured = GeminiSettings(_env_file=None, gemini_api_key=None, gemini_model=None)
    with httpx.Client(transport=httpx.MockTransport(no_network)) as client:
        result = GeminiQuestionGenerator(unconfigured, client).generate(case.item)
    assert result.batch.status == "insufficient_information"
    assert result.batch.questions == []
    assert not result.api_called


@pytest.mark.parametrize("text", ["각인이 HJ-27인가요?", "각인이 ＨＪ－２７인가요?", "H J - 2 7이 적혀 있나요?"])
def test_exact_answer_leak_is_rejected(settings, item, text):
    with pytest.raises(GeminiError, match="ANSWER_LEAK_DETECTED"):
        generate(settings, item, httpx.Response(200, json=envelope(batch(text))))


@pytest.mark.parametrize("feature_id", ["missing-feature", "color"])
def test_unknown_or_public_feature_cannot_be_questioned(settings, item, feature_id):
    with pytest.raises(GeminiError, match="INVALID_FEATURE_REFERENCE"):
        generate(settings, item, httpx.Response(200, json=envelope(batch(feature_id=feature_id))))


@pytest.mark.parametrize("content", [
    {"status": "ready", "questions": [], "reason": "no questions"},
    {"status": "insufficient_information", "questions": [{"feature_id": "mark", "text": "각인은?"}], "reason": "bad"},
    {"status": "approved", "questions": [], "reason": "approved"},
    {**batch(), "approved": True},
    {"status": "ready", "questions": batch()["questions"] * 2, "reason": "duplicate"},
    batch(text="   "),
])
def test_invalid_response_contract_is_rejected(settings, item, content):
    with pytest.raises(GeminiError, match="INVALID_RESPONSE"):
        generate(settings, item, httpx.Response(200, json=envelope(content)))


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="not json"),
    httpx.Response(200, json=[]),
    httpx.Response(200, json={"candidates": []}),
    httpx.Response(200, json={"candidates": [None]}),
    httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "\u0060\u0060\u0060json\n{}\n\u0060\u0060\u0060"}]}}]}),
])
def test_malformed_provider_responses_are_sanitized(settings, item, response):
    with pytest.raises(GeminiError, match="INVALID_RESPONSE"):
        generate(settings, item, response)


@pytest.mark.parametrize(("status", "code"), [
    (400, "INVALID_REQUEST"), (401, "AUTHENTICATION_FAILED"), (403, "AUTHENTICATION_FAILED"),
    (404, "MODEL_UNAVAILABLE"), (429, "RATE_LIMITED"), (503, "PROVIDER_ERROR"), (302, "PROVIDER_ERROR"),
])
def test_http_errors_never_expose_provider_body_or_retry(settings, item, status, code):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, text="sensitive provider body test-only-key", headers={"location": "https://example.com"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GeminiError) as exc:
            GeminiQuestionGenerator(settings, client).generate(item)
    assert str(exc.value) == code
    assert len(requests) == 1


@pytest.mark.parametrize(("error", "code"), [(httpx.ReadTimeout, "REQUEST_TIMEOUT"), (httpx.ConnectError, "NETWORK_ERROR")])
def test_transport_errors_are_sanitized(settings, item, error, code):
    def handler(request):
        raise error("test-only-key", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GeminiError, match=code):
            GeminiQuestionGenerator(settings, client).generate(item)


@pytest.mark.parametrize(("body", "code"), [
    (envelope(finish="MAX_TOKENS"), "INCOMPLETE_RESPONSE"),
    (envelope(finish="SAFETY"), "CONTENT_BLOCKED"),
    ({"promptFeedback": {"blockReason": "SAFETY"}}, "CONTENT_BLOCKED"),
])
def test_blocked_or_truncated_generation_is_not_a_valid_result(settings, item, body, code):
    with pytest.raises(GeminiError, match=code):
        generate(settings, item, httpx.Response(200, json=body))


def test_oversized_response_is_rejected(settings, item):
    with pytest.raises(GeminiError, match="RESPONSE_TOO_LARGE"):
        generate(settings, item, httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1)))


def test_missing_key_fails_before_network(item):
    settings = GeminiSettings(_env_file=None, gemini_api_key="", gemini_model="test-model")
    with pytest.raises(GeminiError, match="GEMINI_NOT_CONFIGURED"):
        generate(settings, item, httpx.Response(200, json=envelope()))


def test_backend_settings_do_not_require_gemini():
    settings = Settings(
        _env_file=None, postgres_db="test", postgres_user="test", postgres_password="test",
        gemini_api_key=None, gemini_model=None,
    )
    assert settings.gemini_api_key is None


@pytest.mark.parametrize("model", ["../model", "model?key=secret", "models/model"])
def test_model_cannot_change_request_path(model):
    with pytest.raises(ValidationError):
        GeminiSettings(_env_file=None, gemini_model=model)


def test_duplicate_feature_ids_are_rejected(item):
    data = item.model_dump()
    data["features"].append(data["features"][0])
    with pytest.raises(ValidationError):
        ItemEvidence.model_validate(data)


def test_dry_run_records_plan_without_network_or_fake_results(settings, monkeypatch):
    monkeypatch.setattr(experiment, "source_revision", lambda: "test-revision")

    def no_network(request):
        pytest.fail("dry-run must never make network requests")

    with httpx.Client(transport=httpx.MockTransport(no_network)) as client:
        report = experiment.run_cases(experiment.load_cases(), settings, live=False, repeat=2, client=client)
    assert report["actual_api_requests"] == 0
    assert report["planned_api_requests"] == 6
    assert report["planned_runs"] == 12
    assert all(row["outcome"] == "not_run" and "result" not in row for row in report["runs"])
    assert "test-only-key" not in json.dumps(report)
    assert "측정한 결과가 아니다" in experiment.markdown_report(report)


def test_experiment_stops_on_rate_limit(settings, monkeypatch):
    monkeypatch.setattr(experiment, "source_revision", lambda: "test-revision")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429, text="provider body")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = experiment.run_cases(experiment.load_cases(), settings, live=True, repeat=3, client=client)
    assert len(requests) == report["actual_api_requests"] == 1
    assert report["runs"][0]["error_code"] == "RATE_LIMITED"


def test_cli_does_not_overwrite_previous_results(tmp_path):
    output = tmp_path / "previous.json"
    output.write_text("keep", encoding="utf-8")
    with pytest.raises(SystemExit):
        experiment.main(["--dry-run", "--output", str(output)])
    assert output.read_text(encoding="utf-8") == "keep"
