"""Control-loop telemetry must not confuse tool volume with planning."""

from types import SimpleNamespace
from uuid import uuid4

from sedna.engagement import reporting


def event(kind, **payload):
    return SimpleNamespace(event_id=uuid4(), type=kind, payload=SimpleNamespace(**payload))


def test_outcome_coverage_counts_distinct_completed_calls_only():
    project = getattr(reporting, "project_control_loop_metrics", None)
    assert callable(project), "Missing deterministic control-loop metrics projection"
    events = [
        event("tool_call_started", call_id="one", decision_id=None),
        event("tool_call_started", call_id="two", decision_id=None),
        event("tool_call_started", call_id="pending", decision_id=None),
        event("tool_call_completed", call_id="one"),
        event("tool_call_completed", call_id="two"),
        event("outcome_assessed", tool_call_ids=("one",)),
        event("outcome_assessed", tool_call_ids=("one", "pending", "unknown")),
    ]
    metrics = project(events)
    assert metrics.completed_calls == 2
    assert metrics.assessed_completed_calls == 1
    assert metrics.outcome_coverage == 0.5
    assert metrics.started_calls == 3


def test_empty_journal_does_not_claim_perfect_coverage():
    project = getattr(reporting, "project_control_loop_metrics", None)
    assert callable(project), "Missing deterministic control-loop metrics projection"
    metrics = project([])
    assert metrics.outcome_coverage is None
    assert metrics.decision_link_coverage is None


def test_dangling_or_future_decisions_do_not_count_as_linked():
    events = [
        event("decision_recorded", decision_id="manual", proposal_id=None),
        event("tool_call_started", call_id="linked", decision_id="manual"),
        event("tool_call_started", call_id="dangling", decision_id="missing"),
        event("tool_call_started", call_id="future", decision_id="later"),
        event("decision_recorded", decision_id="later", proposal_id=None),
    ]
    metrics = reporting.project_control_loop_metrics(events)
    assert metrics.decision_linked_calls == 1
    assert metrics.decision_link_coverage == 1 / 3


def test_manual_decision_is_not_a_frontier_linked_call():
    proposal_id = uuid4()
    events = [
        event("frontier_proposed", proposal=SimpleNamespace(proposal_id=proposal_id)),
        event("decision_recorded", decision_id="manual", proposal_id=None),
        event("decision_recorded", decision_id="planner", proposal_id=proposal_id),
        event("decision_recorded", decision_id="orphan", proposal_id=uuid4()),
        event("tool_call_started", call_id="one", decision_id="manual"),
        event("tool_call_started", call_id="two", decision_id="planner"),
        event("tool_call_started", call_id="three", decision_id="orphan"),
    ]
    metrics = reporting.project_control_loop_metrics(events)
    assert metrics.decision_linked_calls == 3
    assert getattr(metrics, "frontier_linked_calls", None) == 1
