import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_ownership_verification.api import get_ownership_ai
from app.ai_ownership_verification.search import normalized
from app.ai_ownership_verification.workflow import get_ownership_result
from app.ai_ownership_verification.workflow_ai import GeminiWorkflowAI
from app.ai_ownership_verification.workflow_schemas import SearchClues, SearchMatch
from app.api import get_store
from app.config import GeminiSettings, Settings, get_settings
from app.db import Base, get_session
from app.dev_device import ASSETS
from app.errors import IntegrationError
from app.main import app
from app.models import (DeviceCommand, FoundItem, ItemImage, Kiosk, Locker, OwnershipAttempt, Registration,
                        ReturnCode, StorageAssignment, now)


class FakeAI:
    def __init__(self):
        self.clues = SearchClues(category="필통", color="검은색", location="도서관 근처")
        self.broken_options = False
        self.calls = []

    def extract(self, description):
        self.calls.append(("extract", description))
        return self.clues

    def similar_items(self, clues, items):
        shortlist = []
        for item, facts in items.items():
            fields = [field for field in clues if field in facts]
            if fields and all({normalized(field, value) for value in facts[field]} ==
                              {normalized(field, clues[field])} for field in fields):
                shortlist.append(SearchMatch(item=item, matched_fields=fields))
        return shortlist

    def distractors(self, field, known, count):
        self.calls.append(("distractors", field, known))
        if self.broken_options:
            return known
        return {
            "distinctive_features": ["노란 리본", "작은 은색 구슬", "하얀 줄무늬 스티커", "주황색 고리"],
            "visible_text": ["ZX", "TS", "B-9", "VV"],
            "material": ["종이", "금속", "고무", "유리"],
            "shape": ["삼각기둥형", "반원형", "육각기둥형", "타원형"],
            "structure": ["앞 주머니", "손잡이 고리", "뒷면 똑딱이", "가운데 칸막이"],
            "brand": ["Dickies", "Eastpak", "Muji", "Nike"],
            "color": ["red", "yellow", "white", "green"],
        }[field]


def add_item(session, kiosk, *, number, decoration, mark, verified=True, group="mark", stored=True, location="도서관",
             item_id=None):
    values = {
        "category": "pencil_case", "color": ["black"],
        "visible_text": [mark], "distinctive_features": [decoration], "description": "검은색 필통",
    }
    evidence = [
        {"feature_id": "attachment", "field": "distinctive_features", "value": decoration,
         "group": "decoration", "visibility": "private", "verified": verified, "distinguishing": True},
        {"feature_id": "mark", "field": "visible_text", "value": mark,
         "group": group, "visibility": "private", "verified": verified, "distinguishing": True},
    ]
    reg = Registration(kiosk_id=kiosk.id, status="stored", analysis_status="succeeded",
                       found_location=location, analysis_features={"data": values, "ownership_evidence": evidence})
    locker = Locker(kiosk_id=kiosk.id, locker_number=number)
    session.add_all([reg, locker])
    session.flush()
    item = FoundItem(id=item_id or uuid4(), registration_id=reg.id, status="stored" if stored else "awaiting_deposit")
    session.add(item)
    session.flush()
    session.add(StorageAssignment(found_item_id=item.id, locker_id=locker.id, status="stored"))
    return item.id


