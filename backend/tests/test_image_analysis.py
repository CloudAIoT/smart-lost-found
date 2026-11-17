import hashlib
import json
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID

from pydantic import SecretStr
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.image_analysis import GeminiFeatureAnalyzer, _slots
from app.integrations import make_store, IntegrationError
from app.models import Registration, FoundItem, ItemImage
from imageprocess import ServiceError
from imageprocess.app.services.gemini_service import GeminiService
from test_registration import env, start, photos


FEATURES = {'category': 'pencil_case', 'color': ['black'], 'shape': '직사각형',
            'surface_texture': '직조 표면', 'brand': None, 'visible_text': None,
            'structure': ['측면 지퍼'], 'distinctive_features': None}


def test_s3_read_is_bounded_before_decoding_even_if_capture_limit_is_larger(env, monkeypatch):
    reads = []
    class Body(BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)
    body = Body(b'x' * 100)
    monkeypatch.setattr('app.integrations.boto3.client', lambda *args, **kwargs:
                        SimpleNamespace(get_object=lambda **kwargs: {'Body': body}))
    env.settings.s3_bucket, env.settings.s3_region, env.settings.preview_ttl_seconds = 'test', 'test', 60
    env.settings.image_analysis_max_bytes = 16
    with pytest.raises(IntegrationError, match='INVALID_IMAGE'):
        make_store(env.settings).read('test-key')
    assert reads == [17] and body.closed


@pytest.fixture
def model(env, monkeypatch):
    env.settings.gemini_api_key = SecretStr('test-only-key')
    env.analyzer = GeminiFeatureAnalyzer(env.settings)
    from app.api import get_analyzer
    from app.main import app
    app.dependency_overrides[get_analyzer] = lambda: env.analyzer
    calls = []
    def infer(self, images, prompt, *, response_schema):
        calls.append((images, prompt, response_schema))
        assert len(images) == 2 and all(i.mode == 'RGB' for i in images)
        self.usage = {'total_token_count': 42}
        return json.dumps(FEATURES)
    monkeypatch.setattr(GeminiService, 'load', lambda self: None)
    monkeypatch.setattr(GeminiService, 'infer_images', infer)
    return calls


def test_real_preparation_parser_atomic_storage_and_keyless_cache(env, model):
    value = start(env)
    photos(env, value)
    response = env.client.post(f'/api/registrations/{value}/analysis')
    assert response.status_code == 200, response.text
    assert response.json()['analysis_warnings']
    assert 'analysis_features' not in response.json()
    assert len(model) == 1
    with Session(env.engine) as session:
        reg = session.get(Registration, UUID(value))
        assert reg.analysis_features == FEATURES
        assert reg.search_features['category'] == ['pencilcase']
        assert reg.ownership_evidence == []
        assert reg.analysis_metadata['usage']['total_token_count'] == 42
        assert reg.analysis_metadata['image_sha256'] == [hashlib.sha256(env.gateway.jpeg).hexdigest()] * 2
        assert 'test-only-key' not in json.dumps(reg.analysis_metadata)
        assert session.scalar(select(FoundItem)) is None
        assert reg.analysis_input_image_ids == [str(i.id) for i in session.scalars(select(ItemImage).order_by(ItemImage.shot_order))]
    env.settings.gemini_api_key = None
    assert env.client.post(f'/api/registrations/{value}/analysis').status_code == 200
    assert len(model) == 1


def test_missing_key_and_busy_slot_do_not_read_s3_or_change_state(env, model, monkeypatch):
    value = start(env)
    photos(env, value)
    monkeypatch.setattr(env.store, 'read', lambda key: pytest.fail('S3 should not be read'))
    env.settings.gemini_api_key = SecretStr('')
    response = env.client.post(f'/api/registrations/{value}/analysis')
    assert response.status_code == 503 and response.json()['detail']['code'] == 'AI_NOT_CONFIGURED'
    env.settings.gemini_api_key = SecretStr('test-only-key')
    slot = _slots(1)
    with slot:
        response = env.client.post(f'/api/registrations/{value}/analysis')
    assert response.status_code == 409 and response.json()['detail']['code'] == 'ANALYSIS_BUSY'
    with Session(env.engine) as session:
        reg = session.get(Registration, UUID(value))
        assert (reg.status, reg.analysis_status, reg.analysis_input_image_ids) == ('draft', 'pending', None)
    assert not model


def test_input_category_uses_db_name_not_uuid_or_free_text(env, model):
    response = env.client.post('/api/registrations', json={
        'kiosk_id': env.kiosk_id, 'primary_category_id': env.category_id,
        'secondary_description': 'Do not use this description as image evidence'})
    value = response.json()['id']
    photos(env, value)
    assert env.client.post(f'/api/registrations/{value}/analysis').status_code == 200
    prompt = model[0][1]
    assert 'Test only' in prompt and env.category_id not in prompt
    assert 'Do not use this description' not in prompt
    with Session(env.engine) as session:
        assert session.get(Registration, UUID(value)).analysis_metadata['input_category'] == 'Test only'


