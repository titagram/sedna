from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

import pytest

from sedna.engagement import (
    ClosureRequestedPayload,
    DecisionRecordedPayload,
    EngagementManifest,
    EngagementOpenedPayload,
    EvidenceAttachedPayload,
    EvidenceReference,
    ExecutionLaneKey,
    HostKind,
    JournalEvent,
    JournalEventDraft,
    LaneBoundPayload,
    ProofRequirement,
    SystemCorrelation,
    ToolCallCompletedPayload,
    ToolCallStartedPayload,
    ToolCallTerminatedPayload,
    ToolCorrelation,
    UserNotePayload,
    scope_references,
)
from sedna.knowledge.retrieval import AuthorizationScope, AuthorizationState, ValidatedTarget

FIXED_TIME = datetime(2026, 8, 11, 12, 30, tzinfo=UTC)
ENGAGEMENT_ID = UUID("11111111-1111-4111-8111-111111111111")


@pytest.fixture
def fixed_clock() -> Callable[[], datetime]:
    return lambda: FIXED_TIME


@pytest.fixture
def fixed_uuid_factory() -> Callable[[], UUID]:
    next_value = 1

    def factory() -> UUID:
        nonlocal next_value
        value = UUID(f"00000000-0000-4000-8000-{next_value:012d}")
        next_value += 1
        return value

    return factory


@pytest.fixture
def authorized_scope() -> AuthorizationScope:
    return AuthorizationScope(
        state=AuthorizationState.AUTHORIZED,
        exact_targets=(ValidatedTarget.parse("192.0.2.44"),),
    )


@pytest.fixture
def lane() -> ExecutionLaneKey:
    return ExecutionLaneKey(
        host_kind=HostKind.HADES,
        session_id="session-orion",
        task_id="task-root",
    )


@pytest.fixture
def new_lane() -> Callable[..., ExecutionLaneKey]:
    def factory(
        *, session_id: str = "session-orion", task_id: str = "task-root"
    ) -> ExecutionLaneKey:
        return ExecutionLaneKey(
            host_kind=HostKind.HADES,
            session_id=session_id,
            task_id=task_id,
        )

    return factory


@pytest.fixture
def manifest(authorized_scope: AuthorizationScope) -> EngagementManifest:
    return EngagementManifest(
        engagement_id=ENGAGEMENT_ID,
        display_name="HTB-Orion",
        initial_objective="Obtain the user and root flags",
        initial_scope=authorized_scope,
        required_proofs=(
            ProofRequirement(
                proof_id="user-flag",
                kind="flag",
                description="A valid HTB user flag",
            ),
            ProofRequirement(
                proof_id="root-flag",
                kind="flag",
                description="A valid HTB root flag",
            ),
        ),
        created_at=FIXED_TIME,
        created_by_host={"kind": "hades", "adapter_version": "1"},
    )


@pytest.fixture
def opened_draft() -> Callable[..., JournalEventDraft]:
    def factory(*, scope_references=()) -> JournalEventDraft:
        return JournalEventDraft(
            actor="system",
            type="engagement_opened",
            payload=EngagementOpenedPayload(scope_references=scope_references),
            system_correlation=SystemCorrelation(
                source="lifecycle",
                operation_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            ),
        )

    return factory


@pytest.fixture
def lane_bound_draft() -> Callable[[ExecutionLaneKey], JournalEventDraft]:
    def factory(lane: ExecutionLaneKey) -> JournalEventDraft:
        return JournalEventDraft(
            lane=lane,
            actor="host_agent",
            type="lane_bound",
            payload=LaneBoundPayload(lane=lane, binding_reason="engagement_created"),
        )

    return factory


@pytest.fixture
def initial_drafts(opened_draft, lane_bound_draft):
    def factory(manifest: EngagementManifest, lane: ExecutionLaneKey):
        return (
            opened_draft(scope_references=scope_references(manifest.initial_scope)),
            lane_bound_draft(lane),
        )

    return factory


@pytest.fixture
def decision_draft():
    def factory(lane: ExecutionLaneKey, *, decision_id: str = "decision-1"):
        return JournalEventDraft(
            lane=lane,
            actor="host_agent",
            type="decision_recorded",
            payload=DecisionRecordedPayload(
                decision_id=decision_id,
                proposal_id=None,
                strategy="enumerate services",
                rationale="establish the attack surface",
            ),
        )

    return factory