def seed_matching_example(session):
    # The 13 rows cover equivalent colors, missing fields, and explicit conflicts.
    rows = [
        ("필통", ["남색"], "도서관", "직사각형", ["옆 지퍼"], []),
        ("필통", ["navy"], "도서관", "원통형", [], []),
        ("필통", ["남색"], None, "직사각형", ["옆 지퍼"], []),
        ("필통", None, "도서관", "원통형", [], ["MJ"]),
        (None, ["남색"], "도서관", "직사각형", ["옆 지퍼"], []),
        ("필통", None, None, "원통형", [], []),
        (None, ["남색"], None, "직사각형", [], []),
        (None, None, None, None, ["옆 지퍼"], ["AB"]),
        ("필통", ["남색"], "학생회관", "직사각형", [], []),
        ("필통", ["검정색"], "도서관", "원통형", [], []),
        ("지갑", ["남색"], "도서관", "직사각형", ["옆 지퍼"], []),
        ("필통", ["남색"], "도서관 앞 벤치", "직사각형", [], []),
        ("필통", ["남색", "흰색"], "도서관", "직사각형", ["흰색 지퍼"], []),
    ]
    kiosk = Kiosk(name="후보 비교 테스트", location_description="캠퍼스")
    session.add(kiosk)
    session.flush()
    for number, (category, color, location, shape, structure, text) in enumerate(rows, start=1):
        item_id = add_item(session, kiosk, number=number, item_id=UUID(int=number),
                           decoration="테스트용", mark="MJ", location=location)
        reg = session.get(Registration, session.get(FoundItem, item_id).registration_id)
        reg.analysis_features = {
            "data": {"category": category, "color": color, "shape": shape, "structure": structure, "visible_text": text},
            "ownership_evidence": reg.analysis_features["ownership_evidence"][1:] if number == 4 else [],
        }


@pytest.fixture
def ownership(database_engine):
    Base.metadata.create_all(database_engine)
    photos = {}
    with Session(database_engine) as session:
        kiosk = Kiosk(name="보관 키오스크", location_description="체육관")
        session.add(kiosk)
        session.flush()
        first = add_item(session, kiosk, number=1, decoration="파란 원형 핀", mark="MJ")
        second = add_item(session, kiosk, number=2, decoration="빨간 사각 핀", mark="AB")
        add_item(session, kiosk, number=3, decoration="등록 중", mark="ST", stored=False)
        for number, item_id in enumerate((first, second), start=1):
            item = session.get(FoundItem, item_id)
            capture = DeviceCommand(registration_id=item.registration_id, action="capture", shot_order=1,
                                    status="succeeded", expires_at=now())
            session.add(capture)
            session.flush()
            key = f"test/{item_id}.jpg"
            photos[key] = (ASSETS / f"fake_capture_{number}.jpg").read_bytes()
            session.add(ItemImage(registration_id=item.registration_id, capture_command_id=capture.command_id,
                                  shot_order=1, object_key=key, byte_size=len(photos[key]), received_at=now()))
        session.commit()

    ai = FakeAI()
    settings = Settings(_env_file=None, postgres_db="test", postgres_user="test",
                        postgres_password="test", local_development=True, return_code_ttl_seconds=300)

    def sessions():
        with Session(database_engine) as session:
            yield session

    app.dependency_overrides.update({
        get_session: sessions, get_settings: lambda: settings, get_ownership_ai: lambda: ai,
        get_store: lambda: SimpleNamespace(read=photos.__getitem__),
    })
    try:
        with TestClient(app) as client:
            yield SimpleNamespace(client=client, ai=ai, first=first, second=second,
                                  engine=database_engine, settings=settings)
    finally:
        app.dependency_overrides.clear()


BASE = "/api/ownership/attempts"


def begin(env, description="도서관 근처에서 검은색 필통을 잃어버렸어요"):
    response = env.client.post(BASE, json={"description": description})
    assert response.status_code == 200, response.text
    return response.json()


def submit(env, view, label):
    question = view["question"]
    option = next(option for option in question["options"] if option["label"] == label)
    return env.client.post(f'{BASE}/{view["attempt_id"]}/answers',
                           json={"question_id": question["id"], "option_id": option["id"]})


def result_for(env, view):
    with Session(env.engine) as session:
        return get_ownership_result(session, UUID(view["attempt_id"]))


def photo_url(view):
    return f'{BASE}/{view["attempt_id"]}/photo'


def confirm_photo(env, view):
    assert view["status"] == "photo_confirmation" and view["decision"] is None
    photo = env.client.get(photo_url(view))
    assert photo.status_code == 200
    response = env.client.post(photo_url(view) + "-confirmation",
                               json={"confirmed": True, "photo_token": photo.headers["X-Ownership-Photo-Token"]})
    assert response.status_code == 200, response.text
    return response.json()


