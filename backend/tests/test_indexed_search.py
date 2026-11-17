from copy import deepcopy
import json
from pathlib import Path
from uuid import UUID

import pytest
import imageprocess
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_ownership_verification.search import find_candidates
from app.ai_ownership_verification.workflow_schemas import SearchClues
from app.models import FoundItem, Kiosk, Registration
from app.review_evidence import review
from app.search_features import project, normalized
from test_ownership_workflow import ownership, add_item, begin, submit


EXPERIMENT = json.loads((Path(imageprocess.__file__).parent / 'docs/experiments/single-photo-10-cases.json').read_text(encoding='utf-8'))
CATEGORY_CLUES = {'airpod': '이어폰', 'book': '책', 'cap': '모자', 'charger': '충전기', 'cosmetic': '화장품',
                  'doll1': '인형', 'doll2': '인형', 'pencilcase': '필통', 'wallet1': '지갑', 'wallet2': '지갑'}


@pytest.mark.parametrize('case', EXPERIMENT['cases'], ids=lambda case: case['case_id'])
def test_latest_ten_experiment_categories_match_general_korean_clues(case):
    from imageprocess.app.models.schemas import Features
    features = Features.model_validate(case['features']).model_dump()
    projection = project(features)
    assert normalized('category', CATEGORY_CLUES[case['case_id']]) in projection['category']


def test_simple_alias_search_does_not_call_semantic_model(ownership):
    ownership.ai.clues = SearchClues(category='필통', color='검은색')
    ownership.ai.similar_items = lambda *args: pytest.fail('No semantic call for normalized simple fields')
    assert begin(ownership)['initial_candidate_count'] == 2


def test_unknown_color_is_not_a_conflict(ownership):
    with Session(ownership.engine) as session:
        reg = session.get(Registration, session.get(FoundItem, ownership.first).registration_id)
        reg.analysis_features = {**reg.analysis_features, 'color': None}
        reg.search_features = project(reg.analysis_features)
        session.commit()
        status, candidates = find_candidates(session, SearchClues(category='필통', color='검정'), ownership.ai)
        assert status == 'question' and len(candidates) == 2


def test_more_than_500_unrelated_items_do_not_prevent_indexed_lookup(ownership):
    with Session(ownership.engine) as session:
        kiosk = session.scalar(select(Kiosk))
        for number in range(4, 510):
            item_id = add_item(session, kiosk, number=number, decoration='irrelevant', mark='XX')
            reg = session.get(Registration, session.get(FoundItem, item_id).registration_id)
            reg.analysis_features = {**reg.analysis_features, 'category': 'umbrella', 'color': ['red']}
            reg.search_features = project(reg.analysis_features)
        session.commit()
        status, candidates = find_candidates(session, SearchClues(category='필통', color='검정'), ownership.ai)
        assert status == 'question' and len(candidates) == 2


def test_overflow_requests_refinement_and_color_can_refine(ownership):
    with Session(ownership.engine) as session:
        kiosk = session.scalar(select(Kiosk))
        for number in range(4, 54):
            item_id = add_item(session, kiosk, number=number, decoration='irrelevant', mark='XX')
            reg = session.get(Registration, session.get(FoundItem, item_id).registration_id)
            reg.analysis_features = {**reg.analysis_features, 'color': ['red']}
            reg.search_features = project(reg.analysis_features)
        session.commit()
        assert find_candidates(session, SearchClues(category='필통'), ownership.ai) == ('refine_search', [])
        status, candidates = find_candidates(session, SearchClues(category='필통', color='검정'), ownership.ai)
        assert status == 'question' and len(candidates) == 2


def test_review_validates_analysis_reference_and_preserves_previous_review(ownership):
    with Session(ownership.engine) as session:
        reg = session.get(Registration, session.get(FoundItem, ownership.first).registration_id)
        original = deepcopy(reg.ownership_evidence)
        for changes in ({'analysis_id': str(UUID(int=0))}, {'feature_id': 'visible_text:99'},
                        {'value': 'invented'}, {'reviewed_by': ''}, {'reviewed_at': 'no-date'}):
            entries = [{**original[0], **changes}]
            with pytest.raises(ValueError):
                review(session, reg.id, entries)
            session.rollback()
            assert reg.ownership_evidence == original
        with pytest.raises(ValueError):
            review(session, reg.id, [original[0], original[0]])
        session.rollback()
        review(session, reg.id, [original[0]])
        assert len(reg.ownership_evidence) == 1


def test_answer_rejects_changed_analysis_before_next_question(ownership):
    view = begin(ownership)
    with Session(ownership.engine) as session:
        reg = session.get(Registration, session.get(FoundItem, ownership.first).registration_id)
        reg.analysis_metadata = {**reg.analysis_metadata, 'analysis_id': str(UUID(int=1))}
        session.commit()
    response = submit(ownership, view, '파란 원형 핀')
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'OWNERSHIP_EVIDENCE_CHANGED'


def test_legacy_attempts_are_read_only_and_cannot_authorize_return(ownership):
    from app.models import OwnershipAttempt
    from app.errors import BusinessError
    from app.ai_ownership_verification.workflow import get_ownership_result
    view = begin(ownership)
    attempt_id = UUID(view['attempt_id'])
    with Session(ownership.engine) as session:
        row = session.get(OwnershipAttempt, attempt_id)
        row.state = {**row.state, 'policy_version': 'development_v2'}
        session.commit()
    assert ownership.client.get(f'/api/ownership/attempts/{attempt_id}').json()['detail']['code'] == 'OWNERSHIP_ATTEMPT_RESTART_REQUIRED'
    with Session(ownership.engine) as session:
        row = session.get(OwnershipAttempt, attempt_id)
        row.state = {**row.state, 'decision': 'insufficient_evidence', 'current': None}
        session.commit()
        with pytest.raises(BusinessError, match='OWNERSHIP_ATTEMPT_RESTART_REQUIRED'):
            get_ownership_result(session, attempt_id)
    assert ownership.client.get(f'/api/ownership/attempts/{attempt_id}').json()['status'] == 'completed'
