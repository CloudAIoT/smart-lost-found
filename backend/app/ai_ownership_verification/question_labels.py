"""Display labels for descriptions and literal inscriptions."""

import unicodedata


KOREAN_TERMS = {
    "color": {
        "black": "검은색", "white": "흰색", "navy": "남색", "navy blue": "남색", "blue": "파란색",
        "red": "빨간색", "pink": "분홍색", "yellow": "노란색", "green": "초록색", "brown": "갈색",
        "gray": "회색", "grey": "회색", "orange": "주황색", "purple": "보라색", "beige": "베이지색",
        "silver": "은색", "gold": "금색",
    },
    "shape": {
        "rectangle": "직사각형", "rectangular": "직사각형", "circle": "원형", "round": "원형",
        "square": "정사각형", "oval": "타원형", "cylinder": "원통형", "cylindrical": "원통형",
        "triangle": "삼각형", "heart-shaped": "하트형",
    },
    "material": {
        "fabric": "천", "leather": "가죽", "plastic": "플라스틱", "rubber": "고무", "metal": "금속",
        "paper": "종이", "glass": "유리", "wood": "나무", "silicone": "실리콘", "cotton": "면",
        "nylon": "나일론", "vinyl": "비닐",
    },
    "surface_texture": {"matte": "무광", "glossy": "유광", "smooth": "매끄러운 표면", "rough": "거친 표면"},
    "brand": {"apple": "애플", "carhartt": "칼하트", "nike": "나이키", "adidas": "아디다스", "logitech": "로지텍"},
    "structure": {"power switch": "전원 스위치", "dpi button": "감도 버튼", "usb-c port": "유에스비-시 단자"},
}


def is_korean_label(label: str) -> bool:
    label = unicodedata.normalize("NFKC", label)
    return (0 < len(label) <= 100 and all(
        char.isprintable() and (not char.isalpha() or unicodedata.name(char, "").startswith("HANGUL "))
        for char in label
    ) and any(char.isalpha() or char.isdecimal() for char in label))


def is_display_label(field: str, label: str) -> bool:
    if field == "visible_text":
        return bool(label.strip()) and len(label) <= 100 and label.isprintable()
    return is_korean_label(label)


def is_latin_inscription(label: str) -> bool:
    letters = [char for char in label if char.isalpha()]
    return bool(letters) and all(unicodedata.name(char, "").startswith("LATIN ") for char in letters)


def local_label(field: str, value: str) -> str | None:
    if field == "visible_text":
        return value if is_display_label(field, value) else None
    label = unicodedata.normalize("NFKC", value)
    if is_korean_label(label):
        return label
    return KOREAN_TERMS.get(field, {}).get(" ".join(label.casefold().split()))