def test_missing_clues_keep_candidates_and_shape_question_partitions_them(ownership):
    ownership.ai.clues = SearchClues(category="필통", color="남색", location="도서관")
    with Session(ownership.engine) as session:
        for item_id in (ownership.first, ownership.second):
            session.get(FoundItem, item_id).status = "awaiting_deposit"
        seed_matching_example(session)
        session.commit()

    def candidate_numbers(view):
        with Session(ownership.engine) as session:
            row = session.get(OwnershipAttempt, UUID(view["attempt_id"]))
            return {UUID(candidate["item_id"]).int for candidate in row.state["candidates"]}

    view = begin(ownership, "도서관에서 남색 필통을 잃어버렸어요.")
    assert candidate_numbers(view) == set(range(1, 8))
    assert "형태" in view["question"]["text"]
    labels = {option["label"] for option in view["question"]["options"]}
    assert len(labels) == 5 and {"직사각형", "원통형"} <= labels
    assert candidate_numbers(submit(ownership, view, "직사각형").json()) == {1, 3, 5, 7}

    view = begin(ownership, "도서관에서 남색 필통을 잃어버렸어요.")
    view = submit(ownership, view, "원통형").json()
    assert candidate_numbers(view) == {2, 4, 6}
    assert "문구" in view["question"]["text"]
    assert candidate_numbers(submit(ownership, view, "MJ").json()) == {2, 4, 6}


def test_single_candidate_is_asked_about_remaining_shape(ownership):
    with Session(ownership.engine) as session:
        item = session.get(FoundItem, ownership.first)
        reg = session.get(Registration, item.registration_id)
        reg.analysis_features = {**reg.analysis_features["data"], "shape": "직사각형"}
        session.get(FoundItem, ownership.second).status = "awaiting_deposit"
        session.commit()
    view = begin(ownership)
    assert view["initial_candidate_count"] == 1
    assert "형태" in view["question"]["text"]
    pending = submit(ownership, view, "직사각형").json()
    assert pending["status"] == "photo_confirmation"
    assert result_for(ownership, pending) is None


def test_hands_off_only_after_photo_confirmation(ownership):
    first = begin(ownership)
    assert first["initial_candidate_count"] == 2
    assert result_for(ownership, first) is None
    assert ownership.client.get(f'{BASE}/{first["attempt_id"]}').json() == first
    assert set(first["question"]) == {"id", "text", "options"}
    assert "파란 원형 핀" not in first["question"]["text"]
    assert all(set(option) == {"id", "label"} for option in first["question"]["options"])
    assert len({option["label"] for option in first["question"]["options"]}) == 5
    assert {"파란 원형 핀", "빨간 사각 핀"} <= {option["label"] for option in first["question"]["options"]}
    assert str(ownership.first) not in str(first)

    second = submit(ownership, first, "파란 원형 핀").json()
    assert second["status"] == "question"
    assert second["initial_candidate_count"] == 2
    assert result_for(ownership, second) is None
    assert submit(ownership, first, "파란 원형 핀").status_code == 409

    pending = submit(ownership, second, "MJ").json()
    assert result_for(ownership, pending) is None
    assert pending["found_item_id"] is None and pending["question"] is None
    last = confirm_photo(ownership, pending)["attempt"]
    assert last["status"] == "completed" and last["decision"] == "supported"
    assert last["question"] is None
    result = result_for(ownership, last)
    assert result.item_id == ownership.first
    assert result.failure_reason is None
    assert result.matched_evidence_groups == 2
    assert result.answered_questions == 2
    assert result.policy_version == "development_v4"


