"""Immutable contracts for source-backed decision rules."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from sedna.knowledge.schema.common import ReviewStatus, SourceRef

NonEmptyString = Annotated[str, Field(min_length=1)]


class DecisionRule(BaseModel):
    """A source-backed rule connecting observations to an action intent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: NonEmptyString
    trigger_observations: tuple[NonEmptyString, ...] = Field(min_length=1)
    rationale: NonEmptyString
    action_intent: NonEmptyString
    prerequisites: tuple[NonEmptyString, ...] = ()
    expected_evidence: tuple[NonEmptyString, ...] = ()
    success_transitions: tuple[NonEmptyString, ...] = ()
    failure_transitions: tuple[NonEmptyString, ...] = ()
    stop_conditions: tuple[NonEmptyString, ...] = ()
    exceptions: tuple[NonEmptyString, ...] = ()
    alternative_hypotheses: tuple[NonEmptyString, ...] = ()
    capability_refs: tuple[NonEmptyString, ...] = ()
    contradicting_source_refs: tuple[SourceRef, ...] = ()
    review_status: ReviewStatus = ReviewStatus.DRAFT
    source_refs: tuple[SourceRef, ...] = Field(min_length=1)
