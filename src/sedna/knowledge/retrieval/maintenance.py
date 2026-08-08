"""Fail-closed rebuild and parity audit of disposable retrieval projections."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sedna.knowledge.repository import (
    CanonicalKnowledgeRepository,
    SemanticBundleEnumerationError,
)
from sedna.knowledge.retrieval.models import (
    IndexAudit,
    IndexedSourceState,
    RetrievalIndex,
)
from sedna.knowledge.retrieval.projection import project_source_state
from sedna.knowledge.schema import SemanticKnowledgeBundle

_MAX_ISSUES = 32
_MAX_IDENTIFIERS_PER_ISSUE = 16
_MAX_SOURCES = 100_000
_MAX_ARTIFACTS = 10_000_000
_SOURCE_PAGE_SIZE = 100
_MAX_ELAPSED_SECONDS = 7 * 24 * 60 * 60

BoundedCount = Annotated[int, Field(ge=0, le=_MAX_ARTIFACTS)]
BoundedMessage = Annotated[str, Field(min_length=1, max_length=2048)]
BoundedIdentifier = Annotated[str, Field(min_length=1, max_length=512)]


class MaintenanceIssueCode(StrEnum):
    """Closed, actionable reasons a retrieval projection needs attention."""

    CANONICAL_REPOSITORY_INVALID = "canonical_repository_invalid"
    CANONICAL_REPOSITORY_CHANGED = "canonical_repository_changed"
    INDEX_REBUILD_FAILED = "index_rebuild_failed"
    INDEX_UNAVAILABLE = "index_unavailable"
    INDEX_INTEGRITY_FAILURE = "index_integrity_failure"
    MISSING_SOURCE_PROJECTION = "missing_source_projection"
    STALE_SOURCE_PROJECTION = "stale_source_projection"
    ORPHAN_SOURCE_PROJECTION = "orphan_source_projection"


class MaintenanceIssue(BaseModel):
    """One bounded maintenance finding with sampled stable identities."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    code: MaintenanceIssueCode
    message: BoundedMessage
    source_ids: tuple[BoundedIdentifier, ...] = Field(
        default=(), max_length=_MAX_IDENTIFIERS_PER_ISSUE
    )

    @field_validator("source_ids")
    @classmethod
    def normalize_source_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(set(values)))
        if len(normalized) != len(values):
            raise ValueError("maintenance issue source IDs must be unique")
        return normalized


