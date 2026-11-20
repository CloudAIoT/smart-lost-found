from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
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
    # 보관함 Pi: 잠금장치 대신 칸마다 LED, 문 센서 대신 버튼의 GPIO 번호. 1번 칸, 2번 칸 순서
    locker_led_pins: list[int] = Field(default=[17, 27], min_length=1)
    locker_button_pins: list[int] = Field(default=[22, 23], min_length=1)

    # 가상 센서와 MQTT (결정 기록 0004, docs/mqtt-protocol.md)
    # MQTT 토픽 앞에 붙는 키오스크 ID. 백엔드의 키오스크 ID와 같아야 한다(기본값은 개발용 시드 키오스크)
    kiosk_id: str = Field(default="00000000-0000-0000-0000-000000000001", pattern=r"^[A-Za-z0-9-]{1,64}$")
    # true면 /sim 경로로 가상 센서 값을 바꿀 수 있다 (시연용)
    simulation: bool = False
    # MQTT 브로커 주소. 비어 있으면 브로커에 보내지 않고 로그로만 남긴다
    mqtt_host: str = ""
    mqtt_port: int = Field(default=1883, ge=1, le=65535)
    mqtt_username: str = ""
    mqtt_password: SecretStr = SecretStr("")
    # 무게 센서가 이 값(g)보다 무거우면 물건이 있다고 본다
    item_threshold_g: float = Field(default=10.0, gt=0)
    # 문이 닫힌 뒤 무게를 읽기까지 기다리는 시간(초). 닫힐 때 흔들린 값이 가라앉기를 기다린다
    door_settle_seconds: float = Field(default=1.0, ge=0)
    # 마지막 촬영 뒤 이 시간(초)이 지나도 촬영 상자에 물건이 있으면 경고한다
    capture_leftover_seconds: float = Field(default=120.0, gt=0)
    # 가상 센서 값을 저장할 파일. 비우면 저장하지 않는다. Pi를 다시 켜도 보관 중인 물건의 무게가 남게 한다
    sensor_state_file: Path | None = None

    @field_validator("sensor_state_file", mode="before")
    @classmethod
    def empty_means_no_file(cls, value):
        # .env에 'PI_SENSOR_STATE_FILE='처럼 비워 두면 현재 폴더가 아니라 '저장 안 함'으로 읽는다
        return None if value == "" else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
