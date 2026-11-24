"""가상 센서와 상황 판단 (결정 기록 0004).

보관함 상자와 센서를 실제로 만들지 않고, 센서가 있다고 가정한 값으로 상황을 판단해 MQTT로 알린다.
- 보관함 칸: 문 센서(자석식 리드 스위치), 충격 센서(진동), 무게 센서(로드셀)
- 촬영 상자: 무게 센서(로드셀)
센서 값은 /sim 경로(시연용)로 바꾼다. 토픽과 메시지 형식은 docs/mqtt-protocol.md에 있다.
"""

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Protocol

from app.errors import DeviceError
from app.hardware import DoorChange
from app.publisher import Publisher, now_text

log = logging.getLogger("pi")

ItemAction = Literal["put", "take"]


class Scheduler(Protocol):
    def call_later(self, seconds: float, func: Callable[[], None]) -> Callable[[], None]: ...  # 취소 함수를 돌려준다


class ThreadScheduler:
    """정해진 시간 뒤에 함수를 부른다. 테스트에서는 바로 실행하는 대역으로 바꾼다."""

    def call_later(self, seconds: float, func: Callable[[], None]) -> Callable[[], None]:
        timer = threading.Timer(seconds, func)
        timer.daemon = True
        timer.start()
        return timer.cancel


class SensorStore:
    """가상 무게 값을 파일에 저장한다. 실제 센서라면 Pi를 다시 켜도 무게를 읽을 수 있는 것을 흉내 낸다."""

    def __init__(self, path: Path | None):
        self.path = path
        self.save_lock = threading.Lock()

    def load(self) -> dict:
        if self.path is None or not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.exception("가상 센서 값을 읽지 못해 처음 값으로 시작합니다: %s", self.path)
            return {}

    def save(self, key: str, value) -> None:
        if self.path is None:
            return
        with self.save_lock:
            data = self.load()
            data[key] = value
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.path.with_suffix(".tmp")
                temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                temp.replace(self.path)  # 쓰는 도중 꺼져도 원래 파일이 깨지지 않게 바꿔 끼운다
            except OSError:
                log.exception("가상 센서 값을 저장하지 못했습니다: %s", self.path)


class Sender:
    """모니터들이 같이 쓰는 보내기 도구. 보내다 실패해도 장치 동작은 멈추지 않는다."""

    def __init__(self, publisher: Publisher, device: Literal["capture", "locker"]):
        self.publisher = publisher
        self.device = device

    def send(self, path: str, payload: dict, retain: bool = False) -> None:
        try:
            self.publisher.publish(path, {**payload, "at": now_text()}, retain=retain)
        except Exception:
            log.exception("MQTT로 보내지 못했습니다: %s", path)

    def alert(self, code: str, severity: Literal["alarm", "warning"], message: str,
              locker_id: int | None = None, **detail) -> None:
        log.warning("경고 %s: %s", code, message)
        self.send("alerts", {"code": code, "severity": severity, "device": self.device,
                             "locker_id": locker_id, "message": message, "detail": detail})


def item_text(weight_g: float, threshold_g: float) -> Literal["present", "empty"]:
    return "present" if weight_g > threshold_g else "empty"


def next_weight(current_g: float, action: ItemAction, weight_g: float) -> float:
    # 넣으면 무게가 더해지고, 꺼내면 칸이 빈다
    return round(current_g + weight_g, 1) if action == "put" else 0.0


@dataclass(frozen=True)
class LockerState:
    door: Literal["open", "closed"] = "closed"
    weight_g: float = 0.0
    session: Literal["opened", "forced"] | None = None  # 문이 열린 동안: 개방 명령으로 열림 / 명령 없이 열림
    opened_weight_g: float = 0.0  # 문이 열릴 때의 무게


