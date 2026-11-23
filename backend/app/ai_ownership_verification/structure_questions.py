"""Build position questions from explicit descriptions of individual parts."""

import re
import unicodedata

from .workflow_schemas import Candidate, Evidence


POSITION_TERMS = {
    "가운데": ("가운데", "중앙", "정중앙"),
    "왼쪽": ("왼쪽", "좌측"),
    "오른쪽": ("오른쪽", "우측"),
    "앞쪽": ("앞쪽", "앞면", "전면"),
    "뒤쪽": ("뒤쪽", "뒷면", "후면"),
}
POSITIONS = tuple(POSITION_TERMS)
POSITION_ALIASES = {alias: label for label, aliases in POSITION_TERMS.items() for alias in aliases}
POSITION_PATTERN = "|".join(POSITION_ALIASES)
PRESENCE = r"있음|있다|위치함|위치해 있음|위치해 있다|달려 있음|달려 있다|배치됨"
PART_FIRST = re.compile(rf"(?P<part>.+?) (?P<position>{POSITION_PATTERN})(?:에)?(?: (?:{PRESENCE}))?")
POSITION_FIRST = re.compile(rf"(?P<position>{POSITION_PATTERN})에 (?P<part>.+?) (?:{PRESENCE})")
ENGLISH_POSITIONS = {
    "center": "가운데", "centre": "가운데", "middle": "가운데", "centered": "가운데", "centred": "가운데",
    "left": "왼쪽", "left side": "왼쪽", "right": "오른쪽", "right side": "오른쪽",
    "front": "앞쪽", "back": "뒤쪽", "rear": "뒤쪽",
}
ENGLISH_PART = r"[a-z][a-z0-9]*(?:[ -][a-z0-9]+){0,3}"
ENGLISH_PART_FIRST = re.compile(
    rf"(?P<part>{ENGLISH_PART}?) (?:(?:is )?(?:located|positioned|mounted) |is |sits )?"
    rf"(?:(?:at|in|on) (?:the )?)?(?P<position>{'|'.join(ENGLISH_POSITIONS)})"
)
ENGLISH_POSITION_FIRST = re.compile(rf"(?P<position>centered|centred) (?P<part>{ENGLISH_PART})")
PARTICLE = re.compile(r"(?<!잡)(?<!걸)이$|[가는은]$")
NON_PART_WORDS = {"있음", "있다", "위치", "및", "또는", "그리고", "아마", "and", "or", "not", "no", "maybe"}
WHEEL_NAMES = {"휠", "마우스휠", "스크롤휠", "마우스스크롤휠", "scrollwheel", "mousewheel", "mousescrollwheel"}


def part_position(value: str) -> tuple[str, str] | None:
    value = " ".join(unicodedata.normalize("NFKC", value).casefold().split()).removesuffix(".")
    match = PART_FIRST.fullmatch(value) or POSITION_FIRST.fullmatch(value)
    positions = POSITION_ALIASES
    if match is None:
        match = ENGLISH_PART_FIRST.fullmatch(value) or ENGLISH_POSITION_FIRST.fullmatch(value)
        positions = ENGLISH_POSITIONS
    if match is None:
        return None
    part = PARTICLE.sub("", match["part"])
    # A compound or a second position does not specify one unambiguous part.
    if not re.fullmatch(r"[가-힣a-z0-9]+(?:[ -][가-힣a-z0-9]+){0,3}", part) or any(
        word in NON_PART_WORDS or word.startswith(("없", "아니", "아닌", "아님"))
        or word.removesuffix("에") in POSITION_ALIASES or word in ENGLISH_POSITIONS for word in part.split()
    ):
        return None
    if part.replace(" ", "").replace("-", "") in WHEEL_NAMES:
        part = "스크롤 휠"
    return part, positions[match["position"]]


def structure_positions(candidate: Candidate) -> dict[str, tuple[Evidence | None, str]]:
    facts = [(None, value) for value in candidate.narrowing.get("structure", [])]
    facts.extend((evidence, evidence.value) for evidence in candidate.proof if evidence.field == "structure")
    positions: dict[str, dict[str, Evidence | None]] = {}
    for evidence, value in facts:
        location = part_position(value)
        if location:
            part, position = location
            positions.setdefault(part, {})[position] = evidence
    unambiguous = {}
    for part, choices in positions.items():
        if len(choices) == 1:
            (position, evidence), = choices.items()
            unambiguous[part] = (evidence, position)
    return unambiguous
