from typing import Literal
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore', hide_input_in_errors=True)
    inference_backend: Literal['mlx', 'gemini', 'mock'] = 'mlx'
    model_name: str = 'mlx-community/Qwen2.5-VL-3B-Instruct-4bit'
    gemini_model_name: str = 'gemini-3.6-flash'
    gemini_api_key: SecretStr | None = Field(default=None, repr=False)
    gemini_timeout_seconds: int = Field(90, ge=5, le=600)
    gemini_max_output_tokens: int = Field(4096, ge=128, le=32768)
    max_image_size: int = Field(1280, ge=64, le=4096)
    max_upload_bytes: int = Field(10 * 1024 * 1024, gt=0)
    max_image_pixels: int = Field(25_000_000, gt=0)
    blur_threshold: float = Field(30, ge=0)
    darkness_threshold: float = Field(25, ge=0, le=255)
    reject_low_quality: bool = False
    max_tokens: int = Field(512, ge=64, le=4096)
    host: str = '0.0.0.0'
    port: int = Field(8000, ge=1, le=65535)

    @property
    def selected_model_name(self) -> str | None:
        if self.inference_backend == 'gemini':
            return self.gemini_model_name
        return self.model_name if self.inference_backend == 'mlx' else None
