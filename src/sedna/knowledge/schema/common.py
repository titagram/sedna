"""Strict shared contracts for provenance and knowledge classification."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def _reject_final_flag_material(value: str) -> str:
    from sedna.knowledge.parsing.sanitize import sanitize_searchable_text

    if sanitize_searchable_text(value, (value,)) != value:
        raise ValueError("searchable text contains raw or encoded final flag material")
    return value


SearchableString = Annotated[str, AfterValidator(_reject_final_flag_material)]
SearchableNonEmptyString = Annotated[
    str,
    Field(min_length=1),
    AfterValidator(_reject_final_flag_material),
]


class DocumentType(StrEnum):
    """The structural category of a source document."""

    LESSON = "lesson"
    MACHINE_WALKTHROUGH = "machine_walkthrough"
    CHALLENGE_WALKTHROUGH = "challenge_walkthrough"
    CHEATSHEET_REFERENCE = "cheatsheet_reference"
    EXTERNAL_STUB = "external_stub"
    EXCLUDED = "excluded"


class KnowledgeRole(StrEnum):
    """The epistemic lane occupied by an extracted record."""

    REFERENCE = "reference"
    CASE_STUDY = "case_study"
    NEGATIVE_CASE = "negative_case"


class ArtifactType(StrEnum):
    """The type of a canonical knowledge artifact."""

    CONCEPT = "concept"
    METHODOLOGY = "methodology"
    DECISION_RULE = "decision_rule"
    CASE = "case"
    CASE_STEP = "case_step"
    NEGATIVE_EVIDENCE = "negative_evidence"
    ANTI_PATTERN = "anti_pattern"


class Origin(StrEnum):
    """How a record or field was obtained."""

    EXPLICIT = "explicit"
    INFERRED = "inferred"
    DERIVED = "derived"


class ReviewStatus(StrEnum):
    """The human-review state of an artifact."""

    AUTO_EXTRACTED = "auto_extracted"
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"


class Generalizability(StrEnum):
    """The intended degree of transfer beyond a source environment."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SourceQuality(StrEnum):
    """The amount and reliability of usable source material."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    MINIMAL = "minimal"
    UNUSABLE = "unusable"


class IngestionStatus(StrEnum):
    """The disposition of a source in an ingestion run."""

    ACCEPTED = "accepted"
    EXCLUDED = "excluded"
    QUARANTINED = "quarantined"


class SourceLocation(BaseModel):
    """A precise, immutable source span or asset reference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    page: int | None = Field(default=None, ge=1)
    section: str | None = Field(default=None, min_length=1)
    asset_path: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_location(self) -> SourceLocation:
        """Require one addressable location and a non-reversed line span."""
        if not any((self.start_line, self.page, self.section, self.asset_path)):
            raise ValueError("at least one source location must be provided")
        if self.start_line and self.end_line and self.end_line < self.start_line:
            raise ValueError("end_line must not precede start_line")
        return self


class SourceRef(BaseModel):
    """An immutable reference to an exact location in a source file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    location: SourceLocation


class ExtractionMetadata(BaseModel):
    """Version identifiers needed to reproduce a deterministic extraction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(min_length=1)
    parser_id: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    extractor_id: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    prompt_id: str | None = Field(default=None, min_length=1)
    prompt_version: str | None = Field(default=None, min_length=1)
    model_id: str | None = Field(default=None, min_length=1)
    model_version: str | None = Field(default=None, min_length=1)


class CanonicalArtifactMetadata(BaseModel):
    """Epistemic identity and provenance required on every canonical artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_type: ArtifactType
    knowledge_role: KnowledgeRole
    origin: Origin
    review_status: ReviewStatus
    generalizability: Generalizability
    source_refs: tuple[SourceRef, ...] = Field(min_length=1)
    extraction: ExtractionMetadata
