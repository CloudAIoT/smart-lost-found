# 카메라 시험: 초점 방식(매번 맞추기 / 연속 자동 초점)과 해상도별 시간·크기 비교
# 실행: 촬영 Pi에서 python3 camera_timing.py
import io
import json
import time

from libcamera import controls
from picamera2 import Picamera2

SIZES = [(4608, 2592), (2304, 1296)]  # 최대 해상도, 절반 해상도
SHOTS = 3                             # 방식마다 연속 촬영 횟수
FOCUSED = 2                           # AfState 값: 0 대기, 1 찾는 중, 2 맞음, 3 실패


def shot_info(metadata):
    # 촬영 순간의 밝기·노출·렌즈 위치·초점 상태
    return {
        "lux": round(metadata.get("Lux", 0)),
        "exposure_ms": round(metadata.get("ExposureTime", 0) / 1000, 1),
        "gain": round(metadata.get("AnalogueGain", 0), 1),
        "lens": round(metadata.get("LensPosition", 0), 2),
        "af_state": metadata.get("AfState"),
    }


def capture(picam2, path=None):
    # JPEG 한 장을 메모리에 찍고 걸린 시간, 크기(KB), 메타데이터를 돌려준다
    buffer = io.BytesIO()
    t0 = time.perf_counter()
    metadata = picam2.capture_file(buffer, format="jpeg") or picam2.capture_metadata()
    seconds = time.perf_counter() - t0
    if path:
        with open(path, "wb") as f:
            f.write(buffer.getvalue())
    return seconds, round(len(buffer.getvalue()) / 1024), metadata


results = []
for width, height in SIZES:
    picam2 = Picamera2()
    picam2.configure(picam2.create_still_configuration(main={"size": (width, height)}))
    picam2.start()
    time.sleep(1)  # 밝기·색 자동 조정이 자리 잡을 시간

    # A. 촬영할 때마다 초점을 한 번 맞추고 찍기
    picam2.set_controls({"AfMode": controls.AfModeEnum.Auto})
    for shot in range(1, SHOTS + 1):
        t0 = time.perf_counter()
        focused = picam2.autofocus_cycle()
        focus_s = time.perf_counter() - t0
        cap_s, kb, md = capture(picam2, f"/tmp/test_A_{width}.jpg" if shot == 1 else None)
        results.append({"method": "A", "size": f"{width}x{height}", "shot": shot,
                        "focus_s": round(focus_s, 2), "focused": focused,
                        "capture_s": round(cap_s, 2), "total_s": round(focus_s + cap_s, 2),
                        "jpeg_kb": kb, **shot_info(md)})

    # B. 연속 자동 초점을 켜 두고 초점이 맞으면 바로 찍기
    picam2.set_controls({"AfMode": controls.AfModeEnum.Continuous})
    t0 = time.perf_counter()
    while picam2.capture_metadata().get("AfState") != FOCUSED and time.perf_counter() - t0 < 6:
        pass
    settle_s = time.perf_counter() - t0
    for shot in range(1, SHOTS + 1):
        cap_s, kb, md = capture(picam2, f"/tmp/test_B_{width}.jpg" if shot == 1 else None)
        results.append({"method": "B", "size": f"{width}x{height}", "shot": shot,
                        "settle_s": round(settle_s, 2) if shot == 1 else None,
                        "capture_s": round(cap_s, 2), "jpeg_kb": kb, **shot_info(md)})
        time.sleep(0.5)

    picam2.stop()
    picam2.close()

for row in results:
    print(json.dumps(row, ensure_ascii=False))
