"""명령 검증과 중복 처리 (통신 규약 2절·5절)."""

import json
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi.responses import Response

from app.errors import DeviceError, error_body


@dataclass(frozen=True)
class Result:
    """명령 하나의 응답. 같은 명령이 다시 오면 이 값을 그대로 돌려준다."""

    status: int
    body: bytes
    media_type: str

    @classmethod
    def json(cls, status: int, data: dict) -> "Result":
        return cls(status, json.dumps(data, ensure_ascii=False).encode(), "application/json")

    @classmethod
    def from_error(cls, error: DeviceError) -> "Result":
        return cls.json(error.status, error_body(error.code, error.message))

    def response(self) -> Response:
        return Response(content=self.body, status_code=self.status, media_type=self.media_type)


class CommandBook:
    """실행한 명령을 기억하고, 장치가 한 번에 한 명령만 실행하게 한다."""

    def __init__(self, memory: int):
        self.memory = memory
        self.done: OrderedDict[str, tuple[str, Result]] = OrderedDict()
        self.book_lock = threading.Lock()
        self.device_lock = threading.Lock()

    def run(self, command_id: str, request_key: str, expires_at: datetime, action: Callable[[], Result]) -> Result:
        # 1) 이미 실행한 명령이면 만료 여부와 상관없이 처음 결과를 돌려준다
        saved = self._saved(command_id, request_key)
        if saved:
            return saved
        # 2) 새 명령은 만료 시각을 확인한다
        if expires_at.tzinfo is None:
            raise DeviceError(400, "INVALID_COMMAND", "expires_at에 시간대가 없습니다")
        if datetime.now(timezone.utc) >= expires_at:
            raise DeviceError(400, "COMMAND_EXPIRED", "만료된 명령입니다")
        # 3) 장치는 한 번에 한 명령만 실행한다
        if not self.device_lock.acquire(blocking=False):
            raise DeviceError(409, "DEVICE_BUSY", "다른 명령을 실행하고 있습니다")
        try:
            # 1)을 확인한 뒤 같은 명령이 먼저 끝났을 수 있어 한 번 더 본다
            saved = self._saved(command_id, request_key)
            if saved:
                return saved
            try:
                result = action()
            except DeviceError as error:
                # 카메라·잠금장치 고장도 '처음 결과'로 기억해 같은 명령을 다시 실행하지 않는다
                result = Result.from_error(error)
            self._remember(command_id, request_key, result)
            return result
        finally:
            self.device_lock.release()

    def _saved(self, command_id: str, request_key: str) -> Result | None:
        with self.book_lock:
            entry = self.done.get(command_id)
        if entry is None:
            return None
        saved_key, result = entry
        if saved_key != request_key:
            raise DeviceError(400, "INVALID_COMMAND", "같은 command_id에 다른 명령이 왔습니다")
        return result

    def _remember(self, command_id: str, request_key: str, result: Result) -> None:
        with self.book_lock:
            self.done[command_id] = (request_key, result)
            while len(self.done) > self.memory:
                self.done.popitem(last=False)
