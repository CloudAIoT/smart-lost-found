"""GIN-backed shortlist, then optional semantic comparison of at most 50 items."""
import hashlib
import json
from pydantic import ValidationError
from sqlalchemy import select, or_, bindparam, exists, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.errors import IntegrationError
from app.models import Category, FoundItem, Registration, StorageAssignment
from app.search_features import FIELDS, LEGACY_FIELDS, UNKNOWN, normalized, values, tokens
from .workflow_ai import WorkflowAI
from .workflow_schemas import Candidate, Evidence, SearchClues

SEARCH_FIELDS = FIELDS
PROOF_FIELDS = ('distinctive_features', 'visible_text', 'structure', 'surface_texture', 'shape', 'brand', 'color')
NARROW_FIELDS = ('shape', 'surface_texture', 'structure', 'brand', 'color')
MAX_CANDIDATES = 50
SIMPLE_FIELDS = {'category', 'color', 'brand'}


def matches(field, expected, clue):
    a, b = normalized(field, expected), normalized(field, clue)
    if a in UNKNOWN or b in UNKNOWN:
        return False
    if field == 'location' and min(len(a), len(b)) >= 2:
        return a in b or b in a
    return a == b


def stored_items():
    return (select(FoundItem, Registration, Category)
            .join(Registration, FoundItem.registration_id == Registration.id)
            .outerjoin(Category, Registration.primary_category_id == Category.id)
            .join(StorageAssignment, StorageAssignment.found_item_id == FoundItem.id)
            .where(FoundItem.status == 'stored', Registration.status == 'stored',
                   Registration.analysis_status == 'succeeded', StorageAssignment.status == 'stored',
                   StorageAssignment.ended_at.is_(None)))


def valid_evidence(reg, entry):
    try:
        ev = Evidence.model_validate(entry, strict=True)
        metadata = reg.analysis_metadata or {}
        if (metadata.get('schema_version') != 'fixed_common_v2' or not ev.analysis_id
                or ev.analysis_id != metadata.get('analysis_id') or not ev.reviewed_by or not ev.reviewed_at
                or ev.field not in PROOF_FIELDS or not all(ch.isprintable() for ch in ev.value)):
            return None
        raw = (reg.analysis_features or {}).get(ev.field)
        entries = raw if isinstance(raw, list) else [raw]
        field, index = ev.feature_id.split(':')
        if field != ev.field or not index.isdigit() or str(int(index)) != index or int(index) >= len(entries):
            return None
        return ev if entries[int(index)] == ev.value else None
    except (ValidationError, ValueError, TypeError, AttributeError):
        return None


