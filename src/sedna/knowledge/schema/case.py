"""Immutable contracts for chronological, provenance-backed case studies."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sedna.knowledge.schema.common import Origin, ReviewStatus, SourceQuality, SourceRef

NonEmptyString = Annotated[str, Field(min_length=1)]


class CaseState(BaseModel):
    """The relevant environment and access state at a point in a case."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    access: NonEmptyString
    environment: tuple[NonEmptyString, ...] = ()
    privileges: tuple[NonEmptyString, ...] = ()


class CaseHypothesis(BaseModel):
    """An explicit or inferred explanation considered during a case."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    statement: str = Field(min_length=1)
    origin: Origin


class CaseAction(BaseModel):
    """A selected action expressed as intent instead of a tool recipe."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: str = Field(min_length=1)
    capability_ref: str | None = None


class CaseEvidence(BaseModel):
    """A categorized observation supporting or constraining a case step."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: NonEmptyString
    origin: Origin
    category: NonEmptyString | None = None


class CaseStep(BaseModel):
    """One ordered, source-backed transition in a case study."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ordinal: int = Field(ge=1)
    state_before: CaseState
    observations: tuple[str, ...]
    hypotheses: tuple[CaseHypothesis, ...]
    selected_action: CaseAction
    evidence: tuple[CaseEvidence, ...]
    state_after: CaseState
    negative_evidence: tuple[str, ...] = ()
    transfer_conditions: tuple[str, ...] = ()
    case_specific_details: tuple[str, ...] = ()
    requires_validation: bool = True
    source_refs: tuple[SourceRef, ...] = Field(min_length=1)


class KnowledgeCase(BaseModel):
    """An ordered case study with transferable and local context kept distinct."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: NonEmptyString
    title: NonEmptyString
    starting_access: NonEmptyString
    steps: tuple[CaseStep, ...]
    outcome: NonEmptyString
    source_quality: SourceQuality
    review_status: ReviewStatus
    platform: NonEmptyString | None = None
    operating_system: NonEmptyString | None = None
    difficulty: NonEmptyString | None = None
    transferable_properties: tuple[NonEmptyString, ...] = ()
    non_transferable_properties: tuple[NonEmptyString, ...] = ()

    @model_validator(mode="after")
    def validate_unique_ordinals(self) -> KnowledgeCase:
        """Reject duplicate step ordinals so chronology remains unambiguous."""
        if len({step.ordinal for step in self.steps}) != len(self.steps):
            raise ValueError("case step ordinals must be unique")
        return self
