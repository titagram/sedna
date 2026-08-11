from __future__ import annotations

from collections.abc import Callable, Sequence
from uuid import UUID

import pytest

from sedna.engagement import (
    AgentDeviationRecordedPayload,
    ChildLaneLinkedPayload,
    ClosureCancelledPayload,
    ClosureRequestedPayload,
    ControlToolInvokedPayload,
    DecisionRecordedPayload,
    EngagementAbandonedPayload,
    EngagementManifest,
    EngagementOpenedPayload,
    EngagementReopenedPayload,
    EngagementResumedPayload,
    EngagementStatus,
    EventType,
    ExecutionLaneKey,
    JournalEvent,
    JournalEventDraft,
    LaneBinding,
    LaneBoundPayload,
    LaneUnboundPayload,
    ObjectiveChangedPayload,
    ScopeChangedPayload,
    SessionCheckpointedPayload,
    SessionFinalizedPayload,
    SessionStartedPayload,
    SystemCorrelation,
    ToolCallCompletedPayload,
    ToolCallStartedPayload,
    ToolCallTerminatedPayload,
    ToolCorrelation,
    UserNotePayload,
    scope_references,
)
from sedna.engagement.reducer import (
    EVENT_LIFECYCLE_EFFECTS,
    RESUMABLE_STATUSES,
    STATUS_LIFECYCLE_MATRIX,
    EngagementReplayError,
    LifecycleEffect,
    reduce_engagement,
)
from tests.engagement.conftest import build_event_for_test

DraftFactory = Callable[[ExecutionLaneKey], JournalEventDraft]


def event(
    manifest: EngagementManifest,
    draft: JournalEventDraft,
    previous: Sequence[JournalEvent] = (),
) -> JournalEvent:
    """Build one correctly linked event through the public deterministic helper."""

    return build_event_for_test(
        manifest.engagement_id,
        draft,
        sequence=len(previous) + 1,
        previous_hash=previous[-1].event_hash if previous else None,
    )


def chain(
    manifest: EngagementManifest, *drafts: JournalEventDraft
) -> tuple[JournalEvent, ...]:
    events: list[JournalEvent] = []
    for draft in drafts:
        events.append(event(manifest, draft, events))
    return tuple(events)


def system_draft(event_type: EventType, payload, *, source: str = "lifecycle"):
    return JournalEventDraft(
        actor="system",
        type=event_type,
        payload=payload,
        system_correlation=SystemCorrelation(
            source=source,
            operation_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        ),
    )


def opened(manifest: EngagementManifest) -> JournalEventDraft:
    return system_draft(
        EventType.ENGAGEMENT_OPENED,
        EngagementOpenedPayload(
            scope_references=scope_references(manifest.initial_scope)
        ),
    )


def resumed() -> JournalEventDraft:
    return system_draft(
        EventType.ENGAGEMENT_RESUMED,
        EngagementResumedPayload(reason="host session resumed"),
    )


def lane_bound(lane: ExecutionLaneKey) -> JournalEventDraft:
    return JournalEventDraft(
        lane=lane,
        actor="host_agent",
        type=EventType.LANE_BOUND,
        payload=LaneBoundPayload(lane=lane, binding_reason="explicit engagement selector"),
    )


def lane_unbound(lane: ExecutionLaneKey) -> JournalEventDraft:
    return JournalEventDraft(
        lane=lane,
        actor="host_agent",
        type=EventType.LANE_UNBOUND,
        payload=LaneUnboundPayload(lane=lane, reason="session stopped"),
    )


def decision_recorded(
    lane: ExecutionLaneKey, *, decision_id: str = "decision-1"
) -> JournalEventDraft:
    return JournalEventDraft(
        lane=lane,
        actor="host_agent",
        type=EventType.DECISION_RECORDED,
        payload=DecisionRecordedPayload(
            decision_id=decision_id,
            strategy=f"strategy for {decision_id}",
            rationale="test the next bounded hypothesis",
        ),
    )


