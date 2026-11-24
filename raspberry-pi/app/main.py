"""Pi 컨트롤러: 백엔드의 명령을 받아 장치를 움직이고, 가상 센서가 본 상황을 MQTT로 알린다.

실행: uvicorn --factory app.main:create_app --host 0.0.0.0 --port 8000
"""

import hmac
import logging
from contextlib import asynccontextmanager
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
from app.publisher import LogPublisher, Publisher
from app.sensors import CaptureMonitor, LockerMonitor, Scheduler, SensorStore, ThreadScheduler
from app.simulation import capture_routes, locker_routes

log = logging.getLogger("pi")


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(min_length=1, max_length=100)
    expires_at: datetime


class CaptureCommand(Command):
    registration_id: str = Field(min_length=1, max_length=100)


def make_camera(settings: Settings) -> Camera:
    if settings.fake_hardware:
        return FakeCamera(settings.fake_capture_seconds)
    from app.hardware.camera import PiCamera  # Picamera2가 있는 촬영 Pi에서만 불러온다
    return PiCamera((settings.capture_width, settings.capture_height), settings.focus_timeout_seconds)


def make_lockers(settings: Settings) -> Lockers:
    if settings.fake_hardware:
        return FakeLockers(settings.locker_count, settings.fake_door_open_seconds)
    from app.hardware.gpio import GpioLockers  # gpiozero가 있는 보관함 Pi에서만 불러온다
    return GpioLockers(settings.locker_led_pins, settings.locker_button_pins)


def make_publisher(settings: Settings) -> Publisher:
    if not settings.mqtt_host:
        return LogPublisher(f"slf/{settings.kiosk_id}")
    from app.publisher import MqttPublisher
    return MqttPublisher(settings)


def show_pi_logs() -> None:
    # uvicorn은 자기 로거만 설정해서, 그대로 두면 pi 로거의 INFO("MQTT 연결됨" 등)가 버려진다.
    # pi 로거에 출력을 하나 달아 서비스 로그(journalctl)에 INFO부터 보이게 한다.
    if log.handlers:
        return  # create_app이 여러 번 불려도 한 번만 단다
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def create_app(settings: Settings | None = None, camera: Camera | None = None,
               lockers: Lockers | None = None, publisher: Publisher | None = None,
               scheduler: Scheduler | None = None) -> FastAPI:
    show_pi_logs()
    settings = settings or get_settings()
    publisher = publisher or make_publisher(settings)
    scheduler = scheduler or ThreadScheduler()
    store = SensorStore(settings.sensor_state_file)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # MQTT 연결은 서버가 켜질 때 시작하고 꺼질 때 정리한다
        start, stop = getattr(publisher, "start", None), getattr(publisher, "stop", None)
        if start:
            start()
        yield
        if stop:
            stop()

    app = FastAPI(title=f"smart-lost-found Pi ({settings.role})", lifespan=lifespan)
    app.add_exception_handler(DeviceError, handle_device_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    book = CommandBook(settings.command_memory)
    app.state.book = book
    app.state.publisher = publisher

    def check_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
        expected = settings.api_key.get_secret_value().encode()
        if x_api_key is None or not hmac.compare_digest(x_api_key.encode(), expected):
            raise DeviceError(401, "UNAUTHORIZED", "API 키가 올바르지 않습니다")

    auth = [Depends(check_api_key)]

    @app.get("/health", dependencies=auth)
    def health() -> dict:
        return {"status": "ok", "role": settings.role}

    if settings.role == "capture":
        camera = camera or make_camera(settings)
        monitor = CaptureMonitor(publisher, scheduler, store, settings.item_threshold_g,
                                 settings.capture_leftover_seconds)
        app.state.camera = camera
        app.state.capture_monitor = monitor

        @app.post("/capture", dependencies=auth)
        def capture(command: CaptureCommand) -> Response:
            def action() -> Result:
                try:
                    jpeg = camera.capture()
                except Exception:
                    log.exception("촬영 실패: %s", command.command_id)
                    raise DeviceError(500, "CAMERA_ERROR", "카메라 촬영에 실패했습니다")
                monitor.captured(command.registration_id)  # 촬영 상자에 물건이 남는지 지켜보기 시작
                return Result(200, jpeg, "image/jpeg")

            key = f"capture:{command.registration_id}"
            return book.run(command.command_id, key, command.expires_at, action).response()

        if settings.simulation:
            app.include_router(capture_routes(monitor, auth))

    else:
        lockers = lockers or make_lockers(settings)
        monitor = LockerMonitor(lockers.count, publisher, scheduler, store, settings.item_threshold_g,
                                settings.door_settle_seconds,
                                auto=settings.simulation and settings.simulation_auto)
        lockers.listener = monitor.door_changed  # 문이 열리고 닫힐 때 센서 감시에 알린다
        app.state.lockers = lockers
        app.state.locker_monitor = monitor

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

        if settings.simulation:
            app.include_router(locker_routes(lockers, monitor, check_locker, auth))

    # 브로커에 다시 붙을 때마다 지금 상태를 보존 메시지로 다시 보낸다
    on_connected = getattr(publisher, "on_connected", None)
    if on_connected is not None:
        on_connected.append(monitor.publish_all)

    return app
