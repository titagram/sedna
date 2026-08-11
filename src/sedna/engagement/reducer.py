"""Pure deterministic replay for engagement journal lifecycle state."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from uuid import UUID

from sedna.engagement.events import (
    ClosureCancelledPayload,
    ClosureRequestedPayload,
    DecisionRecordedPayload,
    EngagementOpenedPayload,
    EventType,
    JournalEvent,
    LaneBoundPayload,
    LaneUnboundPayload,
    ScopeChangedPayload,
    ToolCallCompletedPayload,
    ToolCallStartedPayload,
    ToolCallTerminatedPayload,
)
from sedna.engagement.models import (
    ActiveDecision,
    ClosureBarrier,
    EngagementManifest,
    EngagementState,
    EngagementStatus,
    ExecutionLaneKey,
    JournalRevision,
    LaneBinding,
    ScopeReference,
    scope_references,
)


class EngagementReplayError(ValueError):
    """The journal is structurally valid but cannot be replayed consistently."""


class LifecycleEffect(StrEnum):
    """Closed lifecycle families used by the replay status matrix."""

    OPEN = "open"
    RECOVERY = "recovery"
    CONTROL_PLANE = "control_plane"
    NEW_WORK = "new_work"
    TOOL_START = "tool_start"
    TOOL_TERMINAL = "tool_terminal"
    BOOKKEEPING = "bookkeeping"
    CLOSURE_REQUEST = "closure_request"
    CLOSURE_CANCEL = "closure_cancel"
    REOPEN = "reopen"
    ABANDON = "abandon"


EVENT_LIFECYCLE_EFFECTS: Mapping[EventType, LifecycleEffect] = MappingProxyType(
    {
        EventType.ENGAGEMENT_OPENED: LifecycleEffect.OPEN,
        EventType.ENGAGEMENT_RESUMED: LifecycleEffect.RECOVERY,
        EventType.LANE_BOUND: LifecycleEffect.CONTROL_PLANE,
        EventType.LANE_UNBOUND: LifecycleEffect.CONTROL_PLANE,
        EventType.CHILD_LANE_LINKED: LifecycleEffect.NEW_WORK,
        EventType.SESSION_STARTED: LifecycleEffect.CONTROL_PLANE,
        EventType.SESSION_CHECKPOINTED: LifecycleEffect.CONTROL_PLANE,
        EventType.SESSION_FINALIZED: LifecycleEffect.CONTROL_PLANE,
        EventType.OBJECTIVE_CHANGED: LifecycleEffect.NEW_WORK,
        EventType.SCOPE_CHANGED: LifecycleEffect.NEW_WORK,
        EventType.DECISION_RECORDED: LifecycleEffect.NEW_WORK,
        EventType.AGENT_DEVIATION_RECORDED: LifecycleEffect.NEW_WORK,
        EventType.TOOL_CALL_STARTED: LifecycleEffect.TOOL_START,
        EventType.TOOL_CALL_COMPLETED: LifecycleEffect.TOOL_TERMINAL,
        EventType.TOOL_CALL_TERMINATED: LifecycleEffect.TOOL_TERMINAL,
        EventType.EVIDENCE_ATTACHED: LifecycleEffect.BOOKKEEPING,
        EventType.EVIDENCE_CAPTURE_FAILED: LifecycleEffect.BOOKKEEPING,
        EventType.UNMATCHED_TOOL_COMPLETION: LifecycleEffect.BOOKKEEPING,
        EventType.UNPLANNED_ACTION: LifecycleEffect.BOOKKEEPING,
        EventType.CONTROL_TOOL_INVOKED: LifecycleEffect.CONTROL_PLANE,
        EventType.CLOSURE_REQUESTED: LifecycleEffect.CLOSURE_REQUEST,
        EventType.CLOSURE_CANCELLED: LifecycleEffect.CLOSURE_CANCEL,
        EventType.ENGAGEMENT_REOPENED: LifecycleEffect.REOPEN,
        EventType.ENGAGEMENT_ABANDONED: LifecycleEffect.ABANDON,
        EventType.SOURCE_SUGGESTED: LifecycleEffect.NEW_WORK,
        EventType.RECOVERY_WARNING: LifecycleEffect.BOOKKEEPING,
        EventType.UNCERTAIN_CORRELATION: LifecycleEffect.BOOKKEEPING,
        EventType.USER_NOTE: LifecycleEffect.BOOKKEEPING,
    }
)

RESUMABLE_STATUSES = frozenset(
    {
        EngagementStatus.ACTIVE,
        EngagementStatus.CLOSING,
        EngagementStatus.ABANDONED,
    }
)

_ACTIVE_EFFECTS = frozenset(
    {
        LifecycleEffect.RECOVERY,
        LifecycleEffect.CONTROL_PLANE,
        LifecycleEffect.NEW_WORK,
        LifecycleEffect.TOOL_START,
        LifecycleEffect.TOOL_TERMINAL,
        LifecycleEffect.BOOKKEEPING,
        LifecycleEffect.CLOSURE_REQUEST,
        LifecycleEffect.ABANDON,
    }
)
_CLOSING_EFFECTS = frozenset(
    {
        LifecycleEffect.RECOVERY,
        LifecycleEffect.CONTROL_PLANE,
        LifecycleEffect.TOOL_TERMINAL,
        LifecycleEffect.BOOKKEEPING,
        LifecycleEffect.CLOSURE_CANCEL,
        LifecycleEffect.REOPEN,
        LifecycleEffect.ABANDON,
    }
)
_ABANDONED_EFFECTS = frozenset(
    {
        LifecycleEffect.RECOVERY,
        LifecycleEffect.CONTROL_PLANE,
        LifecycleEffect.TOOL_TERMINAL,
        LifecycleEffect.BOOKKEEPING,
        LifecycleEffect.REOPEN,
    }
)
_CLOSED_EFFECTS = frozenset(
    {
        LifecycleEffect.CONTROL_PLANE,
        LifecycleEffect.REOPEN,
    }
)

STATUS_LIFECYCLE_MATRIX: Mapping[
    EngagementStatus, frozenset[LifecycleEffect]
] = MappingProxyType(
    {
        EngagementStatus.ACTIVE: _ACTIVE_EFFECTS,
        EngagementStatus.CLOSING: _CLOSING_EFFECTS,
        EngagementStatus.ABANDONED: _ABANDONED_EFFECTS,
        EngagementStatus.CLOSED_UNVERIFIED: _CLOSED_EFFECTS,
        EngagementStatus.CLOSED_VERIFIED: _CLOSED_EFFECTS,
    }
)

_BOUND_LANE_TYPES = frozenset(
    {
        EventType.CHILD_LANE_LINKED,
        EventType.SESSION_STARTED,
        EventType.SESSION_CHECKPOINTED,
        EventType.SESSION_FINALIZED,
        EventType.DECISION_RECORDED,
        EventType.AGENT_DEVIATION_RECORDED,
        EventType.TOOL_CALL_STARTED,
        EventType.CONTROL_TOOL_INVOKED,
    }
)
_ATOMIC_RESTART_ERROR = (
    "closure_cancelled must be immediately followed by tool_call_started"
)


def _canonical_event_hash(item: JournalEvent) -> str:
    payload = item.model_dump(mode="json", exclude={"event_hash"})
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def validate_event_chain(engagement_id: UUID, events: Sequence[JournalEvent]) -> None:
    """Validate identity, sequence, links, hashes, and unique event identities."""

    previous: JournalEvent | None = None
    seen_event_ids: set[UUID] = set()
    for expected_sequence, item in enumerate(events, start=1):
        if item.sequence != expected_sequence:
            raise EngagementReplayError("event sequence must start at one and be contiguous")
        if item.engagement_id != engagement_id:
            raise EngagementReplayError("event engagement ID does not match the manifest")
        if item.event_id in seen_event_ids:
            raise EngagementReplayError("event IDs must be unique")
        seen_event_ids.add(item.event_id)
        expected_previous_hash = previous.event_hash if previous is not None else None
        if item.previous_hash != expected_previous_hash:
            raise EngagementReplayError("event hash link does not match the prior event")
        if item.event_hash != _canonical_event_hash(item):
            raise EngagementReplayError("event hash does not match its canonical envelope")
        previous = item


@dataclass
class _CallState:
    lane: ExecutionLaneKey
    started_sequence: int
    terminal_sequence: int | None = None


@dataclass
class _Accumulator:
    manifest: EngagementManifest
    status: EngagementStatus = EngagementStatus.ACTIVE
    current_scope_references: tuple[ScopeReference, ...] = ()
    opened: bool = False
    bound_lanes: dict[str, ExecutionLaneKey] = field(default_factory=dict)
    active_decisions: dict[str, ActiveDecision] = field(default_factory=dict)
    calls: dict[str, _CallState] = field(default_factory=dict)
    closure: ClosureBarrier | None = None
    operational_restart_required: bool = False
    revision: JournalRevision = field(
        default_factory=lambda: JournalRevision(sequence=0, event_hash="0" * 64)
    )

    @classmethod
    def from_manifest(cls, manifest: EngagementManifest) -> _Accumulator:
        return cls(
            manifest=manifest,
            current_scope_references=scope_references(manifest.initial_scope),
        )

    def apply(self, item: JournalEvent) -> None:
        if not self.opened:
            self._apply_opening(item)
            self._advance_revision(item)
            return

        if item.type is EventType.CLOSURE_CANCELLED:
            payload = item.payload
            if (
                not isinstance(payload, ClosureCancelledPayload)
                or self.closure is None
                or payload.closure_event_id != self.closure.event_id
            ):
                raise EngagementReplayError(
                    "closure cancellation must cite the current closure barrier"
                )

        if (
            self.operational_restart_required
            and item.type is not EventType.TOOL_CALL_STARTED
        ):
            raise EngagementReplayError(_ATOMIC_RESTART_ERROR)

        effect = EVENT_LIFECYCLE_EFFECTS[item.type]
        if effect not in STATUS_LIFECYCLE_MATRIX[self.status]:
            if (
                self.status is EngagementStatus.CLOSING
                and item.type is EventType.TOOL_CALL_STARTED
            ):
                raise EngagementReplayError(
                    "closure must be cancelled before a new operational call starts"
                )
            raise EngagementReplayError(
                f"{item.type.value} is not permitted while engagement is {self.status.value}"
            )

        if item.type in _BOUND_LANE_TYPES:
            self._require_bound(item)

        handler = {
            EventType.ENGAGEMENT_RESUMED: self._apply_inert,
            EventType.LANE_BOUND: self._apply_lane_bound,
            EventType.LANE_UNBOUND: self._apply_lane_unbound,
            EventType.CHILD_LANE_LINKED: self._apply_inert,
            EventType.SESSION_STARTED: self._apply_inert,
            EventType.SESSION_CHECKPOINTED: self._apply_inert,
            EventType.SESSION_FINALIZED: self._apply_inert,
            EventType.OBJECTIVE_CHANGED: self._apply_inert,
            EventType.SCOPE_CHANGED: self._apply_scope_changed,
            EventType.DECISION_RECORDED: self._apply_decision,
            EventType.AGENT_DEVIATION_RECORDED: self._apply_inert,
            EventType.TOOL_CALL_STARTED: self._apply_tool_started,
            EventType.TOOL_CALL_COMPLETED: self._apply_tool_terminal,
            EventType.TOOL_CALL_TERMINATED: self._apply_tool_terminal,
            EventType.EVIDENCE_ATTACHED: self._apply_inert,
            EventType.EVIDENCE_CAPTURE_FAILED: self._apply_inert,
            EventType.UNMATCHED_TOOL_COMPLETION: self._apply_inert,
            EventType.UNPLANNED_ACTION: self._apply_inert,
            EventType.CONTROL_TOOL_INVOKED: self._apply_inert,
            EventType.CLOSURE_REQUESTED: self._apply_closure_requested,
            EventType.CLOSURE_CANCELLED: self._apply_closure_cancelled,
            EventType.ENGAGEMENT_REOPENED: self._apply_reopened,
            EventType.ENGAGEMENT_ABANDONED: self._apply_abandoned,
            EventType.SOURCE_SUGGESTED: self._apply_inert,
            EventType.RECOVERY_WARNING: self._apply_inert,
            EventType.UNCERTAIN_CORRELATION: self._apply_inert,
            EventType.USER_NOTE: self._apply_inert,
        }.get(item.type)
        if handler is None:
            raise EngagementReplayError(f"unsupported event type: {item.type.value}")
        handler(item)
        self._advance_revision(item)

    def _apply_opening(self, item: JournalEvent) -> None:
        if item.type is not EventType.ENGAGEMENT_OPENED:
            raise EngagementReplayError("the first event must be engagement_opened")
        if (
            not isinstance(item.payload, EngagementOpenedPayload)
            or item.payload.scope_references != self.current_scope_references
        ):
            raise EngagementReplayError("opening event does not match the manifest scope")
        self.opened = True

    def _advance_revision(self, item: JournalEvent) -> None:
        self.revision = JournalRevision(
            sequence=item.sequence,
            event_hash=item.event_hash,
        )

    def _lane(self, item: JournalEvent) -> ExecutionLaneKey:
        if item.lane is None:
            raise EngagementReplayError(f"{item.type.value} requires an execution lane")
        return item.lane

    def _require_bound(self, item: JournalEvent) -> None:
        lane = self._lane(item)
        if lane.stable_key not in self.bound_lanes:
            raise EngagementReplayError(
                f"{item.type.value} requires a lane currently bound to this engagement"
            )

    def _apply_inert(self, item: JournalEvent) -> None:
        del item

    def _apply_lane_bound(self, item: JournalEvent) -> None:
        lane = self._lane(item)
        payload = item.payload
        if not isinstance(payload, LaneBoundPayload) or payload.lane != lane:
            raise EngagementReplayError("lane binding does not match the event lane")
        self.bound_lanes[lane.stable_key] = lane

    def _apply_lane_unbound(self, item: JournalEvent) -> None:
        lane = self._lane(item)
        payload = item.payload
        if not isinstance(payload, LaneUnboundPayload) or payload.lane != lane:
            raise EngagementReplayError("lane unbinding does not match the event lane")
        if lane.stable_key not in self.bound_lanes:
            raise EngagementReplayError(
                "lane_unbound requires a lane currently bound to this engagement"
            )
        del self.bound_lanes[lane.stable_key]
        self.active_decisions.pop(lane.stable_key, None)

    def _apply_scope_changed(self, item: JournalEvent) -> None:
        payload = item.payload
        if not isinstance(payload, ScopeChangedPayload):
            raise EngagementReplayError("scope change payload is invalid")
        self.current_scope_references = payload.scope_references

    def _apply_decision(self, item: JournalEvent) -> None:
        lane = self._lane(item)
        payload = item.payload
        if not isinstance(payload, DecisionRecordedPayload):
            raise EngagementReplayError("decision payload is invalid")
        self.active_decisions[lane.stable_key] = ActiveDecision(
            lane=lane,
            decision_id=payload.decision_id,
            proposal_id=payload.proposal_id,
            strategy=payload.strategy,
            rationale=payload.rationale,
            host_adapted_command=payload.host_adapted_command,
        )

    def _apply_tool_started(self, item: JournalEvent) -> None:
        lane = self._lane(item)
        payload = item.payload
        if not isinstance(payload, ToolCallStartedPayload):
            raise EngagementReplayError("tool start payload is invalid")
        if payload.call_id in self.calls:
            raise EngagementReplayError("tool call ID has already been started")
        self.calls[payload.call_id] = _CallState(
            lane=lane,
            started_sequence=item.sequence,
        )
        self.operational_restart_required = False

    def _apply_tool_terminal(self, item: JournalEvent) -> None:
        payload = item.payload
        if not isinstance(payload, (ToolCallCompletedPayload, ToolCallTerminatedPayload)):
            raise EngagementReplayError("tool terminal payload is invalid")
        call = self.calls.get(payload.call_id)
        if call is None:
            raise EngagementReplayError("tool terminal event has no matching started call")
        if call.terminal_sequence is not None:
            raise EngagementReplayError("tool call is already terminal")
        if item.lane != call.lane:
            raise EngagementReplayError("tool terminal event does not match the starting lane")
        call.terminal_sequence = item.sequence

    def _apply_closure_requested(self, item: JournalEvent) -> None:
        payload = item.payload
        if not isinstance(payload, ClosureRequestedPayload):
            raise EngagementReplayError("closure request payload is invalid")
        expected_calls = tuple(
            sorted(
                call_id
                for call_id, call in self.calls.items()
                if call.terminal_sequence is None
            )
        )
        if (
            payload.terminal_watermark != item.sequence - 1
            or payload.in_flight_call_ids != expected_calls
        ):
            raise EngagementReplayError(
                "closure snapshot must match the exact terminal prefix and in-flight calls"
            )
        self.closure = ClosureBarrier(
            event_id=item.event_id,
            terminal_watermark=payload.terminal_watermark,
            in_flight_call_ids=payload.in_flight_call_ids,
            origin=payload.origin,
        )
        self.status = EngagementStatus.CLOSING

    def _apply_closure_cancelled(self, item: JournalEvent) -> None:
        # The durable payload proves barrier identity, not which sealed service issued it.
        # Origin-specific authority is enforced before append by the capability layer.
        payload = item.payload
        if not isinstance(payload, ClosureCancelledPayload):
            raise EngagementReplayError("closure cancellation payload is invalid")
        if self.closure is None or payload.closure_event_id != self.closure.event_id:
            raise EngagementReplayError(
                "closure cancellation must cite the current closure barrier"
            )
        self.closure = None
        self.status = EngagementStatus.ACTIVE
        self.operational_restart_required = True

    def _apply_reopened(self, item: JournalEvent) -> None:
        del item
        self.closure = None
        self.status = EngagementStatus.ACTIVE

    def _apply_abandoned(self, item: JournalEvent) -> None:
        del item
        self.status = EngagementStatus.ABANDONED

    def freeze(self) -> EngagementState:
        if self.operational_restart_required:
            raise EngagementReplayError(_ATOMIC_RESTART_ERROR)
        bindings = tuple(
            LaneBinding(lane=lane, engagement_id=self.manifest.engagement_id)
            for _, lane in sorted(self.bound_lanes.items())
        )
        decisions = tuple(
            decision for _, decision in sorted(self.active_decisions.items())
        )
        in_flight_call_ids = tuple(
            sorted(
                call_id
                for call_id, call in self.calls.items()
                if call.terminal_sequence is None
            )
        )
        closure_ready = self.closure is not None and all(
            self.calls[call_id].terminal_sequence is not None
            for call_id in self.closure.in_flight_call_ids
        )
        return EngagementState(
            revision=self.revision,
            status=self.status,
            scope_references=self.current_scope_references,
            bound_lanes=bindings,
            active_decisions=decisions,
            in_flight_call_ids=in_flight_call_ids,
            closure=self.closure,
            closure_ready=closure_ready,
        )


def reduce_engagement(
    manifest: EngagementManifest,
    events: Sequence[JournalEvent],
) -> EngagementState:
    """Replay a complete engagement journal into immutable lifecycle state."""

    validate_event_chain(manifest.engagement_id, events)
    accumulator = _Accumulator.from_manifest(manifest)
    for item in events:
        accumulator.apply(item)
    return accumulator.freeze()


__all__ = [
    "EVENT_LIFECYCLE_EFFECTS",
    "RESUMABLE_STATUSES",
    "STATUS_LIFECYCLE_MATRIX",
    "EngagementReplayError",
    "LifecycleEffect",
    "reduce_engagement",
    "validate_event_chain",
]