def tool_started(
    lane: ExecutionLaneKey,
    *,
    call_id: str = "call-1",
    decision_id: str | None = None,
) -> JournalEventDraft:
    return JournalEventDraft(
        lane=lane,
        actor="host_agent",
        type=EventType.TOOL_CALL_STARTED,
        payload=ToolCallStartedPayload(
            call_id=call_id,
            tool_name="terminal",
            correlation=ToolCorrelation.uncertain("missing_stable_identity"),
            safe_arguments={},
            decision_id=decision_id,
        ),
    )


def tool_completed(
    lane: ExecutionLaneKey, *, call_id: str = "call-1"
) -> JournalEventDraft:
    return JournalEventDraft(
        lane=lane,
        actor="host_agent",
        type=EventType.TOOL_CALL_COMPLETED,
        payload=ToolCallCompletedPayload(
            call_id=call_id,
            correlation=ToolCorrelation.uncertain("missing_stable_identity"),
            technical_status="returned",
            duration_ms=10,
        ),
    )


def tool_terminated(
    lane: ExecutionLaneKey,
    *,
    call_id: str = "call-1",
    resolution: str = "timed_out",
) -> JournalEventDraft:
    return JournalEventDraft(
        lane=lane,
        actor="host_agent",
        type=EventType.TOOL_CALL_TERMINATED,
        payload=ToolCallTerminatedPayload(
            call_id=call_id,
            resolution=resolution,
            reason="host process ended before post_tool_call",
        ),
    )


def closure_requested(
    *,
    watermark: int,
    in_flight: Sequence[str],
    origin: str = "manual",
) -> JournalEventDraft:
    if origin == "proof_settlement":
        return system_draft(
            EventType.CLOSURE_REQUESTED,
            ClosureRequestedPayload(
                terminal_watermark=watermark,
                in_flight_call_ids=tuple(in_flight),
                reason="all required proofs settled",
                origin=origin,
            ),
            source="proof_settlement",
        )
    return JournalEventDraft(
        actor="user",
        type=EventType.CLOSURE_REQUESTED,
        payload=ClosureRequestedPayload(
            terminal_watermark=watermark,
            in_flight_call_ids=tuple(in_flight),
            reason="requested by operator",
            origin=origin,
        ),
    )


def closure_cancelled(closure_event_id: UUID) -> JournalEventDraft:
    return system_draft(
        EventType.CLOSURE_CANCELLED,
        ClosureCancelledPayload(
            closure_event_id=closure_event_id,
            reason="new work invalidated the barrier",
        ),
    )


def reopened() -> JournalEventDraft:
    return system_draft(
        EventType.ENGAGEMENT_REOPENED,
        EngagementReopenedPayload(reason="operator requested continuation"),
    )


def abandoned() -> JournalEventDraft:
    return system_draft(
        EventType.ENGAGEMENT_ABANDONED,
        EngagementAbandonedPayload(reason="host was interrupted"),
    )


def session_started(lane: ExecutionLaneKey) -> JournalEventDraft:
    return JournalEventDraft(
        lane=lane,
        actor="host_agent",
        type=EventType.SESSION_STARTED,
        payload=SessionStartedPayload(model="gpt-test", platform="hades"),
    )


def session_checkpointed(lane: ExecutionLaneKey) -> JournalEventDraft:
    return JournalEventDraft(
        lane=lane,
        actor="host_agent",
        type=EventType.SESSION_CHECKPOINTED,
        payload=SessionCheckpointedPayload(
            completed=True,
            interrupted=False,
            reason="checkpoint persisted",
        ),
    )


def session_finalized(lane: ExecutionLaneKey) -> JournalEventDraft:
    return JournalEventDraft(
        lane=lane,
        actor="host_agent",
        type=EventType.SESSION_FINALIZED,
        payload=SessionFinalizedPayload(reason="finalized"),
    )


