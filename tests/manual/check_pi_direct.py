"""백엔드를 거치지 않고 Pi 두 대에 통신 규약대로 직접 요청한다.

주소와 키는 backend/.env의 PI_DEVICES에서 읽는다. 확인하는 항목은 docs/device-protocol.md의
정상 흐름과 오류 네 가지다. 촬영한 JPEG은 파일로 저장해 눈으로 볼 수 있게 한다.

실행:
    python tests/manual/check_pi_direct.py
    python tests/manual/check_pi_direct.py --out C:\\temp   # 사진 저장 위치
"""

import argparse
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

os.environ.setdefault("DB_HOST", "127.0.0.1")

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402

TIMEOUT = 30
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'OK' if ok else '실패'}] {name}" + (f" {detail}" if detail else ""))


def expires(seconds: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def request(url: str, key: str, method: str, path: str, body: dict | None = None) -> httpx.Response:
    return httpx.request(method, url.rstrip("/") + path, json=body,
                         headers={"X-API-Key": key}, timeout=TIMEOUT)


def error_code(response: httpx.Response) -> str:
    try:
        return response.json()["detail"]["code"]
    except Exception:
        return response.text[:80]


def check_capture(url: str, key: str, out: Path) -> None:
    print("촬영 Pi")
    health = request(url, key, "GET", "/health")
    record("health", health.status_code == 200 and health.json() == {"status": "ok", "role": "capture"},
           f"{health.status_code} {health.text[:60]}")

    shots = []
    for shot in (1, 2):
        command_id, registration_id = str(uuid4()), str(uuid4())
        body = {"command_id": command_id, "registration_id": registration_id, "expires_at": expires()}
        start = time.monotonic()
        response = request(url, key, "POST", "/capture", body)
        taken = time.monotonic() - start
        jpeg = response.content
        ok = (response.status_code == 200
              and response.headers.get("content-type", "").startswith("image/jpeg")
              and jpeg[:2] == b"\xff\xd8")
        path = out / f"pi-capture-{shot}.jpg"
        if ok:
            path.write_bytes(jpeg)
            shots.append((command_id, registration_id, jpeg))
        record(f"촬영 {shot}번", ok, f"{len(jpeg)}바이트 {taken:.1f}초 → {path}")

    if len(shots) == 2:
        record("두 장이 서로 다른 사진", shots[0][2] != shots[1][2])
        command_id, registration_id, jpeg = shots[0]
        repeat = request(url, key, "POST", "/capture",
                         {"command_id": command_id, "registration_id": registration_id, "expires_at": expires()})
        record("같은 command_id 재요청은 처음 결과를 그대로 돌려준다",
               repeat.status_code == 200 and repeat.content == jpeg, f"{len(repeat.content)}바이트")

    expired = request(url, key, "POST", "/capture",
                      {"command_id": str(uuid4()), "registration_id": str(uuid4()), "expires_at": expires(-60)})
    record("만료된 명령은 거절", expired.status_code == 400 and error_code(expired) == "COMMAND_EXPIRED",
           f"{expired.status_code} {error_code(expired)}")

    wrong = request(url, "wrong-key-for-check", "GET", "/health")
    record("틀린 API 키는 401", wrong.status_code == 401 and error_code(wrong) == "UNAUTHORIZED",
           f"{wrong.status_code} {error_code(wrong)}")


def check_locker(url: str, key: str, number: int, open_wait: float) -> None:
    print("보관함 Pi")
    health = request(url, key, "GET", "/health")
    record("health", health.status_code == 200 and health.json() == {"status": "ok", "role": "locker"},
           f"{health.status_code} {health.text[:60]}")

    before = request(url, key, "GET", f"/lockers/{number}")
    record(f"{number}번 문 상태 확인", before.status_code == 200 and before.json().get("door") == "closed",
           f"{before.status_code} {before.text[:60]}")

    command_id = str(uuid4())
    opened = request(url, key, "POST", f"/lockers/{number}/open",
                     {"command_id": command_id, "expires_at": expires()})
    record(f"{number}번 개방", opened.status_code == 200
           and opened.json() == {"locker_id": number, "result": "opened"}, opened.text[:60])

    during = request(url, key, "GET", f"/lockers/{number}")
    record("개방 직후 문은 open", during.json().get("door") == "open", during.text[:60])

    deadline = time.monotonic() + open_wait
    door = "open"
    while time.monotonic() < deadline:
        door = request(url, key, "GET", f"/lockers/{number}").json().get("door")
        if door == "closed":
            break
        time.sleep(0.5)
    record(f"{open_wait:.0f}초 안에 문이 닫힘", door == "closed", f"door={door}")

    replay = request(url, key, "POST", f"/lockers/{number}/open",
                     {"command_id": command_id, "expires_at": expires()})
    after = request(url, key, "GET", f"/lockers/{number}")
    record("같은 개방 명령이 다시 와도 문은 열리지 않는다",
           replay.status_code == 200 and after.json().get("door") == "closed", after.text[:60])

    missing = request(url, key, "POST", "/lockers/99/open",
                      {"command_id": str(uuid4()), "expires_at": expires()})
    record("없는 보관함 번호는 404", missing.status_code == 404 and error_code(missing) == "LOCKER_NOT_FOUND",
           f"{missing.status_code} {error_code(missing)}")


def check_swapped(capture_url: str, locker_url: str, locker_key: str) -> None:
    """촬영 주소에 보관함 Pi를 꽂으면 역할이 다르다는 것이 health에서 드러난다."""
    print("주소가 바뀐 경우")
    response = request(locker_url, locker_key, "GET", "/health")
    role = response.json().get("role") if response.status_code == 200 else None
    record("보관함 Pi의 health는 capture가 아니다", role == "locker", f"role={role}")


def main() -> None:
    parser = argparse.ArgumentParser(description="실물 Pi에 규약대로 직접 요청한다")
    parser.add_argument("--kiosk-id", default=None, help="생략하면 PI_DEVICES의 첫 키오스크")
    parser.add_argument("--locker", type=int, default=1, help="열어 볼 보관함 번호")
    parser.add_argument("--open-wait", type=float, default=15, help="문이 닫히기를 기다리는 시간(초)")
    parser.add_argument("--out", default=tempfile.gettempdir(), help="촬영 사진을 저장할 폴더")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.pi_devices:
        raise SystemExit("backend/.env에 PI_DEVICES가 없다")
    kiosk_id = args.kiosk_id or next(iter(settings.pi_devices))
    device = settings.pi_devices[kiosk_id]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"키오스크 {kiosk_id}")
    print(f"  촬영 Pi   {device['capture_url']}")
    print(f"  보관함 Pi {device['locker_url']}")

    check_capture(device["capture_url"], device["capture_api_key"], out)
    check_locker(device["locker_url"], device["locker_api_key"], args.locker, args.open_wait)
    check_swapped(device["capture_url"], device["locker_url"], device["locker_api_key"])

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 통과")
    if failed:
        raise SystemExit("실패: " + ", ".join(failed))


if __name__ == "__main__":
    main()