class LockerMonitor:
    """보관함 칸마다 문·충격·무게 센서를 보고 상황을 판단한다."""

    def __init__(self, count: int, publisher: Publisher, scheduler: Scheduler, store: SensorStore,
                 threshold_g: float, settle_seconds: float, auto: bool = False, auto_weight_g: float = 150.0):
        saved = store.load().get("lockers", {})
        self.states = {n: LockerState(weight_g=float(saved.get(str(n), 0.0))) for n in range(1, count + 1)}
        self.sender = Sender(publisher, "locker")
        self.scheduler = scheduler
        self.store = store
        self.threshold_g = threshold_g
        self.settle_seconds = settle_seconds
        self.lock = threading.Lock()
        # 자동 모드: 개방 명령으로 열리면 정상 보관·수령처럼 가상 무게를 알아서 바꾼다
        self.auto = auto
        self.auto_weight_g = auto_weight_g
        self.skip_auto: frozenset[int] = frozenset()  # 다음 개방 한 번은 자동으로 바꾸지 않을 칸

    def has_item(self, weight_g: float) -> bool:
        return weight_g > self.threshold_g

    # 문 센서: 보관함 장치(Lockers)의 listener로 연결한다
    def door_changed(self, number: int, change: DoorChange) -> None:
        if change == "closed":
            self._door_closed(number)
        else:
            self._door_opened(number, change)

    def _door_opened(self, number: int, change: Literal["opened", "forced"]) -> None:
        with self.lock:
            state = replace(self.states[number], door="open", session=change,
                            opened_weight_g=self.states[number].weight_g)
            self.states[number] = state
            self._send_state(number, state)
        self.sender.send(f"lockers/{number}/events", {"type": "door_opened", "locker_id": number,
                                                      "authorized": change == "opened",
                                                      "weight_g": state.weight_g})
        if change == "forced":
            self.sender.alert("FORCED_OPEN", "alarm", f"{number}번 보관함 문이 개방 명령 없이 열렸습니다. "
                              "강제 개방이 의심됩니다.", locker_id=number)
        elif self.auto:
            self._auto_item(number, state.weight_g)

    def _auto_item(self, number: int, opened_weight_g: float) -> None:
        # 정상 흐름 흉내: 빈 칸이 열리면 물건을 넣고(보관), 물건이 있는 칸이 열리면 꺼낸다(수령)
        with self.lock:
            skip = number in self.skip_auto
            self.skip_auto = self.skip_auto - {number}
        if skip:
            log.info("%d번 칸: 이번 개방은 자동으로 넣거나 빼지 않음 (이상 상황 시연)", number)
            return
        action: ItemAction = "take" if self.has_item(opened_weight_g) else "put"
        try:
            self.change_item(number, action, self.auto_weight_g)
        except DeviceError:
            log.warning("%d번 칸 문이 먼저 닫혀 자동으로 무게를 바꾸지 못했습니다", number)
            return
        log.info("%d번 칸 자동으로 물건 %s", number, "꺼냄 (수령)" if action == "take" else "넣음 (보관)")

    def skip_next_auto(self, number: int) -> dict:
        # 다음에 개방 명령으로 열릴 때 한 번은 자동으로 바꾸지 않는다: 보관 안 됨·수령 안 됨 시연
        with self.lock:
            self.skip_auto = self.skip_auto | {number}
        return self.snapshot()

    def _door_closed(self, number: int) -> None:
        with self.lock:
            before = self.states[number]
            state = replace(before, door="closed", session=None)
            self.states[number] = state
            self._send_state(number, state)

        # 문이 닫힐 때 흔들린 무게가 가라앉은 뒤 확인한다
        def check() -> None:
            self._check_after_close(number, before.session, before.opened_weight_g)

        if self.settle_seconds == 0:
            check()
        else:
            self.scheduler.call_later(self.settle_seconds, check)

    def _check_after_close(self, number: int, session: str | None, before_g: float) -> None:
        with self.lock:
            after_g = self.states[number].weight_g
        self.sender.send(f"lockers/{number}/events", {"type": "door_closed", "locker_id": number,
                                                      "authorized": session == "opened",
                                                      "before_g": before_g, "after_g": after_g})
        had, has = self.has_item(before_g), self.has_item(after_g)
        if session == "opened" and not had and not has:
            # 빈 칸을 열었다 닫았는데 그대로 비어 있다: 보관하라고 열어 줬는데 넣지 않았다
            self.sender.alert("ITEM_NOT_DEPOSITED", "warning", f"{number}번 보관함에 물건이 감지되지 않았습니다. "
                              "물건을 넣지 않고 문을 닫았을 수 있습니다.", locker_id=number,
                              before_g=before_g, after_g=after_g)
        elif session == "opened" and had and has:
            # 물건이 있던 칸을 열었다 닫았는데 그대로 있다: 찾으러 와서 꺼내 가지 않았다
            self.sender.alert("ITEM_NOT_TAKEN", "warning", f"{number}번 보관함에 물건이 아직 남아 있습니다. "
                              "꺼내 가지 않고 문을 닫았을 수 있습니다.", locker_id=number,
                              before_g=before_g, after_g=after_g)
        elif session == "forced" and had and not has:
            self.sender.alert("ITEM_MISSING", "alarm", f"강제로 열린 {number}번 보관함에서 물건이 사라졌습니다.",
                              locker_id=number, before_g=before_g, after_g=after_g)

    # 무게 센서 (시뮬레이션)
    def change_item(self, number: int, action: ItemAction, weight_g: float, preset: bool = False) -> dict:
        with self.lock:
            state = self.states[number]
            # 닫힌 칸에는 물건을 넣거나 뺄 수 없다. 시연 준비용 preset만 예외로 둔다
            if state.door == "closed" and not preset:
                raise DeviceError(409, "DOOR_CLOSED", "문이 닫혀 있어 물건을 넣거나 꺼낼 수 없습니다")
            state = replace(state, weight_g=next_weight(state.weight_g, action, weight_g))
            self.states[number] = state
            # 상태 바꾸기, 저장, 보내기를 한 잠금 안에서 해야 동시에 바뀌어도 순서가 뒤집히지 않는다
            self.store.save("lockers", {str(n): s.weight_g for n, s in self.states.items()})
            self._send_state(number, state)
        return self.snapshot()

    # 충격 센서 (시뮬레이션)
    def shock(self, number: int, strength: Literal["weak", "strong"]) -> dict:
        state = self.states[number]
        self.sender.send(f"lockers/{number}/events", {"type": "shock", "locker_id": number, "strength": strength})
        if strength == "strong" and state.door == "closed":
            self.sender.alert("TAMPER", "warning", f"{number}번 보관함에 강한 충격이 감지되었습니다. "
                              "문을 억지로 열려는 시도일 수 있습니다.", locker_id=number)
        return self.snapshot()

    def publish_all(self) -> None:
        # MQTT에 다시 붙을 때 모든 칸의 상태를 다시 보낸다 (보존 메시지)
        with self.lock:
            for number, state in self.states.items():
                self._send_state(number, state)

    def snapshot(self) -> dict:
        return {"auto": self.auto, "skip_auto": sorted(self.skip_auto),
                "lockers": [{"locker_id": n, "door": s.door, "weight_g": s.weight_g,
                             "item": item_text(s.weight_g, self.threshold_g)} for n, s in self.states.items()]}

    def _send_state(self, number: int, state: LockerState) -> None:
        self.sender.send(f"lockers/{number}/state", {"locker_id": number, "door": state.door,
                                                     "weight_g": state.weight_g,
                                                     "item": item_text(state.weight_g, self.threshold_g)},
                         retain=True)