class RetrievalMaintenanceReport(BaseModel):
    """Typed bounded outcome for one rebuild or parity audit operation."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    operation: Literal["rebuild", "audit"]
    succeeded: bool
    canonical_source_count: BoundedCount = 0
    canonical_artifact_count: BoundedCount = 0
    indexed_source_count: BoundedCount = 0
    indexed_artifact_count: BoundedCount = 0
    missing_source_count: BoundedCount = 0
    stale_source_count: BoundedCount = 0
    orphan_source_count: BoundedCount = 0
    missing_artifact_count: BoundedCount = 0
    stale_artifact_count: BoundedCount = 0
    orphan_artifact_count: BoundedCount = 0
    elapsed_seconds: float = Field(ge=0.0, le=_MAX_ELAPSED_SECONDS)
    index_audit: IndexAudit | None = None
    issues: tuple[MaintenanceIssue, ...] = Field(default=(), max_length=_MAX_ISSUES)
    rebuild_required: bool = False

    @field_validator("elapsed_seconds")
    @classmethod
    def require_finite_elapsed(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("maintenance elapsed time must be finite")
        return value

    @field_validator("issues")
    @classmethod
    def normalize_issues(
        cls,
        values: tuple[MaintenanceIssue, ...],
    ) -> tuple[MaintenanceIssue, ...]:
        ordered = tuple(
            sorted(values, key=lambda issue: (issue.code.value, issue.source_ids, issue.message))
        )
        if len(set(ordered)) != len(ordered):
            raise ValueError("maintenance issues must be unique")
        return ordered

    @model_validator(mode="after")
    def derive_rebuild_requirement(self) -> RetrievalMaintenanceReport:
        required = bool(
            not self.succeeded
            or self.issues
            or (self.index_audit is not None and self.index_audit.rebuild_required)
            or self.canonical_source_count != self.indexed_source_count
            or self.canonical_artifact_count != self.indexed_artifact_count
            or any(
                (
                    self.missing_source_count,
                    self.stale_source_count,
                    self.orphan_source_count,
                    self.missing_artifact_count,
                    self.stale_artifact_count,
                    self.orphan_artifact_count,
                )
            )
        )
        object.__setattr__(self, "rebuild_required", required)
        return self


@dataclass(frozen=True, slots=True)
class RetrievalMaintenanceService:
    """Rebuild and compare a disposable index against canonical verified bundles."""

    repository: CanonicalKnowledgeRepository
    index: RetrievalIndex

    def rebuild(self) -> RetrievalMaintenanceReport:
        """Replace the index only after the full canonical corpus validates."""
        started = time.perf_counter()
        try:
            bundles, _ = self._canonical_snapshot()
        except SemanticBundleEnumerationError as error:
            return self._canonical_failure("rebuild", started, error)
        except Exception:
            return self._failure(
                "rebuild",
                started,
                MaintenanceIssueCode.CANONICAL_REPOSITORY_INVALID,
                "canonical semantic repository enumeration failed",
            )

        try:
            self.index.rebuild(bundles)
        except Exception:
            return self._failure(
                "rebuild",
                started,
                MaintenanceIssueCode.INDEX_REBUILD_FAILED,
                "retrieval index rebuild failed and the previous projection was retained",
                canonical_source_count=len(bundles),
                canonical_artifact_count=sum(
                    project_source_state(bundle).artifact_count for bundle in bundles
                ),
            )
        return self._audit(operation="rebuild", started=started)

    def audit(self) -> RetrievalMaintenanceReport:
        """Cross-check canonical source identity and projection parity without mutation."""
        return self._audit(operation="audit", started=time.perf_counter())

    def _audit(
        self,
        *,
        operation: Literal["rebuild", "audit"],
        started: float,
    ) -> RetrievalMaintenanceReport:
        try:
            _, canonical_states = self._canonical_snapshot()
        except SemanticBundleEnumerationError as error:
            return self._canonical_failure(operation, started, error)
        except Exception:
            return self._failure(
                operation,
                started,
                MaintenanceIssueCode.CANONICAL_REPOSITORY_INVALID,
                "canonical semantic repository enumeration failed",
            )

        try:
            index_audit = self.index.audit()
            indexed_states = self._indexed_states()
        except Exception:
            return self._failure(
                operation,
                started,
                MaintenanceIssueCode.INDEX_UNAVAILABLE,
                "retrieval index audit or source-state enumeration failed",
                canonical_source_count=len(canonical_states),
                canonical_artifact_count=sum(
                    state.artifact_count for state in canonical_states.values()
                ),
            )

        try:
            _, latest_canonical_states = self._canonical_snapshot()
        except SemanticBundleEnumerationError as error:
            return self._canonical_failure(operation, started, error)
        except Exception:
            return self._failure(
                operation,
                started,
                MaintenanceIssueCode.CANONICAL_REPOSITORY_INVALID,
                "canonical semantic repository enumeration failed",
            )
        canonical_changed = canonical_states != latest_canonical_states
        canonical_states = latest_canonical_states

        missing_ids = tuple(sorted(canonical_states.keys() - indexed_states.keys()))
        orphan_ids = tuple(sorted(indexed_states.keys() - canonical_states.keys()))
        stale_ids = tuple(
            source_id
            for source_id in sorted(canonical_states.keys() & indexed_states.keys())
            if canonical_states[source_id] != indexed_states[source_id]
        )
        issues: list[MaintenanceIssue] = []
        if canonical_changed:
            issues.append(
                MaintenanceIssue(
                    code=MaintenanceIssueCode.CANONICAL_REPOSITORY_CHANGED,
                    message=(
                        "canonical semantic sources changed during the audit; rebuild from the "
                        "latest complete snapshot"
                    ),
                )
            )
        indexed_state_artifact_count = sum(
            state.artifact_count for state in indexed_states.values()
        )
        if (
            index_audit.rebuild_required
            or index_audit.source_count != len(indexed_states)
            or index_audit.artifact_count != indexed_state_artifact_count
        ):
            issues.append(
                MaintenanceIssue(
                    code=MaintenanceIssueCode.INDEX_INTEGRITY_FAILURE,
                    message="the disposable retrieval index failed its internal integrity audit",
                )
            )
        issues.extend(
            issue
            for issue in (
                _parity_issue(
                    MaintenanceIssueCode.MISSING_SOURCE_PROJECTION,
                    "canonical sources are missing from the retrieval projection",
                    missing_ids,
                ),
                _parity_issue(
                    MaintenanceIssueCode.STALE_SOURCE_PROJECTION,
                    "retrieval source hashes, artifact counts, or projection digests are stale",
                    stale_ids,
                ),
                _parity_issue(
                    MaintenanceIssueCode.ORPHAN_SOURCE_PROJECTION,
                    "retrieval projections exist without a canonical verified source",
                    orphan_ids,
                ),
            )
            if issue is not None
        )

        return RetrievalMaintenanceReport(
            operation=operation,
            succeeded=True,
            canonical_source_count=len(canonical_states),
            canonical_artifact_count=sum(
                state.artifact_count for state in canonical_states.values()
            ),
            indexed_source_count=len(indexed_states),
            indexed_artifact_count=index_audit.artifact_count,
            missing_source_count=len(missing_ids),
            stale_source_count=len(stale_ids),
            orphan_source_count=len(orphan_ids),
            missing_artifact_count=sum(
                canonical_states[source_id].artifact_count for source_id in missing_ids
            ),
            stale_artifact_count=sum(
                canonical_states[source_id].artifact_count for source_id in stale_ids
            ),
            orphan_artifact_count=sum(
                indexed_states[source_id].artifact_count for source_id in orphan_ids
            ),
            elapsed_seconds=_elapsed(started),
            index_audit=index_audit,
            issues=tuple(issues),
        )

    def _canonical_snapshot(
        self,
    ) -> tuple[
        tuple[SemanticKnowledgeBundle, ...],
        dict[str, IndexedSourceState],
    ]:
        bundles = tuple(self.repository.iter_semantic_bundles())
        if len(bundles) > _MAX_SOURCES:
            raise ValueError("canonical semantic source count exceeds the maintenance bound")
        states: dict[str, IndexedSourceState] = {}
        artifact_count = 0
        for bundle in bundles:
            state = project_source_state(bundle)
            if state.source_id in states:
                raise ValueError("canonical semantic source IDs must be unique")
            states[state.source_id] = state
            artifact_count += state.artifact_count
            if artifact_count > _MAX_ARTIFACTS:
                raise ValueError("canonical artifact count exceeds the maintenance bound")
        return bundles, states

    def _indexed_states(self) -> dict[str, IndexedSourceState]:
        states: dict[str, IndexedSourceState] = {}
        artifact_count = 0
        after_source_id: str | None = None
        while True:
            page = self.index.list_source_states(
                after_source_id=after_source_id,
                limit=_SOURCE_PAGE_SIZE,
            )
            if type(page) is not tuple or len(page) > _SOURCE_PAGE_SIZE:
                raise ValueError("retrieval index returned an invalid source-state page")
            if not page:
                return states
            for raw_state in page:
                if type(raw_state) is not IndexedSourceState:
                    raise ValueError("retrieval index returned an invalid source state")
                state = IndexedSourceState.model_validate(raw_state.model_dump(mode="json"))
                if state.source_id in states or (
                    after_source_id is not None and state.source_id <= after_source_id
                ):
                    raise ValueError("retrieval source-state pages are not strictly ordered")
                states[state.source_id] = state
                artifact_count += state.artifact_count
                after_source_id = state.source_id
                if len(states) > _MAX_SOURCES:
                    raise ValueError("indexed source count exceeds the maintenance bound")
                if artifact_count > _MAX_ARTIFACTS:
                    raise ValueError("indexed artifact count exceeds the maintenance bound")
            if len(page) < _SOURCE_PAGE_SIZE:
                return states

    @staticmethod
    def _canonical_failure(
        operation: Literal["rebuild", "audit"],
        started: float,
        error: SemanticBundleEnumerationError,
    ) -> RetrievalMaintenanceReport:
        return RetrievalMaintenanceService._failure(
            operation,
            started,
            MaintenanceIssueCode.CANONICAL_REPOSITORY_INVALID,
            "a canonical semantic source is corrupt, incomplete, unsafe, or stale",
            source_ids=(error.source_id,),
        )

    @staticmethod
    def _failure(
        operation: Literal["rebuild", "audit"],
        started: float,
        code: MaintenanceIssueCode,
        message: str,
        *,
        source_ids: tuple[str, ...] = (),
        canonical_source_count: int = 0,
        canonical_artifact_count: int = 0,
    ) -> RetrievalMaintenanceReport:
        return RetrievalMaintenanceReport(
            operation=operation,
            succeeded=False,
            canonical_source_count=canonical_source_count,
            canonical_artifact_count=canonical_artifact_count,
            elapsed_seconds=_elapsed(started),
            issues=(
                MaintenanceIssue(
                    code=code,
                    message=message,
                    source_ids=source_ids,
                ),
            ),
        )


def _parity_issue(
    code: MaintenanceIssueCode,
    message: str,
    source_ids: tuple[str, ...],
) -> MaintenanceIssue | None:
    if not source_ids:
        return None
    return MaintenanceIssue(
        code=code,
        message=message,
        source_ids=source_ids[:_MAX_IDENTIFIERS_PER_ISSUE],
    )


def _elapsed(started: float) -> float:
    return min(_MAX_ELAPSED_SECONDS, max(0.0, time.perf_counter() - started))


__all__ = [
    "MaintenanceIssue",
    "MaintenanceIssueCode",
    "RetrievalMaintenanceReport",
    "RetrievalMaintenanceService",
]
