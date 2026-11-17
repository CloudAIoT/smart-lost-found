"""Versioned, deterministic search projection shared by ingestion and clue lookup.

Only exact normalized tokens are indexed; this is not a semantic embedding index.
Keep this v1 normalization stable: migrations also use it for legacy backfills.
"""
import re
import unicodedata

FIELDS = ('category', 'color', 'shape', 'surface_texture', 'brand', 'visible_text',
          'structure', 'distinctive_features', 'location')
LEGACY_FIELDS = ('category', 'color', 'brand', 'visible_text', 'distinctive_features')
ALIASES = {
    'category': {'필통': 'pencilcase', '지갑': 'wallet', '가방': 'bag', '백팩': 'bag', 'backpack': 'bag',
                 '우산': 'umbrella', '휴대폰': 'phone', '스마트폰': 'phone', '열쇠': 'keys',
                 '수첩': 'notebook', '공책': 'notebook', '노트': 'notebook', '책': 'book',
                 '안경': 'glasses', '카드지갑': 'wallet', 'cardcase': 'wallet',
                 'cardholder': 'wallet', 'cardwallet': 'wallet',
                 '이어폰': 'earbuds', '무선이어폰': 'earbuds', '이어폰케이스': 'earbuds',
                 'wirelessearbuds': 'earbuds', 'wirelessearbudcase': 'earbuds',
                 '모자': 'cap', '야구모자': 'cap', 'baseballcap': 'cap',
                 '충전기': 'charger', '화장품': 'cosmetic', '파우더': 'cosmetic', 'compactpowder': 'cosmetic',
                 '인형': 'plushtoy', '봉제인형': 'plushtoy',
                 '물병': 'waterbottle', '보온병': 'thermos', '책갈피': 'bookmark'},
    'color': {'검정색': 'black', '검정': 'black', '검은색': 'black', '검은': 'black', '블랙': 'black',
              '흰색': 'white', '하얀색': 'white', '화이트': 'white', '파랑': 'blue', '파란색': 'blue',
              '파란': 'blue', '빨간색': 'red', '빨간': 'red', '빨강': 'red', '남색': 'navy',
              '네이비': 'navy', 'darkblue': 'navy', '초록색': 'green', '녹색': 'green',
              '회색': 'gray', 'grey': 'gray', '노란색': 'yellow', '갈색': 'brown', '분홍색': 'pink'},
    'brand': {'칼하트': 'carhartt', '카하트': 'carhartt', '나이키': 'nike', '아디다스': 'adidas'},
}
UNKNOWN = {'', 'unknown', 'none', 'null', '미상', '모름', '확인불가', '없음'}


def normalized(field: str, value: str) -> str:
    text = re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', value).casefold())
    if field == 'location':
        text = re.sub(r'(?:근처에서|주변에서|부근에서|근처|주변|부근|에서|쪽)$', '', text)
    return ALIASES.get(field, {}).get(text, text)


def values(field: str, value: object) -> list[str]:
    items = value if isinstance(value, list) else [value]
    return [s for s in items[:100] if isinstance(s, str) and normalized(field, s) not in UNKNOWN]


def tokens(field: str, value: str) -> set[str]:
    result = {normalized(field, value)}
    if field not in ('category', 'color', 'brand'):
        result.update(normalized(field, part) for part in re.findall(r'[^\W_]+', value))
    return result - UNKNOWN


def project(features: dict | None, *, category: str | None = None,
            location: str | None = None, legacy: bool = False) -> dict[str, list[str]]:
    raw = features if isinstance(features, dict) else {}
    raw = raw.get('data', raw)
    raw = raw if isinstance(raw, dict) else {}
    allowed = LEGACY_FIELDS if legacy else FIELDS
    result = {}
    for field in FIELDS:
        entries = values(field, raw.get(field)) if field in allowed else []
        if field == 'category' and not entries:
            entries = values(field, category)
        if field == 'location':
            entries = values(field, location)
        selected = set().union(*(tokens(field, s) for s in entries)) if entries else set()
        if selected:
            result[field] = sorted(selected)
    return result