def control_invoked(lane: ExecutionLaneKey) -> JournalEventDraft:
    return JournalEventDraft(
        lane=lane,
        actor="host_agent",
        type=EventType.CONTROL_TOOL_INVOKED,
        payload=ControlToolInvokedPayload(
            control_tool="sedna_manage_engagement",
            correlation=ToolCorrelation.uncertain("missing_stable_identity"),
        ),
    )


def child_linked(lane: ExecutionLaneKey) -> JournalEventDraft:
    return JournalEventDraft(
        lane=lane,
        actor="host_agent",
        type=EventType.CHILD_LANE_LINKED,
        payload=ChildLaneLinkedPayload(
            parent_session_id=lane.session_id,
            child_session_id="session-child",
            child_subagent_id="subagent-1",
        ),
    )


def deviation(lane: ExecutionLaneKey) -> JournalEventDraft:
    return JournalEventDraft(
        lane=lane,
        actor="host_agent",
        type=EventType.AGENT_DEVIATION_RECORDED,
        payload=AgentDeviationRecordedPayload(
            decision_id="decision-1",
            strategy="try another probe",
            rationale="the prior route was blocked",
        ),
    )


def objective_changed() -> JournalEventDraft:
    return system_draft(
        EventType.OBJECTIVE_CHANGED,
        ObjectiveChangedPayload(
            objective="Obtain proof of administrative access",
            authorization_basis="operator amended the objective",
        ),
    )


def scope_changed(manifest: EngagementManifest) -> JournalEventDraft:
    return system_draft(
        EventType.SCOPE_CHANGED,
        ScopeChangedPayload(
            scope=manifest.initial_scope,
            scope_references=scope_references(manifest.initial_scope),
            authorization_basis="operator reconfirmed exact scope",
        ),
    )


def user_note() -> JournalEventDraft:
    return JournalEventDraft(
        actor="user",
        type=EventType.USER_NOTE,
        payload=UserNotePayload(note="inspection note"),
    )


def test_open_bind_decide_and_start_call_reduces_to_active_state(
    manifest, lane
) -> None:
    events = chain(
        manifest,
        opened(manifest),
        lane_bound(lane),
        decision_recorded(lane, decision_id="decision-1"),
        tool_started(lane, call_id="call-1", decision_id="decision-1"),
    )

    state = reduce_engagement(manifest, events)

    assert state.status == "active"
    assert state.bound_lanes == (
        LaneBinding(lane=lane, engagement_id=manifest.engagement_id),
    )
    assert state.active_decisions[0].decision_id == "decision-1"
    assert state.in_flight_call_ids == ("call-1",)


def test_close_waits_at_barrier_until_every_captured_call_is_terminal(
    manifest, lane
) -> None:
    events = chain(
        manifest,
        opened(manifest),
        lane_bound(lane),
        tool_started(lane, call_id="call-1"),
        closure_requested(watermark=3, in_flight=("call-1",)),
    )
    waiting = reduce_engagement(manifest, events)
    ready = reduce_engagement(
        manifest,
        (*events, event(manifest, tool_completed(lane, call_id="call-1"), events)),
    )

    assert waiting.status == "closing"
    assert waiting.closure is not None
    assert waiting.closure.origin == "manual"
    assert waiting.closure_ready is False
    assert ready.status == "closing"
    assert ready.closure_ready is True


@pytest.mark.parametrize("resolution", ["timed_out", "abandoned"])
def test_explicit_terminal_resolution_releases_a_crashed_pre_hook_call(
    manifest, lane, resolution
) -> None:
    events = chain(
        manifest,
        opened(manifest),
        lane_bound(lane),
        tool_started(lane, call_id="call-without-post"),
        closure_requested(watermark=3, in_flight=("call-without-post",)),
    )
    resolved = reduce_engagement(
        manifest,
        (
            *events,
            event(
                manifest,
                tool_terminated(
                    lane,
                    call_id="call-without-post",
                    resolution=resolution,
                ),
                events,
            ),
        ),
    )

    assert resolved.in_flight_call_ids == ()
    assert resolved.closure_ready is True


