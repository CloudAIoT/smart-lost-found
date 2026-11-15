"""Backend-owned model calls can use this module without starting our local HTTP app."""
from dataclasses import dataclass, field
import hashlib
from uuid import UUID

from PIL import Image
from pydantic import BaseModel, ConfigDict

from ..core.config import Settings
from ..core.errors import ServiceError
from ..models.schemas import Features
from .preprocessing import preprocess
from .vision_service import build_prompt, normalize_category, parse_features


@dataclass(frozen=True)
class ImageInput:
    image_id: UUID
    jpeg: bytes = field(repr=False)


@dataclass(frozen=True)
class PreparedItem:
    registration_id: UUID
    image_ids: tuple[UUID, UUID]
    input_category: str | None
    images: tuple[Image.Image, Image.Image] = field(repr=False)
    prompt: str = field(repr=False)
    warnings: tuple[str, ...]
    image_sha256: tuple[str, str]

    @property
    def response_schema(self) -> dict:
        return Features.model_json_schema()


class ItemAnalysisResult(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: str = 'fixed_common_v2'
    registration_id: UUID
    image_ids: tuple[UUID, UUID]
    input_category: str | None
    data: Features
    warnings: list[str]


def _uuid(value) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ServiceError('INVALID_ITEM_ID', 'Registration and image IDs must be UUIDs.') from None


def prepare_item(registration_id: UUID, images: tuple[ImageInput, ImageInput], *,
                 category: str | None = None, settings: Settings | None = None) -> PreparedItem:
    """Validate and preprocess two ordered images. Never construct a client or send a request."""
    registration_id = _uuid(registration_id)
    if not isinstance(images, (tuple, list)) or len(images) != 2 or not all(isinstance(item, ImageInput) for item in images):
        raise ServiceError('INVALID_IMAGE_COUNT', 'Provide exactly two ordered ImageInput values.')
    ids = tuple(_uuid(item.image_id) for item in images)
    if ids[0] == ids[1]:
        raise ServiceError('DUPLICATE_IMAGE_ID', 'The two photo IDs must be different.')
    category = normalize_category(category)
    settings = settings if settings is not None else Settings()
    prepared, warnings, hashes = [], [], []
    for index, item in enumerate(images, 1):
        if not isinstance(item.jpeg, bytes):
            raise ServiceError('INVALID_IMAGE', 'Image content must be bytes.')
        if len(item.jpeg) > settings.max_upload_bytes:
            raise ServiceError('IMAGE_TOO_LARGE', 'Image exceeds the per-photo byte limit.', 413)
        result = preprocess(item.jpeg, settings)
        prepared.append(result.image)
        warnings.extend(f'shot_{index}:{warning}' for warning in result.warnings)
        hashes.append(hashlib.sha256(item.jpeg).hexdigest())
    return PreparedItem(registration_id, ids, category, tuple(prepared),
                        build_prompt(category, image_count=2), tuple(warnings), tuple(hashes))


def parse_item_output(prepared: PreparedItem, raw: str) -> ItemAnalysisResult:
    """Validate one combined response and retain the caller's IDs and shot order."""
    features = parse_features(raw)
    # A known category/brand alone is not usable visual evidence for completing a registration.
    visual_fields = ('color', 'shape', 'surface_texture', 'visible_text', 'structure', 'distinctive_features')
    if not any(getattr(features, name) is not None for name in visual_fields):
        raise ServiceError('INSUFFICIENT_VISUAL_FEATURES', 'The model returned no usable visual observations.', 422)
    return ItemAnalysisResult(registration_id=prepared.registration_id, image_ids=prepared.image_ids,
                              input_category=prepared.input_category, data=features, warnings=list(prepared.warnings))
