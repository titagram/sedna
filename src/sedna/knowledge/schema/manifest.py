"""Immutable contracts describing ingested source documents and their assets."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from sedna.knowledge.schema.common import (
    DocumentType,
    ExtractionMetadata,
    IngestionStatus,
    KnowledgeRole,
    SourceQuality,
)

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonEmptyString = Annotated[str, Field(min_length=1)]


class AssetRef(BaseModel):
    """An immutable source asset identified by its canonical path and content hash."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: NonEmptyString
    sha256: Sha256


class DocumentManifest(BaseModel):
    """The reproducible ingestion record for one source document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: NonEmptyString
    path: NonEmptyString
    sha256: Sha256
    title: NonEmptyString
    language: NonEmptyString
    document_type: DocumentType
    knowledge_role: KnowledgeRole
    quality: SourceQuality
    parser_profile: NonEmptyString
    ingestion_status: IngestionStatus
    extraction: ExtractionMetadata
    assets: tuple[AssetRef, ...] = ()
    quality_reason_codes: tuple[NonEmptyString, ...] = ()
    emitted_artifact_ids: tuple[NonEmptyString, ...] = ()
    warnings: tuple[NonEmptyString, ...] = ()
    quarantine_reasons: tuple[NonEmptyString, ...] = ()