def test_verified_attempt_issues_one_otp_for_the_matched_item(ownership):
    ownership.settings.local_development = False
    ownership.settings.business_api_enabled = True
    first = begin(ownership)
    path = f"/api/found-items/{ownership.first}/return-code"
    body = {"attempt_id": first["attempt_id"]}
    assert ownership.client.post(path).status_code == 422
    assert ownership.client.post(path, json=body).status_code == 403
    second = submit(ownership, first, "파란 원형 핀").json()
    pending = submit(ownership, second, "MJ").json()
    assert pending["status"] == "photo_confirmation"
    assert ownership.client.post(path, json=body).status_code == 403
    url = photo_url(pending)
    assert ownership.client.post(url + "-confirmation", json={"confirmed": True}).json()["detail"]["code"] == \
        "OWNERSHIP_PHOTO_LOAD_REQUIRED"
    photo = ownership.client.get(url)
    assert photo.status_code == 200 and photo.headers["content-type"] == "image/jpeg"
    assert photo.headers["cache-control"] == "no-store"
    assert photo.content == (ASSETS / "fake_capture_1.jpg").read_bytes()
    confirmation = {"confirmed": True, "photo_token": photo.headers["X-Ownership-Photo-Token"]}
    issued = ownership.client.post(url + "-confirmation", json=confirmation)
    assert issued.status_code == 200
    assert issued.headers["cache-control"] == "no-store"
    assert issued.json()["attempt"]["found_item_id"] == str(ownership.first)
    code = issued.json()["return_code"]
    assert len(code["code"]) == 8 and code["kiosk_name"] == "보관 키오스크"
    assert UUID(code["kiosk_id"])
    assert ownership.client.post(f"/api/found-items/{ownership.second}/return-code", json=body).status_code == 403
    assert ownership.client.post(url + "-confirmation", json=confirmation).json()["detail"]["code"] == \
        "OWNERSHIP_RETURN_CODE_ALREADY_ISSUED"
    assert ownership.client.post(path, json=body).json()["detail"]["code"] == "OWNERSHIP_RETURN_CODE_ALREADY_ISSUED"


@pytest.mark.parametrize("needs_color_question", [True, False])
def test_automatic_photo_features_allow_photo_confirmation_and_otp(ownership, needs_color_question):
    color = None if needs_color_question else "남색"
    ownership.ai.clues = SearchClues(color=color, shape="직사각형", surface_texture="직조 표면", structure="측면 지퍼")
    with Session(ownership.engine) as session:
        item = session.get(FoundItem, ownership.first)
        registration = session.get(Registration, item.registration_id)
        registration.analysis_features = {
            "category": "pencil_case", "color": ["navy"], "shape": "직사각형",
            "surface_texture": "직조 표면", "brand": None, "visible_text": ["MJ"],
            "structure": ["측면 지퍼"], "distinctive_features": ["파란 원형 핀"],
        }
        session.commit()
    description = "직조 표면에 측면 지퍼가 달린 직사각형 필통을 잃어버렸어요."
    pending = begin(ownership, description if color is None else f"{color} {description}")
    assert pending["initial_candidate_count"] == 1
    if needs_color_question:
        assert "색상" in pending["question"]["text"]
        pending = submit(ownership, pending, "navy").json()
    assert pending["status"] == "photo_confirmation"
    assert pending["found_item_id"] is None
    assert result_for(ownership, pending) is None
    path = f"/api/found-items/{ownership.first}/return-code"
    assert ownership.client.post(path, json={"attempt_id": pending["attempt_id"]}).status_code == 403
    confirmed = confirm_photo(ownership, pending)
    assert confirmed["attempt"]["decision"] == "supported"
    assert len(confirmed["return_code"]["code"]) == 8
    assert result_for(ownership, confirmed["attempt"]).matched_evidence_groups == 0


def test_no_match_and_missing_clues_do_not_create_a_judgment(ownership):
    ownership.ai.clues = SearchClues(category="우산", color="검은색")
    missing = begin(ownership)
    assert missing["status"] == "no_candidates" and missing["attempt_id"] is None
    ownership.ai.clues = SearchClues()
    assert begin(ownership)["status"] == "needs_clues"


