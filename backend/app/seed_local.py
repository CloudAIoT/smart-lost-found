"""Explicit, repeatable local fixture: python -m app.seed_local."""
from uuid import UUID

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Kiosk, Locker


def main():
    if not get_settings().local_development:
        raise SystemExit("Set LOCAL_DEVELOPMENT=true for local fixtures only.")
    kiosk_id = UUID("00000000-0000-0000-0000-000000000001")
    with SessionLocal() as session:
        if session.get(Kiosk, kiosk_id) is None:
            session.add(Kiosk(id=kiosk_id, name="개발용 키오스크", location_description="테스트 전용, 실제 설치 위치 아님"))
            session.flush()
        for number in (1, 2):
            if session.scalar(select(Locker).where(Locker.kiosk_id == kiosk_id, Locker.locker_number == number)) is None:
                session.add(Locker(kiosk_id=kiosk_id, locker_number=number))
        session.commit()
    print(f"Local kiosk: {kiosk_id}. No production category list is seeded.")


if __name__ == "__main__":
    main()
