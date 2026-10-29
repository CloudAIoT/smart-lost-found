"""장치가 갖춰야 할 기능. 가짜 장치와 실제 장치가 같은 모양을 따른다."""

from typing import Literal, Protocol


class Camera(Protocol):
    def capture(self) -> bytes: ...  # JPEG 바이트


class Lockers(Protocol):
    count: int

    def open(self, number: int) -> None: ...  # 잠금 해제

    def door(self, number: int) -> Literal["open", "closed"]: ...  # 문 센서
