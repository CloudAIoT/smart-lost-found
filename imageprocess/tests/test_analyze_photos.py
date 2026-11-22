import json
from unittest.mock import Mock

from PIL import Image
import pytest

from app.analyze_photos import main, run_batch
from app.core.config import Settings
from app.core.errors import ServiceError
from app.models.schemas import Features


def test_mock_batch_uses_current_nullable_schema_and_filename_categories(tmp_path):
    photos = tmp_path / 'photos'
    photos.mkdir()
    for name in ('doll1.jpg', 'doll2.jpg'):
        Image.new('RGB', (64, 32), 'white').save(photos / name)
    output, records = run_batch(photos, tmp_path/'outputs', Settings(_env_file=None, inference_backend='mock'))
    assert len(records) == 2 and all(r['success'] for r in records)
    assert all(r['input_category'] == 'plush_toy' for r in records)
    assert all(r['data'] == dict.fromkeys(Features.model_fields) for r in records)
    assert all('MOCK_INFERENCE' in r['warnings'] for r in records)
    assert 'null' in (output/'report.md').read_text()
    assert json.loads((output/'manifest.json').read_text())['status'] == 'completed'
    assert json.loads((output/'manifest.json').read_text())['feature_schema'] == 'fixed_common_v3_ko'
    assert all(r['comparison_metadata']['feature_schema'] == 'fixed_common_v3_ko' for r in records)


def test_batch_stops_after_shared_api_failure_and_saves_the_failure(tmp_path):
    photos = tmp_path/'photos'
    photos.mkdir()
    for name in ('wallet1.jpg', 'wallet2.jpg'):
        Image.new('RGB', (64, 32)).save(photos/name)
    analyzer = Mock(side_effect=ServiceError('GEMINI_RATE_LIMITED', 'Quota reached.', 503))
    output, records = run_batch(photos, tmp_path/'outputs', Settings(_env_file=None), analyzer=analyzer)
    analyzer.assert_called_once()
    assert len(records) == 1 and not records[0]['success']
    assert json.loads((output/'manifest.json').read_text())['status'] == 'incomplete'


def test_batch_cli_can_be_verified_offline(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    photos = tmp_path/'photos'
    photos.mkdir()
    Image.new('RGB', (64, 32)).save(photos/'인형1.jpg')
    assert main([str(photos), '--backend', 'mock']) == 0
    assert '"succeeded": 1' in capsys.readouterr().out


def test_start_from_skips_successful_images_and_includes_the_failed_image(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    photos = tmp_path/'photos'
    photos.mkdir()
    names = ('airpod.jpg', 'book.jpg', 'cap.jpg', 'charger.jpg', 'cosmetic.jpg',
             'doll1.jpg', 'doll2.jpg', 'pencilcase.jpg', 'wallet1.jpg', 'wallet2.jpg')
    for name in names:
        Image.new('RGB', (16, 16)).save(photos/name)
    analyzer = Mock(side_effect=lambda path, settings, **kwargs: {
        'success': True, 'data': dict.fromkeys(Features.model_fields), 'timings_ms': {'inference': 0}})
    monkeypatch.setattr('app.analyze_photos.analyze_file', analyzer)
    assert main([str(photos), '--start-from', 'doll2.jpg', '--backend', 'mock']) == 0
    assert [call.args[0].name for call in analyzer.call_args_list] == list(names[6:])
    output = next((tmp_path/'outputs'/'fixed_features').iterdir())
    manifest = json.loads((output/'manifest.json').read_text())
    assert manifest['skipped_before_start'] == list(names[:6])
    assert manifest['succeeded'] == 4 and manifest['status'] == 'completed'
    assert (output/'07_doll2.json').exists()
    assert '[7/10] doll2.jpg' in capsys.readouterr().out


def test_unknown_start_filename_fails_before_inference_or_output_creation(tmp_path):
    photos = tmp_path/'photos'
    photos.mkdir()
    Image.new('RGB', (16, 16)).save(photos/'doll2.jpg')
    analyzer = Mock()
    with pytest.raises(ServiceError) as failure:
        run_batch(photos, tmp_path/'outputs', Settings(_env_file=None),
                  start_from='missing.jpg', analyzer=analyzer)
    assert failure.value.code == 'START_IMAGE_NOT_FOUND'
    analyzer.assert_not_called()
    assert not (tmp_path/'outputs').exists()
