"""Two S3 photos -> imageprocess -> one Gemini call -> validated backend contract."""
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
from threading import BoundedSemaphore
from uuid import uuid4

from pydantic import ValidationError

from imageprocess import ImageInput, ServiceError, prepare_item, parse_item_output
from imageprocess.app.core.config import Settings as ImageSettings
from imageprocess.app.models.schemas import Features
from imageprocess.app.services.gemini_service import GeminiService
from app.errors import BusinessError, IntegrationError
from app.integrations import AnalysisResult


@lru_cache
def _slots(capacity):
    return BoundedSemaphore(capacity)


def valid_features(value):
    try:
        parsed = Features.model_validate(value, strict=True)
        return any(getattr(parsed, field) is not None for field in
                   ('color', 'shape', 'surface_texture', 'visible_text', 'structure', 'distinctive_features'))
    except (ValidationError, RecursionError):
        return False


class GeminiFeatureAnalyzer:
    def __init__(self, settings):
        self.settings = settings

    @contextmanager
    def slot(self):
        key = self.settings.gemini_api_key
        if key is None or not key.get_secret_value().strip():
            raise IntegrationError('AI_NOT_CONFIGURED')
        semaphore = _slots(self.settings.image_analysis_max_concurrency)
        if not semaphore.acquire(blocking=False):
            raise BusinessError('ANALYSIS_BUSY')
        try:
            yield
        finally:
            semaphore.release()

    def analyze(self, registration_id, images, *, category=None):
        settings = ImageSettings(
            _env_file=None, inference_backend='gemini', gemini_api_key=self.settings.gemini_api_key,
            gemini_model_name=self.settings.image_analysis_model,
            gemini_timeout_seconds=self.settings.image_analysis_timeout_seconds,
            gemini_max_output_tokens=self.settings.image_analysis_max_output_tokens,
            max_upload_bytes=min(self.settings.max_image_bytes or self.settings.image_analysis_max_bytes,
                                 self.settings.image_analysis_max_bytes),
            max_image_pixels=self.settings.image_analysis_max_pixels,
            max_image_size=self.settings.image_analysis_max_side, reject_low_quality=False,
        )
        prepared = None
        client = GeminiService(settings)
        try:
            prepared = prepare_item(registration_id, tuple(ImageInput(i.image_id, i.jpeg) for i in images),
                                    category=category, settings=settings)
            client.load()
            raw = client.infer_images(prepared.images, prepared.prompt, response_schema=prepared.response_schema)
            result = parse_item_output(prepared, raw)
            metadata = {
                'analysis_id': str(uuid4()), 'schema_version': result.schema_version, 'provider': 'gemini',
                'model': settings.gemini_model_name, 'input_category': result.input_category,
                'analyzed_at': datetime.now(timezone.utc).isoformat(), 'image_sha256': list(prepared.image_sha256),
                'prompt_sha256': hashlib.sha256(prepared.prompt.encode()).hexdigest(),
                'generation_config': {'temperature': 0, 'max_output_tokens': settings.gemini_max_output_tokens},
                'warnings': result.warnings, 'usage': client.usage,
            }
            return AnalysisResult(result.image_ids, result.data.model_dump(), metadata)
        except ServiceError as error:
            raise IntegrationError(error.code) from None
        finally:
            try:
                client.close()
            finally:
                if prepared:
                    for photo in prepared.images:
                        photo.close()
