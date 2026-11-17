"""Connect registration photos to the shared image processing contract."""

from base64 import b64encode
from io import BytesIO
from uuid import UUID

from app.ai_ownership_verification.workflow_ai import GeminiWorkflowAI
from app.errors import IntegrationError
from app.integrations import AnalysisInput, AnalysisResult


class GeminiPhotoAnalyzer:
    def __init__(self, gemini: GeminiWorkflowAI, category: str | None):
        self.gemini = gemini
        self.category = category

    def analyze(self, registration_id: UUID, images: tuple[AnalysisInput, AnalysisInput]) -> AnalysisResult:
        from imageprocess import ImageInput, ServiceError, parse_item_output, prepare_item
        from imageprocess.app.core.config import Settings

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
                raw = self.gemini.generate_json_text(parts, prepared.response_schema)
                result = parse_item_output(prepared, raw)
            finally:
                for photo in prepared.images:
                    photo.close()
        except ServiceError as error:
            raise IntegrationError(error.code) from None
        except OSError:
            raise IntegrationError("INVALID_IMAGE") from None
        return AnalysisResult(image_ids=result.image_ids, features=result.data.model_dump(mode="json"))
