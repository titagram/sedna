"""Immutable structural contracts at the semantic-extraction boundary."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sedna.knowledge.schema import DocumentManifest

NonEmptyString = Annotated[str, Field(min_length=1)]
BlockIndex = Annotated[int, Field(ge=0)]


class BlockKind(StrEnum):
    """The source-level structure represented by a parsed block."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    CODE = "code"
    ORDERED_LIST_ITEM = "ordered_list_item"
    UNORDERED_LIST_ITEM = "unordered_list_item"
    TABLE = "table"
    BLOCKQUOTE = "blockquote"
    IMAGE = "image"
    HTML = "html"
    THEMATIC_BREAK = "thematic_break"


class ParsedBlock(BaseModel):
    """One structural Markdown block with an exact source line span."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: BlockKind
    text: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    level: int | None = Field(default=None, ge=1, le=6)
    language: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_structure(self) -> ParsedBlock:
        """Reject reversed spans and headings without their structural level."""
        if self.end_line < self.start_line:
            raise ValueError("end_line must not precede start_line")
        if self.kind is BlockKind.HEADING and self.level is None:
            raise ValueError("heading blocks require a level")
        if self.kind is not BlockKind.HEADING and self.level is not None:
            raise ValueError("only heading blocks may define a level")
        return self


class ParsedAsset(BaseModel):
    """A local or remote asset reference found in a source document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target: NonEmptyString
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    alt_text: str | None = None
    title: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_line_range(self) -> ParsedAsset:
        """Keep asset provenance line ranges ordered."""
        if self.end_line < self.start_line:
            raise ValueError("end_line must not precede start_line")
        return self


class ParsedDocument(BaseModel):
    """The loss-minimized structural representation of a source document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: NonEmptyString
    path: NonEmptyString
    blocks: tuple[ParsedBlock, ...] = ()
    assets: tuple[ParsedAsset, ...] = ()
    relationships: tuple[NonEmptyString, ...] = ()


class LogicalSegment(BaseModel):
    """Searchable text grouped over one or more source blocks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    block_indices: tuple[BlockIndex, ...] = Field(min_length=1)
    text: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    heading_path: tuple[NonEmptyString, ...] = ()
    assets: tuple[ParsedAsset, ...] = ()

    @model_validator(mode="after")
    def validate_source_span(self) -> LogicalSegment:
        """Require ordered lines and unique, increasing source block indices."""
        if self.end_line < self.start_line:
            raise ValueError("end_line must not precede start_line")
        if tuple(sorted(set(self.block_indices))) != self.block_indices:
            raise ValueError("block_indices must be unique and increasing")
        return self


class PreparedSource(BaseModel):
    """A manifest plus structural material ready for semantic extraction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest: DocumentManifest
    document: ParsedDocument
    segments: tuple[LogicalSegment, ...]

    @model_validator(mode="after")
    def validate_source_identity(self) -> PreparedSource:
        """Prevent a manifest from being paired with a different parsed source."""
        if self.manifest.source_id != self.document.source_id:
            raise ValueError("manifest and document source_id must match")
        if self.manifest.path != self.document.path:
            raise ValueError("manifest and document path must match")
        return self
