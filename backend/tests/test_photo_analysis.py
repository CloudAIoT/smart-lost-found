import base64
import json
from io import BytesIO
from uuid import uuid4

import httpx
from PIL import Image
import pytest

from app.ai_ownership_verification.workflow_ai import GeminiWorkflowAI
from app.config import GeminiSettings
from app.errors import IntegrationError
from app.integrations import AnalysisInput
from app.photo_analysis import GeminiPhotoAnalyzer, PHOTO_ANALYSIS_FALLBACK_MODELS


def sample_photos():
    values = []
    for color in ("red", "blue"):
        with Image.new("RGB", (40, 25), color=color) as photo, BytesIO() as jpeg:
            photo.save(jpeg, format="JPEG")
            values.append(AnalysisInput(uuid4(), jpeg.getvalue()))
    return tuple(values)


@pytest.mark.parametrize("category, feature_color, expected_category, expected_color", [
    ("필통", "빨강", "필통", "빨강"),
    ("pencil_case", "red", "필통", "빨간색"),
])
def test_analyzer_sends_both_photos_in_order_and_stores_validated_fields(
    category, feature_color, expected_category, expected_color,
):
    photos = []
    for color in ("red", "blue"):
        with Image.new("RGB", (40, 25), color=color) as photo, BytesIO() as jpeg:
            photo.save(jpeg, format="JPEG")
            photos.append(AnalysisInput(uuid4(), jpeg.getvalue()))
    features = {
        "category": category, "color": [feature_color], "shape": "직사각형",
        "surface_texture": "직조 표면", "brand": "Carhartt", "visible_text": ["carhartt"],
        "structure": ["측면 지퍼"], "distinctive_features": None,
    }

    def respond(request):
        payload = json.loads(request.content)
        parts = payload["contents"][0]["parts"]
        assert len(parts) == 3
        assert "specific equivalent category in Korean" in parts[0]["text"]
        assert set(payload["generationConfig"]["responseJsonSchema"]["required"]) == features.keys()
        for part, channel in zip(parts[1:], (0, 2)):
            assert part["inline_data"]["mime_type"] == "image/jpeg"
            with Image.open(BytesIO(base64.b64decode(part["inline_data"]["data"]))) as photo:
                assert photo.getpixel((10, 10))[channel] > 200
        return httpx.Response(200, json={"candidates": [{
            "finishReason": "STOP", "content": {"parts": [{"text": json.dumps(features)}]},
        }]})

    settings = GeminiSettings(_env_file=None, gemini_api_key="test-key", gemini_model="test-model")
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = GeminiPhotoAnalyzer(GeminiWorkflowAI(settings, client), "필통").analyze(uuid4(), tuple(photos))
    assert result.image_ids == tuple(photo.image_id for photo in photos)
    assert result.features == {**features, "category": expected_category, "color": [expected_color]}


@pytest.mark.parametrize("status, expected_calls", [(503, 3), (401, 1)])
def test_analyzer_retries_only_recoverable_provider_failures(status, expected_calls):
    calls = []

    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(status, json={"error": {"message": "must stay private"}})

    settings = GeminiSettings(_env_file=None, gemini_api_key="test-key", gemini_model="configured-model")
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(IntegrationError):
            GeminiPhotoAnalyzer(GeminiWorkflowAI(settings, client), "필통").analyze(uuid4(), sample_photos())

    assert len(calls) == expected_calls
    expected_models = ("configured-model", *PHOTO_ANALYSIS_FALLBACK_MODELS)[:expected_calls]
    assert calls == [f"/v1beta/models/{model}:generateContent" for model in expected_models]
