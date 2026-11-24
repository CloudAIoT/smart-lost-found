"""EC2 API에서 Pi의 MQTT 상태와 새 경고를 확인한다.

브로커에 직접 붙지 않고 백엔드가 MQTT로 받아 저장한 결과를 읽는다. 그래서 이 도구가
상태를 보여 주면 Pi -> EC2 Mosquitto -> 백엔드 구독 -> 조회 API 전 구간이 동작한 것이다.

실행:
    python tests/manual/watch_ec2_mqtt.py --once
    python tests/manual/watch_ec2_mqtt.py
    python tests/manual/watch_ec2_mqtt.py --timeout 120
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = os.environ.get("SLF_BASE_URL", "http://100.73.97.89")


def get_json(base_url: str, path: str, query: dict[str, object] | None = None):
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=10) as response:
            return json.load(response)
    except HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError(f"GET {url} 실패 {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"GET {url} 연결 실패: {exc.reason}") from exc


def state_key(row: dict) -> tuple[str, int | None]:
    return row["device"], row.get("locker_number")


def state_signature(row: dict) -> tuple:
    return tuple(row.get(name) for name in (
        "connection", "door", "weight_g", "item", "reported_at",
    ))


def show_state(row: dict, *, prefix: str = "상태") -> None:
    device = row["device"]
    locker = row.get("locker_number")
    target = f"보관함 {locker}번" if locker is not None else ("촬영 Pi" if device == "capture" else "보관함 Pi")
    values = []
    for key, label in (("connection", "연결"), ("door", "문"), ("item", "물건")):
        if row.get(key) is not None:
            values.append(f"{label}={row[key]}")
    if row.get("weight_g") is not None:
        values.append(f"무게={row['weight_g']}g")
    print(f"{now()} [{prefix}] {target}: {', '.join(values)} (MQTT {row['reported_at']})", flush=True)


def show_alert(row: dict, *, prefix: str = "새 경고") -> None:
    locker = f" / {row['locker_number']}번" if row.get("locker_number") is not None else ""
    print(f"{now()} [{prefix}] {row['severity']} {row['device']}{locker} "
          f"{row['code']}: {row['message']} (MQTT {row['reported_at']})", flush=True)


def now() -> str:
    return datetime.now().astimezone().strftime("%H:%M:%S")


def validate_connected(rows: list[dict]) -> None:
    connections = {
        row["device"]: row.get("connection")
        for row in rows
        if row.get("locker_number") is None
    }
    problems = [f"{role}={connections.get(role, 'missing')}" for role in ("capture", "locker")
                if connections.get(role) != "online"]
    locker_states = [row for row in rows if row["device"] == "locker" and row.get("locker_number") is not None]
    if not locker_states:
        problems.append("locker state=missing")
    if problems:
        raise RuntimeError("MQTT 장치 상태가 준비되지 않았다: " + ", ".join(problems))


def choose_kiosk(base_url: str, requested: str | None) -> str:
    if requested:
        return requested
    kiosks = get_json(base_url, "/api/kiosks", {"limit": 100})
    if not kiosks:
        raise RuntimeError("등록된 키오스크가 없다")
    # 목록의 첫 키오스크가 MQTT 장치가 연결된 키오스크라는 보장은 없다. 상태가 실제로
    # 들어온 키오스크를 찾아 잘못된 기본 선택 때문에 점검이 실패하지 않게 한다.
    for kiosk in kiosks:
        rows = get_json(base_url, f"/api/kiosks/{kiosk['id']}/device-status")
        if rows:
            print(f"키오스크: {kiosk['name']} ({kiosk['id']})")
            return kiosk["id"]
    raise RuntimeError("MQTT 상태를 받은 키오스크가 없다. --kiosk로 확인할 ID를 지정할 수 있다")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="EC2 백엔드가 MQTT로 받은 Pi 상태와 경고를 확인한다",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"EC2 웹/API 주소 (기본: {DEFAULT_BASE_URL})")
    parser.add_argument("--kiosk", help="키오스크 ID. 생략하면 목록의 첫 번째 키오스크")
    parser.add_argument("--once", action="store_true", help="현재 상태만 검사하고 끝낸다")
    parser.add_argument("--interval", type=float, default=0.5, help="API 조회 간격(초, 기본 0.5)")
    parser.add_argument("--timeout", type=float, default=0,
                        help="감시를 끝낼 시간(초). 0이면 Ctrl+C까지 계속")
    args = parser.parse_args()
    if args.interval <= 0 or args.timeout < 0:
        parser.error("--interval은 0보다 커야 하고 --timeout은 0 이상이어야 한다")

    try:
        kiosk_id = choose_kiosk(args.base_url, args.kiosk)
        status_path = f"/api/kiosks/{kiosk_id}/device-status"
        rows = get_json(args.base_url, status_path)
        validate_connected(rows)
        print("Pi -> MQTT -> 백엔드 상태 수신 정상")
        for row in rows:
            show_state(row, prefix="현재 상태")
        if args.once:
            return 0

        alerts_query = {"kiosk_id": kiosk_id, "limit": 200}
        known_alerts = {row["id"] for row in get_json(args.base_url, "/api/alerts", alerts_query)}
        known_states = {state_key(row): state_signature(row) for row in rows}
        print("등록 파이프라인을 진행하세요. MQTT 상태 변경과 새 경고를 표시합니다 (끝내기: Ctrl+C).")
        started = time.monotonic()
        while not args.timeout or time.monotonic() - started < args.timeout:
            time.sleep(args.interval)
            rows = get_json(args.base_url, status_path)
            for row in rows:
                key = state_key(row)
                signature = state_signature(row)
                if known_states.get(key) != signature:
                    show_state(row)
                    known_states[key] = signature

            alerts = get_json(args.base_url, "/api/alerts", alerts_query)
            for alert in reversed(alerts):
                if alert["id"] not in known_alerts:
                    show_alert(alert)
                    known_alerts.add(alert["id"])
        print("감시 시간이 끝났습니다.")
        return 0
    except RuntimeError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n감시를 끝냅니다.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
