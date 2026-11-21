"""Repeatable demo fixture for front-end work: python -m app.seed_demo_items

The ownership question screens only have something to show while items are stored. A pickup
ends the assignment, so an item that was opened once leaves the candidate list and the
question flow can no longer be replayed against it. This script writes ten stored items with
hand-written analysis features and puts them back into the stored state whenever it runs
again, which is what lets the same screens be exercised repeatedly.

It is gated on DEV_FAKE_DEVICE: fabricated stored items must never appear in a deployment
that drives real lockers holding real property. Rows are keyed by uuid5, so re-running
updates the same ten items instead of accumulating new ones. Demo lockers are numbered from
901 to stay clear of the lockers a real kiosk operates.
"""

import argparse
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.dev_device import ASSETS
from app.integrations import make_store
from app.models import (Category, DeviceCommand, FoundItem, ItemImage, Kiosk, Locker, Registration,
                        ReturnCode, StorageAssignment, now)

NAMESPACE = "smart-lost-found/demo-items/v1"
LOCKER_BASE = 900
# 서울과학기술대학교 창학관 앞. 좌표가 있어야 프런트의 키오스크 안내를 볼 수 있다.
DEMO_KIOSK = {"name": "개발 확인용 키오스크", "location_description": "개발 확인용, 실제 설치 위치 아님",
              "latitude": 37.6316, "longitude": 127.0776}

# private 근거는 소유 확인 질문의 재료이고, public 값은 검색과 좁히기 질문에 쓰인다. 같은 종류를
# 여러 개 둔 것은 좁히기 질문 화면을 보기 위해서다. 특징을 모두 채우면 후보가 1건일 때도 값이 있는
# 항목마다 질문이 하나씩 생겨 8문항까지 늘어나므로, 항목마다 3~5개만 채운다.
ITEMS = [
    {"code": "N01", "category": ("notebook", "수첩"), "location": "학생회관 입구",
     "facts": {"visible_text": ["QX"], "distinctive_features": ["물방울 배지"]},
     "private": ("visible_text", "distinctive_features")},
    {"code": "B01", "category": ("backpack", "백팩"), "location": "공학관 복도",
     "facts": {"color": ["black"], "brand": "Nike", "distinctive_features": ["작은 고리"]},
     "private": ("distinctive_features",)},
    {"code": "P01", "category": ("pencil_case", "필통"), "location": "중앙도서관 1층",
     "facts": {"color": ["navy"], "material": "fabric", "brand": "Carhartt",
               "visible_text": ["MJ"], "distinctive_features": ["파란 원형 핀"]},
     "private": ("visible_text", "distinctive_features", "color", "material", "brand")},
    {"code": "P02", "category": ("pencil_case", "필통"), "location": "중앙도서관 1층",
     "facts": {"material": "fabric", "visible_text": ["AB"], "distinctive_features": ["빨간 사각 핀"]},
     "private": ("visible_text", "distinctive_features")},
    {"code": "P03", "category": ("pencil_case", "필통"), "location": "중앙도서관 1층",
     "facts": {"material": "fabric", "visible_text": ["AB"], "distinctive_features": ["초록 줄무늬 배지"]},
     "private": ("visible_text", "distinctive_features")},
    {"code": "W01", "category": ("wallet", "지갑"), "location": "학생회관 입구",
     "facts": {"color": ["brown"], "material": "leather",
               "visible_text": ["AB"], "distinctive_features": ["은색 나뭇잎"]},
     "private": ("visible_text", "distinctive_features")},
    {"code": "W02", "category": ("wallet", "지갑"), "location": "학생회관 입구",
     "facts": {"color": ["black"], "material": "leather",
               "visible_text": ["CD"], "distinctive_features": ["갈색 고리"]},
     "private": ("visible_text", "distinctive_features")},
    {"code": "U01", "category": ("umbrella", "우산"), "location": "공학관 입구",
     "facts": {"color": ["black"], "material": "nylon"},
     "private": ()},
    {"code": "U02", "category": ("umbrella", "우산"), "location": "공학관 입구",
     "facts": {"color": ["black"], "material": "vinyl"},
     "private": ()},
    {"code": "T01", "category": ("water_bottle", "물병"), "location": "체육관 입구",
     "facts": {"color": ["blue"], "shape": "원통형", "distinctive_features": ["찌그러진 밑면"]},
     "private": ("distinctive_features",)},
]


def ident(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"{NAMESPACE}/{label}")


def features(entry: dict) -> dict:
    """Build the analysis envelope the ownership search reads back."""
    raw = {"category": entry["category"][0], "description": f"개발 확인용 더미 물품 {entry['category'][1]}",
           **{field: value for field, value in entry["facts"].items() if value}}
    proof = []
    for field in entry["private"]:
        value = entry["facts"][field]
        proof.append({"feature_id": f"{entry['code']}-{field}", "field": field,
                      "value": value[0] if isinstance(value, list) else value, "group": field,
                      "visibility": "private", "verified": True, "distinguishing": True})
    return {"data": raw, "ownership_evidence": proof}


