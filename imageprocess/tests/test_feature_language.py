import io
import json
from uuid import uuid4

import pytest
from PIL import Image

from app.core.config import Settings
from app.core.errors import ServiceError
from app.models.schemas import Features
from app.services.item_processing import ImageInput, parse_item_output, prepare_item
from app.services.vision_service import build_prompt, parse_features


def payload(**values):
    return {**dict.fromkeys(Features.model_fields), **values}


def test_korean_features_preserve_brand_original_text_and_nulls():
    data = payload(category='필통', color=['검정색'], shape='직사각형',
                   brand='Carhartt', visible_text=['carhartt', 'MADE IN USA'],
                   structure=['측면의 YKK 지퍼'], distinctive_features=['오른쪽 위의 파란색 원형 핀'])
    assert parse_features(json.dumps(data)).model_dump() == data


@pytest.mark.parametrize('category, expected', [('pencil_case', '필통'), ('WALLET', '지갑'),
                                               ('wireless earbuds', '무선 이어폰'), ('새로운 물건 종류', '새로운 물건 종류')])
def test_known_english_labels_are_normalized_without_a_closed_korean_category_list(category, expected):
    features = parse_features(json.dumps(payload(category=category, color=['WHITE', 'grey', 'silver'])))
    assert features.category == expected
    assert features.color == ['흰색', '회색', '은색']


@pytest.mark.parametrize('field, value', [('category', 'unmapped-item-kind'), ('color', ['unmapped-color']),
                                        ('shape', 'rectangular'), ('surface_texture', 'woven fabric'),
                                        ('structure', ['side zipper']), ('distinctive_features', ['blue round pin'])])
def test_untranslated_observations_are_rejected_without_echoing_model_text(field, value):
    with pytest.raises(ServiceError) as failure:
        parse_features(json.dumps(payload(**{field: value})))
    assert failure.value.code == 'INVALID_MODEL_OUTPUT' and failure.value.status == 502
    assert field in failure.value.message
    text = value[0] if isinstance(value, list) else value
    assert text not in failure.value.message


@pytest.mark.parametrize('count', [1, 2])
def test_both_photo_modes_request_korean_values_and_preserve_inscriptions(count):
    prompt = build_prompt('흰색 지갑', image_count=count)
    assert 'category hint (JSON string): "흰색 지갑"' in prompt
    assert 'specific equivalent category in Korean' in prompt
    assert 'color: a list of Korean color names' in prompt
    assert 'Preserve original brand names' in prompt
    assert 'The hint provides no evidence for color' in prompt


def test_two_photo_handoff_normalizes_values_and_preserves_english_input_and_ids():
    stream = io.BytesIO()
    Image.new('RGB', (96, 64), 'white').save(stream, format='JPEG')
    inputs = (ImageInput(uuid4(), stream.getvalue()), ImageInput(uuid4(), stream.getvalue()))
    registration = uuid4()
    prepared = prepare_item(registration, inputs, category='wallet', settings=Settings(_env_file=None))
    data = payload(category='wallet', color=['white'], brand='Apple',
                   visible_text=['Designed by Apple'], distinctive_features=['앞면의 사과 모양 음각 로고'])
    result = parse_item_output(prepared, json.dumps(data))
    assert result.schema_version == 'fixed_common_v3_ko'
    assert result.input_category == 'wallet'
    assert result.image_ids == tuple(value.image_id for value in inputs)
    assert result.registration_id == registration
    assert result.data.category == '지갑' and result.data.color == ['흰색']
    assert result.data.brand == 'Apple' and result.data.visible_text == data['visible_text']
