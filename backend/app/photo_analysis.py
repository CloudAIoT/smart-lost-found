"""Connect registration photos to the shared image processing contract."""

from base64 import b64encode
from collections.abc import Callable
from io import BytesIO
import logging
from time import monotonic
from uuid import UUID

from app.ai_ownership_verification.workflow_ai import GeminiWorkflowAI
from app.errors import IntegrationError
from app.integrations import AnalysisInput, AnalysisResult


logger = logging.getLogger(__name__)

# Image analysis normally uses the configured model first. These known multimodal models keep a
# transient provider/model failure from forcing the user to restart the registration flow.
PHOTO_ANALYSIS_FALLBACK_MODELS = ("gemini-3.1-flash-lite", "gemini-2.5-flash-lite")
RETRYABLE_PROVIDER_ERRORS = frozenset({
    "OWNERSHIP_AI_MODEL_UNAVAILABLE",
    "OWNERSHIP_AI_RATE_LIMITED",
    "OWNERSHIP_AI_REQUEST_FAILED",
    "OWNERSHIP_AI_RESPONSE_TOO_LARGE",
    "OWNERSHIP_AI_TIMEOUT",
    "OWNERSHIP_AI_UNREACHABLE",
    "OWNERSHIP_AI_INVALID_RESPONSE",
})
RETRYABLE_OUTPUT_ERRORS = frozenset({"INVALID_MODEL_OUTPUT", "INSUFFICIENT_VISUAL_FEATURES"})


class GeminiPhotoAnalyzer:
    def __init__(self, gemini: GeminiWorkflowAI, category: str | None,
                 *, on_stage: Callable[..., None] | None = None):
        self.gemini = gemini
        self.category = category
        # Reported so a polling client can say which step is running and which model attempt.
        self.on_stage = on_stage or (lambda stage, attempt=0: None)

    def analyze(self, registration_id: UUID, images: tuple[AnalysisInput, AnalysisInput]) -> AnalysisResult:
        from imageprocess import ImageInput, ServiceError, parse_item_output, prepare_item
        from imageprocess.app.core.config import Settings

        self.on_stage("preparing")
        try:
            prepared = prepare_item(
                registration_id, tuple(ImageInput(image.image_id, image.jpeg) for image in images),
                category=self.category, settings=Settings(_env_file=None),
            )
            try:
                parts = [{"text": prepared.prompt}]
                for photo in prepared.images:
                    with BytesIO() as jpeg:
                        photo.save(jpeg, format="JPEG")
                        parts.append({"inline_data": {"mime_type": "image/jpeg",
                                                      "data": b64encode(jpeg.getvalue()).decode("ascii")}})
                configured = self.gemini.settings.gemini_model
                models = tuple(dict.fromkeys((configured, *PHOTO_ANALYSIS_FALLBACK_MODELS)))
                models = tuple(model for model in models if model)
                deadline = monotonic() + self.gemini.settings.gemini_timeout_seconds
                last_error: IntegrationError | ServiceError | None = None
                for index, model in enumerate(models):
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        assert last_error is not None
                        raise last_error
                    last = index == len(models) - 1
                    try:
                        self.on_stage("inferring", index + 1)
                        raw = self.gemini.generate_json_text(
                            parts, prepared.response_schema, model=model, timeout_seconds=remaining,
                            operation="photo_analysis",
                        )
                        result = parse_item_output(prepared, raw)
                        break
                    except IntegrationError as error:
                        last_error = error
                        if last or error.code not in RETRYABLE_PROVIDER_ERRORS:
                            raise
                        logger.warning("Gemini photo analysis via %s failed (%s), trying the next model",
                                       model, error.code)
                    except ServiceError as error:
                        last_error = error
                        if last or error.code not in RETRYABLE_OUTPUT_ERRORS:
                            raise
                        logger.warning("Gemini photo analysis via %s returned unusable output (%s), "
                                       "trying the next model", model, error.code)
                self.on_stage("verifying")
            finally:
                for photo in prepared.images:
                    photo.close()
        except ServiceError as error:
            raise IntegrationError(error.code) from None
        except OSError:
            raise IntegrationError("INVALID_IMAGE") from None
        return AnalysisResult(image_ids=result.image_ids, features=result.data.model_dump(mode="json"))
