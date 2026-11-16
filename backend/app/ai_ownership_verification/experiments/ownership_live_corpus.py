"""Reproducible synthetic registrations for the live ownership experiment."""

from collections import Counter
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.orm import Session

from app.models import Category, FoundItem, Kiosk, Locker, Registration, StorageAssignment, now


def ident(label):
    return uuid5(NAMESPACE_URL, f"smart-lost-found/ownership-live-100/{label}")


def seed(session: Session):
    categories = {}
    mapping = {}
    counts = Counter()
    kiosk = Kiosk(id=ident("kiosk"), name="합성 보관 키오스크", location_description="합성 체육관")
    session.add(kiosk)
    session.flush()

    def add(code, group, category, name, *, color="black", material="fabric", brand="Generic",
            mark=None, feature=None, location="학생회관 입구", private=(), invalid=None, omit=()):
        if category not in categories:
            row = Category(id=ident(f"category-{category}"), code=category, name=name)
            session.add(row)
            session.flush()
            categories[category] = row.id
        raw = {
            "category": category, "color": [color] if color else [], "material": material,
            "brand": brand, "visible_text": [mark] if mark else [], "distinctive_features": [feature] if feature else [],
            "description": f"합성 등록 물품 {name}. 색상: {color or '미상'}.",
        }
        for field in omit:
            raw.pop(field, None)
        proof = []
        for field in private:
            value = raw[field]
            proof.append({"feature_id": field, "field": field, "value": value[0] if isinstance(value, list) else value,
                          "group": field, "visibility": "private", "verified": True, "distinguishing": True})
        reg_status = "awaiting_deposit" if invalid == "unplaced" else "stored"
        reg = Registration(id=ident(f"reg-{code}"), kiosk_id=kiosk.id, primary_category_id=categories[category],
                           found_location=location, status=reg_status, analysis_status="succeeded",
                           analysis_features={"data": raw, "ownership_evidence": proof})
        locker = Locker(id=ident(f"locker-{code}"), kiosk_id=kiosk.id, locker_number=len(mapping) + 1)
        session.add_all([reg, locker])
        session.flush()
        item = FoundItem(id=ident(f"item-{code}"), registration_id=reg.id, status=reg_status)
        session.add(item)
        session.flush()
        session.add(StorageAssignment(id=ident(f"assignment-{code}"), found_item_id=item.id, locker_id=locker.id,
                                      status="cancelled" if invalid == "ended" else reg_status,
                                      ended_at=now() if invalid == "ended" else None))
        mapping[str(item.id)] = code
        counts[group] += 1

    add("N01", "normal", "notebook", "수첩", mark="QX", feature="물방울 배지", private=("visible_text", "distinctive_features"))
    normal_sets = [("bag", "가방"), ("keys", "열쇠"), ("phone", "휴대폰"), ("glasses", "안경"), ("card_case", "카드지갑")]
    for n in range(2, 26):
        code, name = normal_sets[(n - 2) % len(normal_sets)]
        add(f"N{n:02}", "normal", code, name, color=["black", "white", "blue", "red"][n % 4],
            material=["fabric", "leather", "metal", "plastic"][n % 4],
            brand=["Nike", "Adidas", "Muji", "Generic"][n % 4],
            location=["학생회관 입구", "중앙도서관 1층", "공학관 복도"][n % 3])

    add("P01", "similar", "pencil_case", "필통", color="navy", material="fabric", brand="Carhartt",
        mark="MJ", feature="파란 원형 핀", location="도서관 1층",
        private=("distinctive_features", "visible_text", "material", "brand", "color"))
    for n in range(2, 6):
        add(f"P{n:02}", "similar", "pencil_case", "필통", mark="AB",
            feature="빨간 사각 핀" if n < 4 else "초록 줄무늬 배지", location="도서관 1층",
            private=("distinctive_features", "visible_text"))
    for n in range(1, 6):
        add(f"U{n:02}", "similar", "umbrella", "우산", material="nylon" if n == 1 else "vinyl", brand=None)
        add(f"B{n:02}", "similar", "bag", "가방", feature="작은 고리", brand="Generic")
        add(f"W{n:02}", "similar", "wallet", "지갑", mark="AB" if n < 3 else "CD",
            feature="은색 나뭇잎" if n < 3 else "갈색 고리", private=("distinctive_features", "visible_text"))
        add(f"T{n:02}", "similar", "water_bottle", "물병", material="plastic", brand=None, color="blue")

    add("M01", "missing", "earbuds", "이어폰", color="white", material="unknown", brand=None, location=None)
    add("M02", "missing", "earbuds", "이어폰", color="black", material="unknown", brand=None, location=None)
    add("M03", "missing", "thermos", "보온병", color=None, material="unknown", brand=None, location=None)
    add("M04", "missing", "bookmark", "책갈피", color="red", material="unknown", brand=None, omit=("brand",))
    missing_sets = [("scarf", "목도리"), ("hat", "모자"), ("jacket", "재킷"), ("mouse", "마우스"),
                    ("charger", "충전기"), ("keyring", "열쇠고리")]
    for n in range(5, 41):
        code, name = missing_sets[(n - 5) % len(missing_sets)]
        add(f"M{n:02}", "missing", code, name, color=None if n % 3 == 0 else ("black" if n % 2 == 0 else "blue"),
            material="unknown" if n % 2 else "fabric", brand=None, location=None if n % 3 else "공학관 입구",
            omit=("visible_text",) if n % 7 == 0 else ())

    for n in range(1, 11):
        add(f"E{n:02}", "excluded", "helmet", "헬멧", color="black", material="plastic",
            invalid="unplaced" if n <= 5 else "ended")
    session.commit()
    assert counts == {"normal": 25, "similar": 25, "missing": 40, "excluded": 10}
    return mapping, dict(counts)