def test_replay_preserves_proof_settlement_closure_origin(manifest, lane) -> None:
    state = reduce_engagement(
        manifest,
        chain(
            manifest,
            opened(manifest),
            lane_bound(lane),
            closure_requested(
                watermark=2,
                in_flight=(),
                origin="proof_settlement",
            ),
        ),
    )

    assert state.closure is not None
    assert state.closure.origin == "proof_settlement"


def test_new_operational_call_requires_cancellation_before_start(manifest, lane) -> None:
    invalid = chain(
        manifest,
        opened(manifest),
        lane_bound(lane),
        closure_requested(watermark=2, in_flight=()),
        tool_started(lane, call_id="late-call"),
    )

    with pytest.raises(EngagementReplayError, match="closure must be cancelled"):
        reduce_engagement(manifest, invalid)


def test_exact_barrier_cancellation_then_operational_start_is_atomic(manifest, lane) -> None:
    closing = chain(
        manifest,
        opened(manifest),
        lane_bound(lane),
        closure_requested(watermark=2, in_flight=()),
    )
    cancelled = event(manifest, closure_cancelled(closing[-1].event_id), closing)
    started = event(
        manifest,
        tool_started(lane, call_id="late-call"),
        (*closing, cancelled),
    )

    state = reduce_engagement(manifest, (*closing, cancelled, started))

    assert state.status == "active"
    assert state.closure is None
    assert state.in_flight_call_ids == ("late-call",)


def test_cancellation_must_be_immediately_followed_by_operational_start(
    manifest, lane
) -> None:
    closing = chain(
        manifest,
        opened(manifest),
        lane_bound(lane),
        closure_requested(watermark=2, in_flight=()),
    )
    cancelled = event(manifest, closure_cancelled(closing[-1].event_id), closing)
    intervening = event(manifest, user_note(), (*closing, cancelled))
    started = event(
        manifest,
        tool_started(lane, call_id="late-call"),
        (*closing, cancelled, intervening),
    )

    with pytest.raises(
        EngagementReplayError,
        match="immediately followed by tool_call_started",
    ):
        reduce_engagement(manifest, (*closing, cancelled, intervening, started))


def test_cancellation_cannot_end_replay_without_operational_start(manifest, lane) -> None:
    closing = chain(
        manifest,
        opened(manifest),
        lane_bound(lane),
        closure_requested(watermark=2, in_flight=()),
    )
    cancelled = event(manifest, closure_cancelled(closing[-1].event_id), closing)

    with pytest.raises(
        EngagementReplayError,
        match="immediately followed by tool_call_started",
    ):
        reduce_engagement(manifest, (*closing, cancelled))


def test_cancellation_requires_the_exact_current_barrier_identity(manifest, lane) -> None:
    closing = chain(
        manifest,
        opened(manifest),
        lane_bound(lane),
        closure_requested(
            watermark=2,
            in_flight=(),
            origin="proof_settlement",
        ),
    )
    wrong_id = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")

    with pytest.raises(EngagementReplayError, match="current closure barrier"):
        reduce_engagement(
            manifest,
            (*closing, event(manifest, closure_cancelled(wrong_id), closing)),
        )

    cancelled = event(manifest, closure_cancelled(closing[-1].event_id), closing)
    already_cancelled = event(
        manifest,
        closure_cancelled(closing[-1].event_id),
        (*closing, cancelled),
    )
    with pytest.raises(EngagementReplayError, match="current closure barrier"):
        reduce_engagement(manifest, (*closing, cancelled, already_cancelled))


def test_decisions_are_isolated_per_execution_lane(manifest, lane) -> None:
    child = ExecutionLaneKey.from_host(
        host_kind=lane.host_kind,
        session_id=lane.session_id,
        task_id="task-child",
    )
    state = reduce_engagement(
        manifest,
        chain(
            manifest,
            opened(manifest),
            lane_bound(lane),
            lane_bound(child),
            decision_recorded(lane, decision_id="parent-decision"),
            decision_recorded(child, decision_id="child-decision"),
            decision_recorded(lane, decision_id="parent-replacement"),
        ),
    )

    assert {item.lane.task_id: item.decision_id for item in state.active_decisions} == {
        "task-root": "parent-replacement",
        "task-child": "child-decision",
    }


