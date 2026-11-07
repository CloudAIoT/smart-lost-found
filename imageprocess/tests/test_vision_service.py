import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from app.core.config import Settings
from app.core.errors import ServiceError
from app.services.vision_service import PROMPT, VisionService, parse_features


@pytest.mark.parametrize('raw', [
    None,
    {'category': 'wallet'},
    '[]',
    '{"category":"wallet","category":"bag"}',
    '{"category":NaN}',
    '{"category":Infinity}',
    '```json\n{}\n``` trailing explanation',
])
def test_ambiguous_or_non_json_model_output_is_rejected(raw):
    with pytest.raises(ServiceError) as error:
        parse_features(raw)
    assert error.value.code == 'INVALID_MODEL_OUTPUT'
    assert error.value.status == 502


@pytest.mark.parametrize('generation_result', ['{}', SimpleNamespace(text='{}')])
def test_mlx_model_load_is_cached_and_generation_receives_pil_image(monkeypatch, generation_result):
    model, processor, config = object(), object(), {'model_type': 'qwen2_5_vl'}
    load = Mock(return_value=(model, processor))
    load_config = Mock(return_value=config)
    template = Mock(return_value='formatted prompt')
    generate = Mock(return_value=generation_result)
    mlx_vlm = ModuleType('mlx_vlm')
    mlx_vlm.load = load
    mlx_vlm.generate = generate
    prompt_utils = ModuleType('mlx_vlm.prompt_utils')
    prompt_utils.apply_chat_template = template
    utils = ModuleType('mlx_vlm.utils')
    utils.load_config = load_config
    monkeypatch.setitem(sys.modules, 'mlx_vlm', mlx_vlm)
    monkeypatch.setitem(sys.modules, 'mlx_vlm.prompt_utils', prompt_utils)
    monkeypatch.setitem(sys.modules, 'mlx_vlm.utils', utils)
    settings = Settings(_env_file=None, inference_backend='mlx', max_tokens=128)
    service = VisionService(settings)

    service.load()
    service.load()
    photo = Image.new('RGB', (32, 32), 'white')
    assert service.infer(photo) == '{}'

    assert service.loaded is True
    load.assert_called_once_with(settings.model_name)
    load_config.assert_called_once_with(settings.model_name)
    template.assert_called_once_with(processor, config, PROMPT, num_images=1)
    generate.assert_called_once_with(
        model, processor, 'formatted prompt', image=[photo],
        max_tokens=128, temperature=0.0, verbose=False,
    )


def test_mock_mode_explicitly_returns_unknown_semantics():
    service = VisionService(Settings(_env_file=None, inference_backend='mock'))
    service.load()
    features = parse_features(service.infer(Image.new('RGB', (10, 10))))
    assert features.category == 'unknown'
    assert features.material == 'unknown'
    assert features.brand is None
    assert features.color == []
    assert features.visible_text == []
    assert 'Mock mode' in features.description


def test_unloaded_service_refuses_inference():
    service = VisionService(Settings(_env_file=None, inference_backend='mock'))
    with pytest.raises(ServiceError) as error:
        service.infer(Image.new('RGB', (10, 10)))
    assert error.value.code == 'MODEL_NOT_READY'
    assert error.value.status == 503


def _valid_features_payload():
    return {'category': 'unknown', 'color': ['black'], 'material': 'unknown',
            'brand': None, 'visible_text': [], 'distinctive_features': [],
            'description': 'A black item.'}


def test_single_material_array_is_normalized_without_changing_meaning():
    import json
    payload = _valid_features_payload()
    payload['material'] = ['unknown']
    features = parse_features(json.dumps(payload))
    assert features.material == 'unknown'
    assert features.model_dump() == {**payload, 'material': 'unknown'}


@pytest.mark.parametrize('material', [[], ['canvas', 'polyester'], [12], ['']])
def test_ambiguous_or_invalid_material_arrays_still_fail(material):
    import json
    payload = _valid_features_payload()
    payload['material'] = material
    with pytest.raises(ServiceError) as failure:
        parse_features(json.dumps(payload))
    assert failure.value.code == 'INVALID_MODEL_OUTPUT'
    assert 'material' in failure.value.message


def test_validation_error_identifies_field_without_exposing_model_content():
    import json
    payload = _valid_features_payload()
    payload['color'] = 'private-sensitive-value'
    with pytest.raises(ServiceError) as failure:
        parse_features(json.dumps(payload))
    assert 'color' in failure.value.message
    assert 'private-sensitive-value' not in failure.value.message


def test_category_prompt_does_not_leak_into_later_inferences():
    from app.services.vision_service import build_prompt
    service = VisionService(Settings(_env_file=None, inference_backend='mlx'))
    service.loaded = True
    service.model, service.processor, service.config = object(), object(), {}
    service._prompt = 'cached unhinted prompt'
    service._apply_chat_template = Mock(side_effect=lambda processor, config, prompt, **kwargs: prompt)
    service._generate = Mock(return_value='{}')
    image = Image.new('RGB', (32, 32))
    service.infer(image, category='필통')
    service.infer(image, category='umbrella')
    service.infer(image)
    prompts = [call.args[2] for call in service._generate.call_args_list]
    assert prompts == [build_prompt('필통'), build_prompt('umbrella'), 'cached unhinted prompt']
    assert 'umbrella' not in prompts[0]
    assert '필통' not in prompts[1]
    assert service._prompt == 'cached unhinted prompt'


def test_korean_category_uses_english_model_hint_without_changing_input_label():
    from app.services.vision_service import build_prompt, normalize_category
    assert normalize_category('필통') == '필통'
    assert 'category hint (JSON string): "pencil_case"' in build_prompt('필통')
    assert 'category hint (JSON string): "wallet"' in build_prompt('지갑')
    assert 'category hint (JSON string): "headphones"' in build_prompt('headphones')
