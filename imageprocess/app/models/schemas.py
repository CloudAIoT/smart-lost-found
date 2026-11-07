from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Features(BaseModel):
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
