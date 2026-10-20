from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class GeminiSettings(BaseSettings):
    """Optional AI settings; experiments can load these without DB credentials."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    gemini_api_key: SecretStr | None = None
    gemini_model: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    gemini_timeout_seconds: float = Field(default=30, gt=0, le=120)
    gemini_max_output_tokens: int = Field(default=4096, ge=256, le=8192)


class Settings(GeminiSettings):
    postgres_db: str
    postgres_user: str
    postgres_password: SecretStr
    db_host: str = "db"
    db_port: int = Field(default=5432, ge=1, le=65535)
    # Local fixtures remain separate from deployed API access.
    local_development: bool = False
    business_api_enabled: bool = False
    s3_bucket: str | None = None
    s3_region: str | None = None
    preview_ttl_seconds: int | None = Field(default=None, gt=0)
    max_image_bytes: int | None = Field(default=None, gt=0)
    pi_timeout_seconds: float | None = Field(default=None, gt=0)
    command_ttl_seconds: int | None = Field(default=None, gt=0)
    # Per-kiosk device routing stays in server configuration, never FE input.
    pi_devices: dict[str, dict[str, str]] = Field(default_factory=dict)

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
