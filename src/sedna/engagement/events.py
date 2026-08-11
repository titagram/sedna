"""Closed payloads and versioned event envelopes for engagement journals."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Any, Literal, TypeAlias
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from sedna.engagement.models import (
    CORRELATION_POLICY_VERSION,
    ENGAGEMENT_STATE_PROJECTION_SCHEMA_VERSION,
    EVENT_ENVELOPE_SCHEMA_VERSION,
    MAX_API_CALL_COUNT,
    MAX_EVIDENCE_ITEM_BYTES,
    MAX_HOST_CORRELATION_ID_CHARS,
    MAX_IN_FLIGHT_CALLS,
    MAX_JOURNAL_EVENT_BYTES,
    MAX_JOURNAL_EVENTS,
    MAX_SETTLEMENT_PENDING_RANGES,
    MAX_TOOL_CALL_ORDINAL,
    MAX_TOOL_DURATION_MS,
    MAX_TOOL_NAME_CHARS,
    EngagementManifest,
    EngagementState,
    EvidenceId,
    EvidenceReference,
    ExecutionLaneKey,
    HostAdaptedCommandRecord,
    JournalRevision,
    PendingSubjectCursor,
    ScopeReference,
    SettlementSafeCode,
    Sha256Hex,
    scope_references,
)
from sedna.engagement.normalization import NormalizationFailure, SanitizedHostValue
from sedna.knowledge.retrieval import AuthorizationScope, AuthorizationState

CONTROL_TOOL_POLICY_VERSION = "sedna.control-tools.v1"
CONTROL_TOOL_NAMES = frozenset(
    {
        "sedna_manage_engagement",
        "sedna_plan_next",
        "sedna_record_decision",
        "sedna_add_source",
        "sedna_learn_local",
        "sedna_retrieve_knowledge",
        "sedna_get_knowledge_artifact",
        "sedna_knowledge_maintenance",
    }
)

CorrelationReason: TypeAlias = Literal[
    "missing_stable_identity",
    "normalization_failed",
]


class CorrelationKind(StrEnum):
    TOOL_CALL_ID = "tool_call_id"
    API_ATTEMPT = "api_attempt"
    UNCERTAIN = "uncertain"


def _bounded_identity(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if len(value) > MAX_HOST_CORRELATION_ID_CHARS:
        raise ValueError(f"{field_name} exceeds its bound")
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _bounded_strict_int(
    value: int | None,
    *,
    field_name: str,
    maximum: int,
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError(f"{field_name} must be an in-range integer")
    return value


class ToolCorrelation(BaseModel):
    """A bounded correlation decision derived only from sanitized host metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    policy_version: Literal[CORRELATION_POLICY_VERSION] = CORRELATION_POLICY_VERSION
    kind: CorrelationKind
    lane_key: Annotated[str | None, Field(pattern=r"^lane-[0-9a-f]{32}$")] = None
    host_tool_call_id_sha256: Sha256Hex | None = None
    turn_id: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    api_request_id: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    api_call_count: StrictInt | None = Field(default=None, ge=0, le=MAX_API_CALL_COUNT)
    tool_call_ordinal: StrictInt | None = Field(default=None, ge=0, le=MAX_TOOL_CALL_ORDINAL)
    tool_name_sha256: Sha256Hex | None = None
    sanitized_argument_sha256: Sha256Hex | None = None
    stable_key: Annotated[str | None, Field(pattern=r"^correlation-[0-9a-f]{64}$")] = None
    deduplication_allowed: bool
    reason: CorrelationReason | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> ToolCorrelation:
        stable = self.kind in {CorrelationKind.TOOL_CALL_ID, CorrelationKind.API_ATTEMPT}
        if stable:
            if not self.deduplication_allowed or self.reason is not None or self.stable_key is None:
                raise ValueError("stable correlation has an invalid shape")
        elif self.deduplication_allowed or self.reason is None or self.stable_key is not None:
            raise ValueError("uncertain correlation has an invalid shape")
        return self

    @property
    def call_id(self) -> str | None:
        if self.stable_key is None:
            return None
        return f"call-{sha256(self.stable_key.encode()).hexdigest()}"

    @classmethod
    def uncertain(cls, reason: CorrelationReason) -> ToolCorrelation:
        return cls(
            kind=CorrelationKind.UNCERTAIN,
            deduplication_allowed=False,
            reason=reason,
        )

    @classmethod
    def from_hook(
        cls,
        *,
        lane: ExecutionLaneKey,
        tool_name: str,
        sanitized_arguments: SanitizedHostValue | NormalizationFailure,
        tool_call_id: str | None = None,
        turn_id: str | None = None,
        api_request_id: str | None = None,
        api_call_count: int | None = None,
        tool_call_ordinal: int | None = None,
    ) -> ToolCorrelation:
        if not isinstance(tool_name, str):
            raise ValueError("tool_name is required")
        if len(tool_name) > MAX_TOOL_NAME_CHARS:
            raise ValueError("tool_name is required and bounded")
        normalized_tool_name = tool_name.strip()
        if not normalized_tool_name:
            raise ValueError("tool_name is required and bounded")
        host_tool_call_id = _bounded_identity(tool_call_id, "tool_call_id")
        normalized_turn = _bounded_identity(turn_id, "turn_id")
        normalized_request = _bounded_identity(api_request_id, "api_request_id")
        normalized_count = _bounded_strict_int(
            api_call_count,
            field_name="api_call_count",
            maximum=MAX_API_CALL_COUNT,
        )
        normalized_ordinal = _bounded_strict_int(
            tool_call_ordinal,
            field_name="tool_call_ordinal",
            maximum=MAX_TOOL_CALL_ORDINAL,
        )
        if not isinstance(sanitized_arguments, (SanitizedHostValue, NormalizationFailure)):
            raise TypeError("sanitized_arguments must be a bounded normalized host value")
        if (
            isinstance(sanitized_arguments, SanitizedHostValue)
            and not sanitized_arguments.has_valid_integrity()
        ):
            sanitized_arguments = NormalizationFailure(reason_code="serialization_failed")

        tool_digest = sha256(normalized_tool_name.encode()).hexdigest()
        argument_digest = (
            sanitized_arguments.canonical_digest
            if isinstance(sanitized_arguments, SanitizedHostValue)
            else None
        )
        common: dict[str, Any] = {
            "lane_key": lane.stable_key,
            "turn_id": normalized_turn,
            "api_request_id": normalized_request,
            "api_call_count": normalized_count,
            "tool_call_ordinal": normalized_ordinal,
            "tool_name_sha256": tool_digest,
            "sanitized_argument_sha256": argument_digest,
        }
        if host_tool_call_id is not None:
            host_digest = sha256(host_tool_call_id.encode()).hexdigest()
            material = f"tool-call-id\0{lane.stable_key}\0{host_tool_call_id}".encode()
            return cls(
                kind=CorrelationKind.TOOL_CALL_ID,
                host_tool_call_id_sha256=host_digest,
                stable_key=f"correlation-{sha256(material).hexdigest()}",
                deduplication_allowed=True,
                **common,
            )
        if argument_digest is None:
            return cls.uncertain("normalization_failed")
        if (
            normalized_turn is not None
            and normalized_request is not None
            and normalized_count is not None
            and normalized_ordinal is not None
        ):
            material = "\0".join(
                (
                    "api-attempt",
                    lane.stable_key,
                    normalized_turn,
                    normalized_request,
                    str(normalized_count),
                    str(normalized_ordinal),
                    normalized_tool_name,
                    argument_digest,
                )
            ).encode()
            return cls(
                kind=CorrelationKind.API_ATTEMPT,
                stable_key=f"correlation-{sha256(material).hexdigest()}",
                deduplication_allowed=True,
                **common,
            )
        return cls(
            kind=CorrelationKind.UNCERTAIN,
            deduplication_allowed=False,
            reason="missing_stable_identity",
            **common,
        )