def test_changed_state_during_model_call_is_not_overwritten(env, model, monkeypatch):
    value = start(env)
    photos(env, value)
    infer = GeminiService.infer_images
    def changed(self, *args, **kwargs):
        result = infer(self, *args, **kwargs)
        with Session(env.engine) as session:
            reg = session.get(Registration, UUID(value))
            reg.status = 'blocked'
            session.commit()
        return result
    monkeypatch.setattr(GeminiService, 'infer_images', changed)
    response = env.client.post(f'/api/registrations/{value}/analysis')
    assert response.status_code == 409 and response.json()['detail']['code'] == 'ANALYSIS_RESULT_STALE'
    with Session(env.engine) as session:
        reg = session.get(Registration, UUID(value))
        assert reg.status == 'blocked' and reg.analysis_features is None


@pytest.mark.parametrize('failure', ['invalid_json', 'all_null', 'extra_key', 'timeout', 'rate_limit', 'pixel_limit'])
def test_analysis_failure_retains_photos_and_can_retry(env, model, monkeypatch, failure):
    original = GeminiService.infer_images
    def failed(self, *args, **kwargs):
        if failure == 'timeout':
            raise ServiceError('GEMINI_TIMEOUT', 'timeout')
        if failure == 'rate_limit':
            raise ServiceError('GEMINI_RATE_LIMITED', 'rate')
        if failure == 'all_null':
            return json.dumps(dict.fromkeys(FEATURES))
        if failure == 'extra_key':
            return json.dumps({**FEATURES, 'material': 'fabric'})
        return '{'
    value = start(env)
    photos(env, value)
    monkeypatch.setattr(GeminiService, 'infer_images', failed)
    if failure == 'pixel_limit':
        env.settings.image_analysis_max_pixels = 1
    response = env.client.post(f'/api/registrations/{value}/analysis')
    assert response.status_code == 502, response.text
    with Session(env.engine) as session:
        reg = session.get(Registration, UUID(value))
        assert (reg.status, reg.analysis_status) == ('draft', 'failed')
        assert reg.analysis_features is None and reg.analysis_metadata is None
        assert reg.search_features == {} and reg.ownership_evidence == []
        assert len(reg.analysis_input_image_ids) == 2
    assert len(env.store.objects) == 2
    assert env.client.post(f'/api/registrations/{value}/deposit/open').status_code == 409
    monkeypatch.setattr(GeminiService, 'infer_images', original)
    env.settings.image_analysis_max_pixels = 4000000
    assert env.client.post(f'/api/registrations/{value}/analysis').status_code == 200


def test_legacy_ready_result_is_reanalyzed_and_stored_result_is_not(env, model):
    value = start(env)
    photos(env, value)
    with Session(env.engine) as session:
        reg = session.get(Registration, UUID(value))
        reg.status, reg.analysis_status = 'ready', 'succeeded'
        reg.analysis_features = {'material': 'fabric'}
        session.commit()
    assert env.client.post(f'/api/registrations/{value}/analysis').status_code == 200
    assert len(model) == 1
    assert env.client.post(f'/api/registrations/{value}/deposit/open').status_code == 200
    assert env.client.post(f'/api/registrations/{value}/analysis').status_code == 409


def test_two_photos_through_review_indexed_search_and_ownership(env, model, monkeypatch):
    from app.main import app
    from app.ai_ownership_verification.api import get_ownership_ai
    from app.ai_ownership_verification.workflow_schemas import SearchClues
    from app.ai_ownership_verification.workflow import get_ownership_result
    from app.review_evidence import review
    from app.models import now
    from test_ownership_workflow import FakeAI, begin, submit

    monkeypatch.setitem(FEATURES, 'distinctive_features', ['파란 원형 핀'])
    monkeypatch.setitem(FEATURES, 'visible_text', ['MJ'])
    value = start(env)
    photos(env, value)
    assert env.client.post(f'/api/registrations/{value}/analysis').status_code == 200
    assert env.client.post(f'/api/registrations/{value}/deposit/open').status_code == 200
    assert env.client.post(f'/api/registrations/{value}/deposit/complete', json={'confirmed': True}).status_code == 200
    with Session(env.engine) as session:
        reg = session.get(Registration, UUID(value))
        entries = [{'analysis_id': reg.analysis_metadata['analysis_id'], 'feature_id': field + ':0',
                    'field': field, 'value': reg.analysis_features[field][0], 'group': field,
                    'visibility': 'private', 'verified': True, 'distinguishing': True,
                    'reviewed_by': 'synthetic-test-reviewer', 'reviewed_at': now().isoformat()}
                   for field in ('distinctive_features', 'visible_text')]
        review(session, reg.id, entries)
    ai = FakeAI()
    ai.clues = SearchClues(category='필통', color='검은색')
    ai.similar_items = lambda *args: pytest.fail('Simple search must use indexed features')
    app.dependency_overrides[get_ownership_ai] = lambda: ai
    first = begin(env)
    assert first['initial_candidate_count'] == 1
    second = submit(env, first, '파란 원형 핀').json()
    finished = submit(env, second, 'MJ').json()
    assert finished['decision'] == 'supported'
    with Session(env.engine) as session:
        result = get_ownership_result(session, UUID(finished['attempt_id']))
        assert result.policy_version == 'development_v3'
        assert result.matched_evidence_groups == 2
