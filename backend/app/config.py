from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    postgres_db: str
    postgres_user: str
    postgres_password: SecretStr
    db_host: str = "db"
    db_port: int = Field(default=5432, ge=1, le=65535)
    # Business APIs stay disabled until explicitly enabled for local development.
    local_development: bool = False
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
