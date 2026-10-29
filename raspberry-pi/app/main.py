"""Pi 컨트롤러: 백엔드의 명령을 받아 장치를 움직인다.

실행: uvicorn --factory app.main:create_app --host 0.0.0.0 --port 8000
"""

import hmac
import logging
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Header
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from app.commands import CommandBook, Result
from app.config import Settings, get_settings
from app.errors import DeviceError, handle_device_error, handle_validation_error
from app.hardware import Camera, Lockers
from app.hardware.fake import FakeCamera, FakeLockers

log = logging.getLogger("pi")


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(min_length=1, max_length=100)
    expires_at: datetime


class CaptureCommand(Command):
    registration_id: str = Field(min_length=1, max_length=100)


def create_app(settings: Settings | None = None, camera: Camera | None = None,
               lockers: Lockers | None = None) -> FastAPI:
    settings = settings or get_settings()
    if not settings.fake_hardware and camera is None and lockers is None:
        raise RuntimeError("실제 장치 연결은 아직 없습니다. PI_FAKE_HARDWARE=true로 실행하세요.")

    app = FastAPI(title=f"smart-lost-found Pi ({settings.role})")
    app.add_exception_handler(DeviceError, handle_device_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    book = CommandBook(settings.command_memory)
    app.state.book = book

    def check_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
        expected = settings.api_key.get_secret_value().encode()
        if x_api_key is None or not hmac.compare_digest(x_api_key.encode(), expected):
            raise DeviceError(401, "UNAUTHORIZED", "API 키가 올바르지 않습니다")

    auth = [Depends(check_api_key)]

    @app.get("/health", dependencies=auth)
    def health() -> dict:
        return {"status": "ok", "role": settings.role}

    if settings.role == "capture":
        camera = camera or FakeCamera(settings.fake_capture_seconds)
        app.state.camera = camera

        @app.post("/capture", dependencies=auth)
        def capture(command: CaptureCommand) -> Response:
            def action() -> Result:
                try:
                    return Result(200, camera.capture(), "image/jpeg")
                except Exception:
                    log.exception("촬영 실패: %s", command.command_id)
                    raise DeviceError(500, "CAMERA_ERROR", "카메라 촬영에 실패했습니다")

            key = f"capture:{command.registration_id}"
            return book.run(command.command_id, key, command.expires_at, action).response()

    else:
        lockers = lockers or FakeLockers(settings.locker_count, settings.fake_door_open_seconds)
        app.state.lockers = lockers

        def check_locker(locker_id: int) -> None:
            if not 1 <= locker_id <= lockers.count:
                raise DeviceError(404, "LOCKER_NOT_FOUND", "없는 보관함 번호입니다")

        @app.post("/lockers/{locker_id}/open", dependencies=auth)
        def open_locker(locker_id: int, command: Command) -> Response:
            check_locker(locker_id)

            def action() -> Result:
                try:
                    lockers.open(locker_id)
                except Exception:
                    log.exception("보관함 개방 실패: %s", command.command_id)
                    raise DeviceError(500, "LOCK_ERROR", "보관함을 열지 못했습니다")
                return Result.json(200, {"locker_id": locker_id, "result": "opened"})

            key = f"open:{locker_id}"
            return book.run(command.command_id, key, command.expires_at, action).response()

        @app.get("/lockers/{locker_id}", dependencies=auth)
        def door_state(locker_id: int) -> dict:
            check_locker(locker_id)
            try:
                door = lockers.door(locker_id)
            except Exception:
                log.exception("문 상태 읽기 실패: %s", locker_id)
                raise DeviceError(500, "LOCK_ERROR", "문 상태를 읽지 못했습니다")
            return {"locker_id": locker_id, "door": door}

    return app
