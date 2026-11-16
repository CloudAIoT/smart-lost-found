"""실제 카메라: Camera Module 3를 Picamera2로 다룬다. Picamera2는 촬영 Pi에만 설치돼 있다."""

import io
import logging
import time

from libcamera import controls
from picamera2 import Picamera2

log = logging.getLogger("pi")

# 초점 탐색이 끝난 상태: 맞음, 실패, 취소되어 대기
FOCUS_DONE = (controls.AfStateEnum.Focused, controls.AfStateEnum.Failed, controls.AfStateEnum.Idle)


class PiCamera:
    """카메라를 켜 둔 채로 두고, 찍을 때마다 초점을 한 번 맞춘 뒤 JPEG로 찍는다 (실험 0001의 방식 A)."""

    def __init__(self, size: tuple[int, int], focus_timeout: float):
        self.size = size
        self.focus_timeout = focus_timeout
        self.picam2 = None
        try:
            self._open()  # 서버가 켜질 때 미리 켜 두면 첫 촬영 전에 밝기가 자리 잡는다
        except Exception:
            log.exception("카메라를 켜지 못했습니다. 촬영 요청 때 다시 시도합니다")

    def capture(self) -> bytes:
        try:
            camera = self._open()
            if not self._focus(camera):
                log.warning("초점을 맞추지 못한 채로 찍습니다")
            buffer = io.BytesIO()
            camera.capture_file(buffer, format="jpeg")
            return buffer.getvalue()
        except Exception:
            # 다음 요청에서 새로 연다. 케이블을 다시 꽂아도 서버를 재시작하지 않아도 된다
            self._close()
            raise

    def _open(self):
        if self.picam2 is None:
            camera = Picamera2()
            camera.configure(camera.create_still_configuration(main={"size": self.size}))
            camera.start()
            self.picam2 = camera
        return self.picam2

    def _focus(self, camera) -> bool:
        # autofocus_cycle()과 같은 순서지만 시간 제한을 둔다: 탐색 시작을 본 뒤 끝날 때까지 기다린다
        camera.set_controls({"AfMode": controls.AfModeEnum.Auto, "AfTrigger": controls.AfTriggerEnum.Start})
        deadline = time.monotonic() + self.focus_timeout
        scanning = False
        while time.monotonic() < deadline:
            state = camera.capture_metadata()["AfState"]
            if state == controls.AfStateEnum.Scanning:
                scanning = True
            elif scanning and state in FOCUS_DONE:
                return state == controls.AfStateEnum.Focused
        camera.set_controls({"AfTrigger": controls.AfTriggerEnum.Cancel})
        return False

    def _close(self) -> None:
        if self.picam2 is None:
            return
        try:
            self.picam2.close()
        except Exception:
            log.exception("카메라를 닫지 못했습니다")
        self.picam2 = None
