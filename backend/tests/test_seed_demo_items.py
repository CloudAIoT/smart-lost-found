"""The demo fixture must land items the ownership search accepts, and put them back after a pickup."""

from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai_ownership_verification.search import find_candidates, matches, read_facts, stored_items
from app.ai_ownership_verification.workflow_schemas import SearchClues, SearchMatch
from app.config import Settings
from app.db import Base
from app.models import Category, FoundItem, ItemImage, Kiosk, Locker, Registration, ReturnCode, StorageAssignment, now
from app import seed_demo_items
from app.seed_demo_items import ITEMS, LOCKER_BASE, ident, main


class FakeStore:
    def __init__(self):
        self.objects = {}

    def put(self, key, jpeg):
        self.objects[key] = jpeg


@pytest.fixture
def seeded(database_engine, monkeypatch):
    Base.metadata.create_all(database_engine)
    settings = Settings(_env_file=None, postgres_db="test", postgres_user="test", postgres_password="test",
                        dev_fake_device=True)
    store = FakeStore()
    monkeypatch.setattr(seed_demo_items, "get_settings", lambda: settings)
    monkeypatch.setattr(seed_demo_items, "SessionLocal", sessionmaker(bind=database_engine))
    monkeypatch.setattr(seed_demo_items, "make_store", lambda _: store)
    monkeypatch.setattr("sys.argv", ["seed_demo_items"])
    main()
    return seed_demo_items, database_engine, store


def test_ten_items_are_stored_with_photos(seeded):
    _, engine, store = seeded
    with Session(engine) as session:
        rows = session.execute(stored_items()).all()
        assert len(rows) == len(ITEMS) == 10
        kiosk = session.get(Kiosk, ident("kiosk"))
        assert (kiosk.latitude, kiosk.longitude) != (None, None)
        numbers = sorted(session.scalars(select(Locker.locker_number).where(Locker.kiosk_id == kiosk.id)))
        assert numbers == [LOCKER_BASE + n for n in range(1, 11)]
        # Every item carries the photo the confirmation step reads back.
        keys = session.scalars(select(ItemImage.object_key)).all()
        assert sorted(keys) == sorted(store.objects)
        assert all(store.objects[key].startswith(b"\xff\xd8") for key in keys)


def test_private_evidence_becomes_proof_and_stays_out_of_search(seeded):
    _, engine, _ = seeded
    with Session(engine) as session:
        for code, expected in (("P01", {"visible_text", "distinctive_features", "color", "material", "brand"}),
                               ("N01", {"visible_text", "distinctive_features"}),
                               ("U01", set())):
            reg = session.get(Registration, ident(f"reg-{code}"))
            category = session.get(Category, reg.primary_category_id)
            facts, proof, registered = read_facts(reg, category)
            assert {evidence.field for evidence in proof} == expected, code
            # A verification answer is only worth asking for while the value is not public.
            for evidence in proof:
                assert not any(matches(evidence.field, evidence.value, value)
                               for values in facts.values() for value in values), (code, evidence.field)
            assert registered["category"], code


def test_similar_items_share_the_public_fields_that_force_narrowing(seeded):
    _, engine, _ = seeded
    with Session(engine) as session:
        facts = {}
        for code in ("P01", "P02", "P03"):
            reg = session.get(Registration, ident(f"reg-{code}"))
            facts[code] = read_facts(reg, session.get(Category, reg.primary_category_id))[0]
        assert facts["P01"]["category"] == facts["P02"]["category"] == facts["P03"]["category"]
        assert facts["P01"]["location"] == facts["P02"]["location"]
        # The umbrellas differ only by a public field, which is what a narrowing question uses.
        umbrellas = []
        for code in ("U01", "U02"):
            reg = session.get(Registration, ident(f"reg-{code}"))
            umbrellas.append(read_facts(reg, session.get(Category, reg.primary_category_id))[0])
        assert umbrellas[0]["material"] != umbrellas[1]["material"]


class CategoryMatchingAI:
    """Stands in for Gemini: shortlists every item whose registered category equals the clue."""

    def similar_items(self, clues, items):
        return [SearchMatch(item=key, matched_fields=["category"]) for key, facts in items.items()
                if clues.get("category") in facts.get("category", [])]


def test_the_search_shortlists_the_demo_items_with_question_material(seeded):
    _, engine, _ = seeded
    with Session(engine) as session:
        status, candidates = find_candidates(session, SearchClues(category="필통"), CategoryMatchingAI())
        assert status == "question"
        assert len(candidates) == 3
        # The first question splits the three on a field where each one holds a different value.
        assert all(candidate.proof for candidate in candidates)
        marks = [next(ev.value for ev in candidate.proof if ev.field == "distinctive_features")
                 for candidate in candidates]
        assert len(set(marks)) == 3
        # P01 keeps every feature private, so its narrowing stays empty by design.
        assert [bool(candidate.narrowing) for candidate in candidates].count(True) == 2

        status, single = find_candidates(session, SearchClues(category="수첩"), CategoryMatchingAI())
        assert (status, len(single)) == ("question", 1)
        assert {evidence.field for evidence in single[0].proof} == {"visible_text", "distinctive_features"}

        assert find_candidates(session, SearchClues(category="바이올린"), CategoryMatchingAI()) == ("no_candidates", [])


def test_rerun_restores_a_picked_up_item_without_duplicating_rows(seeded):
    _, engine, _ = seeded
    item_id = ident("item-P01")
    with Session(engine) as session:
        item = session.get(FoundItem, item_id)
        assignment = session.get(StorageAssignment, ident("assignment-P01"))
        item.status = "retrieved"
        assignment.status = "retrieved"
        assignment.ended_at = now()
        session.add(ReturnCode(found_item_id=item_id, code_hash="x" * 64, expires_at=now()))
        session.commit()
        assert len(session.execute(stored_items()).all()) == 9

    main()

    with Session(engine) as session:
        assert len(session.execute(stored_items()).all()) == 10
        assert session.get(FoundItem, item_id).status == "stored"
        assignment = session.get(StorageAssignment, ident("assignment-P01"))
        assert (assignment.status, assignment.ended_at) == ("stored", None)
        # The code issued before the pickup must not open the restored locker.
        assert session.scalar(select(ReturnCode.status).where(ReturnCode.found_item_id == item_id)) == "revoked"
        for table in (Registration, FoundItem, StorageAssignment, Locker, ItemImage):
            assert session.scalar(select(func.count()).select_from(table)) == 10, table


def test_the_fixture_refuses_to_run_without_the_fake_device_flag(database_engine, monkeypatch):
    Base.metadata.create_all(database_engine)
    settings = Settings(_env_file=None, postgres_db="test", postgres_user="test", postgres_password="test")
    monkeypatch.setattr(seed_demo_items, "get_settings", lambda: settings)
    monkeypatch.setattr(seed_demo_items, "SessionLocal", sessionmaker(bind=database_engine))
    monkeypatch.setattr("sys.argv", ["seed_demo_items"])
    with pytest.raises(SystemExit):
        main()
    with Session(database_engine) as session:
        assert session.scalar(select(func.count()).select_from(Registration)) == 0


def test_an_unknown_kiosk_argument_is_rejected(seeded, monkeypatch):
    _, engine, _ = seeded
    monkeypatch.setattr("sys.argv", ["seed_demo_items", "--kiosk", str(UUID(int=7))])
    with pytest.raises(SystemExit):
        main()
