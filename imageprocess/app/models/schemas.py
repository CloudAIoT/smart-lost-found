from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


COMMON_FEATURE_FIELDS = ('category', 'color', 'shape', 'surface_texture', 'brand', 'visible_text', 'structure')
FeatureText = Annotated[str, Field(min_length=1, max_length=1000)]


class Features(BaseModel):
    """Seven required, nullable common fields plus one nullable list of item details."""
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)
    category: str | None = Field(min_length=1, max_length=100)
    color: list[FeatureText] | None = Field(min_length=1, max_length=20)
    shape: str | None = Field(min_length=1, max_length=300)
    surface_texture: str | None = Field(min_length=1, max_length=500)
    brand: str | None = Field(min_length=1, max_length=100)
    visible_text: list[FeatureText] | None = Field(min_length=1, max_length=100)
    structure: list[FeatureText] | None = Field(min_length=1, max_length=20)
    distinctive_features: list[FeatureText] | None = Field(min_length=1, max_length=30)

    @field_validator('color', 'visible_text', 'structure', 'distinctive_features', mode='before')
    @classmethod
    def empty_observations_are_null(cls, value):
        # A legacy empty observation list means no extracted information.
        return None if isinstance(value, list) and not value else value


class LegacyFeatures(BaseModel):
    """Old saved results, accepted only by the offline comparison tool."""
    model_config = ConfigDict(extra='forbid', strict=True)
    category: str = Field(min_length=1, max_length=100)
    color: list[str] = Field(max_length=20)
    material: str = Field(min_length=1, max_length=100)
    brand: str | None
    visible_text: list[str] = Field(max_length=100)
    distinctive_features: list[str] = Field(max_length=100)
    description: str = Field(min_length=1, max_length=2000)


class AnalyzeResponse(BaseModel):
    success: Literal[True] = True
    request_id: str
    input_category: str | None = None
    data: Features
    processing_time_ms: float
    warnings: list[str] = Field(default_factory=list)


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    success: Literal[False] = False
    request_id: str
    error: ErrorDetail
