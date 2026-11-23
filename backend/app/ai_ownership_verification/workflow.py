"""One question at a time; the server owns the shortlist, progress, and decision."""

from collections import Counter
from datetime import timedelta, timezone
from functools import partial
from random import SystemRandom
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.errors import BusinessError, IntegrationError
from app.models import FoundItem, ItemImage, OwnershipAttempt, now
from .search import PROOF_FIELDS, UNKNOWN, find_candidates, normalized, read_facts, stored_items
from .structure_questions import POSITIONS, part_position, structure_positions
from .workflow_ai import WorkflowAI
from .workflow_schemas import (
    AnswerRequest, AttemptState, AttemptView, Candidate, Choice, Evidence, FieldName, OptionView,
    OwnershipResult, PhotoConfirmationRequest, Question, QuestionView, RecordedAnswer,
)


MAX_WRONG_GROUPS = 2
OPTION_COUNT = 5
POLICY_VERSION = "development_v5"
ATTEMPT_TTL = timedelta(minutes=20)
PROMPTS = {
    "distinctive_features": "물건에 있던 장식이나 구별되는 특징 중 기억나는 것을 골라 주세요.",
    "visible_text": "물건에 적힌 문구 중 기억나는 것을 골라 주세요.",
    "shape": "기억하는 물건의 형태를 골라 주세요.",
    "surface_texture": "기억하는 물건의 표면 질감을 골라 주세요.",
    "material": "기억하는 물건의 재질을 골라 주세요.",
    "brand": "기억하는 물건의 브랜드를 골라 주세요.",
    "color": "기억하는 물건의 색상을 골라 주세요.",
}


def question_values(candidate: Candidate, field: FieldName,
                    structure_part: str | None = None) -> tuple[Evidence | None, list[str]]:
    if structure_part is not None:
        position = structure_positions(candidate).get(structure_part)
        return (position[0], [position[1]]) if position else (None, [])
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


def registered_photo(session: Session, item_id: UUID, photo_id: UUID | None = None) -> ItemImage | None:
    stored = session.execute(stored_items().where(FoundItem.id == item_id)).first()
    if stored is None:
        return None
    _, registration, _ = stored
    photos = select(ItemImage).where(ItemImage.registration_id == registration.id)
    if photo_id is not None:
        photos = photos.where(ItemImage.id == photo_id)
    return session.scalar(photos.order_by(ItemImage.shot_order).limit(1))


def finish_questions(session: Session, state: AttemptState) -> None:
    state.decision = "not_confirmed"
    if len(state.candidates) > 1:
        state.failure_reason = "multiple_candidates"
        return

    candidate = state.candidates[0]
    required_fields = set()
    for evidence in candidate.proof:
        position = part_position(evidence.value) if evidence.field == "structure" else None
        required_fields.add((evidence.field, position[0] if position else None))
    answered_fields = {
        (answer.field, answer.structure_part) for answer in state.answers if answer.purpose == "verification"
        and (candidate.item_id in answer.matched_item_ids or candidate.item_id in answer.conflicting_item_ids)
    }
    if not required_fields <= answered_fields:
        state.failure_reason = "verification_incomplete"
        return

    photo = registered_photo(session, candidate.item_id)
    if photo is None:
        state.failure_reason = "photo_unavailable"
        return
    state.decision = None
    state.photo_id = photo.id
    state.photo_token = uuid4()


def advance(session: Session, state: AttemptState, ai: WorkflowAI) -> None:
    """Choose the next usable feature or decide from the recorded answers."""
    state.current = None
    if not state.candidates:
        state.decision = "not_confirmed"
        state.failure_reason = state.failure_reason or "candidates_eliminated"
        return
    used_questions = {(answer.field, answer.structure_part) for answer in state.answers}
    questions: dict[tuple[FieldName, str | None], list[tuple[Evidence | None, list[str]]]] = {}
    for field in PROOF_FIELDS:
        if field in state.mentioned_fields or (field, None) in used_questions:
            continue
        if field != "structure":
            questions[field, None] = [question_values(candidate, field) for candidate in state.candidates]
            continue
        for candidate in state.candidates:
            for part, (evidence, position) in structure_positions(candidate).items():
                if (field, part) not in used_questions:
                    questions.setdefault((field, part), []).append((evidence, [position]))

    choices = []
    for (field, part), candidate_values in questions.items():
        labels, counts, coverage = {}, Counter(), 0
        verification = False
        for evidence, values in candidate_values:
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
        choices.append(((split, coverage, verification), field, part, labels))
    if not choices:
        finish_questions(session, state)
        return

    rank, field, part, labels = max(choices, key=lambda entry: entry[0])
    verification = rank[2]
    if part is not None:
        question_text = f"{part}의 위치는 어디에 가장 가까웠나요?"
        labels = {normalized(field, position): position for position in POSITIONS}
    else:
        question_text = PROMPTS[field]
    if part is None and len(labels) < OPTION_COUNT:
        alternatives = ai.distractors(field, list(labels.values()), OPTION_COUNT - len(labels),
                                      validate=partial(select_distractors, field, labels, state.candidates))
        labels.update((normalized(field, label), label) for label in alternatives)
    options = [Choice(id=uuid4(), label=label, value=value) for value, label in labels.items()]
    SystemRandom().shuffle(options)
    state.current = Question(id=uuid4(), field=field, structure_part=part,
                             purpose="verification" if verification else "narrowing",
                             text=question_text, options=options)


