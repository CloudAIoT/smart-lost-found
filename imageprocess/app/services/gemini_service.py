"""Gemini adapter. Constructing the client does not send images or make API calls."""
from PIL import Image

from app.core.errors import ServiceError
from app.models.schemas import Features


class GeminiService:
    def __init__(self, settings):
        self.settings = settings
        self.client = None
        self.usage = None

    def load(self):
        if self.client is not None:
            return
        key = self.settings.gemini_api_key
        if key is None or not key.get_secret_value().strip():
            raise ServiceError('GEMINI_API_KEY_MISSING', 'Set GEMINI_API_KEY before using the Gemini backend.', 503)
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            raise ServiceError('GEMINI_DEPENDENCY_MISSING', 'Install requirements-gemini.txt in the active Python environment.', 503) from None
        # One attempt, with an explicit timeout; never silently repeat a paid call.
        self.client = genai.Client(
            api_key=key.get_secret_value(),
            http_options=types.HttpOptions(
                timeout=self.settings.gemini_timeout_seconds * 1000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    def infer(self, image: Image.Image, prompt: str) -> str:
        if self.client is None:
            raise ServiceError('MODEL_NOT_READY', 'Gemini client is not initialized.', 503)
        self.usage = None
        try:
            response = self.client.models.generate_content(
                model=self.settings.gemini_model_name,
                contents=[prompt, image],
                config={
                    'temperature': 0.0,
                    'max_output_tokens': self.settings.gemini_max_output_tokens,
                    'response_mime_type': 'application/json',
                    'response_json_schema': Features.model_json_schema(),
                },
            )
            candidates = response.candidates or []
            if not candidates:
                raise ServiceError('GEMINI_RESPONSE_BLOCKED', 'Gemini returned no answer. Check the image and API safety settings.', 502)
            finish = candidates[0].finish_reason
            finish = getattr(finish, 'value', finish)
            if finish == 'MAX_TOKENS':
                raise ServiceError('GEMINI_OUTPUT_TRUNCATED', 'Gemini reached the output limit. Increase GEMINI_MAX_OUTPUT_TOKENS.', 502)
            if finish not in (None, 'STOP'):
                raise ServiceError('GEMINI_RESPONSE_BLOCKED', 'Gemini did not return a completed feature response.', 502)
            text = response.text
            usage = response.usage_metadata
            if usage is not None:
                self.usage = {name: getattr(usage, name, None) for name in (
                    'prompt_token_count', 'candidates_token_count', 'thoughts_token_count', 'total_token_count')}
        except ServiceError:
            raise
        except Exception as exc:
            # API exceptions may include request URLs, keys or image content.
            # Classify by HTTP status only, and never serialize the exception.
            status = getattr(exc, 'code', None)
            if status in (400, 401, 403):
                raise ServiceError('GEMINI_REQUEST_REJECTED', 'Gemini rejected the request. Check the key, model access and request settings.', 502) from None
            if status == 404:
                raise ServiceError('GEMINI_MODEL_NOT_FOUND', 'Gemini model is unavailable. Set GEMINI_MODEL_NAME or pass --model.', 502) from None
            if status == 429:
                raise ServiceError('GEMINI_RATE_LIMITED', 'Gemini quota or rate limit reached. Check quota and billing before retrying.', 503) from None
            raise ServiceError('GEMINI_API_FAILED', 'Gemini request failed. Check connectivity, timeout and API availability.', 502) from None
        if not isinstance(text, str) or not text.strip():
            raise ServiceError('INVALID_MODEL_OUTPUT', 'Gemini did not return feature JSON.', 502)
        return text

    def close(self):
        if self.client is not None:
            self.client.close()
            self.client = None
