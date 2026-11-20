"""가짜 하드웨어: 실물 없이 백엔드 연동을 시험하기 위한 장치."""

import io
import logging
import threading
import time
from datetime import datetime
from typing import Literal

from PIL import Image, ImageDraw

from app.hardware import DoorChange, DoorListener

log = logging.getLogger("pi")


class FakeCamera:
    """찍을 때마다 번호와 시각이 적힌 JPEG를 만든다."""

    def __init__(self, delay_seconds: float):
        self.delay_seconds = delay_seconds
        self.shots = 0

    def capture(self) -> bytes:
        time.sleep(self.delay_seconds)  # 실제 촬영 시간 흉내
        self.shots += 1
        image = Image.new("RGB", (640, 480), (40, 90, 140))
        draw = ImageDraw.Draw(image)
        draw.text((20, 20), f"FAKE CAPTURE #{self.shots}", fill="white")
        draw.text((20, 50), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), fill="white")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue()


class FakeLockers:
    """잠금을 풀면 문이 열렸다가 open_seconds 뒤에 저절로 닫힌다.

    명령 없이 열린 문(강제 개방)은 저절로 닫히지 않고 close()로만 닫힌다.
    """

    def __init__(self, count: int, open_seconds: float):
        self.count = count
        self.open_seconds = open_seconds
        self.listener: DoorListener | None = None
        self.opened = {number: 0 for number in range(1, count + 1)}  # 칸별 개방 횟수
        self.is_open = {number: False for number in range(1, count + 1)}
        self.timers: dict[int, threading.Timer] = {}
        self.state_lock = threading.Lock()

    def open(self, number: int) -> None:
        self.opened[number] += 1
        if self._set_open(number):
            self._notify(number, "opened")  # 닫힘 타이머보다 먼저 알린다
        # 다시 열라는 명령이 와도 문이 닫히는 시각만 뒤로 미룬다
        timer = threading.Timer(self.open_seconds, self.close, args=[number])
        timer.daemon = True
        with self.state_lock:
            old = self.timers.pop(number, None)
            self.timers[number] = timer
        if old:
            old.cancel()
        timer.start()

    def force_open(self, number: int) -> None:
        if self._set_open(number):
            self._notify(number, "forced")

    def close(self, number: int) -> None:
        with self.state_lock:
            if not self.is_open[number]:
                return
            self.is_open[number] = False
            timer = self.timers.pop(number, None)
        if timer:
            timer.cancel()
        self._notify(number, "closed")

    def door(self, number: int) -> Literal["open", "closed"]:
        return "open" if self.is_open[number] else "closed"

    def _set_open(self, number: int) -> bool:
        # 닫혀 있던 문이 열리면 True. 이미 열려 있으면 알리지 않는다
        with self.state_lock:
            if self.is_open[number]:
                return False
            self.is_open[number] = True
            return True

    def _notify(self, number: int, change: DoorChange) -> None:
        if self.listener:
            try:
                self.listener(number, change)
            except Exception:
                log.exception("문 변화를 알리지 못했습니다: %d번 %s", number, change)