class SystemCorrelation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    kind: Literal["system"] = "system"
    source: Literal[
        "recovery",
        "proof_settlement",
        "lifecycle",
        "planning",
        "reporting",
        "promotion",
    ]
    operation_id: UUID


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


class EngagementOpenedPayload(_Payload):
    kind: Literal["engagement_opened"] = "engagement_opened"
    scope_references: tuple[ScopeReference, ...]


class EngagementResumedPayload(_Payload):
    kind: Literal["engagement_resumed"] = "engagement_resumed"
    reason: Annotated[str, Field(min_length=1, max_length=2048)]


class LaneBoundPayload(_Payload):
    kind: Literal["lane_bound"] = "lane_bound"
    lane: ExecutionLaneKey
    binding_reason: Annotated[str, Field(min_length=1, max_length=2048)]


class LaneUnboundPayload(_Payload):
    kind: Literal["lane_unbound"] = "lane_unbound"
    lane: ExecutionLaneKey
    reason: Annotated[str, Field(min_length=1, max_length=2048)]


class ChildLaneLinkedPayload(_Payload):
    kind: Literal["child_lane_linked"] = "child_lane_linked"
    parent_session_id: Annotated[str, Field(min_length=1, max_length=512)]
    child_session_id: Annotated[str, Field(min_length=1, max_length=512)]
    child_subagent_id: Annotated[str, Field(min_length=1, max_length=512)]


