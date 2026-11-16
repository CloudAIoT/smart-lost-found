from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Pi 설정. 환경변수나 raspberry-pi/.env에서 PI_로 시작하는 값을 읽는다."""

    model_config = SettingsConfigDict(
        env_prefix="PI_",
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    role: Literal["capture", "locker"]
    api_key: SecretStr = Field(min_length=8)
    fake_hardware: bool = False
    locker_count: int = Field(default=2, ge=1, le=20)
    fake_capture_seconds: float = Field(default=1.0, ge=0)
    fake_door_open_seconds: float = Field(default=5.0, ge=0)
    # 중복 처리를 위해 기억해 두는 최근 명령 수
    command_memory: int = Field(default=200, ge=1)
    # 실제 카메라 사진 크기. 이미지 분석이 긴 변을 1280px로 줄여 쓰므로 절반 해상도로 충분하다
    capture_width: int = Field(default=2304, ge=640, le=4608)
    capture_height: int = Field(default=1296, ge=480, le=2592)
    # 초점 맞추기 시간 제한(초). 넘으면 초점이 덜 맞은 채로 찍는다
    focus_timeout_seconds: float = Field(default=3.0, gt=0, le=10)


@lru_cache
def get_settings() -> Settings:
    return Settings()