def test_reducer_rejects_gap_bad_hash_and_duplicate_terminal_call(manifest, lane) -> None:
    valid = chain(manifest, opened(manifest))
    with pytest.raises(EngagementReplayError, match="sequence"):
        reduce_engagement(manifest, (valid[0].model_copy(update={"sequence": 2}),))
    with pytest.raises(EngagementReplayError, match="event hash"):
        reduce_engagement(
            manifest,
            (valid[0].model_copy(update={"event_hash": "f" * 64}),),
        )

    terminal = chain(
        manifest,
        opened(manifest),
        lane_bound(lane),
        tool_started(lane),
        tool_completed(lane),
        tool_terminated(lane),
    )
    with pytest.raises(EngagementReplayError, match="already terminal"):
        reduce_engagement(manifest, terminal)


def test_closure_snapshot_must_equal_the_exact_prefix(manifest, lane) -> None:
    base = chain(
        manifest,
        opened(manifest),
        lane_bound(lane),
        tool_started(lane, call_id="call-1"),
    )
    bad_closure = event(
        manifest,
        closure_requested(watermark=2, in_flight=()),
        base,
    )

    with pytest.raises(EngagementReplayError, match="closure snapshot"):
        reduce_engagement(manifest, (*base, bad_closure))


def test_empty_pre_open_state_has_zero_revision_and_opening_is_strict(manifest) -> None:
    empty = reduce_engagement(manifest, ())
    assert empty.revision.sequence == 0
    assert empty.revision.event_hash == "0" * 64
    assert empty.status == "active"

    first_note = chain(manifest, user_note())
    with pytest.raises(EngagementReplayError, match="first event"):
        reduce_engagement(manifest, first_note)

    wrong_scope = system_draft(
        EventType.ENGAGEMENT_OPENED,
        EngagementOpenedPayload(scope_references=()),
    )
    with pytest.raises(EngagementReplayError, match="manifest scope"):
        reduce_engagement(manifest, chain(manifest, wrong_scope))


@pytest.mark.parametrize(
    "draft_factory",
    [
        session_started,
        session_checkpointed,
        session_finalized,
        control_invoked,
        decision_recorded,
        child_linked,
        deviation,
        tool_started,
    ],
)
def test_lane_scoped_work_rejects_never_bound_and_previously_unbound_lanes(
    manifest, lane, draft_factory: DraftFactory
) -> None:
    never_bound = chain(manifest, opened(manifest), draft_factory(lane))
    with pytest.raises(EngagementReplayError, match="currently bound"):
        reduce_engagement(manifest, never_bound)

    previously_bound = chain(
        manifest,
        opened(manifest),
        lane_bound(lane),
        lane_unbound(lane),
        draft_factory(lane),
    )
    with pytest.raises(EngagementReplayError, match="currently bound"):
        reduce_engagement(manifest, previously_bound)


def test_completion_after_unbind_or_abandon_resolves_only_the_original_call_lane(
    manifest, lane
) -> None:
    after_unbind = chain(
        manifest,
        opened(manifest),
        lane_bound(lane),
        tool_started(lane),
        lane_unbound(lane),
        tool_completed(lane),
    )
    assert reduce_engagement(manifest, after_unbind).in_flight_call_ids == ()

    after_abandon = chain(
        manifest,
        opened(manifest),
        lane_bound(lane),
        tool_started(lane),
        abandoned(),
        tool_terminated(lane),
    )
    assert reduce_engagement(manifest, after_abandon).in_flight_call_ids == ()

    wrong_lane = ExecutionLaneKey.from_host(
        host_kind=lane.host_kind,
        session_id="session-other",
        task_id="task-other",
    )
    mismatch = chain(
        manifest,
        opened(manifest),
        lane_bound(lane),
        tool_started(lane),
        tool_completed(wrong_lane),
    )
    with pytest.raises(EngagementReplayError, match="starting lane"):
        reduce_engagement(manifest, mismatch)