class SessionStartedPayload(_Payload):
    kind: Literal["session_started"] = "session_started"
    model: Annotated[str, Field(min_length=1, max_length=512)]
    platform: Annotated[str, Field(min_length=1, max_length=512)]


class SessionCheckpointedPayload(_Payload):
    kind: Literal["session_checkpointed"] = "session_checkpointed"
    completed: bool
    interrupted: bool
    reason: Annotated[str, Field(min_length=1, max_length=2048)]


class SessionFinalizedPayload(_Payload):
    kind: Literal["session_finalized"] = "session_finalized"
    reason: Annotated[str, Field(min_length=1, max_length=2048)]
    settlement_status: Literal[
        "not_configured", "complete", "incomplete", "failed", "unavailable"
    ] = "not_configured"
    pending_range_count: StrictInt = Field(default=0, ge=0, le=MAX_SETTLEMENT_PENDING_RANGES)
    next_pending_offset: StrictInt | None = Field(
        default=None, ge=0, le=MAX_EVIDENCE_ITEM_BYTES
    )
    next_pending_subject: PendingSubjectCursor | None = None
    pending_inventory_sha256: Sha256Hex | None = None
    safe_code: SettlementSafeCode | None = None

    @model_validator(mode="after")
    def validate_settlement(self) -> SessionFinalizedPayload:
        empty = (
            self.pending_range_count == 0
            and self.next_pending_offset is None
            and self.next_pending_subject is None
            and self.pending_inventory_sha256 is None
        )
        if self.settlement_status in {"not_configured", "complete"}:
            if not empty or self.safe_code is not None:
                raise ValueError("completed settlement must have an empty pending shape")
        elif self.settlement_status == "incomplete":
            if (
                self.pending_range_count <= 0
                or self.pending_inventory_sha256 is None
                or self.safe_code
                not in {"evidence_budget_exhausted", "interpretation_incomplete"}
            ):
                raise ValueError("incomplete settlement requires bounded pending inventory")
        elif self.settlement_status == "failed":
            if self.safe_code != "interpretation_failed":
                raise ValueError("failed settlement requires interpretation_failed")
            if not empty and (
                self.pending_range_count <= 0 or self.pending_inventory_sha256 is None
            ):
                raise ValueError("failed settlement has an invalid pending shape")
        elif not empty or self.safe_code not in {
            "journal_unavailable",
            "journal_corrupt",
            "settlement_unavailable",
        }:
            raise ValueError("unavailable settlement has an invalid closed shape")
        return self


