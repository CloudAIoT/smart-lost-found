"""Normalize known labels and check Korean text without calling a translation API."""
import re

from ..core.errors import ServiceError
from ..models.schemas import Features


# Compatibility aliases, not a closed category list. New Korean labels are accepted.
CATEGORY_NAMES = {
    'pencil_case': '필통', 'pencilcase': '필통', 'wallet': '지갑',
    'card_wallet': '카드 지갑', 'card_holder': '카드 지갑', 'cardholder': '카드 지갑',
    'bag': '가방', 'backpack': '백팩', 'tote_bag': '토트백', 'umbrella': '우산',
    'phone': '휴대폰', 'smartphone': '스마트폰', 'keys': '열쇠', 'key': '열쇠',
    'plush_toy': '봉제 인형', 'plush_doll': '봉제 인형', 'doll': '인형',
    'cap': '모자', 'hat': '모자', 'book': '책', 'charger': '충전기',
    'charging_adapter': '충전 어댑터', 'power_adapter': '전원 어댑터',
    'cosmetic': '화장품', 'cosmetics': '화장품', 'compact_powder': '콤팩트 파우더',
    'wireless_earbuds': '무선 이어폰', 'earbuds': '이어폰', 'earphones': '이어폰',
    'headphones': '헤드폰', 'earbuds_case': '무선 이어폰 케이스',
    'wireless_earbuds_case': '무선 이어폰 케이스', 'earphone_case': '이어폰 케이스',
    'glasses': '안경', 'sunglasses': '선글라스', 'laptop': '노트북',
    'clothing': '의류', 'shirt': '셔츠', 'pen': '펜', 'other': '기타',
}
COLOR_NAMES = {
    'black': '검정색', 'white': '흰색', 'red': '빨간색', 'orange': '주황색',
    'yellow': '노란색', 'green': '초록색', 'blue': '파란색', 'purple': '보라색',
    'pink': '분홍색', 'brown': '갈색', 'gray': '회색', 'grey': '회색',
    'beige': '베이지색', 'ivory': '아이보리색', 'navy': '남색', 'navy_blue': '남색',
    'khaki': '카키색', 'gold': '금색', 'golden': '금색', 'silver': '은색',
    'transparent': '투명', 'clear': '투명', 'multicolor': '여러 색', 'multicolored': '여러 색',
}
_KOREAN = re.compile(r'[가-힣ᄀ-ᇿㄱ-ㅣ]')


def korean_category_label(value: str) -> str:
    key = re.sub(r'[\s-]+', '_', value.casefold())
    return CATEGORY_NAMES.get(key, value)


def normalize_feature_language(features: Features) -> Features:
    """Require some Korean text in observations; preserve brand names and visible inscriptions."""
    data = features.model_dump()
    if data['category'] is not None:
        data['category'] = korean_category_label(data['category'])
    if data['color'] is not None:
        data['color'] = [COLOR_NAMES.get(re.sub(r'[\s-]+', '_', value.casefold()), value)
                         for value in data['color']]
    invalid = []
    for name in ('category', 'color', 'shape', 'surface_texture', 'structure', 'distinctive_features'):
        value = data[name]
        values = value if isinstance(value, list) else [value] if value is not None else []
        if any(_KOREAN.search(text) is None for text in values):
            invalid.append(name)
    if invalid:
        # Expose only field names, never model text or a guessed translation.
        raise ServiceError('INVALID_MODEL_OUTPUT',
                           'Expected Korean feature values. Invalid fields: ' + ', '.join(invalid) + '.', 502)
    return Features.model_validate(data)
