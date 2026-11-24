"""장치가 갖춰야 할 기능. 가짜 장치와 실제 장치가 같은 모양을 따른다."""

from collections.abc import Callable
from typing import Literal, Protocol

# 문이 바뀌면 부르는 함수: (칸 번호, 바뀐 내용)
# "opened"는 개방 명령으로 열림, "forced"는 명령 없이 열림, "closed"는 닫힘
DoorChange = Literal["opened", "forced", "closed"]
DoorListener = Callable[[int, DoorChange], None]


class Camera(Protocol):
    def capture(self) -> bytes: ...  # JPEG 바이트


class Lockers(Protocol):
    count: int
    listener: DoorListener | None  # 문이 열리고 닫힐 때 알릴 곳

    def open(self, number: int) -> None: ...  # 개방 명령으로 잠금 해제

    def force_open(self, number: int) -> None: ...  # 명령 없이 문이 열림 (시뮬레이션의 강제 개방)

    def close(self, number: int) -> None: ...  # 문이 닫힘 (버튼, 가짜 장치의 타이머, 시뮬레이션)

    def door(self, number: int) -> Literal["open", "closed"]: ...  # 문 센서
