"""Deterministic, atomic persistence for canonical ingestion records."""

from __future__ import annotations

import inspect
import json
import os
import secrets
import stat
import threading
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path, PurePath
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
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
    """Persist canonical JSON through a retained, resolved root descriptor."""

    _DIRECTORIES = frozenset({"manifests", "quarantine", "ingestion_reports"})

    def __init__(self, root: Path) -> None:
        self._descriptor_lock = threading.Lock()
        self._root_fd: int | None = None
        self._require_safe_primitives()
        requested_root = Path(root)
        if "\x00" in os.fspath(requested_root):
            raise ValueError("repository root must not contain NUL")
        requested_root.mkdir(parents=True, exist_ok=True)
        self.root = requested_root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError(f"repository root is not a directory: {self.root}")
        expected = os.stat(self.root, follow_symlinks=False)
        root_fd = os.open(self.root, self._directory_open_flags())
        try:
            actual = os.fstat(root_fd)
            if not stat.S_ISDIR(actual.st_mode) or (actual.st_dev, actual.st_ino) != (
                expected.st_dev,
                expected.st_ino,
            ):
                raise ValueError("repository root changed while it was being opened")
        except Exception:
            os.close(root_fd)
            raise
        self._root_fd = root_fd

    def __enter__(self) -> CanonicalKnowledgeRepository:
        self._ensure_open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()

    def __del__(self) -> None:
        with suppress(Exception):
            self.close()

    def close(self) -> None:
        """Release the retained root descriptor; repeated calls are harmless."""
        descriptor_lock = getattr(self, "_descriptor_lock", None)
        if descriptor_lock is None:
            return
        with descriptor_lock:
            root_fd = self._root_fd
            if root_fd is not None:
                self._root_fd = None
                os.close(root_fd)

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
        target, filename = self._target("manifests", source_id)
        try:
            directory_fd = self._open_child_directory("manifests", create=False)
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"manifest not found for source_id {source_id!r}: {target}"
            ) from exc
        try:
            try:
                file_fd = os.open(
                    filename,
                    self._file_read_flags(),
                    dir_fd=directory_fd,
                )
            except FileNotFoundError as exc:
                raise FileNotFoundError(
                    f"manifest not found for source_id {source_id!r}: {target}"
                ) from exc
            except OSError as exc:
                raise ValueError(
                    f"invalid manifest for source_id {source_id!r}: {target}"
                ) from exc

            try:
                file_status = os.fstat(file_fd)
                if not stat.S_ISREG(file_status.st_mode):
                    raise ValueError("manifest target is not a regular file")
                with os.fdopen(file_fd, mode="r", encoding="utf-8") as stream:
                    file_fd = -1
                    payload = json.load(stream)
                manifest = DocumentManifest.model_validate(payload)
            finally:
                if file_fd >= 0:
                    os.close(file_fd)
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError(f"invalid manifest for source_id {source_id!r}: {target}") from exc
        finally:
            os.close(directory_fd)

        if manifest.source_id != source_id:
            raise ValueError(
                f"invalid manifest for source_id {source_id!r}: {target}; "
                f"record contains source_id {manifest.source_id!r}"
            )
        return manifest

    def _write_model(self, directory: str, record_id: str, model: BaseModel) -> Path:
        target, filename = self._target(directory, record_id)
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
        directory_fd = self._open_child_directory(directory, create=True)
        try:
            self._atomic_write(directory_fd, filename, payload)
        finally:
            os.close(directory_fd)
        return target

    def _target(self, directory: str, record_id: str) -> tuple[Path, str]:
        self._ensure_open()
        _validate_stable_id(record_id)
        if directory not in self._DIRECTORIES:
            raise ValueError(f"unsupported canonical directory: {directory!r}")
        relative_target = PurePath(directory, f"{record_id}.json")
        self._validate_relative_target(relative_target)
        return self.root.joinpath(*relative_target.parts), relative_target.name

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

    def _open_child_directory(self, directory: str, *, create: bool) -> int:
        if directory not in self._DIRECTORIES:
            raise ValueError(f"unsupported canonical directory: {directory!r}")
        with self._descriptor_lock:
            root_fd = self._ensure_open()
            if create:
                with suppress(FileExistsError):
                    os.mkdir(directory, mode=0o755, dir_fd=root_fd)
            try:
                directory_fd = os.open(
                    directory,
                    self._directory_open_flags(),
                    dir_fd=root_fd,
                )
            except FileNotFoundError:
                raise
            except OSError as exc:
                raise ValueError(
                    "repository directory may resolve outside repository root: "
                    f"{directory!r}"
                ) from exc
        try:
            directory_status = os.fstat(directory_fd)
            if not stat.S_ISDIR(directory_status.st_mode):
                raise ValueError(f"repository child is not a directory: {directory!r}")
        except Exception:
            os.close(directory_fd)
            raise
        return directory_fd

    @staticmethod
    def _atomic_write(directory_fd: int, filename: str, payload: str) -> None:
        temporary_name: str | None = None
        try:
            for _ in range(32):
                candidate = f".{filename}.{secrets.token_hex(16)}.tmp"
                try:
                    temporary_fd = os.open(
                        candidate,
                        CanonicalKnowledgeRepository._file_create_flags(),
                        0o600,
                        dir_fd=directory_fd,
                    )
                except FileExistsError:
                    continue
                temporary_name = candidate
                break
            else:
                raise FileExistsError("could not allocate a unique temporary file")

            try:
                with os.fdopen(
                    temporary_fd,
                    mode="w",
                    encoding="utf-8",
                    newline="",
                ) as stream:
                    temporary_fd = -1
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                if temporary_fd >= 0:
                    os.close(temporary_fd)

            os.replace(
                temporary_name,
                filename,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            temporary_name = None
            os.fsync(directory_fd)
        finally:
            if temporary_name is not None:
                with suppress(FileNotFoundError):
                    os.unlink(temporary_name, dir_fd=directory_fd)

    @staticmethod
    def _directory_open_flags() -> int:
        return (
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )

    @staticmethod
    def _file_read_flags() -> int:
        return os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)

    @staticmethod
    def _file_create_flags() -> int:
        return (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )

    def _ensure_open(self) -> int:
        if self._root_fd is None:
            raise RuntimeError("repository is closed")
        return self._root_fd

    @staticmethod
    def _require_safe_primitives() -> None:
        required_constants = ("O_DIRECTORY", "O_NOFOLLOW")
        if any(not hasattr(os, name) for name in required_constants):
            raise RuntimeError("platform lacks safe descriptor-relative filesystem support")
        if any(
            function not in os.supports_dir_fd
            for function in (os.open, os.mkdir, os.unlink)
        ):
            raise RuntimeError("platform lacks safe descriptor-relative filesystem support")
        replace_parameters = inspect.signature(os.replace).parameters
        if not {"src_dir_fd", "dst_dir_fd"}.issubset(replace_parameters):
            raise RuntimeError("platform lacks safe descriptor-relative replace support")


__all__ = [
    "CanonicalKnowledgeRepository",
    "IngestionFailure",
    "IngestionReport",
    "QuarantineRecord",
]
