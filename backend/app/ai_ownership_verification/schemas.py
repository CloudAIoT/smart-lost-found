"""Experiment-only contracts. They do not prescribe the team's DB/API schema."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Feature(Contract):
    feature_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=100)
    value: str | None = Field(default=None, max_length=1000)
    visibility: Literal["public", "private"]
    verified: bool = False
    distinguishing: bool = False

    @property
    def usable_for_question(self) -> bool:
        return (
            self.visibility == "private"
            and self.verified
            and self.distinguishing
            and bool(self.value and self.value.strip())
        )


class ItemEvidence(Contract):
    item_id: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=100)
    features: list[Feature] = Field(max_length=20)

    @model_validator(mode="after")
    def unique_feature_ids(self):
        ids = [feature.feature_id for feature in self.features]
        if len(ids) != len(set(ids)):
            raise ValueError("feature_id must be unique within an item")
        return self

    @property
    def usable_features(self) -> list[Feature]:
        return [feature for feature in self.features if feature.usable_for_question]


class Question(Contract):
    # The application, rather than the model, will assign persistent question IDs.
    feature_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=300)


class QuestionBatch(Contract):
    status: Literal["ready", "insufficient_information"]
    questions: list[Question] = Field(max_length=3)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def consistent_status(self):
        if (self.status == "ready") != bool(self.questions):
            raise ValueError("ready requires questions; insufficient_information forbids them")
        ids = [question.feature_id for question in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("a feature may be questioned only once per batch")
        return self


class ExperimentCase(Contract):
    case_id: str = Field(min_length=1, max_length=80)
    description: str
    item: ItemEvidence
    review_points: list[str] = Field(min_length=1)
    # This is an expected test condition, never proof that the model is correct.
    expected_status: Literal["ready", "insufficient_information"]