def evidence_digest(reg):
    return hashlib.sha256(json.dumps(reg.ownership_evidence or [], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_facts(reg, category):
    envelope = reg.analysis_features if reg.analysis_status == 'succeeded' and isinstance(reg.analysis_features, dict) else {}
    raw = envelope.get('data', envelope)
    raw = raw if isinstance(raw, dict) else {}
    allowed = SEARCH_FIELDS if (reg.analysis_metadata or {}).get('schema_version') == 'fixed_common_v2' else LEGACY_FIELDS
    facts = {field: values(field, raw.get(field)) if field in allowed else [] for field in SEARCH_FIELDS}
    facts['location'] = values('location', reg.found_location)
    if category and not facts['category']:
        facts['category'] = values('category', category.name)
    registered = {field: list(dict.fromkeys(entries)) for field, entries in facts.items() if entries}
    reviewed = [ev for entry in (reg.ownership_evidence or [])[:20] if (ev := valid_evidence(reg, entry))]
    for field in SEARCH_FIELDS:
        if field in ('visible_text', 'distinctive_features', 'structure'):
            facts[field] = [ev.value for ev in reviewed if ev.field == field and ev.visibility == 'public']
        facts[field] = [value for value in facts[field] if not any(
            ev.field == field and ev.visibility == 'private' and matches(field, ev.value, value) for ev in reviewed)]
    proof = []
    for field in PROOF_FIELDS:
        options = [ev for ev in reviewed if ev.field == field]
        if len(options) == 1:
            ev = options[0]
            if (ev.visibility == 'private' and ev.verified and ev.distinguishing
                    and not any(matches(ev.field, ev.value, value) for public in facts.values() for value in public)):
                proof.append(ev)
    return facts, proof, registered


def containment(field, token, dialect):
    if dialect == 'postgresql':
        return Registration.search_features.op('@>')(bindparam(None, {field: [token]}, type_=JSONB))
    entries = func.json_each(Registration.search_features, '$.' + field).table_valued('value')
    return exists(select(1).select_from(entries).where(entries.c.value == token))


def candidate_query(clues, dialect):
    conditions = [containment(field, token, dialect) for field, clue in clues.items() for token in tokens(field, clue)]
    query = stored_items().where(or_(*conditions))
    # Known type conflicts cannot be rescued by common colors. NULL remains unknown.
    # Model-inferred brands are not reliable enough for a hard exclusion.
    for field in ('category',):
        if field in clues:
            missing = (Registration.search_features[field].is_(None) if dialect == 'postgresql'
                       else func.json_type(Registration.search_features, '$.' + field).is_(None))
            query = query.where(or_(missing,
                                   containment(field, normalized(field, clues[field]), dialect)))
    if set(clues) <= SIMPLE_FIELDS and 'color' in clues:
        missing = (Registration.search_features['color'].is_(None) if dialect == 'postgresql'
                   else func.json_type(Registration.search_features, '$.color').is_(None))
        query = query.where(or_(missing,
                               containment('color', normalized('color', clues['color']), dialect)))
    return query.order_by(FoundItem.id).limit(MAX_CANDIDATES + 1)


def find_candidates(session: Session, clues: SearchClues, ai: WorkflowAI):
    provided = {f: v for f, v in clues.model_dump(exclude_none=True).items() if normalized(f, v) not in UNKNOWN}
    if not provided:
        return 'needs_clues', []
    rows = session.execute(candidate_query(provided, session.bind.dialect.name)).all()
    if len(rows) > MAX_CANDIDATES:
        return 'refine_search', []
    candidates, features = [], {}
    for item, reg, category in rows:
        facts, proof, registered = read_facts(reg, category)
        narrowing = {f: facts[f][0].strip() for f in NARROW_FIELDS
                     if f not in provided and len(facts[f]) == 1 and 1 <= len(facts[f][0].strip()) <= 100
                     and all(ch.isprintable() for ch in facts[f][0])}
        candidates.append(Candidate(item_id=item.id, analysis_id=(reg.analysis_metadata or {}).get('analysis_id'),
            evidence_digest=evidence_digest(reg),
            narrowing=narrowing, proof=[ev for ev in proof if ev.field not in provided and not any(
                matches(ev.field, ev.value, clue) for clue in provided.values())]))
        features[len(candidates)] = registered
    session.rollback()
    if not candidates:
        return 'no_candidates', []
    if set(provided) <= SIMPLE_FIELDS:
        shortlisted = []
        for index, candidate in enumerate(candidates, 1):
            facts = features[index]
            matched = [f for f, clue in provided.items() if any(matches(f, v, clue) for v in facts.get(f, []))]
            conflicts = {f for f in provided if facts.get(f) and f not in matched}
            if matched and 'category' not in conflicts and not ('color' in conflicts and matched == ['category']):
                candidate.initial_matches = matched
                shortlisted.append(candidate)
    else:
        shortlisted, seen = [], set()
        for match in ai.similar_items(provided, features):
            available, fields = features.get(match.item), set(match.matched_fields)
            if available is None or match.item in seen or not fields or not fields <= (available.keys() & provided.keys()):
                raise IntegrationError('OWNERSHIP_AI_INVALID_MATCHES')
            seen.add(match.item)
            candidate = candidates[match.item - 1]
            candidate.initial_matches = sorted(fields)
            shortlisted.append(candidate)
    return ('question' if shortlisted else 'no_candidates'), shortlisted