def test_semantic_search_rejects_model_ids_outside_the_supplied_batch(ownership):
    def invalid_match(clues, items):
        assert len(items) == 2
        assert all("description" not in facts for facts in items.values())
        return [SearchMatch(item=max(items) + 1, matched_fields=["category"])]

    ownership.ai.similar_items = invalid_match
    response = ownership.client.post(BASE, json={"description": "필통을 잃어버렸어요"})
    assert response.status_code == 502
    assert response.json() == {"detail": {"code": "OWNERSHIP_AI_INVALID_MATCHES"}}


@pytest.mark.parametrize(("last_answer", "matches"), [("navy", 5), ("red", 4)])
def test_continues_past_two_matches_and_four_questions_until_exhausted(ownership, last_answer, matches):
    ownership.ai.clues = SearchClues(category="필통")
    with Session(ownership.engine) as session:
        reg = session.get(Registration, session.get(FoundItem, ownership.first).registration_id)
        raw = reg.analysis_features
        reg.analysis_features = {
            "data": {**raw["data"], "color": ["navy"], "material": "fabric", "brand": "Carhartt"},
            "ownership_evidence": raw["ownership_evidence"] + [
                {"feature_id": field, "field": field, "value": value, "group": field,
                 "visibility": "private", "verified": True, "distinguishing": True}
                for field, value in [("material", "fabric"), ("brand", "Carhartt"), ("color", "navy")]
            ],
        }
        session.commit()

    view = begin(ownership)
    asked = {view["question"]["id"]}
    for label in ("파란 원형 핀", "MJ", "fabric", "Carhartt"):
        response = submit(ownership, view, label)
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["status"] == "question" and view["decision"] is None
        assert view["question"]["id"] not in asked
        asked.add(view["question"]["id"])
        assert result_for(ownership, view) is None
    assert len(asked) == 5

    pending = submit(ownership, view, last_answer).json()
    final = confirm_photo(ownership, pending)["attempt"]
    assert final["status"] == "completed" and final["decision"] == "supported"
    result = result_for(ownership, final)
    assert result.answered_questions == 5
    assert result.item_id == ownership.first and result.matched_evidence_groups == matches


def test_loss_location_is_compared_to_found_location_not_kiosk(ownership):
    ownership.ai.clues = SearchClues(location="체육관")
    assert begin(ownership)["status"] == "no_candidates"
    ownership.ai.clues = SearchClues(location="도서관 근처")
    assert begin(ownership)["initial_candidate_count"] == 2


def test_two_independent_wrong_answers_eliminate_candidate(ownership):
    view = begin(ownership)
    next_question = submit(ownership, view, "노란 리본").json()
    assert next_question["status"] == "question"
    with Session(ownership.engine) as session:
        attempt = session.get(OwnershipAttempt, UUID(view["attempt_id"]))
        assert len(attempt.state["candidates"]) == 2
    last = submit(ownership, next_question, "ZX").json()
    assert last["status"] == "completed" and last["decision"] == "not_confirmed"
    assert "failure_reason" not in last
    assert result_for(ownership, last).failure_reason == "wrong_evidence_limit"


def test_unverified_raw_features_are_not_proof(ownership):
    with Session(ownership.engine) as session:
        first = session.get(Registration, session.get(FoundItem, ownership.first).registration_id)
        second = session.get(Registration, session.get(FoundItem, ownership.second).registration_id)
        first.analysis_features = {**first.analysis_features, "ownership_evidence": [
            {**ev, "verified": False} for ev in first.analysis_features["ownership_evidence"]]}
        second.analysis_features = second.analysis_features["data"]
        session.commit()
    view = begin(ownership)
    assert view["decision"] == "not_confirmed"
    assert result_for(ownership, view).failure_reason == "multiple_candidates"
    assert view["question"] is None
    assert not any(call[0] == "distractors" for call in ownership.ai.calls)


