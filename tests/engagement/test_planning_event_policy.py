"""Lifecycle and ownership policy for all typed planning events."""

from sedna.engagement import EngagementStatus, EventType
from sedna.engagement.reducer import (
    EVENT_LIFECYCLE_EFFECTS,
    STATUS_LIFECYCLE_MATRIX,
    LifecycleEffect,
)
from sedna.engagement.service import EVENT_APPEND_OWNER_BY_TYPE

SETTLEMENT = {
    EventType.OBSERVATION_EXTRACTED,
    EventType.HYPOTHESIS_FORMED,
    EventType.MISSING_INFORMATION_IDENTIFIED,
    EventType.OUTCOME_ASSESSED,
    EventType.OBJECTIVE_PROOF_OBSERVED,
    EventType.INTERPRETATION_SUCCEEDED,
    EventType.INTERPRETATION_FAILED,
}
ACTIVE = {
    EventType.PLAN_REQUESTED,
    EventType.FRONTIER_PROPOSED,
    EventType.FRONTIER_CRITICIZED,
    EventType.FRONTIER_REPAIRED,
    EventType.FRONTIER_REJECTED,
    EventType.PLANNING_GAP_RECORDED,
    EventType.STRATEGY_RECONCILED,
    EventType.STRATEGY_ARCHIVED,
    EventType.STRATEGY_REACTIVATED,
    EventType.RESEARCH_QUERY_PROPOSED,
    EventType.RESEARCH_SOURCE_CONSULTED,
    EventType.RESEARCH_SOURCE_ASSESSED,
}


def test_planning_event_partition_and_owner_map_are_exhaustive() -> None:
    planning = {
        event
        for event in EventType
        if EVENT_APPEND_OWNER_BY_TYPE[event.value] == "planning_capability"
    }
    assert planning == SETTLEMENT | ACTIVE
    assert len(planning) == 19
    assert set(EVENT_APPEND_OWNER_BY_TYPE) == {event.value for event in EventType}


def test_settlement_events_are_legal_noops_in_every_status() -> None:
    for event in SETTLEMENT:
        assert EVENT_LIFECYCLE_EFFECTS[event] is LifecycleEffect.SETTLEMENT_BOOKKEEPING
        assert all(
            LifecycleEffect.SETTLEMENT_BOOKKEEPING in STATUS_LIFECYCLE_MATRIX[status]
            for status in EngagementStatus
        )


def test_active_planning_events_are_legal_only_while_active() -> None:
    for event in ACTIVE:
        assert EVENT_LIFECYCLE_EFFECTS[event] is LifecycleEffect.ACTIVE_PLANNING
    assert LifecycleEffect.ACTIVE_PLANNING in STATUS_LIFECYCLE_MATRIX[EngagementStatus.ACTIVE]
    assert all(
        LifecycleEffect.ACTIVE_PLANNING not in STATUS_LIFECYCLE_MATRIX[status]
        for status in EngagementStatus
        if status is not EngagementStatus.ACTIVE
    )
