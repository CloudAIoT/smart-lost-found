"""로컬 백엔드에 사용자 흐름대로 요청을 보내고 S3와 보관함을 눈으로 확인한다.

local_server.py로 띄운 백엔드에 HTTP로 붙는다. 등록 생성부터 촬영 2장, 미리보기, 분석,
보관함 개방, 보관 완료까지 차례로 부르고 단계마다 결과를 적는다. 확인하는 것은 세 가지다.

- S3에 사진이 올라갔는지: DB에 적힌 객체 키로 S3 head_object를 부르고, 미리보기 서명 URL을
  실제로 내려받아 JPEG인지 본다
- 보관함이 열렸는지: 백엔드 응답만 믿지 않고 보관함 Pi의 GET /lockers/{번호}로 문 상태를 본다
- 보관이 끝났는지: 문이 닫힌 뒤 완료 요청을 보내고 등록 상태가 stored인지 본다

실행:
    python tests/manual/check_deposit_flow.py
    python tests/manual/check_deposit_flow.py --pickup    # 쓴 칸을 수령 흐름으로 비운다
"""

import argparse
import os
import sys
import time
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

os.environ.setdefault("DB_HOST", "127.0.0.1")

import boto3  # noqa: E402
import httpx  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.models import FoundItem, ItemImage  # noqa: E402

TIMEOUT = 60


def call(client: httpx.Client, method: str, path: str, body: dict | None = None) -> dict:
    response = client.request(method, path, json=body)
    if response.status_code >= 400:
        raise SystemExit(f"{method} {path} 실패 {response.status_code}: {response.text}")
    print(f"  {method} {path} {response.status_code}")
    return response.json()