def start(session: Session, description: str, ai: WorkflowAI) -> AttemptView:
    clues = ai.extract(description)
    status, candidates = find_candidates(session, clues, ai)
    if status != "question":
        return AttemptView(status=status)
    state = AttemptState(original_count=len(candidates), candidates=candidates, policy_version=POLICY_VERSION,
                         mentioned_fields=list(clues.model_dump(exclude_none=True)))
    advance(session, state, ai)
    attempt_id = uuid4()
    session.add(OwnershipAttempt(id=attempt_id, expires_at=now() + ATTEMPT_TTL, version=1,
                                 state=state.model_dump(mode="json")))
    session.commit()
    return view(attempt_id, state)


def view(attempt_id: UUID, state: AttemptState) -> AttemptView:
    question = state.current
    status = "question"
    if state.decision:
        status = "completed"
    elif state.photo_id:
        status = "photo_confirmation"
    return AttemptView(
        status=status,
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


def pending_photo_attempt(session: Session, attempt_id: UUID) -> tuple[OwnershipAttempt, AttemptState]:
    row = load_attempt(session, attempt_id)
    state = AttemptState.model_validate(row.state)
    if state.return_code_issued:
        raise BusinessError("OWNERSHIP_RETURN_CODE_ALREADY_ISSUED")
    if state.decision or not state.photo_id or not state.photo_token or len(state.candidates) != 1:
        raise BusinessError("OWNERSHIP_PHOTO_NOT_PENDING")
    return row, state


def pending_photo(session: Session, state: AttemptState) -> ItemImage:
    photo = registered_photo(session, state.candidates[0].item_id, state.photo_id)
    if photo is None:
        raise BusinessError("OWNERSHIP_PHOTO_UNAVAILABLE", 404)
    return photo


def confirm_photo(session: Session, attempt_id: UUID, body: PhotoConfirmationRequest) -> AttemptView:
    row, state = pending_photo_attempt(session, attempt_id)
    if body.confirmed:
        if body.photo_token != state.photo_token:
            raise BusinessError("OWNERSHIP_PHOTO_LOAD_REQUIRED")
        pending_photo(session, state)
        state.decision = "supported"
    else:
        state.decision = "not_confirmed"
        state.failure_reason = "photo_rejected"

    changed = session.execute(update(OwnershipAttempt).where(
        OwnershipAttempt.id == attempt_id, OwnershipAttempt.version == row.version, OwnershipAttempt.expires_at > now()
    ).values(version=row.version + 1, state=state.model_dump(mode="json"))
      .execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        session.rollback()
        pending_photo_attempt(session, attempt_id)
        raise BusinessError("OWNERSHIP_PHOTO_NOT_PENDING")
    return view(attempt_id, state)


def record_answer(state: AttemptState, answer: AnswerRequest) -> None:
    question = state.current
    if not question or state.decision or question.id != answer.question_id:
        raise BusinessError("OWNERSHIP_QUESTION_NOT_CURRENT")
    choice = next((option for option in question.options if option.id == answer.option_id), None)
    if choice is None:
        raise BusinessError("OWNERSHIP_OPTION_NOT_FOUND", 422)
    survivors, matched, conflicting = [], [], []
    reached_wrong_limit = False
    for candidate in state.candidates:
        evidence, expected = question_values(candidate, question.field, question.structure_part)
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
                else:
                    reached_wrong_limit = True
    state.candidates = survivors
    if not survivors and reached_wrong_limit:
        state.failure_reason = "wrong_evidence_limit"
    state.answers.append(RecordedAnswer(question_id=question.id, field=question.field, option_id=choice.id,
                                        purpose=question.purpose, structure_part=question.structure_part,
                                        outcome="unknown" if choice.value is None else "answered",
                                        matched_item_ids=matched, conflicting_item_ids=conflicting))


def answer(session: Session, attempt_id: UUID, body: AnswerRequest, ai: WorkflowAI) -> AttemptView:
    row = load_attempt(session, attempt_id)
    version, state = row.version, AttemptState.model_validate(row.state)
    record_answer(state, body)
    session.rollback()
    advance(session, state, ai)
    changed = session.execute(update(OwnershipAttempt).where(
        OwnershipAttempt.id == attempt_id, OwnershipAttempt.version == version, OwnershipAttempt.expires_at > now()
    ).values(version=version + 1, state=state.model_dump(mode="json"))
      .execution_options(synchronize_session=False))
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
        if any(ev not in current_proof for ev in candidate.proof):
            raise BusinessError("OWNERSHIP_EVIDENCE_CHANGED")
    return OwnershipResult(attempt_id=attempt.id, item_id=candidate.item_id if candidate else None,
                           decision=state.decision, failure_reason=state.failure_reason,
                           policy_version=state.policy_version,
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
    if not attempt.state.get("photo_id"):
        raise BusinessError("OWNERSHIP_ATTEMPT_RESTART_REQUIRED")
    changed = session.execute(update(OwnershipAttempt).where(
        OwnershipAttempt.id == attempt_id, OwnershipAttempt.version == attempt.version,
        OwnershipAttempt.expires_at > now(),
    ).values(version=attempt.version + 1, state={**attempt.state, "return_code_issued": True})
      .execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        load_attempt(session, attempt_id)
        raise BusinessError("OWNERSHIP_RETURN_CODE_ALREADY_ISSUED")
