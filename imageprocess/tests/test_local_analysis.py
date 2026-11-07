import json
from unittest.mock import Mock

import pytest
from PIL import Image

from app.analyze_image import analyze_file, build_parser, main
from app.core.config import Settings
from app.core.errors import ServiceError


def test_local_mock_pipeline_saves_explicitly_marked_result(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    image = tmp_path / 'item.png'
    output = tmp_path / 'result.json'
    Image.new('RGB', (96, 64), 'white').save(image)
    assert main([str(image), '--backend', 'mock', '--output', str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == json.loads(output.read_text(encoding='utf-8'))
    assert result['success'] is True
    assert result['backend'] == 'mock'
    assert result['model_name'] is None
    assert 'MOCK_INFERENCE' in result['warnings']
    assert result['data']['category'] == 'unknown'
    assert all(value >= 0 for value in result['timings_ms'].values())


def test_invalid_image_is_rejected_before_model_loading(tmp_path):
    image = tmp_path / 'invalid.jpg'
    image.write_bytes(b'not an image')
    service = Mock()
    with pytest.raises(ServiceError) as failure:
        analyze_file(image, Settings(_env_file=None), service)
    assert failure.value.code == 'INVALID_IMAGE'
    service.load.assert_not_called()
    service.infer.assert_not_called()


def test_invalid_model_output_does_not_save_success_result(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    image = tmp_path / 'item.png'
    output = tmp_path / 'result.json'
    Image.new('RGB', (96, 64), 'white').save(image)
    monkeypatch.setattr('app.analyze_image.VisionService.infer', lambda self, image, category=None: 'not JSON')
    assert main([str(image), '--backend', 'mock', '--output', str(output)]) == 1
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'INVALID_MODEL_OUTPUT'
    assert not output.exists()


def test_local_cli_defaults_to_real_model_even_if_env_uses_mock(monkeypatch):
    monkeypatch.setenv('INFERENCE_BACKEND', 'mock')
    args = build_parser().parse_args(['item.jpg'])
    assert args.backend == 'mlx'


def test_missing_file_returns_failure_without_model_loading(tmp_path, capsys):
    assert main([str(tmp_path / 'missing.jpg'), '--backend', 'mock']) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['success'] is False
    assert result['error']['code'] == 'IMAGE_READ_FAILED'


def test_local_category_reaches_model_without_overriding_its_prediction(tmp_path):
    image = tmp_path / 'item.png'
    Image.new('RGB', (96, 64), 'white').save(image)
    payload = {'category': 'pencil_case', 'color': ['black'], 'material': 'unknown',
               'brand': None, 'visible_text': [], 'distinctive_features': [],
               'description': 'A black pencil case.'}
    service = Mock()
    service.infer.return_value = json.dumps(payload)
    result = analyze_file(image, Settings(_env_file=None, inference_backend='mock'), service, category=' 필통 ')
    assert service.infer.call_args.kwargs == {'category': '필통'}
    service.load.assert_called_once()
    assert result['input_category'] == '필통'
    assert result['data']['category'] == 'pencil_case'


def test_cli_accepts_category_without_requesting_notes(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    image = tmp_path / 'item.png'
    Image.new('RGB', (96, 64), 'white').save(image)
    assert main([str(image), '--backend', 'mock', '--category', '필통']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['input_category'] == '필통'
    assert 'MOCK_INFERENCE' in result['warnings']
    assert 'notes' not in result


def test_cli_rejects_empty_category_before_loading_image(tmp_path, capsys):
    assert main([str(tmp_path / 'missing.jpg'), '--category', ' ']) == 1
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'INVALID_CATEGORY'
