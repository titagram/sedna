"""Strict, dependency-neutral contracts for versioned engagement state."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, TypeAlias
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from sedna.knowledge.retrieval import AuthorizationScope, AuthorizationState, TargetKind

ENGAGEMENT_MANIFEST_SCHEMA_VERSION = "sedna.engagement-manifest.v1"
EVENT_ENVELOPE_SCHEMA_VERSION = "sedna.journal-event.v1"
ENGAGEMENT_STATE_PROJECTION_SCHEMA_VERSION = "sedna.engagement-state.v1"
LANE_POLICY_VERSION = "sedna.execution-lane.v1"
CORRELATION_POLICY_VERSION = "sedna.tool-correlation.v1"
MAX_JOURNAL_EVENT_BYTES = 65_536
MAX_JOURNAL_BATCH_EVENTS = 512
MAX_MANIFEST_BYTES = 1 * 1024 * 1024
MAX_JOURNAL_BYTES = 256 * 1024 * 1024
MAX_JOURNAL_EVENTS = 100_000
MAX_ENGAGEMENTS = 10_000
MAX_ENGAGEMENT_DIRECTORY_ENTRIES = 11_000
MAX_EVIDENCE_OBJECTS = 110_000
MAX_EVIDENCE_DIRECTORY_ENTRIES = 120_000
MAX_EVIDENCE_ITEM_BYTES = 64 * 1024 * 1024
MAX_EVIDENCE_ENGAGEMENT_BYTES = 4 * 1024 * 1024 * 1024
MAX_JOURNAL_HEAD_BYTES = 16 * 1024
MAX_CREATE_INTENT_BYTES = (
    4 * ((MAX_MANIFEST_BYTES + 2 * (MAX_JOURNAL_EVENT_BYTES + 1) + 2) // 3)
    + 128 * 1024
)
MAX_PENDING_APPEND_BYTES = (
    4 * ((MAX_JOURNAL_BATCH_EVENTS * (MAX_JOURNAL_EVENT_BYTES + 1) + 2) // 3) + 128 * 1024
)
MAX_TAIL_RECOVERY_INTENT_BYTES = 256 * 1024
MAX_CAPTURE_INTENT_BYTES = 64 * 1024
MAX_RECOVERABLE_TAIL_BYTES = MAX_JOURNAL_EVENT_BYTES
MAX_DERIVED_PROJECTION_BYTES = 64 * 1024 * 1024
MAX_TOOL_NAME_CHARS = 256
MAX_HOST_CORRELATION_ID_CHARS = 512
MAX_TOOL_CALL_ORDINAL = 65_535
MAX_API_CALL_COUNT = 1_000_000
MAX_TOOL_DURATION_MS = 86_400_000
MAX_REQUIRED_PROOFS = 64
MAX_SCOPE_EVENT_BYTES = 60 * 1024
MAX_IN_FLIGHT_CALLS = 512
MAX_SETTLEMENT_PENDING_RANGES = 2_147_483_647
MAX_HOST_RESULT_BYTES = 256 * 1024
MAX_PUBLIC_INVENTORY_ITEMS = 64
MAX_HEALTH_ENTRIES_PER_STORE = 512
MAX_HEALTH_ENTRIES_TOTAL = 4_096
MAX_HEALTH_OCCURRENCES = 2_147_483_647


class HostKind(StrEnum):
    HADES = "hades"
    HERMES = "hermes"
    OTHER = "other"


class EngagementStatus(StrEnum):
    ACTIVE = "active"
    CLOSING = "closing"
    CLOSED_UNVERIFIED = "closed_unverified"
    CLOSED_VERIFIED = "closed_verified"
    ABANDONED = "abandoned"


class ExecutionLaneKey(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    host_kind: HostKind
    session_id: Annotated[str, Field(min_length=1, max_length=512)]
    task_id: Annotated[str, Field(min_length=1, max_length=512)]

    @classmethod
    def from_host(
        cls,
        *,
        host_kind: HostKind,
        session_id: str,
        task_id: str | None,
    ) -> ExecutionLaneKey:
        clean_session = session_id.strip()
        if not clean_session:
            raise ValueError("session_id is required")
        clean_task = (task_id or "").strip() or f"root:{clean_session}"
        return cls(host_kind=host_kind, session_id=clean_session, task_id=clean_task)

    @field_validator("session_id", "task_id")
    @classmethod
    def reject_surrounding_whitespace(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("lane identifiers cannot contain surrounding whitespace")
        return value

    @property
    def stable_key(self) -> str:
        payload = f"{self.host_kind.value}\0{self.session_id}\0{self.task_id}".encode()
        return f"lane-{sha256(payload).hexdigest()[:32]}"


class ScopeReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    reference_id: Annotated[str, Field(pattern=r"^scope-[0-9a-f]{32}$")]
    kind: Literal["exact_target", "cidr", "hostname", "url_origin", "generic_id"]
    value: Annotated[str, Field(min_length=1, max_length=2048)]


EvidenceId: TypeAlias = Annotated[str, Field(pattern=r"^evidence-sha256-[0-9a-f]{64}$")]
Sha256Hex: TypeAlias = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PendingSubjectCursor: TypeAlias = Annotated[str, Field(pattern=r"^pending-[0-9a-f]{64}$")]


def validate_confined_relative_path(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("confined relative path must be a string")
    if not value or len(value) > 4096 or "\0" in value or "\\" in value:
        raise ValueError("invalid confined relative path")
    if value.startswith("/") or value != unicodedata.normalize("NFC", value):
        raise ValueError("invalid confined relative path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("invalid confined relative path")
    if re.match(r"^[A-Za-z]:", parts[0]):
        raise ValueError("invalid confined relative path")
    if PurePosixPath(value).as_posix() != value:
        raise ValueError("invalid confined relative path")
    return value


ConfinedRelativePath: TypeAlias = Annotated[
    str,
    BeforeValidator(validate_confined_relative_path),
    Field(min_length=1, max_length=4096),
]


class ProofRequirement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    proof_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
    kind: Literal["flag", "access", "custom"]
    description: Annotated[str, Field(min_length=1, max_length=512)]

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("description is required")
        return normalized


class CaptureLimitation(StrEnum):
    PROVIDER_OR_HOST_SECRET_REDACTED = "provider_or_host_secret_redacted_before_persistence"
    HOST_REPORTED_TRUNCATION = "host_reported_truncation"
    EXTERNAL_ARTIFACT_NOT_CAPTURED = "external_artifact_not_captured"


SettlementSafeCode: TypeAlias = Literal[
    "evidence_budget_exhausted",
    "interpretation_incomplete",
    "interpretation_failed",
    "journal_unavailable",
    "journal_corrupt",
    "settlement_unavailable",
]


class JournalRevision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    sequence: StrictInt = Field(ge=0, le=MAX_JOURNAL_EVENTS)
    event_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def scope_references(scope: AuthorizationScope) -> tuple[ScopeReference, ...]:
    """Expand an already-normalized authorization into stable sorted references."""

    expanded: list[tuple[str, str]] = []
    for target in scope.exact_targets:
        if target.kind is TargetKind.INVALID or target.normalized is None:
            raise ValueError("scope contains an invalid exact target")
        expanded.append(("exact_target", target.normalized))
    expanded.extend(("cidr", value) for value in scope.cidrs)
    expanded.extend(("hostname", value) for value in scope.hostnames)
    expanded.extend(("url_origin", value) for value in scope.url_origins)
    expanded.extend(("generic_id", value) for value in scope.generic_ids)
    references: list[ScopeReference] = []
    for kind, value in sorted(expanded):
        digest = sha256((kind + "\0" + value).encode()).hexdigest()[:32]
        references.append(
            ScopeReference(
                reference_id=f"scope-{digest}",
                kind=kind,
                value=value,
            )
        )
    return tuple(references)


class HostIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    kind: HostKind
    adapter_version: Annotated[str, Field(min_length=1, max_length=128)]


class EngagementManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    schema_version: Literal[ENGAGEMENT_MANIFEST_SCHEMA_VERSION] = ENGAGEMENT_MANIFEST_SCHEMA_VERSION
    engagement_id: UUID
    display_name: Annotated[str, Field(min_length=1, max_length=256)]
    initial_objective: Annotated[str, Field(min_length=1, max_length=8192)]
    initial_scope: AuthorizationScope
    required_proofs: tuple[ProofRequirement, ...] = Field(
        default=(), max_length=MAX_REQUIRED_PROOFS
    )
    created_at: datetime
    created_by_host: HostIdentity

    @field_validator("display_name", "initial_objective", mode="before")
    @classmethod
    def normalize_display_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            return " ".join(value.split())
        return value

    @field_validator("created_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("created_at must be UTC-aware")
        return value

    @model_validator(mode="after")
    def validate_manifest(self) -> EngagementManifest:
        if self.initial_scope.state is not AuthorizationState.AUTHORIZED:
            raise ValueError("initial_scope must be authorized")
        if not scope_references(self.initial_scope):
            raise ValueError("initial_scope must contain a target")
        proof_ids = [item.proof_id for item in self.required_proofs]
        if len(set(proof_ids)) != len(proof_ids):
            raise ValueError("required proof_id values must be unique")
        return self


class EvidenceReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    evidence_id: EvidenceId
    sha256: Sha256Hex
    size: StrictInt = Field(ge=0, le=MAX_EVIDENCE_ITEM_BYTES)
    media_type: Annotated[str, Field(min_length=1, max_length=256)]
    representation: Annotated[str, Field(min_length=1, max_length=128)]
    relative_path: ConfinedRelativePath
    capture_limitations: tuple[CaptureLimitation, ...] = Field(default=(), max_length=3)

    @model_validator(mode="after")
    def validate_reference(self) -> EvidenceReference:
        if self.evidence_id != f"evidence-sha256-{self.sha256}":
            raise ValueError("evidence_id must contain the content digest")
        if len(set(self.capture_limitations)) != len(self.capture_limitations):
            raise ValueError("capture_limitations must be unique")
        ordered = tuple(sorted(self.capture_limitations, key=lambda item: item.value))
        object.__setattr__(self, "capture_limitations", ordered)
        return self


class EvidenceSlice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    evidence_id: EvidenceId
    offset: StrictInt = Field(ge=0)
    data: bytes
    complete: bool


class HostAdaptedCommandRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    origin: Literal["host_adapted"] = "host_adapted"
    command_template: Annotated[str, Field(min_length=1, max_length=8192)]
    placeholder_names: tuple[Annotated[str, Field(min_length=1, max_length=64)], ...] = Field(
        default=(), max_length=32
    )
    adaptation_note: Annotated[str | None, Field(min_length=1, max_length=2048)] = None
    requires_validation: Literal[True] = True

    @model_validator(mode="after")
    def validate_record(self) -> HostAdaptedCommandRecord:
        if len(set(self.placeholder_names)) != len(self.placeholder_names):
            raise ValueError("placeholder_names must be unique")
        if len(self.model_dump_json().encode("utf-8")) > 16 * 1024:
            raise ValueError("host-adapted command record exceeds 16 KiB")
        return self


class ActiveDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    lane: ExecutionLaneKey
    decision_id: Annotated[str, Field(min_length=1, max_length=512)]
    proposal_id: UUID | None = None
    strategy: Annotated[str, Field(min_length=1, max_length=8192)]
    rationale: Annotated[str, Field(min_length=1, max_length=8192)]
    host_adapted_command: HostAdaptedCommandRecord | None = None


class ClosureBarrier(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    event_id: UUID
    terminal_watermark: StrictInt = Field(ge=0, le=MAX_JOURNAL_EVENTS)
    in_flight_call_ids: tuple[Annotated[str, Field(min_length=1, max_length=512)], ...] = Field(
        default=(), max_length=MAX_IN_FLIGHT_CALLS
    )
    origin: Literal["manual", "proof_settlement"] = "manual"

    @model_validator(mode="after")
    def validate_calls(self) -> ClosureBarrier:
        if tuple(sorted(set(self.in_flight_call_ids))) != self.in_flight_call_ids:
            raise ValueError("in_flight_call_ids must be sorted and unique")
        return self


class LaneBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    lane: ExecutionLaneKey
    engagement_id: UUID


class EngagementState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    revision: JournalRevision
    status: EngagementStatus
    scope_references: tuple[ScopeReference, ...]
    bound_lanes: tuple[LaneBinding, ...] = ()
    active_decisions: tuple[ActiveDecision, ...] = ()
    in_flight_call_ids: tuple[Annotated[str, Field(min_length=1, max_length=512)], ...] = Field(
        default=(), max_length=MAX_IN_FLIGHT_CALLS
    )
    closure: ClosureBarrier | None = None
    closure_ready: bool = False
    projection_version: Literal[ENGAGEMENT_STATE_PROJECTION_SCHEMA_VERSION] = (
        ENGAGEMENT_STATE_PROJECTION_SCHEMA_VERSION
    )
    journal_healthy: bool = True

    @model_validator(mode="after")
    def validate_state(self) -> EngagementState:
        if tuple(sorted(set(self.in_flight_call_ids))) != self.in_flight_call_ids:
            raise ValueError("in_flight_call_ids must be sorted and unique")
        if self.closure_ready and self.closure is None:
            raise ValueError("closure_ready requires a closure barrier")
        return self


class OrphanEvidencePage(BaseModel):
    """A frozen bounded page of orphan evidence names/digests."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    names: tuple[str, ...] = Field(max_length=256)
    total_count: StrictInt = Field(ge=0, le=MAX_EVIDENCE_OBJECTS)
    next_after_name: str | None = None
    summary: tuple[tuple[Sha256Hex, StrictInt], ...] = ()
