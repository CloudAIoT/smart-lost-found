"""Small, injectable REST client for the question-generation experiment."""

from dataclasses import dataclass, field
import json
from pathlib import Path
import time
import unicodedata

import httpx
from pydantic import ValidationError

from app.config import GeminiSettings
from .schemas import ItemEvidence, QuestionBatch


PROMPT_PATH = Path(__file__).parent / "experiments/prompts/question_generation.txt"
API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_RESPONSE_BYTES = 262144


class GeminiError(RuntimeError):
    """Expose a stable code, not provider bodies, request headers, or API keys."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GenerationResult:
    batch: QuestionBatch
    api_called: bool
    latency_ms: int = 0
    model_version: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    raw_text: str | None = None


def _normalize(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def validate_questions(batch: QuestionBatch, item: ItemEvidence) -> None:
    eligible_ids = {feature.feature_id for feature in item.usable_features}
    private_values = [
        _normalize(feature.value)
        for feature in item.features
        if feature.visibility == "private" and feature.value and feature.value.strip()
    ]
    for question in batch.questions:
        if question.feature_id not in eligible_ids:
            raise GeminiError("INVALID_FEATURE_REFERENCE")
        text = _normalize(question.text)
        if not text:
            raise GeminiError("INVALID_RESPONSE")
        if any(value in text for value in private_values):
            raise GeminiError("ANSWER_LEAK_DETECTED")
    # Paraphrased hints cannot be reliably caught by string matching.
    # All generated questions still require human review in this experiment.


class GeminiQuestionGenerator:
    def __init__(self, settings: GeminiSettings, client: httpx.Client):
        self.settings = settings
        self.client = client

    def require_configuration(self) -> None:
        key = self.settings.gemini_api_key
        if not key or not key.get_secret_value().strip() or not self.settings.gemini_model:
            raise GeminiError("GEMINI_NOT_CONFIGURED")

    def generate(self, item: ItemEvidence) -> GenerationResult:
        if not item.usable_features:
            return GenerationResult(
                batch=QuestionBatch(
                    status="insufficient_information",
                    questions=[],
                    reason="확인 가능한 비공개 고유 특징이 없습니다.",
                ),
                api_called=False,
            )
        self.require_configuration()
        payload = {
            "systemInstruction": {"parts": [{"text": PROMPT_PATH.read_text(encoding="utf-8")}]},
            "contents": [{
                "role": "user",
                "parts": [{"text": json.dumps({
                    "item_id": item.item_id,
                    "category": item.category,
                    "features": [feature.model_dump() for feature in item.usable_features],
                }, ensure_ascii=False)}],
            }],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": QuestionBatch.model_json_schema(),
                "maxOutputTokens": self.settings.gemini_max_output_tokens,
            },
        }
        started = time.perf_counter()
        try:
            with self.client.stream(
                "POST",
                f"{API_ROOT}/{self.settings.gemini_model}:generateContent",
                json=payload,
                headers={"x-goog-api-key": self.settings.gemini_api_key.get_secret_value()},
                timeout=self.settings.gemini_timeout_seconds,
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    code = {
                        400: "INVALID_REQUEST", 401: "AUTHENTICATION_FAILED",
                        403: "AUTHENTICATION_FAILED", 404: "MODEL_UNAVAILABLE",
                        429: "RATE_LIMITED",
                    }.get(response.status_code, "PROVIDER_ERROR")
                    raise GeminiError(code)
                data = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise GeminiError("RESPONSE_TOO_LARGE")
        except httpx.TimeoutException:
            raise GeminiError("REQUEST_TIMEOUT") from None
        except httpx.RequestError:
            raise GeminiError("NETWORK_ERROR") from None
        latency = round((time.perf_counter() - started) * 1000)
        try:
            envelope = json.loads(data)
            if envelope.get("promptFeedback", {}).get("blockReason"):
                raise GeminiError("CONTENT_BLOCKED")
            candidates = envelope["candidates"]
            if not isinstance(candidates, list) or len(candidates) != 1:
                raise GeminiError("INVALID_RESPONSE")
            candidate = candidates[0]
            finish = candidate.get("finishReason")
            if finish in {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "RECITATION"}:
                raise GeminiError("CONTENT_BLOCKED")
            if finish != "STOP":
                raise GeminiError("INCOMPLETE_RESPONSE")
            parts = candidate["content"]["parts"]
            if not isinstance(parts, list):
                raise GeminiError("INVALID_RESPONSE")
            raw_text = "".join(
                part["text"] for part in parts
                if "text" in part and not part.get("thought", False)
            )
            batch = QuestionBatch.model_validate_json(raw_text, strict=True)
            validate_questions(batch, item)
            usage_raw = envelope.get("usageMetadata") or {}
            usage = {
                key: usage_raw[key]
                for key in ("promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount", "totalTokenCount")
                if type(usage_raw.get(key)) is int and usage_raw[key] >= 0
            }
            model_version = envelope.get("modelVersion")
            if model_version is not None and not isinstance(model_version, str):
                raise GeminiError("INVALID_RESPONSE")
        except (ValueError, KeyError, TypeError, AttributeError, ValidationError):
            raise GeminiError("INVALID_RESPONSE") from None
        return GenerationResult(batch, True, latency, model_version, usage, raw_text)
