import json
from pathlib import Path
from unittest.mock import Mock

from PIL import Image
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import ServiceError
from app.explore_features import Exploration, build_exploration_prompt, category_from_filename, run_experiment


PAYLOAD = {
    'observed_category': 'wallet',
    'common_features': [{'name': 'surface_finish', 'value': '무광', 'evidence': '앞면에 광택이 거의 보이지 않는다.'}],
    'distinctive_features': [{'name': 'corner_mark', 'value': '오른쪽 모서리 흰 점',
                             'evidence': '사진에 보이는 오른쪽 아래 모서리에 흰 점이 있다.',
                             'distinction_reason': '흰 점의 위치로 비슷한 물건과 구별할 수 있다.'}],
    'uncertainties': ['뒷면은 보이지 않는다.'],
}


@pytest.mark.parametrize('filename, category', [('wallet2.jpg', 'wallet'), ('doll1.JPG', 'plush_toy'),
                                             ('pencilcase.jpg', 'pencil_case'), ('airpod.jpg', 'wireless_earbuds'),
                                             ('필통_03.png', '필통')])
def test_filename_supplies_category_without_sequence_number(filename, category):
    assert category_from_filename(Path(filename)) == category


def test_free_feature_names_and_fewer_than_five_are_valid_but_null_and_duplicates_are_not():
    assert Exploration.model_validate(PAYLOAD).common_features[0].name == 'surface_finish'
    invalid = json.loads(json.dumps(PAYLOAD))
    invalid['common_features'][0]['value'] = None
    with pytest.raises(ValidationError):
        Exploration.model_validate(invalid)
    invalid = json.loads(json.dumps(PAYLOAD))
    invalid['common_features'].append(invalid['common_features'][0])
    with pytest.raises(ValidationError):
        Exploration.model_validate(invalid)


def test_batch_stores_independent_inputs_and_reports_without_touching_images(tmp_path):
    photos = tmp_path / 'photos'
    photos.mkdir()
    for name in ('wallet1.jpg', 'wallet2.jpg'):
        Image.new('RGB', (64, 32), 'white').save(photos / name)
    original = {path.name: path.read_bytes() for path in photos.iterdir()}
    service = Mock(usage={'total_token_count': 123})
    service.infer.return_value = json.dumps(PAYLOAD)
    settings = Settings(_env_file=None, inference_backend='gemini')
    output, records = run_experiment(photos, tmp_path / 'outputs', settings, service=service)
    assert len(records) == 2 and all(record['success'] for record in records)
    assert all(record['input_category'] == 'wallet' and record['input_description'] is None for record in records)
    assert service.infer.call_count == 2
    assert all(call.args[1] == build_exploration_prompt('wallet') for call in service.infer.call_args_list)
    assert 'surface_finish' not in build_exploration_prompt('wallet')
    service.close.assert_called_once()
    assert original == {path.name: path.read_bytes() for path in photos.iterdir()}
    inventory = json.loads((output / 'field_inventory.json').read_text())
    assert next(item for item in inventory if item['name'] == 'surface_finish')['common_photo_count'] == 2
    assert json.loads((output / 'manifest.json').read_text())['status'] == 'completed'
    report = (output / 'report.md').read_text()
    assert 'wallet1.jpg' in report and 'wallet2.jpg' in report and '오른쪽 모서리 흰 점' in report


def test_shared_api_failure_stops_batch_and_preserves_failure_report(tmp_path):
    photos = tmp_path / 'photos'
    photos.mkdir()
    for name in ('wallet1.jpg', 'wallet2.jpg'):
        Image.new('RGB', (64, 32)).save(photos / name)
    service = Mock()
    service.infer.side_effect = ServiceError('GEMINI_RATE_LIMITED', 'Quota reached.', 503)
    output, records = run_experiment(photos, tmp_path / 'outputs', Settings(_env_file=None), service=service)
    service.infer.assert_called_once()
    service.close.assert_called_once()
    assert records[0]['error']['code'] == 'GEMINI_RATE_LIMITED'
    assert json.loads((output / 'manifest.json').read_text())['status'] == 'incomplete'
    assert 'GEMINI_RATE_LIMITED' in (output / 'report.md').read_text()