def category_id(session: Session, code: str, name: str) -> UUID:
    existing = session.scalar(select(Category).where(Category.code == code))
    if existing is not None:
        return existing.id
    row = Category(id=ident(f"category-{code}"), code=code, name=name)
    session.add(row)
    session.flush()
    return row.id


def demo_kiosk(session: Session) -> UUID:
    kiosk = session.get(Kiosk, ident("kiosk"))
    if kiosk is None:
        kiosk = Kiosk(id=ident("kiosk"), **DEMO_KIOSK)
        session.add(kiosk)
        session.flush()
    return kiosk.id


def locker_id(session: Session, kiosk: UUID, code: str, number: int) -> UUID:
    existing = session.scalar(select(Locker).where(Locker.kiosk_id == kiosk, Locker.locker_number == number))
    if existing is not None:
        return existing.id
    row = Locker(id=ident(f"locker-{code}"), kiosk_id=kiosk, locker_number=number)
    session.add(row)
    session.flush()
    return row.id


def place(session: Session, kiosk: UUID, entry: dict, number: int, store) -> tuple[UUID, int, str]:
    """Create or restore one stored item, returning the item id, locker number and action taken."""
    code = entry["code"]
    envelope = features(entry)
    image_id = ident(f"image-{code}")
    command_id = str(ident(f"capture-{code}"))
    key = f"demo/{code}.jpg"

    reg = session.get(Registration, ident(f"reg-{code}"))
    action = "restored" if reg else "created"
    if reg is None:
        reg = Registration(id=ident(f"reg-{code}"), kiosk_id=kiosk,
                           primary_category_id=category_id(session, *entry["category"]),
                           secondary_description="개발 확인용 더미 물품", found_location=entry["location"])
        session.add(reg)
    reg.status = "stored"
    reg.analysis_status = "succeeded"
    reg.analysis_features = envelope
    reg.analysis_input_image_ids = [str(image_id)]
    session.flush()

    if session.get(DeviceCommand, command_id) is None:
        session.add(DeviceCommand(command_id=command_id, registration_id=reg.id, action="capture",
                                  shot_order=1, status="succeeded", expires_at=now()))
        session.flush()
    jpeg = (ASSETS / "fake_capture_1.jpg").read_bytes()
    if session.get(ItemImage, image_id) is None:
        session.add(ItemImage(id=image_id, registration_id=reg.id, capture_command_id=command_id,
                              shot_order=1, object_key=key, byte_size=len(jpeg)))
        session.flush()
    # The photo confirmation step reads the object itself, so it is uploaded on every run.
    if store is not None:
        store.put(key, jpeg)

    item = session.get(FoundItem, ident(f"item-{code}"))
    if item is None:
        item = FoundItem(id=ident(f"item-{code}"), registration_id=reg.id)
        session.add(item)
    item.status = "stored"
    session.flush()

    # An outstanding code for a restored item would open a locker nobody is standing at.
    session.execute(update(ReturnCode).where(ReturnCode.found_item_id == item.id,
                    ReturnCode.status.in_(("active", "opening"))).values(status="revoked"))

    assignment = session.get(StorageAssignment, ident(f"assignment-{code}"))
    if assignment is None:
        assignment = StorageAssignment(id=ident(f"assignment-{code}"), found_item_id=item.id,
                                       locker_id=locker_id(session, kiosk, code, number))
        session.add(assignment)
    assignment.status = "stored"
    assignment.ended_at = None
    assignment.deposited_at = assignment.deposited_at or now()
    session.flush()
    return item.id, number, action


def main():
    parser = argparse.ArgumentParser(description="개발 확인용 보관 물품 10건을 만들거나 보관 상태로 되돌린다.")
    parser.add_argument("--kiosk", type=UUID, default=None,
                        help="기존 키오스크에 붙인다. 생략하면 개발 확인용 키오스크를 쓴다.")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.dev_fake_device:
        raise SystemExit("Set DEV_FAKE_DEVICE=true. Demo items must not exist where real lockers are operated.")
    try:
        store = make_store(settings)
    except Exception as error:  # noqa: BLE001  (설정이 없으면 사진 단계만 빠진 채로 계속한다)
        store = None
        print(f"S3 not configured ({error}); photo confirmation will fail for these items.")

    with SessionLocal() as session:
        if args.kiosk is not None:
            if session.get(Kiosk, args.kiosk) is None:
                raise SystemExit(f"Kiosk {args.kiosk} not found.")
            kiosk = args.kiosk
        else:
            kiosk = demo_kiosk(session)
        placed = [place(session, kiosk, entry, LOCKER_BASE + index, store)
                  for index, entry in enumerate(ITEMS, start=1)]
        session.commit()

    print(f"Kiosk: {kiosk}")
    for (item_id, number, action), entry in zip(placed, ITEMS):
        print(f"  {entry['code']} {entry['category'][1]:<4} locker {number} item {item_id} ({action})")
    print(f"{len(placed)} items stored. Re-run after a pickup to put them back.")


if __name__ == "__main__":
    main()