def test_public_difference_narrows_candidates_before_photo_confirmation(ownership):
    with Session(ownership.engine) as session:
        for item_id, material in [(ownership.first, "fabric"), (ownership.second, "plastic")]:
            reg = session.get(Registration, session.get(FoundItem, item_id).registration_id)
            reg.analysis_features = {**reg.analysis_features["data"], "material": material}
        session.commit()
    view = begin(ownership)
    assert "재질" in view["question"]["text"]
    assert {option["label"] for option in view["question"]["options"]} == {"fabric", "plastic", "종이", "금속", "고무"}
    wrong = submit(ownership, view, "종이").json()
    assert wrong["decision"] == "not_confirmed" and result_for(ownership, wrong).item_id is None
    assert result_for(ownership, wrong).failure_reason == "candidates_eliminated"
    view = begin(ownership)
    pending = submit(ownership, view, "fabric").json()
    finished = confirm_photo(ownership, pending)["attempt"]
    result = result_for(ownership, finished)
    assert result.item_id == ownership.first
    assert result.decision == "supported" and result.matched_evidence_groups == 0
    assert result.failure_reason is None


def test_mentioned_feature_is_not_asked_again(ownership):
    ownership.ai.clues = SearchClues(category="필통", color="검은색", distinctive_features="파란 원형 핀")
    view = begin(ownership)
    assert view["initial_candidate_count"] == 1
    assert "문구" in view["question"]["text"]
    assert confirm_photo(ownership, submit(ownership, view, "MJ").json())["attempt"]["decision"] == "supported"


def test_only_eligible_private_feature_is_enough_when_it_all_matches(ownership):
    ownership.ai.clues = SearchClues(category="필통", distinctive_features="파란 원형 핀")
    with Session(ownership.engine) as session:
        session.get(FoundItem, ownership.second).status = "awaiting_deposit"
        session.commit()
    view = begin(ownership)
    assert "문구" in view["question"]["text"]
    view = submit(ownership, view, "MJ").json()
    assert "색상" in view["question"]["text"]
    finished = confirm_photo(ownership, submit(ownership, view, "black").json())["attempt"]
    assert finished["decision"] == "supported"
    assert result_for(ownership, finished).matched_evidence_groups == 1


def test_same_group_does_not_count_as_two_independent_wrong_answers(ownership):
    with Session(ownership.engine) as session:
        reg = session.get(Registration, session.get(FoundItem, ownership.first).registration_id)
        reg.analysis_features = {**reg.analysis_features, "ownership_evidence": [
            {**ev, "group": "same_mark"} for ev in reg.analysis_features["ownership_evidence"]]}
        session.commit()
    first = begin(ownership)
    second = submit(ownership, first, "노란 리본").json()
    last = confirm_photo(ownership, submit(ownership, second, "ZX").json())["attempt"]
    assert last["decision"] == "supported"
    result = result_for(ownership, last)
    assert result.item_id == ownership.first and result.matched_evidence_groups == 0


def test_matching_all_answers_for_two_candidates_does_not_authorize_return(ownership):
    with Session(ownership.engine) as session:
        first = session.get(Registration, session.get(FoundItem, ownership.first).registration_id)
        second = session.get(Registration, session.get(FoundItem, ownership.second).registration_id)
        second.analysis_features = first.analysis_features
        session.commit()

    view = submit(ownership, begin(ownership), "파란 원형 핀").json()
    finished = submit(ownership, view, "MJ").json()
    assert finished["status"] == "completed" and finished["decision"] == "not_confirmed"
    assert finished["found_item_id"] is None
    assert result_for(ownership, finished).failure_reason == "multiple_candidates"
    for item_id in (ownership.first, ownership.second):
        response = ownership.client.post(f"/api/found-items/{item_id}/return-code",
                                         json={"attempt_id": finished["attempt_id"]})
        assert response.status_code == 403


