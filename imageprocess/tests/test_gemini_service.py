import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from app.analyze_image import analyze_file
from app.core.config import Settings
from app.core.errors import ServiceError
from app.models.schemas import Features
from app.services.vision_service import VisionService, build_prompt, parse_features

PAYLOAD = {'category': 'pencil_case', 'color': ['black'], 'material': 'unknown',
           'brand': None, 'visible_text': [], 'distinctive_features': [],
           'description': 'A black pencil case.'}


@pytest.fixture
def gemini(monkeypatch):
    google, genai = ModuleType('google'), ModuleType('google.genai')
    genai.types = SimpleNamespace(HttpOptions=lambda **kwargs: SimpleNamespace(**kwargs),
                                 HttpRetryOptions=lambda **kwargs: SimpleNamespace(**kwargs))
    client = Mock()
    client.models.generate_content.return_value = SimpleNamespace(
        text=json.dumps(PAYLOAD), candidates=[SimpleNamespace(finish_reason='STOP')],
        usage_metadata=SimpleNamespace(prompt_token_count=100, candidates_token_count=30,
                                       thoughts_token_count=10, total_token_count=140))
    genai.Client = Mock(return_value=client)
    google.genai = genai
    monkeypatch.setitem(sys.modules, 'google', google)
    monkeypatch.setitem(sys.modules, 'google.genai', genai)
    config = Settings(_env_file=None, inference_backend='gemini', gemini_api_key='test-only-placeholder',
                      gemini_model_name='test-model')
    return config, genai.Client, client


def test_gemini_load_makes_no_inference_and_reuses_client(gemini):
    config, factory, client = gemini
    service = VisionService(config)
    service.load()
    service.load()
    factory.assert_called_once()
    client.models.generate_content.assert_not_called()
    options = factory.call_args.kwargs['http_options']
    assert options.timeout == 90000
    assert options.retry_options.attempts == 1
    assert service.loaded is True


def test_gemini_receives_shared_prompt_image_and_strict_json_schema(gemini):
    config, _, client = gemini
    service = VisionService(config)
    service.load()
    image = Image.new('RGB', (32, 32))
    assert parse_features(service.infer(image, category='필통')).model_dump() == PAYLOAD
    request = client.models.generate_content.call_args.kwargs
    assert request['model'] == 'test-model'
    assert request['contents'] == [build_prompt('필통'), image]
    assert request['config']['response_json_schema'] == Features.model_json_schema()
    assert request['config']['response_mime_type'] == 'application/json'
    assert request['config']['temperature'] == 0.0
    assert service.usage['total_token_count'] == 140
    assert 'test-only-placeholder' not in repr(config)
    service.close()
    client.close.assert_called_once()
    assert service.loaded is False


def test_missing_key_is_reported_without_loading_model_or_api():
    config = Settings(_env_file=None, inference_backend='gemini', gemini_api_key=None)
    with pytest.raises(ServiceError) as failure:
        VisionService(config).load()
    assert failure.value.code == 'GEMINI_API_KEY_MISSING'


@pytest.mark.parametrize('status, code', [(400, 'GEMINI_REQUEST_REJECTED'), (401, 'GEMINI_REQUEST_REJECTED'),
                                        (403, 'GEMINI_REQUEST_REJECTED'), (404, 'GEMINI_MODEL_NOT_FOUND'),
                                        (429, 'GEMINI_RATE_LIMITED'), (500, 'GEMINI_API_FAILED')])
def test_api_errors_hide_credentials_and_are_not_retried(gemini, status, code):
    config, _, client = gemini
    error = RuntimeError('private-token-in-url: test-only-placeholder')
    error.code = status
    client.models.generate_content.side_effect = error
    service = VisionService(config)
    service.load()
    with pytest.raises(ServiceError) as failure:
        service.infer(Image.new('RGB', (32, 32)))
    assert failure.value.code == code
    assert 'test-only-placeholder' not in failure.value.message
    assert 'private-token-in-url' not in failure.value.message
    assert client.models.generate_content.call_count == 1


@pytest.mark.parametrize('reason, code', [('MAX_TOKENS', 'GEMINI_OUTPUT_TRUNCATED'),
                                        ('SAFETY', 'GEMINI_RESPONSE_BLOCKED')])
def test_incomplete_answers_are_not_treated_as_features(gemini, reason, code):
    config, _, client = gemini
    client.models.generate_content.return_value.candidates[0].finish_reason = reason
    service = VisionService(config)
    service.load()
    with pytest.raises(ServiceError) as failure:
        service.infer(Image.new('RGB', (32, 32)))
    assert failure.value.code == code


def test_gemini_file_result_has_model_usage_fingerprints_and_closes_client(gemini, tmp_path):
    config, _, client = gemini
    path = tmp_path / 'image.png'
    Image.new('RGB', (64, 32), 'white').save(path)
    result = analyze_file(path, config, category='필통')
    assert result['backend'] == 'gemini'
    assert result['model_name'] == 'test-model'
    assert result['input_category'] == '필통'
    assert result['data'] == PAYLOAD
    assert result['usage']['total_token_count'] == 140
    assert len(result['comparison_metadata']['image_sha256']) == 64
    assert result['comparison_metadata']['prepared_size'] == [64, 32]
    assert 'test-only-placeholder' not in json.dumps(result)
    client.close.assert_called_once()


def test_cli_gemini_model_override_and_nested_output(gemini, tmp_path, monkeypatch, capsys):
    from app.analyze_image import main
    _, _, client = gemini
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GEMINI_API_KEY', 'test-only-placeholder')
    path = tmp_path/'image.png'
    Image.new('RGB', (64, 32)).save(path)
    output = tmp_path/'outputs'/'gemini.json'
    assert main([str(path), '--backend', 'gemini', '--model', 'override-model',
                 '--category', '필통', '--output', str(output)]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record['model_name'] == 'override-model'
    assert client.models.generate_content.call_args.kwargs['model'] == 'override-model'
    assert json.loads(output.read_text()) == record


def test_cli_missing_key_preserves_actionable_error(tmp_path, monkeypatch, capsys):
    from app.analyze_image import main
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    path = tmp_path/'image.png'
    Image.new('RGB', (64, 32)).save(path)
    assert main([str(path), '--backend', 'gemini']) == 1
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'GEMINI_API_KEY_MISSING'
