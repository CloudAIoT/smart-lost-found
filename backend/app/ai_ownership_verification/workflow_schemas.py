"""Public inputs and server-only records for the development workflow."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator

from .schemas import Contract


FieldName = Literal[
    "category", "color", "shape", "surface_texture", "brand", "visible_text", "structure",
    "distinctive_features", "location", "material", "description",
]
Clue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ChoiceLabel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Decision = Literal["supported", "not_confirmed"]
FailureReason = Literal[
    "candidates_eliminated", "wrong_evidence_limit", "multiple_candidates",
    "verification_incomplete", "photo_unavailable", "photo_rejected",
    # Previously completed attempts can still contain these reasons.
    "no_verified_evidence", "verification_answers_mismatch",
]
PolicyVersion = Literal["development_v1", "development_v2", "development_v3", "development_v4", "development_v5",
                        "development_v6"]
SearchStage = Literal["queued", "clue_extraction", "candidate_search", "question_ready"]
# A search that ends without a question keeps its outcome on the attempt, so a poll can report it.
SearchStatus = Literal["needs_clues", "no_candidates", "search_limit", "failed"]


class SearchRequest(Contract):
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class SearchClues(Contract):
    category: Clue | None = None
    color: Clue | None = None
    shape: Clue | None = None
    surface_texture: Clue | None = None
    material: Clue | None = None
    brand: Clue | None = None
    visible_text: Clue | None = None
    structure: Clue | None = None
    distinctive_features: Clue | None = None
    description: Clue | None = None
    location: Clue | None = None


class SearchProgress(Contract):
    """What the background search is doing. Written by the worker, read by polling clients."""

    stage: SearchStage = "queued"
    started_at: datetime
    deadline_at: datetime
    finished_at: datetime | None = None
    searched_batches: int = 0
    total_batches: int | None = None
    error_code: str | None = None


class Distractors(Contract):
    labels: list[ChoiceLabel] = Field(max_length=8)


class KoreanLabels(Contract):
    labels: list[ChoiceLabel] = Field(min_length=1, max_length=5)


class SearchMatch(Contract):
    item: int = Field(ge=1)
    matched_fields: list[FieldName] = Field(min_length=1, max_length=10)


class SearchAssessment(Contract):
    item: int = Field(ge=1)
    decision: Literal["match", "different", "unknown"]
    matched_fields: list[FieldName] = Field(max_length=10)


class SimilarItems(Contract):
    results: list[SearchAssessment] = Field(max_length=100)


class Evidence(Contract):
    feature_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    field: FieldName
    value: ChoiceLabel
    group: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    visibility: Literal["public", "private"]
    verified: bool
    distinguishing: bool


class Candidate(Contract):
    item_id: UUID
    proof: list[Evidence]
    initial_matches: list[FieldName] = Field(default_factory=list)
    narrowing: dict[FieldName, list[ChoiceLabel]] = Field(default_factory=dict)
    matched_groups: list[str] = Field(default_factory=list)

    @field_validator("narrowing", mode="before")
    @classmethod
    def read_legacy_single_values(cls, value):
        if isinstance(value, dict):
            return {field: [labels] if isinstance(labels, str) else labels for field, labels in value.items()}
        return value


class Choice(Contract):
    id: UUID
    label: str
    value: str | None
    equivalent_values: list[str] = Field(default_factory=list)


class Question(Contract):
    id: UUID
    field: FieldName
    structure_part: str | None = None
    purpose: Literal["narrowing", "verification"]
    text: str
    options: list[Choice]


class RecordedAnswer(Contract):
    question_id: UUID
    field: FieldName
    structure_part: str | None = None
    purpose: Literal["narrowing", "verification"]
    option_id: UUID
    outcome: Literal["answered", "unknown"]
    matched_item_ids: list[UUID]
    conflicting_item_ids: list[UUID]


class AttemptState(Contract):
    # A pending attempt is stored before the search runs, so these three start empty.
    original_count: int = 0
    candidates: list[Candidate] = Field(default_factory=list)
    mentioned_fields: list[FieldName] = Field(default_factory=list)
    progress: SearchProgress | None = None
    search_status: SearchStatus | None = None
    policy_version: PolicyVersion = "development_v1"
    answers: list[RecordedAnswer] = Field(default_factory=list)
    current: Question | None = None
    photo_id: UUID | None = None
    photo_token: UUID | None = None
    decision: Decision | None = None
    failure_reason: FailureReason | None = None
    return_code_issued: bool = False

    @field_validator("decision", mode="before")
    @classmethod
    def read_legacy_decision(cls, value):
        return "not_confirmed" if value == "insufficient_evidence" else value


class AnswerRequest(Contract):
    question_id: UUID
    option_id: UUID


class PhotoConfirmationRequest(Contract):
    confirmed: bool = Field(strict=True)
    photo_token: UUID | None = None


class OptionView(Contract):
    id: UUID
    label: str


class QuestionView(Contract):
    id: UUID
    text: str
    options: list[OptionView]


class AttemptView(Contract):
    status: Literal[
        "pending", "needs_clues", "no_candidates", "refine_search", "search_limit", "question",
        "photo_confirmation", "completed",
    ]
    attempt_id: UUID | None = None
    initial_candidate_count: int | None = None
    question: QuestionView | None = None
    decision: Decision | None = None
    found_item_id: UUID | None = None
    # Only set while a search is running, or on the response that ends one.
    stage: SearchStage | None = None
    elapsed_ms: int | None = None
    retry_after_ms: int | None = None


class OwnershipResult(Contract):
    """Internal handoff; the downstream backend owns the return policy."""

    attempt_id: UUID
    item_id: UUID | None
    decision: Decision
    failure_reason: FailureReason | None = None
    policy_version: PolicyVersion = "development_v1"
    matched_evidence_groups: int
    answered_questions: int