def test_legacy_completed_hold_is_returned_as_not_confirmed(ownership):
    view = begin(ownership)
    with Session(ownership.engine) as session:
        attempt = session.get(OwnershipAttempt, UUID(view["attempt_id"]))
        attempt.state = {**attempt.state, "policy_version": "development_v2",
                         "current": None, "decision": "insufficient_evidence"}
        session.commit()

    response = ownership.client.get(f'{BASE}/{view["attempt_id"]}')
    assert response.status_code == 200
    finished = response.json()
    assert finished["status"] == "completed" and finished["decision"] == "not_confirmed"
    result = result_for(ownership, finished)
    assert result.decision == "not_confirmed" and result.failure_reason is None


def test_provider_failure_during_next_question_does_not_consume_answer(ownership):
    first = begin(ownership)
    ownership.ai.broken_options = True
    assert submit(ownership, first, "파란 원형 핀").json() == {"detail": {"code": "OWNERSHIP_AI_INVALID_OPTIONS"}}
    assert ownership.client.get(f'{BASE}/{first["attempt_id"]}').json() == first
    ownership.ai.broken_options = False
    assert submit(ownership, first, "파란 원형 핀").json()["status"] == "question"


def test_invalid_option_expired_attempt_and_production_gate(ownership):
    view = begin(ownership)
    url = f'{BASE}/{view["attempt_id"]}'
    response = ownership.client.post(url + "/answers", json={"question_id": view["question"]["id"], "option_id": str(uuid4())})
    assert response.status_code == 422
    with Session(ownership.engine) as session:
        row = session.get(OwnershipAttempt, UUID(view["attempt_id"]))
        row.expires_at = now() - timedelta(seconds=1)
        session.commit()
    assert ownership.client.get(url).status_code == 410
    ownership.settings.local_development = False
    ownership.settings.business_api_enabled = True
    assert ownership.client.post(BASE, json={"description": "필통"}).status_code == 200
    ownership.settings.business_api_enabled = False
    assert ownership.client.post(BASE, json={"description": "필통"}).status_code == 503


def test_unavailable_item_cannot_be_consumed_as_supported(ownership):
    first = begin(ownership)
    second = submit(ownership, first, "파란 원형 핀").json()
    pending = submit(ownership, second, "MJ").json()
    url = photo_url(pending)
    token = ownership.client.get(url).headers["X-Ownership-Photo-Token"]
    with Session(ownership.engine) as session:
        session.get(FoundItem, ownership.first).status = "awaiting_deposit"
        session.commit()
    denied = ownership.client.post(url + "-confirmation", json={"confirmed": True, "photo_token": token})
    assert denied.status_code == 404 and denied.json()["detail"]["code"] == "OWNERSHIP_PHOTO_UNAVAILABLE"
    assert result_for(ownership, pending) is None


def test_changed_evidence_rolls_back_photo_confirmation(ownership):
    first = begin(ownership)
    second = submit(ownership, first, "파란 원형 핀").json()
    pending = submit(ownership, second, "MJ").json()
    url = photo_url(pending)
    token = ownership.client.get(url).headers["X-Ownership-Photo-Token"]
    with Session(ownership.engine) as session:
        reg = session.get(Registration, session.get(FoundItem, ownership.first).registration_id)
        reg.analysis_features = reg.analysis_features["data"]
        session.commit()
    denied = ownership.client.post(url + "-confirmation", json={"confirmed": True, "photo_token": token})
    assert denied.status_code == 409 and denied.json()["detail"]["code"] == "OWNERSHIP_EVIDENCE_CHANGED"
    assert result_for(ownership, pending) is None
    with Session(ownership.engine) as session:
        assert session.scalars(select(ReturnCode)).all() == []