class ObjectiveChangedPayload(_Payload):
    kind: Literal["objective_changed"] = "objective_changed"
    objective: Annotated[str, Field(min_length=1, max_length=8192)]
    authorization_basis: Annotated[str, Field(min_length=1, max_length=2048)]


class ScopeChangedPayload(_Payload):
    kind: Literal["scope_changed"] = "scope_changed"
    scope: AuthorizationScope
    scope_references: tuple[ScopeReference, ...]
    authorization_basis: Annotated[str, Field(min_length=1, max_length=2048)]

    @model_validator(mode="after")
    def validate_scope(self) -> ScopeChangedPayload:
        if self.scope.state is not AuthorizationState.AUTHORIZED:
            raise ValueError("changed scope must be authorized")
        if self.scope_references != scope_references(self.scope):
            raise ValueError("scope_references must exactly describe scope")
        return self


class DecisionRecordedPayload(_Payload):
    kind: Literal["decision_recorded"] = "decision_recorded"
    decision_id: Annotated[str, Field(min_length=1, max_length=512)]
    proposal_id: UUID | None = None
    strategy: Annotated[str, Field(min_length=1, max_length=8192)]
    rationale: Annotated[str, Field(min_length=1, max_length=8192)]
    host_adapted_command: HostAdaptedCommandRecord | None = None


class AgentDeviationRecordedPayload(_Payload):
    kind: Literal["agent_deviation_recorded"] = "agent_deviation_recorded"
    decision_id: Annotated[str, Field(min_length=1, max_length=512)]
    strategy: Annotated[str, Field(min_length=1, max_length=8192)]
    rationale: Annotated[str, Field(min_length=1, max_length=8192)]


class _EvidencePairPayload(_Payload):
    evidence_id: EvidenceId | None = None
    evidence_attachment_event_id: UUID | None = None

    @model_validator(mode="after")
    def validate_evidence_pair(self) -> _EvidencePairPayload:
        if (self.evidence_id is None) != (self.evidence_attachment_event_id is None):
            raise ValueError("evidence and attachment event IDs must be supplied together")
        return self


