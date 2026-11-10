"""Read registered features and submit every eligible item for semantic search."""

import re
import unicodedata
from itertools import batched

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import IntegrationError
from app.models import Category, FoundItem, Registration, StorageAssignment
from .workflow_ai import WorkflowAI
from .workflow_schemas import Candidate, Evidence, SearchClues


SEARCH_FIELDS = ("category", "color", "material", "brand", "location", "visible_text", "distinctive_features")
PROOF_FIELDS = ("distinctive_features", "visible_text", "material", "brand", "color")
NARROW_FIELDS = ("material", "brand", "color")
MAX_REGISTERED_ROWS = 500
SEARCH_BATCH_SIZE = 100
ALIASES = {
    "category": {
        "필통": "pencilcase", "지갑": "wallet", "가방": "bag", "백팩": "backpack",
        "우산": "umbrella", "휴대폰": "phone", "스마트폰": "phone", "열쇠": "keys",
    },
    "color": {
        "검은색": "black", "검정": "black", "검정색": "black", "검은": "black", "블랙": "black",
        "흰색": "white", "하얀색": "white", "화이트": "white", "파랑": "blue",
        "파란색": "blue", "파란": "blue", "빨간색": "red", "빨간": "red", "빨강": "red",
    },
    "material": {"천": "fabric", "천재질": "fabric", "가죽": "leather", "플라스틱": "plastic"},
    "brand": {"칼하트": "carhartt", "카하트": "carhartt", "나이키": "nike", "아디다스": "adidas"},
}
UNKNOWN = {"", "unknown", "none", "null", "미상", "모름", "확인불가", "없음"}


def normalized(field: str, value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    text = re.sub(r"[\W_]+", "", text)
    if field == "location":
        text = re.sub(r"(?:근처에서|주변에서|부근에서|근처|주변|부근|에서|쯤)$", "", text)
    return ALIASES.get(field, {}).get(text, text)


def matches(field: str, expected: str, clue: str) -> bool:
    a, b = normalized(field, expected), normalized(field, clue)
    if a in UNKNOWN or b in UNKNOWN:
        return False
    if field == "location" and min(len(a), len(b)) >= 2:
        return a in b or b in a
    return a == b


def values(field: str, value: object) -> list[str]:
    items = value if isinstance(value, list) else [value]
    return [text for text in items[:100] if isinstance(text, str) and normalized(field, text) not in UNKNOWN]


def stored_items():
    return (select(FoundItem, Registration, Category)
            .join(Registration, FoundItem.registration_id == Registration.id)
            .outerjoin(Category, Registration.primary_category_id == Category.id)
            .join(StorageAssignment, StorageAssignment.found_item_id == FoundItem.id)
            .where(FoundItem.status == "stored", Registration.status == "stored",
                   StorageAssignment.status == "stored", StorageAssignment.ended_at.is_(None)))


def read_facts(
    reg: Registration, category: Category | None,
) -> tuple[dict[str, list[str]], list[Evidence], dict[str, list[str]]]:
    envelope = reg.analysis_features if reg.analysis_status == "succeeded" and isinstance(reg.analysis_features, dict) else {}
    raw = envelope.get("data", envelope)
    raw = raw if isinstance(raw, dict) else {}
    facts = {field: values(field, raw.get(field)) for field in SEARCH_FIELDS}
    facts["location"] = values("location", reg.found_location)
    if category:
        facts["category"].extend(values("category", [category.code, category.name]))
    registered = {field: list(dict.fromkeys(values)) for field, values in facts.items() if values}

    # This is reviewed server-side metadata, separate from model-extracted fields.
    metadata = envelope.get("ownership_evidence")
    reviewed: list[Evidence] = []
    if isinstance(metadata, list):
        for entry in metadata[:20]:
            try:
                evidence = Evidence.model_validate(entry, strict=True)
            except ValidationError:
                continue
            if evidence.field in PROOF_FIELDS and all(ch.isprintable() for ch in evidence.value) and any(
                matches(evidence.field, value, evidence.value) for value in facts[evidence.field]
            ):
                reviewed.append(evidence)
    for field in SEARCH_FIELDS:
        if field in ("visible_text", "distinctive_features"):
            facts[field] = [ev.value for ev in reviewed if ev.field == field and ev.visibility == "public"]
        facts[field] = [value for value in facts[field] if not any(
            ev.field == field and ev.visibility == "private" and matches(field, ev.value, value) for ev in reviewed
        )]
    proof = []
    for field in PROOF_FIELDS:
        options = [ev for ev in reviewed if ev.field == field]
        # A prototype choice asks about one unambiguous value per feature type.
        if len(options) == 1:
            ev = options[0]
            if (ev.visibility == "private" and ev.verified and ev.distinguishing
                and not any(matches(ev.field, ev.value, value) for public in facts.values() for value in public)):
                proof.append(ev)
    return facts, proof, registered


def find_candidates(session: Session, clues: SearchClues, ai: WorkflowAI) -> tuple[str, list[Candidate]]:
    provided = {field: value for field, value in clues.model_dump(exclude_none=True).items()
                if normalized(field, value) not in UNKNOWN}
    searchable = {field: clue for field, clue in provided.items() if field in SEARCH_FIELDS}
    if not searchable:
        return "needs_clues", []
    rows = session.execute(stored_items().order_by(FoundItem.id).limit(MAX_REGISTERED_ROWS + 1)).all()
    if len(rows) > MAX_REGISTERED_ROWS:
        return "search_limit", []
    candidates = []
    features = []
    for item, reg, category in rows:
        facts, proof, registered = read_facts(reg, category)
        narrowing = {}
        for field in NARROW_FIELDS:
            if field in provided or len(facts[field]) != 1:
                continue
            label = facts[field][0].strip()
            if 1 <= len(label) <= 100 and all(ch.isprintable() for ch in label):
                narrowing[field] = label
        candidates.append(Candidate(
            item_id=item.id, narrowing=narrowing,
            proof=[ev for ev in proof if ev.field not in provided and not any(
                matches(ev.field, ev.value, clue) for clue in provided.values())]))
        features.append(registered)

    session.rollback()  # Release the read transaction before calling Gemini.
    shortlisted = []
    for batch in batched(enumerate(features, start=1), SEARCH_BATCH_SIZE):
        items = dict(batch)
        seen = set()
        for match in ai.similar_items(searchable, items):
            available = items.get(match.item)
            fields = set(match.matched_fields)
            if available is None or match.item in seen or not fields or not fields <= (available.keys() & searchable.keys()):
                raise IntegrationError("OWNERSHIP_AI_INVALID_MATCHES")
            seen.add(match.item)
            candidate = candidates[match.item - 1]
            candidate.initial_matches = sorted(fields)
            shortlisted.append(candidate)
    return ("question" if shortlisted else "no_candidates"), shortlisted
