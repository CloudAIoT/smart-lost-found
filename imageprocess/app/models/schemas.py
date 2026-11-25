from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


COMMON_FEATURE_FIELDS = ('category', 'color', 'shape', 'surface_texture', 'brand', 'visible_text', 'structure')
FEATURE_SCHEMA_VERSION = 'fixed_common_v3_ko'
FeatureText = Annotated[str, Field(min_length=1, max_length=1000)]


class Features(BaseModel):
    """Seven required, nullable common fields plus one nullable list of item details."""
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)
    category: str | None = Field(min_length=1, max_length=100, description='물품 종류를 한국어로 작성. 확인 불가 시 null.')
    color: list[FeatureText] | None = Field(min_length=1, max_length=20, description='주요 색상을 한국어로 작성. 확인 불가 시 null.')
    shape: str | None = Field(min_length=1, max_length=300, description='눈에 보이는 전체 형태를 한국어로 작성.')
    surface_texture: str | None = Field(min_length=1, max_length=500, description='관찰 가능한 표면 질감을 한국어로 작성.')
    brand: str | None = Field(min_length=1, max_length=100, description='식별한 브랜드명을 원문으로 보존. 확인 불가 시 null.')
    visible_text: list[FeatureText] | None = Field(min_length=1, max_length=100, description='실제로 읽히는 글자를 번역 없이 원문으로 보존.')
    structure: list[FeatureText] | None = Field(min_length=1, max_length=20, description='눈에 보이는 주요 구조를 한국어로 작성.')
    distinctive_features: list[FeatureText] | None = Field(min_length=1, max_length=30, description='구분에 도움이 되는 구체적 특징을 한국어로 작성. 고유명사는 원문 사용 가능.')

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
