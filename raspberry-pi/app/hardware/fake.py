"""가짜 하드웨어: 실물 없이 백엔드 연동을 시험하기 위한 장치."""

import io
import time
from datetime import datetime
from typing import Literal

from PIL import Image, ImageDraw


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
    """잠금을 풀면 문이 열렸다가 open_seconds 뒤에 닫힌 것처럼 보인다."""

    def __init__(self, count: int, open_seconds: float):
        self.count = count
        self.open_seconds = open_seconds
        self.opened = {number: 0 for number in range(1, count + 1)}  # 칸별 개방 횟수
        self.closes_at = {number: 0.0 for number in range(1, count + 1)}

    def open(self, number: int) -> None:
        self.opened[number] += 1
        self.closes_at[number] = time.monotonic() + self.open_seconds

    def door(self, number: int) -> Literal["open", "closed"]:
        return "open" if time.monotonic() < self.closes_at[number] else "closed"