class ToolCallStartedPayload(_Payload):
    kind: Literal["tool_call_started"] = "tool_call_started"
    call_id: Annotated[str, Field(min_length=1, max_length=512)]
    tool_name: Annotated[str, Field(min_length=1, max_length=MAX_TOOL_NAME_CHARS)]
    correlation: ToolCorrelation
    safe_arguments: dict[str, Any]
    argument_evidence_id: EvidenceId | None = None
    argument_attachment_event_id: UUID | None = None
    decision_id: Annotated[str | None, Field(min_length=1, max_length=512)] = None

    @model_validator(mode="after")
    def validate_argument_pair(self) -> ToolCallStartedPayload:
        if (self.argument_evidence_id is None) != (self.argument_attachment_event_id is None):
            raise ValueError("argument evidence and attachment event IDs must be supplied together")
        try:
            encoded = json.dumps(
                self.safe_arguments,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ValueError("safe_arguments must be canonical JSON") from exc
        if len(encoded) > MAX_JOURNAL_EVENT_BYTES:
            raise ValueError("safe_arguments exceed the journal event byte bound")
        return self


class ToolCallCompletedPayload(_EvidencePairPayload):
    kind: Literal["tool_call_completed"] = "tool_call_completed"
    call_id: Annotated[str, Field(min_length=1, max_length=512)]
    correlation: ToolCorrelation
    technical_status: Literal["returned", "blocked", "cancelled", "error", "unknown"]
    duration_ms: StrictInt = Field(ge=0, le=MAX_TOOL_DURATION_MS)
    error_type: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    possible_terminal_evidence: bool = False


class ToolCallTerminatedPayload(_Payload):
    kind: Literal["tool_call_terminated"] = "tool_call_terminated"
    call_id: Annotated[str, Field(min_length=1, max_length=512)]
    resolution: Literal["timed_out", "abandoned"]
    reason: Annotated[str, Field(min_length=1, max_length=2048)]


class EvidenceAttachedPayload(_Payload):
    kind: Literal["evidence_attached"] = "evidence_attached"
    evidence: EvidenceReference


class EvidenceCaptureFailedPayload(_Payload):
    kind: Literal["evidence_capture_failed"] = "evidence_capture_failed"
    call_id: Annotated[str, Field(min_length=1, max_length=512)]
    capture_role: Literal["arguments", "result"]
    reason_code: Literal[
        "item_quota_exceeded",
        "engagement_quota_exceeded",
        "evidence_object_limit_exceeded",
        "normalization_limit_exceeded",
        "unsupported_value",
        "serialization_failed",
        "external_artifact_unavailable",
    ]
    observed_size: StrictInt | None = Field(default=None, ge=0, le=MAX_EVIDENCE_ITEM_BYTES)
    observed_sha256: Sha256Hex | None = None

    @model_validator(mode="after")
    def validate_observation(self) -> EvidenceCaptureFailedPayload:
        if (self.observed_size is None) != (self.observed_sha256 is None):
            raise ValueError("observed size and digest must be supplied together")
        quota_reasons = {
            "item_quota_exceeded",
            "engagement_quota_exceeded",
            "evidence_object_limit_exceeded",
        }
        if self.reason_code in quota_reasons and self.observed_size is None:
            raise ValueError("quota failures require a safe observed size and digest")
        if self.reason_code not in quota_reasons and self.observed_size is not None:
            raise ValueError("this failure cannot retain observed material")
        return self


class UnmatchedToolCompletionPayload(_EvidencePairPayload):
    kind: Literal["unmatched_tool_completion"] = "unmatched_tool_completion"
    correlation: ToolCorrelation
    technical_status: Literal["returned", "blocked", "cancelled", "error", "unknown"]
    duration_ms: StrictInt = Field(ge=0, le=MAX_TOOL_DURATION_MS)
    reason_code: Annotated[str, Field(min_length=1, max_length=512)]


class UnplannedActionPayload(_Payload):
    kind: Literal["unplanned_action"] = "unplanned_action"
    call_id: Annotated[str, Field(min_length=1, max_length=512)]
    reason: Annotated[str, Field(min_length=1, max_length=2048)]


class ControlToolInvokedPayload(_Payload):
    kind: Literal["control_tool_invoked"] = "control_tool_invoked"
    control_tool: Annotated[str, Field(min_length=1, max_length=MAX_TOOL_NAME_CHARS)]
    policy_version: Literal[CONTROL_TOOL_POLICY_VERSION] = CONTROL_TOOL_POLICY_VERSION
    correlation: ToolCorrelation

    @field_validator("control_tool")
    @classmethod
    def require_control_tool(cls, value: str) -> str:
        if value not in CONTROL_TOOL_NAMES:
            raise ValueError("control_tool is not in the versioned allowlist")
        return value


class ClosureRequestedPayload(_Payload):
    kind: Literal["closure_requested"] = "closure_requested"
    terminal_watermark: StrictInt = Field(ge=0, le=MAX_JOURNAL_EVENTS)
    in_flight_call_ids: tuple[Annotated[str, Field(min_length=1, max_length=512)], ...] = Field(
        default=(), max_length=MAX_IN_FLIGHT_CALLS
    )
    reason: Annotated[str, Field(min_length=1, max_length=2048)]
    origin: Literal["manual", "proof_settlement"] = "manual"

    @model_validator(mode="after")
    def normalize_calls(self) -> ClosureRequestedPayload:
        if len(set(self.in_flight_call_ids)) != len(self.in_flight_call_ids):
            raise ValueError("in_flight_call_ids must be unique")
        object.__setattr__(self, "in_flight_call_ids", tuple(sorted(self.in_flight_call_ids)))
        return self


class ClosureCancelledPayload(_Payload):
    kind: Literal["closure_cancelled"] = "closure_cancelled"
    closure_event_id: UUID
    reason: Annotated[str, Field(min_length=1, max_length=2048)]


class EngagementReopenedPayload(_Payload):
    kind: Literal["engagement_reopened"] = "engagement_reopened"
    reason: Annotated[str, Field(min_length=1, max_length=2048)]


class EngagementAbandonedPayload(_Payload):
    kind: Literal["engagement_abandoned"] = "engagement_abandoned"
    reason: Annotated[str, Field(min_length=1, max_length=2048)]


class SourceSuggestedPayload(_Payload):
    kind: Literal["source_suggested"] = "source_suggested"
    source_id: Annotated[str, Field(min_length=1, max_length=512)]
    locator: Annotated[str, Field(min_length=1, max_length=4096)]


class RecoveryWarningPayload(_Payload):
    kind: Literal["recovery_warning"] = "recovery_warning"
    reason_code: Annotated[str, Field(min_length=1, max_length=512)]
    evidence_id: EvidenceId


class UncertainCorrelationPayload(_Payload):
    kind: Literal["uncertain_correlation"] = "uncertain_correlation"
    call_id: Annotated[str, Field(min_length=1, max_length=512)]
    reason_code: CorrelationReason


class UserNotePayload(_Payload):
    kind: Literal["user_note"] = "user_note"
    note: Annotated[str, Field(min_length=1, max_length=8192)]


EventPayload: TypeAlias = Annotated[
    EngagementOpenedPayload
    | EngagementResumedPayload
    | LaneBoundPayload
    | LaneUnboundPayload
    | ChildLaneLinkedPayload
    | SessionStartedPayload
    | SessionCheckpointedPayload
    | SessionFinalizedPayload
    | ObjectiveChangedPayload
    | ScopeChangedPayload
    | DecisionRecordedPayload
    | AgentDeviationRecordedPayload
    | ToolCallStartedPayload
    | ToolCallCompletedPayload
    | ToolCallTerminatedPayload
    | EvidenceAttachedPayload
    | EvidenceCaptureFailedPayload
    | UnmatchedToolCompletionPayload
    | UnplannedActionPayload
    | ControlToolInvokedPayload
    | ClosureRequestedPayload
    | ClosureCancelledPayload
    | EngagementReopenedPayload
    | EngagementAbandonedPayload
    | SourceSuggestedPayload
    | RecoveryWarningPayload
    | UncertainCorrelationPayload
    | UserNotePayload,
    Field(discriminator="kind"),
]


class EventType(StrEnum):
    ENGAGEMENT_OPENED = "engagement_opened"
    ENGAGEMENT_RESUMED = "engagement_resumed"
    LANE_BOUND = "lane_bound"
    LANE_UNBOUND = "lane_unbound"
    CHILD_LANE_LINKED = "child_lane_linked"
    SESSION_STARTED = "session_started"
    SESSION_CHECKPOINTED = "session_checkpointed"
    SESSION_FINALIZED = "session_finalized"
    OBJECTIVE_CHANGED = "objective_changed"
    SCOPE_CHANGED = "scope_changed"
    DECISION_RECORDED = "decision_recorded"
    AGENT_DEVIATION_RECORDED = "agent_deviation_recorded"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    TOOL_CALL_TERMINATED = "tool_call_terminated"
    EVIDENCE_ATTACHED = "evidence_attached"
    EVIDENCE_CAPTURE_FAILED = "evidence_capture_failed"
    UNMATCHED_TOOL_COMPLETION = "unmatched_tool_completion"
    UNPLANNED_ACTION = "unplanned_action"
    CONTROL_TOOL_INVOKED = "control_tool_invoked"
    CLOSURE_REQUESTED = "closure_requested"
    CLOSURE_CANCELLED = "closure_cancelled"
    ENGAGEMENT_REOPENED = "engagement_reopened"
    ENGAGEMENT_ABANDONED = "engagement_abandoned"
    SOURCE_SUGGESTED = "source_suggested"
    RECOVERY_WARNING = "recovery_warning"
    UNCERTAIN_CORRELATION = "uncertain_correlation"
    USER_NOTE = "user_note"


_LANE_REQUIRED_TYPES = frozenset(
    {
        EventType.LANE_BOUND,
        EventType.LANE_UNBOUND,
        EventType.CHILD_LANE_LINKED,
        EventType.SESSION_STARTED,
        EventType.SESSION_CHECKPOINTED,
        EventType.SESSION_FINALIZED,
        EventType.DECISION_RECORDED,
        EventType.AGENT_DEVIATION_RECORDED,
        EventType.TOOL_CALL_STARTED,
        EventType.TOOL_CALL_COMPLETED,
        EventType.TOOL_CALL_TERMINATED,
        EventType.UNMATCHED_TOOL_COMPLETION,
        EventType.EVIDENCE_ATTACHED,
        EventType.EVIDENCE_CAPTURE_FAILED,
        EventType.UNPLANNED_ACTION,
        EventType.CONTROL_TOOL_INVOKED,
        EventType.UNCERTAIN_CORRELATION,
    }
)

_SYSTEM_SOURCE_BY_TYPE: dict[EventType, str] = {
    EventType.ENGAGEMENT_OPENED: "lifecycle",
    EventType.ENGAGEMENT_RESUMED: "lifecycle",
    EventType.OBJECTIVE_CHANGED: "lifecycle",
    EventType.SCOPE_CHANGED: "lifecycle",
    EventType.CLOSURE_CANCELLED: "lifecycle",
    EventType.ENGAGEMENT_REOPENED: "lifecycle",
    EventType.ENGAGEMENT_ABANDONED: "lifecycle",
    EventType.SOURCE_SUGGESTED: "planning",
    EventType.RECOVERY_WARNING: "recovery",
}


def _validate_envelope(
    *,
    event_type: EventType,
    payload: EventPayload,
    lane: ExecutionLaneKey | None,
    actor: str,
    system_correlation: SystemCorrelation | None,
) -> None:
    if event_type.value != payload.kind:
        raise ValueError("event type must match payload kind")
    if event_type in _LANE_REQUIRED_TYPES and lane is None:
        raise ValueError("this event type requires an execution lane")
    payload_lane = getattr(payload, "lane", None)
    if payload_lane is not None and payload_lane != lane:
        raise ValueError("event lane must exactly match the payload lane")
    if lane is not None and (actor == "system" or system_correlation is not None):
        raise ValueError("host lane and system correlation are mutually exclusive")
    if actor == "system" and system_correlation is None:
        raise ValueError("system-owned events require typed system correlation")
    if actor != "system" and system_correlation is not None:
        raise ValueError("ordinary user or host events forbid system correlation")
    if actor == "host_agent" and lane is None:
        raise ValueError("host events require an exact execution lane")
    if system_correlation is not None:
        expected_source = _SYSTEM_SOURCE_BY_TYPE.get(event_type)
        if event_type is EventType.CLOSURE_REQUESTED:
            expected_source = (
                "proof_settlement"
                if isinstance(payload, ClosureRequestedPayload)
                and payload.origin == "proof_settlement"
                else None
            )
        if expected_source is None or system_correlation.source != expected_source:
            raise ValueError("system correlation source does not match the event type")
    if (
        event_type is EventType.CLOSURE_REQUESTED
        and isinstance(payload, ClosureRequestedPayload)
        and payload.origin == "proof_settlement"
        and (
            system_correlation is None
            or system_correlation.source != "proof_settlement"
        )
    ):
        raise ValueError("proof settlement requires proof_settlement system correlation")


def _canonical_event_line_bytes(event: JournalEvent) -> bytes:
    try:
        return json.dumps(
            event.model_dump(mode="json", warnings="error"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("event cannot be canonically serialized") from exc


class JournalEventDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    event_id: UUID | None = None
    lane: ExecutionLaneKey | None = None
    turn_id: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    actor_id: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    actor: Annotated[str, Field(min_length=1, max_length=128)]
    type: EventType
    payload: EventPayload
    system_correlation: SystemCorrelation | None = None
    idempotency_key: Annotated[str | None, Field(min_length=1, max_length=1024)] = None

    @model_validator(mode="after")
    def validate_envelope(self) -> JournalEventDraft:
        _validate_envelope(
            event_type=self.type,
            payload=self.payload,
            lane=self.lane,
            actor=self.actor,
            system_correlation=self.system_correlation,
        )
        return self


class JournalEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    schema_version: Literal[EVENT_ENVELOPE_SCHEMA_VERSION] = EVENT_ENVELOPE_SCHEMA_VERSION
    event_id: UUID
    sequence: StrictInt = Field(ge=1, le=MAX_JOURNAL_EVENTS)
    occurred_at: datetime
    engagement_id: UUID
    previous_hash: Sha256Hex | None = None
    event_hash: Sha256Hex
    lane: ExecutionLaneKey | None = None
    turn_id: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    actor_id: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    actor: Annotated[str, Field(min_length=1, max_length=128)]
    type: EventType
    payload: EventPayload
    system_correlation: SystemCorrelation | None = None
    idempotency_key: Annotated[str | None, Field(min_length=1, max_length=1024)] = None

    @field_validator("occurred_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("occurred_at must be UTC-aware")
        return value

    @model_validator(mode="after")
    def validate_envelope(self) -> JournalEvent:
        _validate_envelope(
            event_type=self.type,
            payload=self.payload,
            lane=self.lane,
            actor=self.actor,
            system_correlation=self.system_correlation,
        )
        if self.sequence == 1 and self.previous_hash is not None:
            raise ValueError("the first event cannot have a previous hash")
        if self.sequence > 1 and self.previous_hash is None:
            raise ValueError("later events require a previous hash")
        if len(_canonical_event_line_bytes(self)) > MAX_JOURNAL_EVENT_BYTES:
            raise ValueError("canonical event line exceeds the journal event byte bound")
        return self


class EngagementSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    engagement_id: UUID
    revision: JournalRevision
    manifest: EngagementManifest
    events: tuple[JournalEvent, ...] = Field(max_length=MAX_JOURNAL_EVENTS)
    state: EngagementState

    @model_validator(mode="after")
    def validate_snapshot(self) -> EngagementSnapshot:
        if self.manifest.engagement_id != self.engagement_id:
            raise ValueError("manifest identity does not match snapshot")
        if self.state.revision != self.revision:
            raise ValueError("state revision does not match snapshot")
        if any(event.engagement_id != self.engagement_id for event in self.events):
            raise ValueError("event identity does not match snapshot")
        if self.events:
            seen_event_ids: set[UUID] = set()
            previous: JournalEvent | None = None
            for expected_sequence, event in enumerate(self.events, start=1):
                if event.sequence != expected_sequence:
                    raise ValueError("event sequence must start at one and be contiguous")
                if event.event_id in seen_event_ids:
                    raise ValueError("event_id values must be unique")
                seen_event_ids.add(event.event_id)
                if previous is not None and event.previous_hash != previous.event_hash:
                    raise ValueError("event previous hash does not match the prior event")
                previous = event
            last = self.events[-1]
            if (last.sequence, last.event_hash) != (
                self.revision.sequence,
                self.revision.event_hash,
            ):
                raise ValueError("revision does not match the event chain")
        elif self.revision.sequence != 0:
            raise ValueError("an empty snapshot requires revision zero")
        if self.state.projection_version != ENGAGEMENT_STATE_PROJECTION_SCHEMA_VERSION:
            raise ValueError("state projection version is not supported")
        return self


__all__ = [
    name for name in globals() if name.endswith("Payload") and not name.startswith("_")
]
__all__ += [
    "CONTROL_TOOL_NAMES",
    "CONTROL_TOOL_POLICY_VERSION",
    "CorrelationKind",
    "EngagementSnapshot",
    "EventPayload",
    "EventType",
    "JournalEvent",
    "JournalEventDraft",
    "SystemCorrelation",
    "ToolCorrelation",
]
