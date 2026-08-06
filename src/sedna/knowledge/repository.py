"""Deterministic, atomic persistence for canonical ingestion records."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path, PurePath
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from sedna.knowledge.schema import DocumentManifest, ExtractionMetadata


def _validate_stable_id(value: str) -> str:
    if (
        value in {".", ".."}
        or "\x00" in value
        or "/" in value
        or "\\" in value
        or Path(value).is_absolute()
    ):
        raise ValueError("identifier must be a safe path segment")
    return value


StableId = Annotated[str, Field(min_length=1), AfterValidator(_validate_stable_id)]
NonEmptyString = Annotated[str, Field(min_length=1)]


def _sorted_unique(values: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(sorted(values))
    if len(set(normalized)) != len(normalized):
        raise ValueError("values must be unique")
    return normalized


class QuarantineRecord(BaseModel):
    """A reviewable explanation for why one source could not be prepared."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    quarantine_id: StableId
    source_id: StableId
    reason_codes: tuple[NonEmptyString, ...] = Field(min_length=1)
    messages: tuple[NonEmptyString, ...] = Field(min_length=1)
    parser_profile: NonEmptyString
    extraction: ExtractionMetadata

    @field_validator("reason_codes", "messages")
    @classmethod
    def normalize_explanations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Make semantically set-like explanations byte-stable."""
        return _sorted_unique(values)


class IngestionFailure(BaseModel):
    """One deterministic source-level failure included in a run report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: StableId
    reason_code: NonEmptyString
    message: NonEmptyString


class IngestionReport(BaseModel):
    """A complete deterministic accounting of a foundation ingestion run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: StableId
    extraction: ExtractionMetadata
    inventoried_source_ids: tuple[StableId, ...] = ()
    accepted_source_ids: tuple[StableId, ...] = ()
    excluded_source_ids: tuple[StableId, ...] = ()
    quarantined_source_ids: tuple[StableId, ...] = ()
    unchanged_source_ids: tuple[StableId, ...] = ()
    failures: tuple[IngestionFailure, ...] = ()
    warnings: tuple[NonEmptyString, ...] = ()

    @field_validator(
        "inventoried_source_ids",
        "accepted_source_ids",
        "excluded_source_ids",
        "quarantined_source_ids",
        "unchanged_source_ids",
        "warnings",
    )
    @classmethod
    def normalize_string_sets(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Canonicalize order and reject duplicate report entries."""
        return _sorted_unique(values)

    @field_validator("failures")
    @classmethod
    def normalize_failures(
        cls, values: tuple[IngestionFailure, ...]
    ) -> tuple[IngestionFailure, ...]:
        """Keep failures stable regardless of source traversal order."""
        ordered = tuple(
            sorted(values, key=lambda item: (item.source_id, item.reason_code, item.message))
        )
        if len({item.source_id for item in ordered}) != len(ordered):
            raise ValueError("each failed source must appear once")
        return ordered

    @model_validator(mode="after")
    def validate_complete_accounting(self) -> IngestionReport:
        """Require each inventoried source to have exactly one run outcome."""
        outcome_ids = (
            self.accepted_source_ids
            + self.excluded_source_ids
            + self.quarantined_source_ids
            + self.unchanged_source_ids
            + tuple(failure.source_id for failure in self.failures)
        )
        if len(outcome_ids) != len(set(outcome_ids)) or set(outcome_ids) != set(
            self.inventoried_source_ids
        ):
            raise ValueError("every inventoried source must have exactly one outcome")
        return self


class CanonicalKnowledgeRepository:
    """Persist canonical JSON beneath one resolved repository root."""

    def __init__(self, root: Path) -> None:
        requested_root = Path(root)
        if "\x00" in os.fspath(requested_root):
            raise ValueError("repository root must not contain NUL")
        requested_root.mkdir(parents=True, exist_ok=True)
        self.root = requested_root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError(f"repository root is not a directory: {self.root}")

    def write_manifest(self, manifest: DocumentManifest) -> Path:
        """Atomically persist one source manifest."""
        return self._write_model("manifests", manifest.source_id, manifest)

    def write_quarantine(self, record: QuarantineRecord) -> Path:
        """Atomically persist one quarantine explanation."""
        return self._write_model("quarantine", record.source_id, record)

    def write_ingestion_report(self, report: IngestionReport) -> Path:
        """Atomically persist one deterministic run report."""
        return self._write_model("ingestion_reports", report.run_id, report)

    def load_manifest(self, source_id: str) -> DocumentManifest:
        """Load and validate one manifest, with path-specific errors."""
        target = self._target("manifests", source_id, create_parent=False)
        if not target.is_file():
            raise FileNotFoundError(f"manifest not found for source_id {source_id!r}: {target}")
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            return DocumentManifest.model_validate(payload)
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"invalid manifest for source_id {source_id!r}: {target}") from exc

    def _write_model(self, directory: str, record_id: str, model: BaseModel) -> Path:
        target = self._target(directory, record_id, create_parent=True)
        payload = (
            json.dumps(
                model.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        )
        self._atomic_write(target, payload)
        return target

    def _target(self, directory: str, record_id: str, *, create_parent: bool) -> Path:
        _validate_stable_id(record_id)
        relative_target = PurePath(directory, f"{record_id}.json")
        self._validate_relative_target(relative_target)
        target = self.root.joinpath(*relative_target.parts)
        parent = target.parent
        if create_parent:
            parent.mkdir(parents=True, exist_ok=True)
        if parent.exists():
            self._require_within_root(parent.resolve(strict=True))
        if target.is_symlink():
            self._require_within_root(target.resolve(strict=False))
        self._require_within_root(target.resolve(strict=False))
        return target

    @staticmethod
    def _validate_relative_target(relative_target: PurePath) -> None:
        text = os.fspath(relative_target)
        if (
            relative_target.is_absolute()
            or "\x00" in text
            or any(part in {"", ".", ".."} for part in relative_target.parts)
            or any("/" in part or "\\" in part for part in relative_target.parts)
        ):
            raise ValueError("target must be a safe relative path")

    def _require_within_root(self, path: Path) -> None:
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"target resolves outside repository root: {path}") from exc

    @staticmethod
    def _atomic_write(target: Path, payload: str) -> None:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, target)
            temporary_path = None
            CanonicalKnowledgeRepository._fsync_directory(target.parent)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(directory, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


__all__ = [
    "CanonicalKnowledgeRepository",
    "IngestionFailure",
    "IngestionReport",
    "QuarantineRecord",
]
