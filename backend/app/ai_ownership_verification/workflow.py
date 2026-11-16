"""One question at a time; the server owns the shortlist, progress, and decision."""

from datetime import timedelta, timezone
from random import SystemRandom
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.errors import BusinessError, IntegrationError
from app.models import FoundItem, OwnershipAttempt, now
from .search import NARROW_FIELDS, PROOF_FIELDS, UNKNOWN, find_candidates, normalized, read_facts, stored_items
from .workflow_ai import WorkflowAI
from .workflow_schemas import (
    AnswerRequest, AttemptState, AttemptView, Candidate, Choice, FieldName, OptionView,
    OwnershipResult, Question, QuestionView, RecordedAnswer,
)


MAX_WRONG_GROUPS = 2
OPTION_COUNT = 5
POLICY_VERSION = "development_v2"
ATTEMPT_TTL = timedelta(minutes=20)
PROMPTS = {
    "distinctive_features": "물건에 있던 장식이나 구별되는 특징 중 기억나는 것을 골라 주세요.",
    "visible_text": "물건에 적힌 문구 중 기억나는 것을 골라 주세요.",
    "material": "기억하는 물건의 재질을 골라 주세요.",
    "brand": "기억하는 물건의 브랜드를 골라 주세요.",
    "color": "기억하는 물건의 색상을 골라 주세요.",
}


def option_values(candidates: list[Candidate], field: FieldName) -> dict[str, str]:
    options = {}
    for candidate in candidates:
        for feature in candidate.proof:
            if feature.field == field:
                options.setdefault(normalized(field, feature.value), feature.value)
    return options


def advance(state: AttemptState, ai: WorkflowAI) -> None:
    """Choose the next usable feature or decide from the recorded answers."""
    state.current = None
    if not state.candidates:
        state.decision = "not_confirmed"
        return
    used_fields = set(state.mentioned_fields) | {answer.field for answer in state.answers}
    choices = []
    for field in PROOF_FIELDS:
        if field in used_fields:
            continue
        labels = option_values(state.candidates, field)
        if 1 <= len(labels) <= OPTION_COUNT:
            coverage = sum(any(ev.field == field for ev in candidate.proof) for candidate in state.candidates)
            choices.append((len(labels), coverage, True, field, labels))
    if len(state.candidates) > 1:
        for field in NARROW_FIELDS:
            if field in used_fields or option_values(state.candidates, field):
                continue
            known = [candidate.narrowing[field] for candidate in state.candidates if field in candidate.narrowing]
            labels = {normalized(field, label): label for label in known}
            if 2 <= len(labels) <= OPTION_COUNT:
                choices.append((len(labels), len(known), False, field, labels))
    if not choices:
        state.decision = "insufficient_evidence"
        if len(state.candidates) == 1:
            candidate = state.candidates[0]
            confirmed = {answer.field for answer in state.answers
                         if answer.purpose == "verification" and candidate.item_id in answer.matched_item_ids}
            if candidate.proof and all(evidence.field in confirmed for evidence in candidate.proof):
                state.decision = "supported"
        return

    _, _, verification, field, labels = max(choices, key=lambda entry: entry[:3])
    if len(labels) < OPTION_COUNT:
        for label in ai.distractors(field, list(labels.values()), OPTION_COUNT - len(labels)):
            key = normalized(field, label)
            if not all(ch.isprintable() for ch in label) or key in UNKNOWN or key in {"모르겠어요", "기억안남"}:
                continue
            if any(key in known or known in key for known in labels):
                continue
            # Avoid revealing other known private facts as distractors.
            if any(normalized(ev.field, ev.value) == normalized(ev.field, label)
                   for candidate in state.candidates for ev in candidate.proof):
                continue
            labels[key] = label
            if len(labels) == OPTION_COUNT:
                break
    if len(labels) != OPTION_COUNT:
        raise IntegrationError("OWNERSHIP_AI_INVALID_OPTIONS")
    options = [Choice(id=uuid4(), label=label, value=value) for value, label in labels.items()]
    SystemRandom().shuffle(options)
    state.current = Question(id=uuid4(), field=field, purpose="verification" if verification else "narrowing",
                             text=PROMPTS[field], options=options)


def start(session: Session, description: str, ai: WorkflowAI) -> AttemptView:
    clues = ai.extract(description)
    status, candidates = find_candidates(session, clues, ai)
    if status != "question":
        return AttemptView(status=status)
    state = AttemptState(original_count=len(candidates), candidates=candidates, policy_version=POLICY_VERSION,
                         mentioned_fields=list(clues.model_dump(exclude_none=True)))
    advance(state, ai)
    attempt_id = uuid4()
    session.add(OwnershipAttempt(id=attempt_id, expires_at=now() + ATTEMPT_TTL, version=1,
                                 state=state.model_dump(mode="json")))
    session.commit()
    return view(attempt_id, state)


def view(attempt_id: UUID, state: AttemptState) -> AttemptView:
    question = state.current
    return AttemptView(
        status="completed" if state.decision else "question",
        attempt_id=attempt_id, initial_candidate_count=state.original_count,
        decision=state.decision,
        question=QuestionView(id=question.id, text=question.text,
                              options=[OptionView(id=option.id, label=option.label) for option in question.options])
        if question else None,
    )