@pytest.mark.parametrize(
    "recovery_factory",
    [resumed, session_started, session_checkpointed, session_finalized, control_invoked],
)
def test_closing_admits_recovery_control_plane_without_cancelling_barrier(
    manifest, lane, recovery_factory
) -> None:
    closing = chain(
        manifest,
        opened(manifest),
        lane_bound(lane),
        closure_requested(watermark=2, in_flight=()),
    )
    draft = recovery_factory() if recovery_factory is resumed else recovery_factory(lane)

    state = reduce_engagement(manifest, (*closing, event(manifest, draft, closing)))

    assert state.status == "closing"
    assert state.closure is not None
    assert state.closure.event_id == closing[-1].event_id


@pytest.mark.parametrize(
    "new_work_factory",
    [decision_recorded, child_linked, deviation, tool_started],
)
def test_closing_rejects_bound_lane_new_work(
    manifest, lane, new_work_factory: DraftFactory
) -> None:
    closing = chain(
        manifest,
        opened(manifest),
        lane_bound(lane),
        closure_requested(watermark=2, in_flight=()),
    )
    with pytest.raises(EngagementReplayError):
        reduce_engagement(
            manifest,
            (*closing, event(manifest, new_work_factory(lane), closing)),
        )


def test_closing_rejects_objective_and_scope_changes(manifest, lane) -> None:
    closing = chain(
        manifest,
        opened(manifest),
        lane_bound(lane),
        closure_requested(watermark=2, in_flight=()),
    )
    for draft in (objective_changed(), scope_changed(manifest)):
        with pytest.raises(EngagementReplayError, match="not permitted"):
            reduce_engagement(manifest, (*closing, event(manifest, draft, closing)))


def test_abandoned_allows_inspection_but_requires_reopen_before_new_work(
    manifest, lane
) -> None:
    abandoned_events = chain(
        manifest,
        opened(manifest),
        lane_bound(lane),
        abandoned(),
        resumed(),
        session_checkpointed(lane),
        control_invoked(lane),
    )
    inspected = reduce_engagement(manifest, abandoned_events)
    assert inspected.status == "abandoned"

    invalid = event(manifest, decision_recorded(lane), abandoned_events)
    with pytest.raises(EngagementReplayError, match="not permitted"):
        reduce_engagement(manifest, (*abandoned_events, invalid))

    reopen_event = event(manifest, reopened(), abandoned_events)
    decision_event = event(
        manifest,
        decision_recorded(lane),
        (*abandoned_events, reopen_event),
    )
    reopened_state = reduce_engagement(
        manifest,
        (*abandoned_events, reopen_event, decision_event),
    )
    assert reopened_state.status == "active"
    assert reopened_state.active_decisions[0].decision_id == "decision-1"


def test_crash_recovery_can_resolve_old_call_from_a_new_session(manifest, lane) -> None:
    recovery_lane = ExecutionLaneKey.from_host(
        host_kind=lane.host_kind,
        session_id="session-recovery",
        task_id="task-recovery",
    )
    events = chain(
        manifest,
        opened(manifest),
        lane_bound(lane),
        tool_started(lane, call_id="orphaned-call"),
        closure_requested(watermark=3, in_flight=("orphaned-call",)),
        lane_unbound(lane),
        resumed(),
        lane_bound(recovery_lane),
        session_started(recovery_lane),
        tool_terminated(lane, call_id="orphaned-call"),
    )

    state = reduce_engagement(manifest, events)

    assert state.status == "closing"
    assert state.in_flight_call_ids == ()
    assert state.closure_ready is True
    assert state.bound_lanes == (
        LaneBinding(lane=recovery_lane, engagement_id=manifest.engagement_id),
    )


