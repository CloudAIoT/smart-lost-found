import io
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import Settings
from app.main import create_app


FEATURES = {
    'category': 'wallet',
    'color': ['black'],
    'material': 'leather',
    'brand': None,
    'visible_text': ['분실물'],
    'distinctive_features': ['silver zipper'],
    'description': 'A small black leather wallet with a silver zipper.',
}


class FakeVisionService:
    def __init__(self, *, output=None, load_error=None, inference_error=None):
        self.loaded = False
        self.output = json.dumps(FEATURES) if output is None else output
        self.load_error = load_error
        self.inference_error = inference_error
        self.images = []
        self.categories = []

    def load(self):
        if self.load_error is not None:
            raise self.load_error
        self.loaded = True

    def infer(self, image, category=None):
        self.images.append(image.copy())
        self.categories.append(category)
        if self.inference_error is not None:
            raise self.inference_error
        return self.output


def settings(**overrides):
    values = dict(inference_backend='mock', blur_threshold=0, darkness_threshold=0)
    values.update(overrides)
    return Settings(_env_file=None, **values)


def image_bytes(size=(80, 40), color='white', image_format='PNG'):
    stream = io.BytesIO()
    Image.new('RGB', size, color).save(stream, format=image_format)
    return stream.getvalue()


def upload(client, content=None, **kwargs):
    if content is None:
        content = image_bytes()
    return client.post('/analyze', files={'image': ('item.png', content, 'image/png')}, **kwargs)


