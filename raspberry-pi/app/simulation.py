"""시연용 시뮬레이션 경로 (결정 기록 0004).

실제 센서가 없으므로, 센서가 이런 값을 읽었다고 가정하고 상황을 만든다. PI_SIMULATION=true일 때만 열린다.
백엔드가 쓰는 통신 규약(docs/device-protocol.md)과는 별개이며, 같은 API 키로 보호한다.
"""

from collections.abc import Callable, Sequence
from typing import Literal

from fastapi import APIRouter
from fastapi.params import Depends
from pydantic import BaseModel, ConfigDict, Field

from app.hardware import Lockers
from app.sensors import CaptureMonitor, LockerMonitor


class ItemChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["put", "take"]  # put: 넣기(무게를 더함), take: 꺼내기(칸이 빔)
    weight_g: float = Field(default=150.0, gt=0, le=20000)  # 넣을 물건의 무게
    preset: bool = False  # 보관함 시연 준비용: 문이 닫혀 있어도 무게를 바꾼다


class DoorAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["open", "close"]


class Shock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strength: Literal["weak", "strong"] = "strong"


def locker_routes(lockers: Lockers, monitor: LockerMonitor, check_locker: Callable[[int], None],
                  dependencies: Sequence[Depends]) -> APIRouter:
    router = APIRouter(prefix="/sim", dependencies=list(dependencies))

    @router.get("/state")
    def state() -> dict:
        return monitor.snapshot()

    @router.post("/lockers/{locker_id}/item")
    def item(locker_id: int, body: ItemChange) -> dict:
        check_locker(locker_id)
        return monitor.change_item(locker_id, body.action, body.weight_g, body.preset)

    @router.post("/lockers/{locker_id}/door")
    def door(locker_id: int, body: DoorAction) -> dict:
        check_locker(locker_id)
        if body.action == "open":
            lockers.force_open(locker_id)  # 개방 명령 없이 문이 열린 상황 = 강제 개방
        else:
            lockers.close(locker_id)  # 버튼을 누른 것과 같다
        return monitor.snapshot()

    @router.post("/lockers/{locker_id}/shock")
    def shock(locker_id: int, body: Shock) -> dict:
        check_locker(locker_id)
        return monitor.shock(locker_id, body.strength)

    @router.post("/lockers/{locker_id}/skip-auto")
    def skip_auto(locker_id: int) -> dict:
        # 다음 개방 한 번은 자동으로 넣거나 빼지 않는다 (보관 안 됨·수령 안 됨 시연)
        check_locker(locker_id)
        return monitor.skip_next_auto(locker_id)

    return router


def capture_routes(monitor: CaptureMonitor, dependencies: Sequence[Depends]) -> APIRouter:
    router = APIRouter(prefix="/sim", dependencies=list(dependencies))

    @router.get("/state")
    def state() -> dict:
        return monitor.snapshot()

    @router.post("/capture/item")
    def item(body: ItemChange) -> dict:
        return monitor.change_item(body.action, body.weight_g)

    return router