def test_rejecting_photo_ends_attempt_without_otp(ownership):
    first = begin(ownership)
    assert ownership.client.get(photo_url(first)).status_code == 409
    second = submit(ownership, first, "파란 원형 핀").json()
    pending = submit(ownership, second, "MJ").json()
    url = photo_url(pending)
    token = ownership.client.get(url).headers["X-Ownership-Photo-Token"]
    response = ownership.client.post(url + "-confirmation", json={"confirmed": False})
    assert response.status_code == 200 and response.json()["return_code"] is None
    finished = response.json()["attempt"]
    assert finished["status"] == "completed" and finished["decision"] == "not_confirmed"
    assert finished["found_item_id"] is None
    assert result_for(ownership, finished).failure_reason == "photo_rejected"
    assert ownership.client.post(url + "-confirmation", json={"confirmed": True, "photo_token": token}).status_code == 409
    with Session(ownership.engine) as session:
        assert session.scalars(select(ReturnCode)).all() == []


def test_missing_registered_photo_prevents_success(ownership):
    first = begin(ownership)
    second = submit(ownership, first, "파란 원형 핀").json()
    with Session(ownership.engine) as session:
        item = session.get(FoundItem, ownership.first)
        photo = session.scalar(select(ItemImage).where(ItemImage.registration_id == item.registration_id))
        session.delete(photo)
        session.commit()
    finished = submit(ownership, second, "MJ").json()
    assert finished["decision"] == "not_confirmed"
    assert result_for(ownership, finished).failure_reason == "photo_unavailable"


def gemini_json(result):
    return httpx.Response(200, json={"candidates": [{"finishReason": "STOP",
                          "content": {"parts": [{"text": json.dumps(result)}]}}]})


def test_gemini_extracts_only_literal_user_clues():
    settings = GeminiSettings(_env_file=None, gemini_api_key="test-key", gemini_model="test-model")
    replies = iter(['{"category":"필통","color":"검은색","location":"도서관"}', '{"brand":"Carhartt"}'])

    def handler(request):
        assert request.headers["x-goog-api-key"] == "test-key"
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP",
                              "content": {"parts": [{"text": next(replies)}]}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ai = GeminiWorkflowAI(settings, client)
        assert ai.extract("도서관에서 검은색 필통을 잃었어요").brand is None
        with pytest.raises(IntegrationError, match="OWNERSHIP_UNGROUNDED_CLUE"):
            ai.extract("검은색 필통")


@pytest.mark.parametrize("mode", ["matched", "no_matches", "incomplete"])
def test_gemini_search_sends_explicit_unknowns_and_checks_full_batch(mode):
    settings = GeminiSettings(_env_file=None, gemini_api_key="test-key", gemini_model="test-model")
    calls = []

    def handler(request):
        calls.append(request.url.path)
        payload = json.loads(request.content)
        sent = json.loads(payload["contents"][0]["parts"][0]["text"])
        assert sent == {
            "clues": {"category": "필통", "color": "남색", "location": "도서관"},
            "items": [
                {"item": 1, "features": {"category": None, "color": ["navy"], "location": None}},
                {"item": 2, "features": {"category": ["필통"], "color": None, "location": None}},
                {"item": 3, "features": {"category": ["지갑"], "color": ["남색"], "location": None}},
            ],
        }
        rows = [
            {"item": 1, "decision": "match", "matched_fields": ["color"]},
            {"item": 2, "decision": "match", "matched_fields": ["category"]},
            {"item": 3, "decision": "different", "matched_fields": []},
        ]
        if mode == "no_matches":
            rows = [{**row, "decision": "different", "matched_fields": []} for row in rows]
        return gemini_json({"results": rows[:-1] if mode == "incomplete" else rows})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ai = GeminiWorkflowAI(settings, client)
        clues = {"category": "필통", "color": "남색", "location": "도서관"}
        items = {1: {"color": ["navy"]}, 2: {"category": ["필통"]},
                 3: {"category": ["지갑"], "color": ["남색"]}}
        if mode == "incomplete":
            with pytest.raises(IntegrationError, match="OWNERSHIP_AI_INVALID_MATCHES"):
                ai.similar_items(clues, items)
            assert len(calls) == 1
            return
        result = ai.similar_items(clues, items)
    assert [item.item for item in result] == ([1, 2] if mode == "matched" else [])
    assert len(calls) == 1