def load_attempt(session: Session, attempt_id: UUID) -> OwnershipAttempt:
    row = session.get(OwnershipAttempt, attempt_id, populate_existing=True)
    if row is None:
        raise BusinessError("OWNERSHIP_ATTEMPT_NOT_FOUND", 404)
    expiry = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if expiry <= now():
        raise BusinessError("OWNERSHIP_ATTEMPT_EXPIRED", 410)
    if not row.state.get("decision") and row.state.get("policy_version") != POLICY_VERSION:
        raise BusinessError("OWNERSHIP_ATTEMPT_RESTART_REQUIRED")
    return row


def get_view(session: Session, attempt_id: UUID) -> AttemptView:
    return view(attempt_id, AttemptState.model_validate(load_attempt(session, attempt_id).state))


def record_answer(state: AttemptState, answer: AnswerRequest) -> None:
    question = state.current
    if not question or state.decision or question.id != answer.question_id:
        raise BusinessError("OWNERSHIP_QUESTION_NOT_CURRENT")
    choice = next((option for option in question.options if option.id == answer.option_id), None)
    if choice is None:
        raise BusinessError("OWNERSHIP_OPTION_NOT_FOUND", 422)
    survivors, matched, conflicting = [], [], []
    registered_narrowing_answer = question.purpose == "narrowing" and any(
        question.field in candidate.narrowing
        and normalized(question.field, candidate.narrowing[question.field]) == choice.value
        for candidate in state.candidates
    )
    for candidate in state.candidates:
        evidence = next((ev for ev in candidate.proof if ev.field == question.field), None) if question.purpose == "verification" else None
        expected = evidence.value if evidence else (
            candidate.narrowing.get(question.field) if question.purpose == "narrowing" else None)
        if choice.value is None or expected is None:
            survivors.append(candidate)
        elif normalized(question.field, expected) == choice.value:
            matched.append(candidate.item_id)
            if evidence and evidence.group not in candidate.matched_groups:
                candidate.matched_groups.append(evidence.group)
            survivors.append(candidate)
        else:
            conflicting.append(candidate.item_id)
            if evidence:
                wrong_fields = {answer.field for answer in state.answers if answer.purpose == "verification"
                                and candidate.item_id in answer.conflicting_item_ids}
                wrong_fields.add(evidence.field)
                wrong_groups = {ev.group for ev in candidate.proof if ev.field in wrong_fields}
                if len(wrong_groups) < MAX_WRONG_GROUPS:
                    survivors.append(candidate)
            elif not registered_narrowing_answer:
                survivors.append(candidate)
    state.candidates = survivors
    state.answers.append(RecordedAnswer(question_id=question.id, field=question.field, option_id=choice.id,
                                        purpose=question.purpose,
                                        outcome="unknown" if choice.value is None else "answered",
                                        matched_item_ids=matched, conflicting_item_ids=conflicting))


def answer(session: Session, attempt_id: UUID, body: AnswerRequest, ai: WorkflowAI) -> AttemptView:
    row = load_attempt(session, attempt_id)
    version, state = row.version, AttemptState.model_validate(row.state)
    record_answer(state, body)
    session.rollback()
    advance(state, ai)
    changed = session.execute(update(OwnershipAttempt).where(
        OwnershipAttempt.id == attempt_id, OwnershipAttempt.version == version, OwnershipAttempt.expires_at > now()
    ).values(version=version + 1, state=state.model_dump(mode="json")))
    if changed.rowcount != 1:
        session.rollback()
        load_attempt(session, attempt_id)
        raise BusinessError("OWNERSHIP_QUESTION_NOT_CURRENT")
    session.commit()
    return view(attempt_id, state)


def get_ownership_result(session: Session, attempt_id: UUID) -> OwnershipResult | None:
    """Sunjae's in-process integration point, not a public result-detail API."""
    state = AttemptState.model_validate(load_attempt(session, attempt_id).state)
    if not state.decision:
        return None
    candidate = state.candidates[0] if len(state.candidates) == 1 else None
    if state.decision == "supported":
        item = None if candidate is None else session.execute(
            stored_items().where(FoundItem.id == candidate.item_id)
        ).first()
        if item is None:
            raise BusinessError("OWNERSHIP_ITEM_NO_LONGER_AVAILABLE")
        _, registration, category = item
        _, current_proof, _ = read_facts(registration, category)
        matched_fields = {entry.field for entry in state.answers
                          if entry.purpose == "verification" and candidate.item_id in entry.matched_item_ids}
        if any(ev not in current_proof for ev in candidate.proof if ev.field in matched_fields):
            raise BusinessError("OWNERSHIP_EVIDENCE_CHANGED")
    return OwnershipResult(attempt_id=attempt_id, item_id=candidate.item_id if candidate else None,
                           decision=state.decision, policy_version=state.policy_version,
                           matched_evidence_groups=len(candidate.matched_groups) if candidate else 0,
                           answered_questions=len(state.answers))
