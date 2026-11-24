"""One question at a time; the server owns the shortlist, progress, and decision."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import partial
from random import SystemRandom
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.errors import BusinessError, IntegrationError
from app.models import FoundItem, ItemImage, OwnershipAttempt, now
from .question_labels import is_display_label, is_latin_inscription, local_label
from .search import PROOF_FIELDS, UNKNOWN, find_candidates, normalized, read_facts, stored_items
from .structure_questions import POSITIONS, part_position, structure_positions
from .workflow_ai import WorkflowAI
from .workflow_schemas import (
    AnswerRequest, AttemptState, AttemptView, Candidate, Choice, Evidence, FieldName, OptionView,
    OwnershipResult, PhotoConfirmationRequest, Question, QuestionView, RecordedAnswer, SearchProgress,
)


MAX_WRONG_GROUPS = 2
OPTION_COUNT = 5
POLICY_VERSION = "development_v6"
ATTEMPT_TTL = timedelta(minutes=20)
# How long a polling client should wait before asking again.
SEARCH_POLL_INTERVAL_MS = 700
# A queued job has not spent its budget yet, and a running one may overshoot it slightly.
QUEUE_GRACE = timedelta(seconds=15)
WORKER_GRACE = timedelta(seconds=5)
NON_ANSWERS = UNKNOWN | {"모르겠어요", "기억안남"}
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


def localized_labels(field: FieldName, values: list[str], ai: WorkflowAI) -> list[str]:
    if field == "visible_text":
        return values
    local, unresolved = {}, []
    for value in dict.fromkeys(values):
        if label := local_label(field, value):
            local[value] = label
        else:
            unresolved.append(value)
    if unresolved:
        translated = ai.localize(field, unresolved)
        if len(translated) != len(unresolved) or any(
            not is_display_label(field, label) or normalized(field, label) in NON_ANSWERS for label in translated
        ):
            raise IntegrationError("OWNERSHIP_AI_INVALID_OPTIONS")
        local.update(zip(unresolved, translated, strict=True))
    return [local[value] for value in values]


def select_distractors(field: FieldName, known_labels: dict[str, tuple[str, list[str]]], candidates: list[Candidate],
                       proposed: list[str]) -> list[str]:
    seen = set(known_labels)
    seen_values = {value for _, values in known_labels.values() for value in values}
    latin_only = field == "visible_text" and all(is_latin_inscription(label) for label, _ in known_labels.values())
    private_values = set()
    for candidate in candidates:
        for evidence in candidate.proof:
            private_values.add((evidence.field, normalized(evidence.field, evidence.value)))
            if label := local_label(evidence.field, evidence.value):
                private_values.add((evidence.field, normalized(evidence.field, label)))
    usable = []
    for original in proposed:
        label = local_label(field, original)
        if label is None or (latin_only and not is_latin_inscription(label)):
            continue
        key = normalized(field, label)
        if key in NON_ANSWERS or key in seen_values or normalized(field, original) in seen_values:
            continue
        if any(key in other or other in key for other in seen):
            continue
        # Avoid revealing other known private facts as distractors.
        if any((private_field, normalized(private_field, label)) in private_values
               or (private_field, normalized(private_field, original)) in private_values for private_field in PROOF_FIELDS):
            continue
        usable.append(label)
        seen.add(key)
        if len(seen) == OPTION_COUNT:
            return usable
    raise IntegrationError("OWNERSHIP_AI_INVALID_OPTIONS")


def question_options(field: FieldName, labels: dict[str, str], candidates: list[Candidate], ai: WorkflowAI) -> list[Choice]:
    localized = localized_labels(field, list(labels.values()), ai)
    options_by_label: dict[str, tuple[str, list[str]]] = {}
    for value, label in zip(labels, localized, strict=True):
        key = normalized(field, label)
        _, values = options_by_label.setdefault(key, (label, []))
        values.append(value)
    if len(options_by_label) < OPTION_COUNT:
        displayed = [label for label, _ in options_by_label.values()]
        alternatives = ai.distractors(field, displayed, OPTION_COUNT - len(displayed),
                                      validate=partial(select_distractors, field, options_by_label, candidates))
        for label in alternatives:
            key = normalized(field, label)
            options_by_label[key] = (label, [key])
    if len(options_by_label) != OPTION_COUNT or any(
        not is_display_label(field, label) for label, _ in options_by_label.values()
    ):
        raise IntegrationError("OWNERSHIP_AI_INVALID_OPTIONS")
    return [Choice(id=uuid4(), label=label, value=values[0], equivalent_values=values[1:])
            for label, values in options_by_label.values()]


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
        part_label, = localized_labels(field, [part], ai)
        question_text = f"{part_label}의 위치는 어디에 가장 가까웠나요?"
        labels = {normalized(field, position): position for position in POSITIONS}
    else:
        question_text = PROMPTS[field]
    options = question_options(field, labels, state.candidates, ai)
    SystemRandom().shuffle(options)
    state.current = Question(id=uuid4(), field=field, structure_part=part,
                             purpose="verification" if verification else "narrowing",
                             text=question_text, options=options)


def create_pending(session: Session, attempt_id: UUID, budget_seconds: float) -> None:
    """Store the attempt before the search runs, so the caller has an id to poll right away."""
    started = now()
    state = AttemptState(policy_version=POLICY_VERSION, progress=SearchProgress(
        stage="queued", started_at=started,
        deadline_at=started + timedelta(seconds=budget_seconds) + QUEUE_GRACE))
    session.add(OwnershipAttempt(id=attempt_id, expires_at=started + ATTEMPT_TTL, version=1,
                                 state=state.model_dump(mode="json")))
    session.commit()


def save_state(session: Session, attempt_id: UUID, state: AttemptState) -> None:
    session.execute(update(OwnershipAttempt).where(OwnershipAttempt.id == attempt_id)
                    .values(version=OwnershipAttempt.version + 1, state=state.model_dump(mode="json"))
                    .execution_options(synchronize_session=False))
    session.commit()


def run_search(session: Session, attempt_id: UUID, description: str, ai: WorkflowAI, *,
               budget_seconds: float, concurrency: int = 1) -> None:
    """Fill a pending attempt in place. Stage changes are stored so a poll can report progress."""
    state = AttemptState.model_validate(load_attempt(session, attempt_id).state)
    progress = state.progress or SearchProgress(stage="queued", started_at=now(), deadline_at=now())
    state.progress = progress
    # The budget starts when a worker picks the job up, not when it was queued.
    progress.deadline_at = now() + timedelta(seconds=budget_seconds) + WORKER_GRACE
    progress.stage = "clue_extraction"
    save_state(session, attempt_id, state)
    try:
        clues = ai.extract(description)
        progress.stage = "candidate_search"
        save_state(session, attempt_id, state)

        def searched(done: int, total: int) -> None:
            progress.searched_batches = done
            progress.total_batches = total
            save_state(session, attempt_id, state)

        status, candidates = find_candidates(session, clues, ai, concurrency=concurrency, on_batches=searched)
        if status != "question":
            state.search_status = status
        else:
            state.original_count = len(candidates)
            state.candidates = candidates
            state.mentioned_fields = list(clues.model_dump(exclude_none=True))
            advance(session, state, ai)
    except IntegrationError as error:
        # The stage is left where it broke: it names the failing call, and the code says why.
        state.search_status = "failed"
        progress.error_code = error.code
    else:
        progress.stage = "question_ready"
    progress.finished_at = now()
    save_state(session, attempt_id, state)


def fail_search(session: Session, attempt_id: UUID, code: str) -> None:
    """Close a pending attempt no worker will finish, so the next poll gets an answer."""
    row = session.get(OwnershipAttempt, attempt_id)
    if row is None:
        return
    state = AttemptState.model_validate(row.state)
    if state.progress is None or state.progress.finished_at is not None:
        return
    state.search_status = "failed"
    state.progress.error_code = code
    state.progress.finished_at = now()
    save_state(session, attempt_id, state)


def utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def elapsed_ms(progress: SearchProgress) -> int:
    return max(0, int((utc(progress.finished_at or now()) - utc(progress.started_at)).total_seconds() * 1000))


def view(attempt_id: UUID, state: AttemptState) -> AttemptView:
    progress = state.progress
    if progress is not None and progress.finished_at is None:
        if utc(progress.deadline_at) <= now():
            # No worker finished this one: the process restarted, or the provider outlasted the
            # budget without raising. Reported as a timeout rather than polled until the TTL.
            raise IntegrationError("OWNERSHIP_AI_TIMEOUT")
        return AttemptView(status="pending", attempt_id=attempt_id, stage=progress.stage,
                           elapsed_ms=elapsed_ms(progress), retry_after_ms=SEARCH_POLL_INTERVAL_MS)
    if state.search_status == "failed":
        raise IntegrationError((progress.error_code if progress else None) or "OWNERSHIP_AI_REQUEST_FAILED")
    spent = elapsed_ms(progress) if progress else None
    if state.search_status is not None:
        return AttemptView(status=state.search_status, attempt_id=attempt_id, elapsed_ms=spent,
                           stage=progress.stage if progress else None)
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
        stage=progress.stage if progress else None, elapsed_ms=spent,
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
    accepted = {choice.value, *choice.equivalent_values}
    survivors, matched, conflicting = [], [], []
    reached_wrong_limit = False
    for candidate in state.candidates:
        evidence, expected = question_values(candidate, question.field, question.structure_part)
        if choice.value is None or not expected:
            survivors.append(candidate)
        elif any(normalized(question.field, label) in accepted for label in expected):
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
