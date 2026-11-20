"""보관함 Pi의 실제 장치: 칸마다 LED 하나와 버튼 하나 (결정 기록 0002).

잠금장치와 문 센서는 향후 과제라서 시제품에서는 이것으로 보여 준다.
칸이 열리면 그 칸의 LED가 깜빡이고, 물건을 넣고 버튼을 누르면 문이 닫힌 것으로 본다.
명령 없이 문이 열리면(시뮬레이션의 강제 개방) LED를 빠르게 깜빡여 경보를 보여 준다.
"""

import logging
import threading
from typing import Literal

from gpiozero import LED, Button

from app.hardware import DoorChange, DoorListener

log = logging.getLogger("pi")


class GpioLockers:
    def __init__(self, led_pins: list[int], button_pins: list[int]):
        if len(led_pins) != len(button_pins):
            raise ValueError("LED 핀과 버튼 핀의 개수가 같아야 합니다")
        self.count = len(led_pins)
        self.listener: DoorListener | None = None
        self.leds = [LED(pin) for pin in led_pins]
        # 버튼의 한쪽은 GPIO, 다른 쪽은 GND에 연결한다. Pi 안의 풀업 저항을 쓴다
        self.buttons = [Button(pin) for pin in button_pins]
        self.is_open = [False] * self.count
        self.state_lock = threading.Lock()
        for number, button in enumerate(self.buttons, start=1):
            button.when_pressed = lambda number=number: self.close(number)

    def open(self, number: int) -> None:
        changed = self._set_open(number)
        self.leds[number - 1].blink(on_time=0.3, off_time=0.3)
        if changed:
            self._notify(number, "opened")

    def force_open(self, number: int) -> None:
        if self._set_open(number):
            self.leds[number - 1].blink(on_time=0.1, off_time=0.1)
            self._notify(number, "forced")

    def close(self, number: int) -> None:
        # 버튼을 누르거나 시뮬레이션에서 문을 닫으면 불린다. 열려 있던 칸만 닫는다
        with self.state_lock:
            if not self.is_open[number - 1]:
                return
            self.is_open[number - 1] = False
        self.leds[number - 1].off()
        log.info("%d번 칸 문 닫힘", number)
        self._notify(number, "closed")

    def door(self, number: int) -> Literal["open", "closed"]:
        with self.state_lock:
            return "open" if self.is_open[number - 1] else "closed"

    def _set_open(self, number: int) -> bool:
        # 닫혀 있던 문이 열리면 True. 이미 열려 있으면 알리지 않는다
        with self.state_lock:
            if self.is_open[number - 1]:
                return False
            self.is_open[number - 1] = True
            return True

    def _notify(self, number: int, change: DoorChange) -> None:
        if self.listener:
            try:
                self.listener(number, change)
            except Exception:
                log.exception("문 변화를 알리지 못했습니다: %d번 %s", number, change)