def assert_error(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert body['success'] is False
    assert body['error']['code'] == code
    assert isinstance(body['error']['message'], str)
    assert body['error']['message']
    assert body['request_id']
    assert response.headers['x-request-id'] == body['request_id']
    return body


def test_analyze_returns_validated_features_and_correlation_id():
    service = FakeVisionService()
    with TestClient(create_app(settings(), service)) as client:
        response = upload(client, headers={'X-Request-ID': 'test-request-123'})

    assert response.status_code == 200
    body = response.json()
    assert body['success'] is True
    assert body['data'] == FEATURES
    assert body['warnings'] == ['MOCK_INFERENCE']
    assert body['processing_time_ms'] >= 0
    assert body['request_id'] == 'test-request-123'
    assert response.headers['x-request-id'] == body['request_id']
    assert len(service.images) == 1
    assert service.images[0].mode == 'RGB'


def test_invalid_file_is_rejected_before_inference():
    service = FakeVisionService()
    with TestClient(create_app(settings(), service)) as client:
        response = upload(client, b'this is not an image')
    assert_error(response, 422, 'INVALID_IMAGE')
    assert service.images == []


def test_unsupported_image_format_is_rejected():
    service = FakeVisionService()
    with TestClient(create_app(settings(), service)) as client:
        response = client.post(
            '/analyze', files={'image': ('item.gif', image_bytes(image_format='GIF'), 'image/gif')}
        )
    assert_error(response, 422, 'INVALID_IMAGE')
    assert service.images == []


def test_upload_byte_limit_is_enforced_before_inference():
    service = FakeVisionService()
    with TestClient(create_app(settings(max_upload_bytes=64), service)) as client:
        response = upload(client, image_bytes())
    assert_error(response, 413, 'IMAGE_TOO_LARGE')
    assert service.images == []


def test_decoded_pixel_limit_is_enforced_before_inference():
    service = FakeVisionService()
    with TestClient(create_app(settings(max_image_pixels=100), service)) as client:
        response = upload(client, image_bytes((11, 10)))
    assert_error(response, 413, 'IMAGE_TOO_LARGE')
    assert service.images == []


def test_image_is_resized_with_aspect_ratio_preserved():
    service = FakeVisionService()
    with TestClient(create_app(settings(max_image_size=128), service)) as client:
        response = upload(client, image_bytes((400, 200)))
    assert response.status_code == 200
    assert service.images[0].size == (128, 64)


def test_low_quality_image_returns_warnings_by_default():
    service = FakeVisionService()
    with TestClient(create_app(settings(blur_threshold=30, darkness_threshold=25), service)) as client:
        response = upload(client, image_bytes(color='black'))
    assert response.status_code == 200
    assert set(response.json()['warnings']) == {'IMAGE_BLURRY', 'IMAGE_DARK', 'MOCK_INFERENCE'}
    assert len(service.images) == 1


def test_low_quality_rejection_can_be_enabled():
    service = FakeVisionService()
    config = settings(blur_threshold=30, darkness_threshold=25, reject_low_quality=True)
    with TestClient(create_app(config, service)) as client:
        response = upload(client, image_bytes(color='black'))
    assert_error(response, 422, 'IMAGE_LOW_QUALITY')
    assert service.images == []


def test_inference_failure_has_safe_error_envelope():
    service = FakeVisionService(inference_error=RuntimeError('private model path /secret/model'))
    with TestClient(create_app(settings(), service)) as client:
        response = upload(client)
    body = assert_error(response, 502, 'AI_INFERENCE_FAILED')
    assert '/secret/model' not in body['error']['message']


@pytest.mark.parametrize('output', [
    'not JSON',
    json.dumps({'category': 'wallet'}),
    json.dumps({**FEATURES, 'color': 'black'}),
    json.dumps({**FEATURES, 'unrequested_key': 'value'}),
])
def test_invalid_model_output_returns_502(output):
    service = FakeVisionService(output=output)
    with TestClient(create_app(settings(), service)) as client:
        response = upload(client)
    assert_error(response, 502, 'INVALID_MODEL_OUTPUT')


def test_fenced_json_model_output_is_validated():
    service = FakeVisionService(output='```json\n' + json.dumps(FEATURES) + '\n```')
    with TestClient(create_app(settings(), service)) as client:
        response = upload(client)
    assert response.status_code == 200
    assert response.json()['data'] == FEATURES


def test_health_reports_ready_backend_after_startup():
    service = FakeVisionService()
    assert service.loaded is False
    with TestClient(create_app(settings(), service)) as client:
        response = client.get('/health')
        assert service.loaded is True
    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'ok'
    assert body['model_loaded'] is True
    assert body['backend'] == 'mock'


def test_health_returns_503_for_unloaded_model():
    service = FakeVisionService()
    with TestClient(create_app(settings(), service)) as client:
        service.loaded = False
        response = client.get('/health')
    assert response.status_code == 503
    assert response.json()['status'] == 'degraded'
    assert response.json()['model_loaded'] is False


def test_model_startup_failure_keeps_health_available_and_analysis_unavailable():
    service = FakeVisionService(load_error=RuntimeError('cannot load model'))
    with TestClient(create_app(settings(), service)) as client:
        health = client.get('/health')
        response = upload(client)
    assert health.status_code == 503
    assert health.json()['model_loaded'] is False
    assert_error(response, 503, 'MODEL_NOT_READY')
    assert service.images == []


def test_missing_image_uses_standard_error_envelope():
    service = FakeVisionService()
    with TestClient(create_app(settings(), service)) as client:
        response = client.post('/analyze', headers={'X-Request-ID': 'missing-image'})
    body = assert_error(response, 422, 'REQUEST_VALIDATION_ERROR')
    assert body['request_id'] == 'missing-image'
    assert response.headers['x-request-id'] == body['request_id']
    assert service.images == []


def test_oversized_declared_body_is_rejected_before_multipart_parsing():
    service = FakeVisionService()
    with TestClient(create_app(settings(max_upload_bytes=64), service)) as client:
        response = client.post(
            '/analyze', content=b'invalid multipart',
            headers={'Content-Type': 'multipart/form-data; boundary=test',
                     'Content-Length': str(64 + 64 * 1024 + 1)},
        )
    assert_error(response, 413, 'IMAGE_TOO_LARGE')
    assert service.images == []


def test_chunked_body_limit_does_not_require_content_length():
    service = FakeVisionService()
    chunks = iter([
        b'--test\r\nContent-Disposition: form-data; name="image"; filename="item.png"\r\n'
        b'Content-Type: image/png\r\n\r\n',
        b'x' * 30_000,
        b'x' * 30_000,
        b'x' * 30_000,
        b'\r\n--test--\r\n',
    ])
    with TestClient(create_app(settings(max_upload_bytes=64), service)) as client:
        response = client.post(
            '/analyze', content=chunks,
            headers={'Content-Type': 'multipart/form-data; boundary=test'},
        )
    assert_error(response, 413, 'IMAGE_TOO_LARGE')
    assert service.images == []


def test_health_remains_available_during_inference_and_second_analysis_is_busy():
    class BlockingService(FakeVisionService):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def infer(self, image, category=None):
            result = super().infer(image, category=category)
            self.entered.set()
            if not self.release.wait(timeout=5):
                raise RuntimeError('test inference was not released')
            return result

    service = BlockingService()
    with TestClient(create_app(settings(), service)) as client, ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(upload, client)
        try:
            assert service.entered.wait(timeout=3), 'inference did not start'
            health = client.get('/health')
            busy = upload(client)
            assert health.status_code == 200
            assert health.json()['model_loaded'] is True
            assert_error(busy, 503, 'SERVER_BUSY')
        finally:
            service.release.set()
        assert pending.result(timeout=3).status_code == 200
    assert len(service.images) == 1


def test_unknown_route_has_correlated_error_envelope():
    with TestClient(create_app(settings(), FakeVisionService())) as client:
        response = client.get('/unknown', headers={'X-Request-ID': 'missing-route'})
    body = assert_error(response, 404, 'HTTP_ERROR')
    assert body['request_id'] == 'missing-route'


def test_unsafe_request_id_is_replaced():
    with TestClient(create_app(settings(), FakeVisionService())) as client:
        response = upload(client, headers={'X-Request-ID': 'unsafe id with spaces'})
    assert response.status_code == 200
    request_id = response.json()['request_id']
    assert request_id != 'unsafe id with spaces'
    assert response.headers['x-request-id'] == request_id


def test_logs_contain_stages_and_request_id_without_private_input_or_output(caplog):
    output = json.dumps({**FEATURES, 'visible_text': ['private-printed-password'],
                         'description': 'private-generated-description'})
    service = FakeVisionService(output=output)
    caplog.set_level(logging.INFO)
    with TestClient(create_app(settings(), service)) as client:
        response = client.post(
            '/analyze',
            files={'image': ('private-owner-name.png', image_bytes(), 'image/png')},
            headers={'X-Request-ID': 'privacy-test', 'Authorization': 'Bearer private-secret-token'},
        )
    assert response.status_code == 200
    application_logs = '\n'.join(
        record.getMessage() for record in caplog.records if record.name.startswith('app.')
    )
    assert 'privacy-test' in application_logs
    assert 'preprocessing' in application_logs
    assert 'inference' in application_logs
    assert 'parsing success' in application_logs
    assert 'duration_ms=' in application_logs
    for private_value in ['private-printed-password', 'private-generated-description',
                          'private-owner-name', 'private-secret-token']:
        assert private_value not in application_logs


def test_optional_category_is_passed_to_model_and_reported_separately():
    service = FakeVisionService()
    with TestClient(create_app(settings(), service)) as client:
        hinted = upload(client, data={'category': '  필통  '})
        unhinted = upload(client)
    assert hinted.status_code == 200
    assert hinted.json()['input_category'] == '필통'
    # The prediction remains model output; the API does not force the input label.
    assert hinted.json()['data']['category'] == FEATURES['category']
    assert unhinted.status_code == 200
    assert unhinted.json()['input_category'] is None
    assert service.categories == ['필통', None]


@pytest.mark.parametrize('category', ['   ', 'a' * 101, '필통\n명령'])
def test_invalid_category_is_rejected_before_inference(category):
    service = FakeVisionService()
    with TestClient(create_app(settings(), service)) as client:
        response = upload(client, data={'category': category})
    assert_error(response, 422, 'INVALID_CATEGORY')
    assert service.images == []
