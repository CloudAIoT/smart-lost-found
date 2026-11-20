"""One question at a time; the server owns the shortlist, progress, and decision."""

from collections import Counter
from datetime import timedelta, timezone
from random import SystemRandom
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.errors import BusinessError, IntegrationError
from app.models import FoundItem, OwnershipAttempt, now
from .search import PROOF_FIELDS, UNKNOWN, find_candidates, normalized, read_facts, stored_items
from .workflow_ai import WorkflowAI
from .workflow_schemas import (
    AnswerRequest, AttemptState, AttemptView, Candidate, Choice, Evidence, FieldName, OptionView,
    OwnershipResult, Question, QuestionView, RecordedAnswer,
)


MAX_WRONG_GROUPS = 2
OPTION_COUNT = 5
POLICY_VERSION = "development_v3"
ATTEMPT_TTL = timedelta(minutes=20)
PROMPTS = {
    "distinctive_features": "물건에 있던 장식이나 구별되는 특징 중 기억나는 것을 골라 주세요.",
    "visible_text": "물건에 적힌 문구 중 기억나는 것을 골라 주세요.",
    "structure": "기억하는 물건의 구성이나 부품을 골라 주세요.",
    "shape": "기억하는 물건의 형태를 골라 주세요.",
    "surface_texture": "기억하는 물건의 표면 질감을 골라 주세요.",
    "material": "기억하는 물건의 재질을 골라 주세요.",
    "brand": "기억하는 물건의 브랜드를 골라 주세요.",
    "color": "기억하는 물건의 색상을 골라 주세요.",
}


def question_values(candidate: Candidate, field: FieldName) -> tuple[Evidence | None, list[str]]:
    evidence = next((ev for ev in candidate.proof if ev.field == field), None)
    return evidence, [evidence.value] if evidence else candidate.narrowing.get(field, [])


def select_distractors(field: FieldName, known_labels: dict[str, str], candidates: list[Candidate],
                       proposed: list[str]) -> list[str]:
    seen = set(known_labels)
    usable = []
    for label in proposed:
        key = normalized(field, label)
        if not all(ch.isprintable() for ch in label) or key in UNKNOWN or key in {"모르겠어요", "기억안남"}:
            continue
        if any(key in other or other in key for other in seen):
            continue
        # Avoid revealing other known private facts as distractors.
        if any(normalized(ev.field, ev.value) == normalized(ev.field, label)
               for candidate in candidates for ev in candidate.proof):
            continue
        usable.append(label)
        seen.add(key)
        if len(seen) == OPTION_COUNT:
            return usable
    raise IntegrationError("OWNERSHIP_AI_INVALID_OPTIONS")


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
        labels, counts, coverage = {}, Counter(), 0
        verification = False
        for candidate in state.candidates:
            evidence, values = question_values(candidate, field)
            if not values:
                continue
            coverage += 1
            verification = verification or evidence is not None
            options = {normalized(field, label): label for label in values}
            labels.update(options)
            counts.update(options.keys())
        if not 1 <= len(labels) <= OPTION_COUNT:
            continue
        if len(state.candidates) > 1 and not verification and min(counts.values()) == len(state.candidates):
            continue
        # With missing values retained, prefer the question that reduces the largest answer group most.
        split = coverage - max(counts.values())
        choices.append(((split, coverage, verification), field, labels))
    if not choices:
        state.decision = "insufficient_evidence"
        if len(state.candidates) == 1:
            candidate = state.candidates[0]
            confirmed = {answer.field for answer in state.answers
                         if answer.purpose == "verification" and candidate.item_id in answer.matched_item_ids}
            if candidate.proof and all(evidence.field in confirmed for evidence in candidate.proof):
                state.decision = "supported"
        return

    rank, field, labels = max(choices, key=lambda entry: entry[0])
    verification = rank[2]
    if len(labels) < OPTION_COUNT:
        proposed = ai.distractors(field, list(labels.values()), OPTION_COUNT - len(labels))
        alternatives = select_distractors(field, labels, state.candidates, proposed)
        labels.update((normalized(field, label), label) for label in alternatives)
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
        found_item_id=state.candidates[0].item_id if state.decision == "supported" else None,
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
    for candidate in state.candidates:
        evidence, expected = question_values(candidate, question.field)
        if choice.value is None or not expected:
            survivors.append(candidate)
        elif any(normalized(question.field, label) == choice.value for label in expected):
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
    return _ownership_result(session, load_attempt(session, attempt_id))


def _ownership_result(session: Session, attempt: OwnershipAttempt) -> OwnershipResult | None:
    state = AttemptState.model_validate(attempt.state)
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
    return OwnershipResult(attempt_id=attempt.id, item_id=candidate.item_id if candidate else None,
                           decision=state.decision, policy_version=state.policy_version,
                           matched_evidence_groups=len(candidate.matched_groups) if candidate else 0,
                           answered_questions=len(state.answers))


def authorize_return_code(session: Session, attempt_id: UUID, item_id: UUID) -> None:
    """Claim a successful attempt in the same transaction as code issuance."""
    attempt = load_attempt(session, attempt_id)
    result = _ownership_result(session, attempt)
    if result is None or result.decision != "supported" or result.item_id != item_id:
        raise BusinessError("OWNERSHIP_REQUIRED", 403)
    if attempt.state.get("return_code_issued"):
        raise BusinessError("OWNERSHIP_RETURN_CODE_ALREADY_ISSUED")
    changed = session.execute(update(OwnershipAttempt).where(
        OwnershipAttempt.id == attempt_id, OwnershipAttempt.version == attempt.version,
        OwnershipAttempt.expires_at > now(),
    ).values(version=attempt.version + 1, state={**attempt.state, "return_code_issued": True})
      .execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        load_attempt(session, attempt_id)
        raise BusinessError("OWNERSHIP_RETURN_CODE_ALREADY_ISSUED")
