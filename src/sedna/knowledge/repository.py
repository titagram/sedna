"""Deterministic, atomic persistence for canonical ingestion records."""

from __future__ import annotations

import fcntl
import inspect
import json
import os
import secrets
import stat
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePath
from typing import Annotated, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from sedna.knowledge.parsing import PreparedSource
from sedna.knowledge.parsing.models import validate_prepared_source
from sedna.knowledge.schema import (
    DocumentManifest,
    ExtractionMetadata,
    SemanticKnowledgeBundle,
    SemanticQuarantineRecord,
    SemanticVerificationRecord,
)
from sedna.knowledge.semantic.compiler import (
    SEMANTIC_COMPILER_VERSION,
    SEMANTIC_SCHEMA_VERSION,
)
from sedna.knowledge.semantic.drafts import SemanticCompilationResult
from sedna.knowledge.semantic.prompts import (
    CRITIC_PROMPT_VERSION,
    EXTRACTOR_PROMPT_VERSION,
    REPAIR_PROMPT_VERSION,
)

_ModelT = TypeVar("_ModelT", bound=BaseModel)
_MAX_SEMANTIC_INVENTORY_FILES = 100_000
_MAX_SEMANTIC_RECORD_BYTES = 64 * 1024 * 1024


def _validate_stable_id(value: str) -> str:
    if (
        not value
        or value in {".", ".."}
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


class SemanticBundleEnumerationError(ValueError):
    """One canonical semantic source cannot safely participate in retrieval."""

    def __init__(self, source_id: str, reason_code: str, message: str) -> None:
        self.source_id = source_id
        self.reason_code = reason_code
        super().__init__(f"semantic source {source_id!r} {message}")


class SemanticSnapshotChangedError(RuntimeError):
    """The canonical semantic inventory changed before a guarded index commit."""


@dataclass(frozen=True, slots=True)
class SemanticRepositorySnapshot:
    """One lock-consistent verified semantic corpus and raw inventory revision."""

    bundles: tuple[SemanticKnowledgeBundle, ...]
    revision: str


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
    """Persist canonical JSON through a retained, resolved root descriptor.

    Write methods return the nominal canonical ``Path`` beneath the original resolved
    root pathname. IO remains bound to the retained directory when that pathname is
    renamed or replaced, so a returned path is a location hint rather than an identity
    handle in that exceptional case.

    Source transitions use POSIX ``flock`` locks opened relative to the retained
    root.  The locks are advisory, but every transition entry point participates;
    their open-file-description lifetime also releases locks after process death.
    Construction fails closed on platforms without these POSIX semantics.
    """

    _DIRECTORIES = frozenset(
        {
            "manifests",
            "quarantine",
            "ingestion_reports",
            "semantic_bundles",
            "semantic_compilation_guards",
            "semantic_verification",
            "semantic_quarantine",
            "transactions",
        }
    )
    _SEMANTIC_DIRECTORIES = (
        "semantic_bundles",
        "semantic_verification",
        "semantic_quarantine",
    )

    def __init__(self, root: Path) -> None:
        self._descriptor_lock = threading.RLock()
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
        try:
            self._recover_pending_transactions()
        except BaseException:
            self.close()
            raise

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
        """Persist one source manifest and return its nominal canonical path."""
        return self._write_model("manifests", manifest.source_id, manifest)

    def write_quarantine(self, record: QuarantineRecord) -> Path:
        """Persist one quarantine explanation and return its nominal canonical path."""
        return self._write_model("quarantine", record.source_id, record)

    def write_ingestion_report(self, report: IngestionReport) -> Path:
        """Persist one deterministic report and return its nominal canonical path."""
        return self._write_model("ingestion_reports", report.run_id, report)

    def write_semantic_result(self, result: SemanticCompilationResult) -> None:
        """Durably apply one verified or quarantined semantic source disposition."""
        if not isinstance(result, SemanticCompilationResult):
            raise TypeError("result must be a SemanticCompilationResult")
        result = SemanticCompilationResult.model_validate(
            result.model_dump(mode="json", warnings="error")
        )
        if result.disposition in {"failed", "unchanged"}:
            return

        verification = result.verification
        if verification is None:
            raise ValueError("terminal semantic result requires verification")
        source_id = verification.source_id
        source_sha256 = verification.source_sha256
        _validate_stable_id(source_id)
        if result.disposition == "verified":
            bundle = result.bundle
            if (
                bundle is None
                or bundle.source_id != source_id
                or bundle.source_sha256 != source_sha256
            ):
                raise ValueError("semantic bundle and verification identities must match")
        else:
            quarantine = result.quarantine
            if (
                quarantine is None
                or quarantine.source_id != source_id
                or quarantine.source_sha256 != source_sha256
            ):
                raise ValueError("semantic quarantine and verification identities must match")

        with (
            self._semantic_inventory_lock(exclusive=True),
            self._source_transition_lock(source_id),
        ):
            self._recover_source(source_id)
            snapshots = {
                directory: self._read_optional_bytes(directory, source_id)
                for directory in self._SEMANTIC_DIRECTORIES
            }
            self._write_semantic_transition_journal(source_id, snapshots)
            try:
                self._write_model("semantic_verification", source_id, verification)
                if result.disposition == "verified":
                    self._write_model("semantic_bundles", source_id, result.bundle)
                    self._delete_record("semantic_quarantine", source_id)
                else:
                    self._write_model("semantic_quarantine", source_id, result.quarantine)
                    self._delete_record("semantic_bundles", source_id)
                self._fsync_directories(self._SEMANTIC_DIRECTORIES)
                self._delete_semantic_transition_journal(source_id)
            except BaseException as original_error:
                rollback_errors = self._restore_semantic_snapshots(source_id, snapshots)
                if not rollback_errors:
                    try:
                        self._delete_semantic_transition_journal(source_id)
                    except BaseException as rollback_error:
                        rollback_errors.append(rollback_error)
                for rollback_error in rollback_errors:
                    original_error.add_note(
                        "semantic transition rollback remains recoverable: "
                        f"{type(rollback_error).__name__}: {rollback_error}"
                    )
                raise

    def load_semantic_bundle(self, source_id: str) -> SemanticKnowledgeBundle:
        """Load a strictly validated verified semantic bundle."""
        return self._load_semantic_component(source_id, "bundle")

    def load_semantic_verification(self, source_id: str) -> SemanticVerificationRecord:
        """Load a strictly validated semantic verification record."""
        return self._load_semantic_component(source_id, "verification")

    def load_semantic_quarantine(self, source_id: str) -> SemanticQuarantineRecord:
        """Load a strictly validated semantic quarantine record."""
        return self._load_semantic_component(source_id, "quarantine")

    def iter_semantic_bundles(self) -> Iterator[SemanticKnowledgeBundle]:
        """Return a sorted snapshot iterator of current verified semantic bundles.

        Enumeration and every record load remain descriptor-relative.  Valid quarantined
        dispositions are omitted; any unsafe, incomplete, corrupt, or stale verified state
        fails the complete enumeration before a caller can consume a partial corpus.
        """
        return iter(self.semantic_bundle_snapshot().bundles)

    def semantic_bundle_snapshot(self) -> SemanticRepositorySnapshot:
        """Return verified bundles and a revision from one shared inventory lock."""
        with self._semantic_inventory_lock(exclusive=False):
            return self._semantic_bundle_snapshot_locked()

    @contextmanager
    def semantic_snapshot_guard(self, expected_revision: str) -> Iterator[None]:
        """Verify and hold one semantic revision stable through an external commit."""
        if (
            type(expected_revision) is not str
            or len(expected_revision) != 64
            or any(character not in "0123456789abcdef" for character in expected_revision)
        ):
            raise ValueError("semantic snapshot revision must be a lowercase SHA-256 digest")
        with self._semantic_inventory_lock(exclusive=False):
            self._require_semantic_inventory_revision(expected_revision)
            try:
                yield
            finally:
                self._require_semantic_inventory_revision(expected_revision)

    def _require_semantic_inventory_revision(self, expected_revision: str) -> None:
        try:
            self._require_no_pending_semantic_transactions()
            entries, _ = self._semantic_inventory_entries()
            self._require_no_pending_semantic_transactions()
        except Exception as error:
            raise SemanticSnapshotChangedError(
                "canonical semantic inventory became unsafe during index commit"
            ) from error
        if self._semantic_inventory_revision(entries) != expected_revision:
            raise SemanticSnapshotChangedError(
                "canonical semantic inventory changed during index commit"
            )

    def _semantic_bundle_snapshot_locked(self) -> SemanticRepositorySnapshot:
        self._require_no_pending_semantic_transactions()
        entries, source_ids = self._semantic_inventory_entries()
        bundles: list[SemanticKnowledgeBundle] = []
        for source_id in source_ids:
            try:
                with self._source_transition_lock(source_id):
                    bundle, verification, quarantine = self._load_semantic_state(source_id)
                if bundle is None:
                    # A complete quarantine pair is canonical but deliberately not retrievable.
                    if verification is not None and quarantine is not None:
                        continue
                    raise ValueError("semantic state does not contain a verified bundle")
                self._require_current_retrieval_bundle(bundle)
            except SemanticBundleEnumerationError:
                raise
            except (OSError, RuntimeError, ValueError) as exc:
                raise SemanticBundleEnumerationError(
                    source_id,
                    "invalid_semantic_record",
                    f"is invalid for retrieval: {exc}",
                ) from exc
            bundles.append(bundle)
        final_entries, _ = self._semantic_inventory_entries()
        self._require_no_pending_semantic_transactions()
        if final_entries != entries:
            raise SemanticBundleEnumerationError(
                "<repository>",
                "semantic_inventory_changed",
                "changed during canonical enumeration",
            )
        return SemanticRepositorySnapshot(
            bundles=tuple(bundles),
            revision=self._semantic_inventory_revision(entries),
        )

    def _require_no_pending_semantic_transactions(self) -> None:
        pending = self._pending_semantic_transaction_journals()
        if pending:
            source_id, journal_name = pending[0]
            raise SemanticBundleEnumerationError(
                source_id,
                "pending_semantic_transaction",
                f"has unresolved canonical transaction journal {journal_name!r}",
            )

    def _pending_semantic_transaction_journals(self) -> tuple[tuple[str, str], ...]:
        try:
            directory_fd = self._open_child_directory("transactions", create=False)
        except FileNotFoundError:
            return ()
        try:
            names: list[tuple[str, str]] = []
            entry_count = 0
            with os.scandir(directory_fd) as iterator:
                for entry in iterator:
                    entry_count += 1
                    if entry_count > _MAX_SEMANTIC_INVENTORY_FILES:
                        raise SemanticBundleEnumerationError(
                            "<repository>",
                            "semantic_transaction_inventory_too_large",
                            "exceeds the bounded transaction journal inventory",
                        )
                    source_id: str | None = None
                    if entry.name.endswith(".semantic-transaction.json"):
                        source_id = entry.name[: -len(".semantic-transaction.json")]
                    elif entry.name.endswith(".transaction.json"):
                        source_id = entry.name[: -len(".transaction.json")]
                    if source_id is not None:
                        names.append((source_id, entry.name))

            checked: list[tuple[str, str]] = []
            for source_id, name in sorted(names):
                try:
                    _validate_stable_id(source_id)
                    before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if not stat.S_ISREG(before.st_mode):
                        raise ValueError("transaction journal is not a regular file")
                    journal_fd = os.open(name, self._file_read_flags(), dir_fd=directory_fd)
                except (OSError, ValueError) as error:
                    raise SemanticBundleEnumerationError(
                        source_id or "<empty>",
                        "unsafe_semantic_transaction_journal",
                        f"has unsafe pending transaction journal {name!r}",
                    ) from error
                try:
                    opened = os.fstat(journal_fd)
                    after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if (
                        not stat.S_ISREG(opened.st_mode)
                        or self._semantic_file_identity(opened)
                        != self._semantic_file_identity(before)
                        or self._semantic_file_identity(opened)
                        != self._semantic_file_identity(after)
                    ):
                        raise SemanticBundleEnumerationError(
                            source_id,
                            "unsafe_semantic_transaction_journal",
                            f"changed pending transaction journal {name!r}",
                        )
                except OSError as error:
                    raise SemanticBundleEnumerationError(
                        source_id,
                        "unsafe_semantic_transaction_journal",
                        f"could not safely inspect pending transaction journal {name!r}",
                    ) from error
                finally:
                    os.close(journal_fd)
                checked.append((source_id, name))
            return tuple(checked)
        finally:
            os.close(directory_fd)

    def load_current_semantic_result(
        self,
        prepared: PreparedSource,
        *,
        semantic_schema_version: str = SEMANTIC_SCHEMA_VERSION,
        extractor_prompt_version: str = EXTRACTOR_PROMPT_VERSION,
        critic_prompt_version: str = CRITIC_PROMPT_VERSION,
        repair_prompt_version: str = REPAIR_PROMPT_VERSION,
        compiler_version: str = SEMANTIC_COMPILER_VERSION,
        pin_models: bool = False,
        extractor_model_id: str | None = None,
        critic_model_id: str | None = None,
    ) -> SemanticCompilationResult | None:
        """Atomically load one current, cross-validated verified semantic pair."""
        prepared = validate_prepared_source(prepared)
        if pin_models and (not extractor_model_id or not critic_model_id):
            raise ValueError("model-pinned currentness requires both model identifiers")
        source_id = prepared.manifest.source_id
        self._target("semantic_bundles", source_id)
        with (
            self._semantic_inventory_lock(exclusive=True),
            self._source_transition_lock(source_id),
        ):
            self._recover_source(source_id)
            try:
                bundle, verification, quarantine = self._load_semantic_state(source_id)
            except ValueError:
                return None
            if verification is None:
                return None

            foundation = prepared.manifest.extraction
            manifest = (
                bundle.compilation_manifest
                if bundle is not None
                else quarantine.compilation_manifest
                if quarantine is not None
                else None
            )
            if manifest is None:
                return None
            current = (
                verification.source_id == source_id
                and verification.source_sha256 == prepared.manifest.sha256
                and manifest.foundation_schema_version == foundation.schema_version
                and manifest.foundation_parser_id == foundation.parser_id
                and manifest.foundation_parser_version == foundation.parser_version
                and (
                    bundle.schema_version == semantic_schema_version
                    if bundle is not None
                    else quarantine is not None
                    and quarantine.semantic_schema_version == semantic_schema_version
                )
                and manifest.extractor_prompt_version == extractor_prompt_version
                and manifest.critic_prompt_version == critic_prompt_version
                and manifest.repair_prompt_version == repair_prompt_version
                and manifest.compiler_version == compiler_version
            )
            if pin_models:
                current = current and (
                    manifest.extractor_model_id == extractor_model_id
                    and manifest.critic_model_id == critic_model_id
                )
            if not current:
                return None
            if bundle is None:
                if quarantine is None or verification.adjudication != "quarantined":
                    return None
                return SemanticCompilationResult(
                    disposition="unchanged",
                    verification=verification,
                    quarantine=quarantine,
                )
            if quarantine is not None or verification.adjudication != "verified":
                return None
            return SemanticCompilationResult(
                disposition="unchanged",
                bundle=bundle,
                verification=verification,
            )

    @contextmanager
    def semantic_compilation_guard(self, source_id: str) -> Iterator[None]:
        """Serialize check, compile, and persistence for one semantic source."""
        _validate_stable_id(source_id)
        directory_fd = self._open_child_directory("semantic_compilation_guards", create=True)
        lock_fd = -1
        try:
            try:
                lock_fd = os.open(
                    f"{source_id}.lock",
                    self._lock_open_flags(),
                    0o600,
                    dir_fd=directory_fd,
                )
            except OSError as exc:
                raise ValueError(
                    f"semantic compilation guard is not a confined regular file: {exc}"
                ) from exc
            if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
                raise ValueError("semantic compilation guard is not a regular file")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield
        finally:
            if lock_fd >= 0:
                with suppress(OSError):
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            os.close(directory_fd)

    def semantic_result_is_current(
        self,
        prepared: PreparedSource,
        *,
        semantic_schema_version: str = SEMANTIC_SCHEMA_VERSION,
        extractor_prompt_version: str = EXTRACTOR_PROMPT_VERSION,
        critic_prompt_version: str = CRITIC_PROMPT_VERSION,
        repair_prompt_version: str = REPAIR_PROMPT_VERSION,
        compiler_version: str = SEMANTIC_COMPILER_VERSION,
        pin_models: bool = False,
        extractor_model_id: str | None = None,
        critic_model_id: str | None = None,
    ) -> bool:
        """Return whether verified canonical semantics match all configured inputs."""
        return (
            self.load_current_semantic_result(
                prepared,
                semantic_schema_version=semantic_schema_version,
                extractor_prompt_version=extractor_prompt_version,
                critic_prompt_version=critic_prompt_version,
                repair_prompt_version=repair_prompt_version,
                compiler_version=compiler_version,
                pin_models=pin_models,
                extractor_model_id=extractor_model_id,
                critic_model_id=critic_model_id,
            )
            is not None
        )

    def quarantine_exists(self, source_id: str) -> bool:
        """Return whether a regular quarantine record exists for ``source_id``."""
        return self._record_exists("quarantine", source_id)

    def delete_quarantine(self, source_id: str) -> bool:
        """Delete one stale quarantine record, returning whether it existed."""
        return self._delete_record("quarantine", source_id)

    def load_quarantine(self, source_id: str) -> QuarantineRecord:
        """Load and strictly validate one quarantine record and its identities."""
        target, filename = self._target("quarantine", source_id)
        try:
            directory_fd = self._open_child_directory("quarantine", create=False)
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"quarantine not found for source_id {source_id!r}: {target}"
            ) from exc
        try:
            try:
                file_fd = os.open(filename, self._file_read_flags(), dir_fd=directory_fd)
            except FileNotFoundError as exc:
                raise FileNotFoundError(
                    f"quarantine not found for source_id {source_id!r}: {target}"
                ) from exc
            except OSError as exc:
                raise ValueError(
                    f"invalid quarantine for source_id {source_id!r}: {target}"
                ) from exc
            try:
                if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                    raise ValueError("quarantine target is not a regular file")
                with os.fdopen(file_fd, mode="r", encoding="utf-8") as stream:
                    file_fd = -1
                    payload = json.load(stream)
                record = QuarantineRecord.model_validate(payload)
            except (OSError, UnicodeError, ValueError) as exc:
                raise ValueError(
                    f"invalid quarantine for source_id {source_id!r}: {target}; {exc}"
                ) from exc
            finally:
                if file_fd >= 0:
                    os.close(file_fd)
        finally:
            os.close(directory_fd)

        expected_quarantine_id = f"quarantine-{source_id}"
        if record.source_id != source_id or record.quarantine_id != expected_quarantine_id:
            raise ValueError(
                f"invalid quarantine for source_id {source_id!r}: {target}; "
                "record identity does not match requested source"
            )
        return record

    def transition_source(
        self,
        manifest: DocumentManifest,
        quarantine: QuarantineRecord | None,
    ) -> None:
        """Durably commit one source disposition or recover its previous bytes."""
        self.validate_source_state(manifest, quarantine)
        with (
            self._semantic_inventory_lock(exclusive=True),
            self._source_transition_lock(manifest.source_id),
        ):
            self._recover_source(manifest.source_id)
            semantic_snapshots: dict[str, bytes | None] | None = None
            if manifest.ingestion_status.value != "accepted":
                semantic_snapshots = {
                    directory: self._read_optional_bytes(directory, manifest.source_id)
                    for directory in self._SEMANTIC_DIRECTORIES
                }
                self._write_semantic_transition_journal(manifest.source_id, semantic_snapshots)
                try:
                    for directory in self._SEMANTIC_DIRECTORIES:
                        self._delete_record(directory, manifest.source_id)
                    self._fsync_directories(self._SEMANTIC_DIRECTORIES)
                except BaseException:
                    self._restore_semantic_snapshots(manifest.source_id, semantic_snapshots)
                    self._delete_semantic_transition_journal(manifest.source_id)
                    raise
            old_manifest = self._read_optional_bytes("manifests", manifest.source_id)
            old_quarantine = self._read_optional_bytes("quarantine", manifest.source_id)
            self._write_transition_journal(
                manifest.source_id,
                old_manifest,
                old_quarantine,
            )
            try:
                if quarantine is None:
                    self.delete_quarantine(manifest.source_id)
                else:
                    self.write_quarantine(quarantine)
                self.write_manifest(manifest)
            except BaseException as original_error:
                rollback_errors: list[BaseException] = []
                if semantic_snapshots is not None:
                    rollback_errors.extend(
                        self._restore_semantic_snapshots(
                            manifest.source_id,
                            semantic_snapshots,
                        )
                    )
                    if not rollback_errors:
                        try:
                            self._delete_semantic_transition_journal(manifest.source_id)
                        except BaseException as rollback_error:
                            rollback_errors.append(rollback_error)
                rollback_errors.extend(
                    self._restore_source_snapshots(
                        manifest.source_id,
                        old_manifest,
                        old_quarantine,
                    )
                )
                if not rollback_errors:
                    try:
                        self._delete_transition_journal(manifest.source_id)
                    except BaseException as rollback_error:
                        rollback_errors.append(rollback_error)
                for rollback_error in rollback_errors:
                    original_error.add_note(
                        "transition rollback remains recoverable: "
                        f"{type(rollback_error).__name__}: {rollback_error}"
                    )
                raise
            self._delete_transition_journal(manifest.source_id)
            if semantic_snapshots is not None:
                self._delete_semantic_transition_journal(manifest.source_id)

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
                raise ValueError(f"invalid manifest for source_id {source_id!r}: {target}") from exc

            try:
                file_status = os.fstat(file_fd)
                if not stat.S_ISREG(file_status.st_mode):
                    raise ValueError("manifest target is not a regular file")
                with os.fdopen(file_fd, mode="r", encoding="utf-8") as stream:
                    file_fd = -1
                    payload = json.load(stream)
                manifest = DocumentManifest.model_validate(payload)
            except (OSError, UnicodeError, ValueError) as exc:
                raise ValueError(
                    f"invalid manifest for source_id {source_id!r}: {target}; {exc}"
                ) from exc
            finally:
                if file_fd >= 0:
                    os.close(file_fd)
        finally:
            os.close(directory_fd)

        if manifest.source_id != source_id:
            raise ValueError(
                f"invalid manifest for source_id {source_id!r}: {target}; "
                f"record contains source_id {manifest.source_id!r}"
            )
        return manifest

    def _load_semantic_component(
        self,
        source_id: str,
        component: str,
    ) -> SemanticKnowledgeBundle | SemanticVerificationRecord | SemanticQuarantineRecord:
        directory = {
            "bundle": "semantic_bundles",
            "verification": "semantic_verification",
            "quarantine": "semantic_quarantine",
        }[component]
        target, _ = self._target(directory, source_id)
        with (
            self._semantic_inventory_lock(exclusive=True),
            self._source_transition_lock(source_id),
        ):
            self._recover_source(source_id)
            bundle, verification, quarantine = self._load_semantic_state(source_id)
        record = {
            "bundle": bundle,
            "verification": verification,
            "quarantine": quarantine,
        }[component]
        if record is None:
            raise FileNotFoundError(
                f"semantic {component} not found for source_id {source_id!r}: {target}"
            )
        return record

    def _load_semantic_state(
        self,
        source_id: str,
    ) -> tuple[
        SemanticKnowledgeBundle | None,
        SemanticVerificationRecord | None,
        SemanticQuarantineRecord | None,
    ]:
        bundle = self._read_optional_model(
            "semantic_bundles", source_id, SemanticKnowledgeBundle, "semantic bundle"
        )
        verification = self._read_optional_model(
            "semantic_verification",
            source_id,
            SemanticVerificationRecord,
            "semantic verification",
        )
        quarantine = self._read_optional_model(
            "semantic_quarantine",
            source_id,
            SemanticQuarantineRecord,
            "semantic quarantine",
        )
        if bundle is None and verification is None and quarantine is None:
            return None, None, None

        records = tuple(
            record for record in (bundle, verification, quarantine) if record is not None
        )
        if any(record.source_id != source_id for record in records):
            raise ValueError(
                f"invalid semantic state for source_id {source_id!r}: record identity mismatch"
            )
        hashes = {record.source_sha256 for record in records}
        if len(hashes) != 1:
            raise ValueError(
                f"invalid semantic state for source_id {source_id!r}: record identity mismatch"
            )
        if bundle is not None:
            if (
                verification is None
                or quarantine is not None
                or verification.adjudication != "verified"
            ):
                raise ValueError(
                    f"invalid semantic state for source_id {source_id!r}: "
                    "bundle and verification disposition mismatch"
                )
            if verification.critic_call.model != bundle.compilation_manifest.critic_model_id:
                raise ValueError(
                    f"invalid semantic state for source_id {source_id!r}: "
                    "critic model identity mismatch"
                )
        elif quarantine is not None:
            if verification is None or verification.adjudication != "quarantined":
                raise ValueError(
                    f"invalid semantic state for source_id {source_id!r}: "
                    "quarantine and verification disposition mismatch"
                )
        else:
            raise ValueError(
                f"invalid semantic state for source_id {source_id!r}: orphan verification"
            )
        return bundle, verification, quarantine

    def _semantic_inventory_entries(
        self,
    ) -> tuple[tuple[tuple[str, str, str, int], ...], tuple[str, ...]]:
        entries: list[tuple[str, str, str, int]] = []
        source_ids: set[str] = set()
        for directory in self._SEMANTIC_DIRECTORIES:
            try:
                directory_fd = self._open_child_directory(directory, create=False)
            except FileNotFoundError:
                continue
            try:
                names: list[str] = []
                entry_count = 0
                with os.scandir(directory_fd) as iterator:
                    for entry in iterator:
                        entry_count += 1
                        if entry_count > _MAX_SEMANTIC_INVENTORY_FILES:
                            raise SemanticBundleEnumerationError(
                                "<repository>",
                                "semantic_inventory_too_large",
                                "exceeds the bounded semantic file inventory",
                            )
                        if entry.name.endswith(".json"):
                            names.append(entry.name)
                names.sort()
                for name in names:
                    if not name.endswith(".json"):
                        continue
                    source_id = name[:-5]
                    try:
                        _validate_stable_id(source_id)
                    except ValueError as exc:
                        raise SemanticBundleEnumerationError(
                            source_id or "<empty>",
                            "unsafe_semantic_filename",
                            "has an unsafe canonical filename",
                        ) from exc
                    try:
                        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        if not stat.S_ISREG(before.st_mode):
                            raise ValueError("semantic record is not a regular file")
                        file_fd = os.open(name, self._file_read_flags(), dir_fd=directory_fd)
                    except (OSError, ValueError) as exc:
                        raise SemanticBundleEnumerationError(
                            source_id,
                            "unsafe_semantic_record",
                            f"has an unsafe {directory} record",
                        ) from exc
                    try:
                        opened = os.fstat(file_fd)
                        if not stat.S_ISREG(opened.st_mode) or self._semantic_file_identity(
                            opened
                        ) != self._semantic_file_identity(before):
                            raise SemanticBundleEnumerationError(
                                source_id,
                                "unsafe_semantic_record",
                                f"has a non-regular {directory} record",
                            )
                        chunks: list[bytes] = []
                        size = 0
                        while chunk := os.read(file_fd, 1024 * 1024):
                            size += len(chunk)
                            if size > _MAX_SEMANTIC_RECORD_BYTES:
                                raise SemanticBundleEnumerationError(
                                    source_id,
                                    "semantic_record_too_large",
                                    f"has an oversized {directory} record",
                                )
                            chunks.append(chunk)
                        payload = b"".join(chunks)
                        final = os.fstat(file_fd)
                        after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        if self._semantic_file_identity(opened) != self._semantic_file_identity(
                            final
                        ) or self._semantic_file_identity(opened) != self._semantic_file_identity(
                            after
                        ):
                            raise SemanticBundleEnumerationError(
                                source_id,
                                "semantic_record_changed",
                                f"changed while reading {directory}",
                            )
                    except OSError as exc:
                        raise SemanticBundleEnumerationError(
                            source_id,
                            "unsafe_semantic_record",
                            f"could not read {directory} safely",
                        ) from exc
                    finally:
                        os.close(file_fd)
                    entries.append((directory, name, sha256(payload).hexdigest(), len(payload)))
                    source_ids.add(source_id)
            finally:
                os.close(directory_fd)
        return tuple(sorted(entries)), tuple(sorted(source_ids))

    @staticmethod
    def _semantic_file_identity(status: os.stat_result) -> tuple[object, ...]:
        return (
            status.st_mode,
            status.st_dev,
            status.st_ino,
            status.st_nlink,
            status.st_uid,
            status.st_gid,
            status.st_size,
            getattr(status, "st_mtime_ns", status.st_mtime),
            getattr(status, "st_ctime_ns", status.st_ctime),
        )

    @staticmethod
    def _semantic_inventory_revision(
        entries: tuple[tuple[str, str, str, int], ...],
    ) -> str:
        payload = json.dumps(
            entries,
            ensure_ascii=True,
            sort_keys=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        return sha256(payload.encode("ascii")).hexdigest()

    @staticmethod
    def _require_current_retrieval_bundle(bundle: SemanticKnowledgeBundle) -> None:
        manifest = bundle.compilation_manifest
        if (
            bundle.schema_version != SEMANTIC_SCHEMA_VERSION
            or manifest.compiler_version != SEMANTIC_COMPILER_VERSION
            or manifest.extractor_prompt_version != EXTRACTOR_PROMPT_VERSION
            or manifest.critic_prompt_version != CRITIC_PROMPT_VERSION
            or manifest.repair_prompt_version != REPAIR_PROMPT_VERSION
        ):
            raise SemanticBundleEnumerationError(
                bundle.source_id,
                "stale_semantic_record",
                "is not current and must be semantically recompiled",
            )

    def _read_optional_model(
        self,
        directory: str,
        source_id: str,
        model_type: type[_ModelT],
        record_name: str,
    ) -> _ModelT | None:
        target, _ = self._target(directory, source_id)
        try:
            raw = self._read_optional_bytes(directory, source_id)
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"invalid {record_name} for source_id {source_id!r}: {target}; {exc}"
            ) from exc
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
            return model_type.model_validate(payload)
        except (UnicodeError, ValueError) as exc:
            raise ValueError(
                f"invalid {record_name} for source_id {source_id!r}: {target}; {exc}"
            ) from exc

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

    def _record_exists(self, directory: str, record_id: str) -> bool:
        self._target(directory, record_id)
        try:
            directory_fd = self._open_child_directory(directory, create=False)
        except FileNotFoundError:
            return False
        try:
            try:
                file_fd = os.open(
                    f"{record_id}.json",
                    self._file_read_flags(),
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                return False
            except OSError as exc:
                raise ValueError(f"invalid {directory} record for source_id {record_id!r}") from exc
            try:
                if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                    raise ValueError(f"invalid {directory} record for source_id {record_id!r}")
                return True
            finally:
                os.close(file_fd)
        finally:
            os.close(directory_fd)

    def _delete_record(self, directory: str, record_id: str) -> bool:
        _, filename = self._target(directory, record_id)
        try:
            directory_fd = self._open_child_directory(directory, create=False)
        except FileNotFoundError:
            return False
        try:
            try:
                os.unlink(filename, dir_fd=directory_fd)
            except FileNotFoundError:
                return False
            os.fsync(directory_fd)
            return True
        finally:
            os.close(directory_fd)

    def _read_optional_bytes(self, directory: str, record_id: str) -> bytes | None:
        self._target(directory, record_id)
        try:
            directory_fd = self._open_child_directory(directory, create=False)
        except FileNotFoundError:
            return None
        try:
            try:
                file_fd = os.open(
                    f"{record_id}.json",
                    self._file_read_flags(),
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                return None
            try:
                if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                    raise ValueError(f"{directory} target is not a regular file")
                with os.fdopen(file_fd, mode="rb") as stream:
                    file_fd = -1
                    return stream.read()
            finally:
                if file_fd >= 0:
                    os.close(file_fd)
        finally:
            os.close(directory_fd)

    def _restore_snapshot(
        self,
        directory: str,
        record_id: str,
        payload: bytes | None,
    ) -> None:
        if payload is None:
            self._delete_record(directory, record_id)
            return
        directory_fd = self._open_child_directory(directory, create=True)
        try:
            self._atomic_write_bytes(directory_fd, f"{record_id}.json", payload)
        finally:
            os.close(directory_fd)

    def _restore_source_snapshots(
        self,
        source_id: str,
        manifest: bytes | None,
        quarantine: bytes | None,
    ) -> list[BaseException]:
        errors: list[BaseException] = []
        for directory, payload in (
            ("manifests", manifest),
            ("quarantine", quarantine),
        ):
            try:
                self._restore_snapshot(directory, source_id, payload)
            except BaseException as exc:
                errors.append(exc)
        return errors

    def _restore_semantic_snapshots(
        self,
        source_id: str,
        snapshots: dict[str, bytes | None],
    ) -> list[BaseException]:
        errors: list[BaseException] = []
        for directory in self._SEMANTIC_DIRECTORIES:
            try:
                self._restore_snapshot(directory, source_id, snapshots[directory])
            except BaseException as exc:
                errors.append(exc)
        if not errors:
            try:
                self._fsync_directories(self._SEMANTIC_DIRECTORIES)
            except BaseException as exc:
                errors.append(exc)
        return errors

    def _fsync_directories(self, directories: Iterable[str]) -> None:
        for directory in directories:
            directory_fd = self._open_child_directory(directory, create=True)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

    @contextmanager
    def _semantic_inventory_lock(self, *, exclusive: bool) -> Iterator[None]:
        with self._descriptor_lock:
            root_fd = self._ensure_open()
            lock_fd = os.dup(root_fd)
            try:
                retained = os.fstat(root_fd)
                duplicated = os.fstat(lock_fd)
                retained_identity = (retained.st_dev, retained.st_ino)
                if (
                    not stat.S_ISDIR(retained.st_mode)
                    or not stat.S_ISDIR(duplicated.st_mode)
                    or retained_identity != (duplicated.st_dev, duplicated.st_ino)
                ):
                    raise RuntimeError("semantic inventory lock lost repository root identity")
                fcntl.flock(lock_fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
                yield
            finally:
                with suppress(OSError):
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)

    @contextmanager
    def _source_transition_lock(self, source_id: str) -> Iterator[None]:
        _validate_stable_id(source_id)
        directory_fd = self._open_child_directory("transactions", create=True)
        lock_fd = -1
        try:
            lock_fd = os.open(
                f"{source_id}.lock",
                self._lock_open_flags(),
                0o600,
                dir_fd=directory_fd,
            )
            if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
                raise ValueError("source transition lock is not a regular file")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield
        finally:
            if lock_fd >= 0:
                with suppress(OSError):
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            os.close(directory_fd)

    def _recover_pending_transactions(self) -> None:
        with self._semantic_inventory_lock(exclusive=True):
            try:
                directory_fd = self._open_child_directory("transactions", create=False)
            except FileNotFoundError:
                return
            try:
                names = tuple(sorted(os.listdir(directory_fd)))
            finally:
                os.close(directory_fd)
            foundation_suffix = ".transaction.json"
            semantic_suffix = ".semantic-transaction.json"
            source_ids: set[str] = set()
            for name in names:
                if name.endswith(semantic_suffix):
                    source_ids.add(name[: -len(semantic_suffix)])
                elif name.endswith(foundation_suffix):
                    source_ids.add(name[: -len(foundation_suffix)])
            for source_id in sorted(source_ids):
                _validate_stable_id(source_id)
                with self._source_transition_lock(source_id):
                    self._recover_source(source_id)

    def _recover_source(self, source_id: str) -> None:
        self._recover_foundation_source(source_id)
        self._recover_semantic_source(source_id)

    def _recover_foundation_source(self, source_id: str) -> None:
        journal = self._read_transition_journal(source_id)
        if journal is None:
            return
        manifest, quarantine = journal
        errors = self._restore_source_snapshots(source_id, manifest, quarantine)
        if errors:
            error = OSError(f"could not recover interrupted transition for {source_id!r}")
            for recovery_error in errors:
                error.add_note(f"{type(recovery_error).__name__}: {recovery_error}")
            raise error from errors[0]
        self._delete_transition_journal(source_id)

    def _recover_semantic_source(self, source_id: str) -> None:
        snapshots = self._read_semantic_transition_journal(source_id)
        if snapshots is None:
            return
        errors = self._restore_semantic_snapshots(source_id, snapshots)
        if errors:
            error = OSError(f"could not recover interrupted semantic transition for {source_id!r}")
            for recovery_error in errors:
                error.add_note(f"{type(recovery_error).__name__}: {recovery_error}")
            raise error from errors[0]
        self._delete_semantic_transition_journal(source_id)

    def _write_transition_journal(
        self,
        source_id: str,
        manifest: bytes | None,
        quarantine: bytes | None,
    ) -> None:
        payload = (
            json.dumps(
                {
                    "manifest_hex": None if manifest is None else manifest.hex(),
                    "quarantine_hex": None if quarantine is None else quarantine.hex(),
                    "source_id": source_id,
                    "version": 1,
                },
                ensure_ascii=True,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("ascii")
        directory_fd = self._open_child_directory("transactions", create=True)
        try:
            self._atomic_write_bytes(
                directory_fd,
                f"{source_id}.transaction.json",
                payload,
            )
        finally:
            os.close(directory_fd)

    def _read_transition_journal(
        self,
        source_id: str,
    ) -> tuple[bytes | None, bytes | None] | None:
        _validate_stable_id(source_id)
        try:
            directory_fd = self._open_child_directory("transactions", create=False)
        except FileNotFoundError:
            return None
        filename = f"{source_id}.transaction.json"
        try:
            try:
                file_fd = os.open(
                    filename,
                    self._file_read_flags(),
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                return None
            try:
                if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                    raise ValueError("transition journal is not a regular file")
                with os.fdopen(file_fd, mode="rb") as stream:
                    file_fd = -1
                    raw = stream.read()
            finally:
                if file_fd >= 0:
                    os.close(file_fd)
        finally:
            os.close(directory_fd)
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or set(payload) != {
                "manifest_hex",
                "quarantine_hex",
                "source_id",
                "version",
            }:
                raise ValueError("unexpected transition journal fields")
            if (
                payload["source_id"] != source_id
                or type(payload["version"]) is not int
                or payload["version"] != 1
            ):
                raise ValueError("transition journal identity or version mismatch")
            manifest = self._decode_optional_hex(payload["manifest_hex"])
            quarantine = self._decode_optional_hex(payload["quarantine_hex"])
        except (TypeError, UnicodeError, ValueError) as exc:
            raise ValueError(f"invalid interrupted transition journal for {source_id!r}") from exc
        return manifest, quarantine

    @staticmethod
    def _decode_optional_hex(value: object) -> bytes | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("snapshot must be hexadecimal text or null")
        return bytes.fromhex(value)

    def _delete_transition_journal(self, source_id: str) -> None:
        try:
            directory_fd = self._open_child_directory("transactions", create=False)
        except FileNotFoundError:
            return
        try:
            try:
                os.unlink(
                    f"{source_id}.transaction.json",
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                return
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _write_semantic_transition_journal(
        self,
        source_id: str,
        snapshots: dict[str, bytes | None],
    ) -> None:
        _validate_stable_id(source_id)
        if set(snapshots) != set(self._SEMANTIC_DIRECTORIES):
            raise ValueError("semantic transition snapshots are incomplete")
        payload = (
            json.dumps(
                {
                    "kind": "semantic",
                    "snapshots": {
                        directory: (
                            None if snapshots[directory] is None else snapshots[directory].hex()
                        )
                        for directory in self._SEMANTIC_DIRECTORIES
                    },
                    "source_id": source_id,
                    "version": 1,
                },
                ensure_ascii=True,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("ascii")
        directory_fd = self._open_child_directory("transactions", create=True)
        try:
            self._atomic_write_bytes(
                directory_fd,
                f"{source_id}.semantic-transaction.json",
                payload,
            )
        finally:
            os.close(directory_fd)

    def _read_semantic_transition_journal(
        self,
        source_id: str,
    ) -> dict[str, bytes | None] | None:
        _validate_stable_id(source_id)
        try:
            directory_fd = self._open_child_directory("transactions", create=False)
        except FileNotFoundError:
            return None
        filename = f"{source_id}.semantic-transaction.json"
        try:
            try:
                file_fd = os.open(filename, self._file_read_flags(), dir_fd=directory_fd)
            except FileNotFoundError:
                return None
            try:
                if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                    raise ValueError("semantic transition journal is not a regular file")
                with os.fdopen(file_fd, mode="rb") as stream:
                    file_fd = -1
                    raw = stream.read()
            finally:
                if file_fd >= 0:
                    os.close(file_fd)
        finally:
            os.close(directory_fd)
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or set(payload) != {
                "kind",
                "snapshots",
                "source_id",
                "version",
            }:
                raise ValueError("unexpected semantic transition journal fields")
            if (
                payload["kind"] != "semantic"
                or payload["source_id"] != source_id
                or type(payload["version"]) is not int
                or payload["version"] != 1
                or not isinstance(payload["snapshots"], dict)
                or set(payload["snapshots"]) != set(self._SEMANTIC_DIRECTORIES)
            ):
                raise ValueError("semantic transition journal identity or version mismatch")
            return {
                directory: self._decode_optional_hex(payload["snapshots"][directory])
                for directory in self._SEMANTIC_DIRECTORIES
            }
        except (TypeError, UnicodeError, ValueError) as exc:
            raise ValueError(
                f"invalid interrupted semantic transition journal for {source_id!r}"
            ) from exc

    def _delete_semantic_transition_journal(self, source_id: str) -> None:
        try:
            directory_fd = self._open_child_directory("transactions", create=False)
        except FileNotFoundError:
            return
        try:
            try:
                os.unlink(
                    f"{source_id}.semantic-transaction.json",
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                return
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    @staticmethod
    def validate_source_state(
        manifest: DocumentManifest,
        quarantine: QuarantineRecord | None,
    ) -> None:
        """Validate the complete canonical disposition pair for one source."""
        is_quarantined = manifest.ingestion_status.value == "quarantined"
        if is_quarantined != (quarantine is not None):
            raise ValueError("quarantined manifests require exactly one quarantine record")
        if quarantine is None:
            if manifest.quarantine_reasons:
                raise ValueError("non-quarantined manifest cannot contain quarantine reasons")
            return
        if (
            quarantine.source_id != manifest.source_id
            or quarantine.quarantine_id != f"quarantine-{manifest.source_id}"
            or quarantine.parser_profile != manifest.parser_profile
            or quarantine.extraction != manifest.extraction
            or quarantine.reason_codes != tuple(sorted(manifest.quarantine_reasons))
        ):
            raise ValueError("manifest and quarantine record contracts do not match")

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
                    f"repository directory may resolve outside repository root: {directory!r}"
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
        CanonicalKnowledgeRepository._atomic_write_bytes(
            directory_fd,
            filename,
            payload.encode("utf-8"),
        )

    @staticmethod
    def _atomic_write_bytes(directory_fd: int, filename: str, payload: bytes) -> None:
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
                    mode="wb",
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
        return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)

    @staticmethod
    def _file_read_flags() -> int:
        return os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)

    @staticmethod
    def _file_create_flags() -> int:
        return os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)

    @staticmethod
    def _lock_open_flags() -> int:
        return os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)

    def _ensure_open(self) -> int:
        if self._root_fd is None:
            raise RuntimeError("repository is closed")
        return self._root_fd

    @staticmethod
    def _require_safe_primitives() -> None:
        required_constants = ("O_DIRECTORY", "O_NONBLOCK", "O_NOFOLLOW")
        if any(not hasattr(os, name) for name in required_constants):
            raise RuntimeError("platform lacks safe descriptor-relative filesystem support")
        if any(function not in os.supports_dir_fd for function in (os.open, os.mkdir, os.unlink)):
            raise RuntimeError("platform lacks safe descriptor-relative filesystem support")
        replace_parameters = inspect.signature(os.replace).parameters
        if not {"src_dir_fd", "dst_dir_fd"}.issubset(replace_parameters):
            raise RuntimeError("platform lacks safe descriptor-relative replace support")
        if not hasattr(fcntl, "flock"):
            raise RuntimeError("platform lacks POSIX source-transition locking")


__all__ = [
    "CanonicalKnowledgeRepository",
    "IngestionFailure",
    "IngestionReport",
    "QuarantineRecord",
    "SemanticBundleEnumerationError",
    "SemanticRepositorySnapshot",
    "SemanticSnapshotChangedError",
]