def pi_door(settings, kiosk_id: str, number: int) -> str:
    """보관함 Pi에 직접 물어본다. 백엔드를 거치지 않는다."""
    device = settings.pi_devices[kiosk_id]
    headers = {"X-API-Key": device["locker_api_key"]} if device.get("locker_api_key") else {}
    response = httpx.get(f"{device['locker_url'].rstrip('/')}/lockers/{number}", headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()["door"]


def wait_for_closed(settings, kiosk_id: str, number: int, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pi_door(settings, kiosk_id, number) == "closed":
            print(f"  {number}번 문 closed")
            return
        time.sleep(0.5)
    raise SystemExit(f"{number}번 보관함 문이 {timeout}초 안에 닫히지 않았다")


def check_s3(settings, engine, registration_id: str, client: httpx.Client) -> None:
    s3 = boto3.client("s3", region_name=settings.s3_region)
    with Session(engine) as session:
        rows = list(session.scalars(select(ItemImage)
                                    .where(ItemImage.registration_id == UUID(registration_id))
                                    .order_by(ItemImage.shot_order)))
        images = [(image.shot_order, image.object_key, image.byte_size) for image in rows]
        for image in rows:
            print(f"  DB item_images {image.shot_order}번 id={image.id} {image.content_type} "
                  f"{image.byte_size}바이트 received_at={image.received_at}")
    if len(images) != 2:
        raise SystemExit(f"DB에 사진이 2장이 아니다: {images}")
    for shot, key, size in images:
        head = s3.head_object(Bucket=settings.s3_bucket, Key=key)
        print(f"  S3 {shot}번 {key} {head['ContentLength']}바이트 {head.get('ContentType')}")
        if head["ContentLength"] != size:
            raise SystemExit(f"S3 객체 크기({head['ContentLength']})와 DB 기록({size})이 다르다")
        if head.get("ContentType") != "image/jpeg":
            raise SystemExit(f"S3 객체의 Content-Type이 image/jpeg가 아니다: {head.get('ContentType')}")
        url = call(client, "GET", f"/api/registrations/{registration_id}/shots/{shot}/preview")["url"]
        downloaded = httpx.get(url, timeout=30)
        downloaded.raise_for_status()
        if downloaded.content[:2] != b"\xff\xd8" or len(downloaded.content) != size:
            raise SystemExit(f"{shot}번 미리보기 URL로 받은 내용이 저장된 JPEG와 다르다")
        print(f"  미리보기 {shot}번 내려받기 {len(downloaded.content)}바이트 JPEG")


def pickup(settings, engine, client: httpx.Client, kiosk_id: str, registration_id: str) -> None:
    """같은 칸을 다시 쓰려면 수령까지 끝내 칸을 비운다."""
    if not settings.return_code_ttl_seconds:
        raise SystemExit("수령까지 보려면 backend/.env에 RETURN_CODE_TTL_SECONDS가 필요하다")
    with Session(engine) as session:
        found_item_id = str(session.scalar(select(FoundItem.id)
                                           .where(FoundItem.registration_id == UUID(registration_id))))
    code = call(client, "POST", f"/api/found-items/{found_item_id}/return-code")["code"]
    opened = call(client, "POST", "/api/pickup/open", {"code": code, "kiosk_id": kiosk_id})
    number = opened["locker_number"]
    print(f"  수령 개방 {number}번, Pi 문 상태 {pi_door(settings, kiosk_id, number)}")
    wait_for_closed(settings, kiosk_id, number)
    done = call(client, "POST", f"/api/pickup/{found_item_id}/complete", {"confirmed": True})
    print(f"  수령 완료 item_status={done['item_status']} assignment_status={done['assignment_status']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="로컬 백엔드로 등록 흐름을 끝까지 확인한다")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--kiosk-id", default=None, help="생략하면 GET /api/kiosks의 첫 번째를 쓴다")
    parser.add_argument("--shots-only", action="store_true",
                        help="촬영 2장과 S3·DB 저장까지만 보고 멈춘다. 분석기와 보관함 Pi가 없어도 된다")
    parser.add_argument("--pickup", action="store_true", help="수령까지 진행해 쓴 칸을 비운다")
    args = parser.parse_args()

    settings = get_settings()
    engine = create_engine(settings.database_url)
    with httpx.Client(base_url=args.base_url, timeout=TIMEOUT) as client:
        print("1. 키오스크 확인")
        kiosks = call(client, "GET", "/api/kiosks")
        kiosk_id = args.kiosk_id or kiosks[0]["id"]
        if kiosk_id not in settings.pi_devices:
            raise SystemExit(f"키오스크 {kiosk_id}의 Pi 주소가 backend/.env의 PI_DEVICES에 없다")
        print(f"  키오스크 {kiosk_id}")

        print("2. 등록 시작")
        created = call(client, "POST", "/api/registrations",
                       {"kiosk_id": kiosk_id, "secondary_description": "수동 확인용 물건"})
        registration_id = created["id"]
        print(f"  등록 {registration_id} status={created['status']}")

        print("3. 촬영 2장 (Pi가 찍어 백엔드가 S3에 올린다)")
        for shot in (1, 2):
            view = call(client, "POST", f"/api/registrations/{registration_id}/shots/{shot}")
            print(f"  {shot}번 저장 status={view['status']} images={len(view['images'])}")

        print("4. S3와 DB 확인")
        check_s3(settings, engine, registration_id, client)
        if args.shots_only:
            engine.dispose()
            print("촬영까지 확인했다. 등록", registration_id)
            return

        print("5. 분석")
        analyzed = call(client, "POST", f"/api/registrations/{registration_id}/analysis")
        print(f"  status={analyzed['status']} analysis_status={analyzed['analysis_status']}")

        print("6. 보관함 개방")
        opened = call(client, "POST", f"/api/registrations/{registration_id}/deposit/open")
        number = opened["locker_number"]
        print(f"  {number}번 배정, status={opened['status']}, Pi 문 상태 {pi_door(settings, kiosk_id, number)}")

        print("7. 문이 닫히기를 기다린 뒤 보관 완료")
        wait_for_closed(settings, kiosk_id, number)
        completed = call(client, "POST", f"/api/registrations/{registration_id}/deposit/complete",
                         {"confirmed": True})
        print(f"  status={completed['status']} locker_number={completed['locker_number']}")
        if completed["status"] != "stored":
            raise SystemExit(f"보관이 끝나지 않았다: {completed}")

        if args.pickup:
            print("8. 수령으로 칸 비우기")
            pickup(settings, engine, client, kiosk_id, registration_id)

    engine.dispose()
    print("끝. 등록", registration_id)


if __name__ == "__main__":
    main()
