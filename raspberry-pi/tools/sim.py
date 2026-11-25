"""시연용: Pi의 가상 센서를 움직인다 (결정 기록 0004). 표준 라이브러리만 쓴다.

Pi를 PI_SIMULATION=true로 띄워야 한다. 주소는 --url 또는 환경변수 PI_SIM_URL,
API 키는 --key 또는 환경변수 PI_SIM_API_KEY로 준다.

예 (보관함 Pi):
  python tools/sim.py state
  python tools/sim.py locker 1 put 150      # 열린 1번 칸에 150g 물건 넣기
  python tools/sim.py locker 1 take         # 열린 1번 칸에서 물건 꺼내기
  python tools/sim.py locker 1 close        # 문 닫기 (버튼을 누른 것과 같다)
  python tools/sim.py locker 2 force-open   # 개방 명령 없이 문 열기 = 강제 개방
  python tools/sim.py locker 2 shock        # 강한 충격 (weak를 붙이면 약한 충격)
  python tools/sim.py locker 1 preset 150   # 시연 준비: 닫힌 칸에 물건이 있는 상태로 만들기
  python tools/sim.py locker 1 clear        # 시연 준비: 닫힌 칸을 빈 상태로 되돌리기
  python tools/sim.py locker 1 skip         # 자동 모드에서 다음 개방 한 번은 넣거나 빼지 않기 (보관 안 됨·수령 안 됨 시연)
예 (촬영 Pi):
  python tools/sim.py capture put 200
  python tools/sim.py capture take
"""

import argparse
import json
import os
import urllib.error
import urllib.request


def call(url: str, key: str, method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url.rstrip("/") + path, data=data, method=method,
                                     headers={"X-API-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read()).get("detail", {})
        except ValueError:
            detail = {}
        raise SystemExit(f"실패 {error.code}: {detail.get('code', '')} {detail.get('message', '')}")
    except urllib.error.URLError as error:
        raise SystemExit(f"Pi에 연결하지 못했습니다: {error.reason}")


def locker_request(number: int, action: str, value: str | None) -> tuple[str, dict]:
    if action in ("put", "preset"):
        body = {"action": "put", "weight_g": float(value or 150), "preset": action == "preset"}
        return f"/sim/lockers/{number}/item", body
    if action in ("take", "clear"):
        return f"/sim/lockers/{number}/item", {"action": "take", "preset": action == "clear"}
    if action == "force-open":
        return f"/sim/lockers/{number}/door", {"action": "open"}
    if action == "close":
        return f"/sim/lockers/{number}/door", {"action": "close"}
    if action == "skip":
        return f"/sim/lockers/{number}/skip-auto", {}
    return f"/sim/lockers/{number}/shock", {"strength": value or "strong"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Pi의 가상 센서를 움직인다")
    parser.add_argument("--url", default=os.environ.get("PI_SIM_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--key", default=os.environ.get("PI_SIM_API_KEY"))
    sub = parser.add_subparsers(dest="target", required=True)
    sub.add_parser("state", help="지금 센서 값 보기")
    locker = sub.add_parser("locker", help="보관함 칸 (보관함 Pi)")
    locker.add_argument("number", type=int)
    locker.add_argument("action", choices=["put", "take", "close", "force-open", "shock", "preset", "clear", "skip"])
    locker.add_argument("value", nargs="?", help="put·preset은 무게(g), shock은 weak 또는 strong")
    capture = sub.add_parser("capture", help="촬영 상자 (촬영 Pi)")
    capture.add_argument("action", choices=["put", "take"])
    capture.add_argument("grams", nargs="?", type=float, default=150)
    args = parser.parse_args()
    if not args.key:
        raise SystemExit("API 키가 없습니다. --key 또는 환경변수 PI_SIM_API_KEY로 주세요")

    if args.target == "state":
        result = call(args.url, args.key, "GET", "/sim/state")
    elif args.target == "capture":
        body = {"action": args.action, "weight_g": args.grams} if args.action == "put" else {"action": "take"}
        result = call(args.url, args.key, "POST", "/sim/capture/item", body)
    else:
        path, body = locker_request(args.number, args.action, args.value)
        result = call(args.url, args.key, "POST", path, body)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
