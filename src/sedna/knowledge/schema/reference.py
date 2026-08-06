"""Immutable contracts for source-backed, transferable reference artifacts."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from sedna.knowledge.schema.common import ArtifactType, ReviewStatus, SourceRef

NonEmptyString = Annotated[str, Field(min_length=1)]


class ReferenceArtifact(BaseModel):
    """A concise, source-backed statement that can inform future cases."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: NonEmptyString
    statement: NonEmptyString
    artifact_type: ArtifactType
    applicable_situations: tuple[NonEmptyString, ...] = ()
    prerequisites: tuple[NonEmptyString, ...] = ()
    action_intent: NonEmptyString | None = None
    expected_evidence: tuple[NonEmptyString, ...] = ()
    success_implications: tuple[NonEmptyString, ...] = ()
    failure_implications: tuple[NonEmptyString, ...] = ()
    stop_implications: tuple[NonEmptyString, ...] = ()
    exceptions: tuple[NonEmptyString, ...] = ()
    warnings: tuple[NonEmptyString, ...] = ()
    capability_refs: tuple[NonEmptyString, ...] = ()
    observed_at: NonEmptyString | None = None
    review_status: ReviewStatus = ReviewStatus.AUTO_EXTRACTED
    source_refs: tuple[SourceRef, ...] = Field(min_length=1)
