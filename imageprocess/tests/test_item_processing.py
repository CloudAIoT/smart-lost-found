import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock
from uuid import uuid4

from PIL import Image
import pytest

from app.core.config import Settings
from app.core.errors import ServiceError
from app.models.schemas import Features
from app.services.item_processing import ImageInput, parse_item_output, prepare_item


def jpeg(color):
    stream = io.BytesIO()
    Image.new('RGB', (96, 64), color).save(stream, format='JPEG')
    return stream.getvalue()


def pair():
    return (ImageInput(uuid4(), jpeg('white')), ImageInput(uuid4(), jpeg('black')))


def config(**kwargs):
    return Settings(_env_file=None, **kwargs)


def test_backend_boundary_preserves_registration_ids_photo_order_and_nullable_fields():
    inputs, registration = pair(), uuid4()
    prepared = prepare_item(registration, inputs, category='필통', settings=config())
    assert prepared.registration_id == registration
    assert prepared.image_ids == tuple(item.image_id for item in inputs)
    assert prepared.images[0].getpixel((0, 0)) == (255, 255, 255)
    assert prepared.images[1].getpixel((0, 0)) == (0, 0, 0)
    assert 'shot order 1 then 2' in prepared.prompt
    assert 'Do not count the same attachment twice' in prepared.prompt
    assert '"pencil_case"' in prepared.prompt
    payload = dict.fromkeys(Features.model_fields)
    payload.update(category='pencil_case', color=['black'])
    result = parse_item_output(prepared, json.dumps(payload))
    encoded = result.model_dump(mode='json')
    assert encoded['image_ids'] == [str(item.image_id) for item in inputs]
    assert encoded['data'] == payload
    assert encoded['schema_version'] == 'fixed_common_v2'
    assert 'shot_2:IMAGE_DARK' in encoded['warnings']


@pytest.mark.parametrize('count', [0, 1, 3])
def test_wrong_photo_count_is_rejected_before_processing(count):
    with pytest.raises(ServiceError) as failure:
        prepare_item(uuid4(), tuple(ImageInput(uuid4(), b'invalid') for _ in range(count)), settings=config())
    assert failure.value.code == 'INVALID_IMAGE_COUNT'


def test_duplicate_photo_id_invalid_second_photo_and_file_size_are_rejected():
    first, second = pair()
    with pytest.raises(ServiceError, match='different'):
        prepare_item(uuid4(), (first, first), settings=config())
    with pytest.raises(ServiceError) as failure:
        prepare_item(uuid4(), (first, ImageInput(second.image_id, b'invalid')), settings=config())
    assert failure.value.code == 'INVALID_IMAGE'
    with pytest.raises(ServiceError) as failure:
        prepare_item(uuid4(), (first, second), settings=config(max_upload_bytes=10))
    assert failure.value.code == 'IMAGE_TOO_LARGE'


def test_empty_or_identity_only_output_cannot_complete_registration():
    prepared = prepare_item(uuid4(), pair(), settings=config())
    for identity in (None, 'plush_toy'):
        payload = dict.fromkeys(Features.model_fields)
        payload.update(category=identity, brand='unverified-brand' if identity else None)
        with pytest.raises(ServiceError) as failure:
            parse_item_output(prepared, json.dumps(payload))
        assert failure.value.code == 'INSUFFICIENT_VISUAL_FEATURES'


def test_invalid_json_or_extra_identity_fields_are_not_accepted_as_model_features():
    prepared = prepare_item(uuid4(), pair(), settings=config())
    for raw in ('not json', json.dumps({**dict.fromkeys(Features.model_fields), 'image_ids': ['forged']})):
        with pytest.raises(ServiceError) as failure:
            parse_item_output(prepared, raw)
        assert failure.value.code == 'INVALID_MODEL_OUTPUT'


def test_package_import_does_not_use_the_backends_app_namespace(tmp_path):
    # Simulate backend/app taking the normal top-level app name.
    backend_app = tmp_path/'backend'/'app'
    backend_app.mkdir(parents=True)
    (backend_app/'__init__.py').write_text('OWNER = "backend"\n')
    root = Path(__file__).resolve().parents[1]
    command = ('import app; assert app.OWNER == "backend"; '
               'from imageprocess import ImageInput, prepare_item, parse_item_output; '
               'import sys; assert "google.genai" not in sys.modules; assert "mlx_vlm" not in sys.modules; '
               'print(prepare_item.__module__)')
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(backend_app.parent), str(root.parent)])}
    result = subprocess.run([sys.executable, '-c', command], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'imageprocess.app.services.item_processing' in result.stdout


def test_prepare_only_cli_does_not_initialize_model_or_use_api(tmp_path, monkeypatch, capsys):
    from app.analyze_item import main
    paths = [tmp_path/'first.jpg', tmp_path/'second.jpg']
    for path in paths:
        path.write_bytes(jpeg('white'))
    service = Mock()
    monkeypatch.setattr('app.analyze_item.VisionService', service)
    output = tmp_path/'prepared.json'
    assert main([*map(str, paths), '--category', '필통', '--prepare-only', '--output', str(output)]) == 0
    service.assert_not_called()
    data = json.loads(output.read_text())
    assert data['inference_performed'] is False and len(data['image_ids']) == 2
    assert 'prepared_only' in capsys.readouterr().out


def test_two_photo_cli_sends_both_views_in_one_model_call(tmp_path, monkeypatch, capsys):
    from app.analyze_item import main
    paths = [tmp_path/'first.jpg', tmp_path/'second.jpg']
    for path in paths:
        path.write_bytes(jpeg('white'))
    service = Mock(usage={'total_token_count': 10})
    service.infer_images.return_value = json.dumps({**dict.fromkeys(Features.model_fields), 'color': ['white']})
    monkeypatch.setattr('app.analyze_item.VisionService', lambda _: service)
    assert main([*map(str, paths), '--category', '인형']) == 0
    service.infer_images.assert_called_once()
    assert len(service.infer_images.call_args.args[0]) == 2
    service.close.assert_called_once()
    assert json.loads(capsys.readouterr().out)['success'] is True


def test_published_backend_schema_matches_runtime_contract():
    path = Path(__file__).resolve().parents[1]/'docs'/'feature-schema.json'
    assert json.loads(path.read_text()) == Features.model_json_schema()