@pytest.fixture
def user_note_draft():
    def factory(note: str = "Operator note"):
        return JournalEventDraft(
            actor="user",
            type="user_note",
            payload=UserNotePayload(note=note),
        )

    return factory


@pytest.fixture
def evidence_attached_draft():
    def factory(lane: ExecutionLaneKey, reference: EvidenceReference):
        return JournalEventDraft(
            lane=lane,
            actor="host_agent",
            type="evidence_attached",
            payload=EvidenceAttachedPayload(evidence=reference),
        )

    return factory


@pytest.fixture
def tool_started():
    def factory(
        lane: ExecutionLaneKey,
        *,
        call_id: str = "call-1",
        decision_id: str | None = None,
    ):
        return JournalEventDraft(
            lane=lane,
            actor="host_agent",
            type="tool_call_started",
            payload=ToolCallStartedPayload(
                call_id=call_id,
                tool_name="terminal",
                correlation=ToolCorrelation.uncertain("missing_stable_identity"),
                safe_arguments={},
                decision_id=decision_id,
            ),
        )

    return factory


@pytest.fixture
def tool_completed():
    def factory(lane: ExecutionLaneKey, *, call_id: str = "call-1"):
        return JournalEventDraft(
            lane=lane,
            actor="host_agent",
            type="tool_call_completed",
            payload=ToolCallCompletedPayload(
                call_id=call_id,
                correlation=ToolCorrelation.uncertain("missing_stable_identity"),
                technical_status="returned",
                duration_ms=10,
            ),
        )

    return factory


@pytest.fixture
def tool_terminated():
    def factory(
        lane: ExecutionLaneKey,
        *,
        call_id: str = "call-1",
        resolution: str = "timed_out",
        reason: str = "host process ended",
    ):
        return JournalEventDraft(
            lane=lane,
            actor="host_agent",
            type="tool_call_terminated",
            payload=ToolCallTerminatedPayload(
                call_id=call_id,
                resolution=resolution,
                reason=reason,
            ),
        )

    return factory


@pytest.fixture
def closure_requested():
    def factory(
        *,
        watermark: int,
        in_flight: Iterable[str],
        origin: str = "manual",
    ):
        system_correlation = None
        actor = "user"
        if origin == "proof_settlement":
            actor = "system"
            system_correlation = SystemCorrelation(
                source="proof_settlement",
                operation_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            )
        return JournalEventDraft(
            actor=actor,
            type="closure_requested",
            payload=ClosureRequestedPayload(
                terminal_watermark=watermark,
                in_flight_call_ids=tuple(in_flight),
                reason="requested by operator",
                origin=origin,
            ),
            system_correlation=system_correlation,
        )

    return factory


def _event_hash(event: JournalEvent) -> str:
    payload = event.model_dump(mode="json", exclude={"event_hash"})
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def build_event_for_test(
    engagement_id: UUID,
    draft: JournalEventDraft,
    *,
    sequence: int,
    previous_hash: str | None,
) -> JournalEvent:
    event = JournalEvent(
        **draft.model_dump(exclude={"event_id"}),
        event_id=draft.event_id or UUID(f"00000000-0000-4000-8000-{sequence:012d}"),
        sequence=sequence,
        occurred_at=FIXED_TIME,
        engagement_id=engagement_id,
        previous_hash=previous_hash,
        event_hash="0" * 64,
    )
    return event.model_copy(update={"event_hash": _event_hash(event)})


@pytest.fixture
def event_chain():
    def factory(manifest: EngagementManifest, *drafts: JournalEventDraft):
        events: list[JournalEvent] = []
        for draft in drafts:
            events.append(
                build_event_for_test(
                    manifest.engagement_id,
                    draft,
                    sequence=len(events) + 1,
                    previous_hash=events[-1].event_hash if events else None,
                )
            )
        return tuple(events)

    return factory


@pytest.fixture
def next_event():
    def factory(events: tuple[JournalEvent, ...], draft: JournalEventDraft):
        if not events:
            raise ValueError("next_event requires an existing event chain")
        return build_event_for_test(
            events[0].engagement_id,
            draft,
            sequence=len(events) + 1,
            previous_hash=events[-1].event_hash,
        )

    return factory