@pytest.mark.parametrize("terminal_status", ["closing", "abandoned"])
def test_session_wide_finalize_records_one_bound_lane_without_new_work(
    manifest, lane, terminal_status
) -> None:
    drafts = [opened(manifest), lane_bound(lane)]
    if terminal_status == "closing":
        drafts.append(closure_requested(watermark=2, in_flight=()))
    else:
        drafts.append(abandoned())
    drafts.append(session_finalized(lane))

    state = reduce_engagement(manifest, chain(manifest, *drafts))

    assert state.status == terminal_status
    if terminal_status == "closing":
        assert state.closure is not None


def test_proof_close_during_finalize_stays_closing_for_m6c(manifest, lane) -> None:
    state = reduce_engagement(
        manifest,
        chain(
            manifest,
            opened(manifest),
            lane_bound(lane),
            closure_requested(
                watermark=2,
                in_flight=(),
                origin="proof_settlement",
            ),
            session_finalized(lane),
        ),
    )

    assert state.status == "closing"
    assert state.closure_ready is True
    assert state.closure is not None
    assert state.closure.origin == "proof_settlement"


def test_reducer_tables_are_closed_over_all_event_types_and_statuses() -> None:
    assert set(EVENT_LIFECYCLE_EFFECTS) == set(EventType)
    assert set(STATUS_LIFECYCLE_MATRIX) == set(EngagementStatus)
    assert {
        EngagementStatus.ACTIVE,
        EngagementStatus.CLOSING,
        EngagementStatus.ABANDONED,
    } == RESUMABLE_STATUSES


@pytest.mark.parametrize(
    "status",
    [EngagementStatus.CLOSED_UNVERIFIED, EngagementStatus.CLOSED_VERIFIED],
)
def test_closed_status_rows_are_inert_until_reopened(status) -> None:
    allowed = STATUS_LIFECYCLE_MATRIX[status]

    assert LifecycleEffect.CONTROL_PLANE in allowed
    assert LifecycleEffect.REOPEN in allowed
    assert LifecycleEffect.NEW_WORK not in allowed
    assert LifecycleEffect.TOOL_START not in allowed
    assert LifecycleEffect.CLOSURE_REQUEST not in allowed
    assert (
        EVENT_LIFECYCLE_EFFECTS[EventType.SESSION_FINALIZED]
        is LifecycleEffect.CONTROL_PLANE
    )


def test_reducer_never_produces_m6c_closed_statuses(manifest, lane) -> None:
    active = reduce_engagement(manifest, chain(manifest, opened(manifest), lane_bound(lane)))
    closing = reduce_engagement(
        manifest,
        chain(
            manifest,
            opened(manifest),
            lane_bound(lane),
            closure_requested(watermark=2, in_flight=()),
        ),
    )
    abandoned_state = reduce_engagement(
        manifest,
        chain(manifest, opened(manifest), lane_bound(lane), abandoned()),
    )

    assert {active.status, closing.status, abandoned_state.status}.isdisjoint(
        {EngagementStatus.CLOSED_UNVERIFIED, EngagementStatus.CLOSED_VERIFIED}
    )


def test_state_maps_freeze_in_stable_lane_and_call_order(manifest, lane) -> None:
    other_lane = ExecutionLaneKey.from_host(
        host_kind=lane.host_kind,
        session_id="a-session",
        task_id="a-task",
    )
    state = reduce_engagement(
        manifest,
        chain(
            manifest,
            opened(manifest),
            lane_bound(lane),
            lane_bound(other_lane),
            decision_recorded(lane, decision_id="z-decision"),
            decision_recorded(other_lane, decision_id="a-decision"),
            tool_started(lane, call_id="z-call"),
            tool_started(other_lane, call_id="a-call"),
        ),
    )

    assert [binding.lane for binding in state.bound_lanes] == [lane, other_lane]
    assert [decision.lane for decision in state.active_decisions] == [lane, other_lane]
    assert state.in_flight_call_ids == ("a-call", "z-call")