class CaptureMonitor:
    """촬영 상자의 무게 센서를 보고, 촬영이 끝났는데 물건이 남아 있으면 경고한다."""

    def __init__(self, publisher: Publisher, scheduler: Scheduler, store: SensorStore,
                 threshold_g: float, leftover_seconds: float):
        self.weight_g = float(store.load().get("capture", 0.0))
        self.sender = Sender(publisher, "capture")
        self.scheduler = scheduler
        self.store = store
        self.threshold_g = threshold_g
        self.leftover_seconds = leftover_seconds
        self.cancel_check: Callable[[], None] | None = None
        self.registration_id: str | None = None
        self.lock = threading.Lock()

    def captured(self, registration_id: str) -> None:
        # 촬영할 때마다 시간을 다시 잰다. 두 번째 촬영 뒤로 물건을 옮길 시간을 준다
        with self.lock:
            if self.cancel_check:
                self.cancel_check()
            self.registration_id = registration_id
            self.cancel_check = self.scheduler.call_later(self.leftover_seconds, self._check_leftover)
            weight_g = self.weight_g
        self.sender.send("capture/events", {"type": "captured", "registration_id": registration_id,
                                            "weight_g": weight_g, "item": item_text(weight_g, self.threshold_g)})

    def _check_leftover(self) -> None:
        with self.lock:
            self.cancel_check = None
            weight_g, registration_id = self.weight_g, self.registration_id
        if weight_g > self.threshold_g:
            self.sender.alert("CAPTURE_ITEM_LEFT", "warning", "촬영이 끝났는데 촬영 상자에 물건이 남아 있습니다.",
                              registration_id=registration_id, weight_g=weight_g,
                              seconds_after_capture=self.leftover_seconds)

    # 무게 센서 (시뮬레이션). 촬영 상자는 앞이 열려 있어 언제든 넣고 뺄 수 있다
    def change_item(self, action: ItemAction, weight_g: float) -> dict:
        with self.lock:
            self.weight_g = next_weight(self.weight_g, action, weight_g)
            self.store.save("capture", self.weight_g)
            self._send_state()
        return self.snapshot()

    def publish_all(self) -> None:
        with self.lock:
            self._send_state()

    def _send_state(self) -> None:
        self.sender.send("capture/state", {"weight_g": self.weight_g,
                                           "item": item_text(self.weight_g, self.threshold_g)}, retain=True)

    def snapshot(self) -> dict:
        return {"capture": {"weight_g": self.weight_g, "item": item_text(self.weight_g, self.threshold_g)}}
