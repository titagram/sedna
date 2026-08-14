from __future__ import annotations

import contextlib
import fcntl
import json
import os
import threading
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from sedna.engagement import (
    CONTROL_TOOL_NAMES,
    CONTROL_TOOL_POLICY_VERSION,
    EngagementJournalService,
)
from sedna.engagement.hades_adapter import HadesEngagementAdapter
from sedna.engagement.service import (
    EngagementSettlementOutcome,
    EngagementSettlementPort,
)

LANE = {"session_id": "session-orion", "task_id": "task-root"}
HOOK_ID = {
    "session_id": "session-orion",
    "task_id": "task-root",
    "turn_id": "turn-1",
    "api_request_id": "request-1",
    "api_call_count": 1,
    "tool_call_id": "tool-call-1",
}


class FakeHadesContext:
    def __init__(self, knowledge_root) -> None:
        self.sedna_knowledge_root = knowledge_root
        self.tools = []
        self.hooks = {}
        self.adapter: Any = None
        self.created: Any = None

    def register_tool(self, **definition) -> None:
        self.tools.append(definition)

    def register_hook(self, name, callback) -> None:
        self.hooks[name] = callback


def registered_adapter(
    tmp_path, **adapter_kwargs
) -> tuple[FakeHadesContext, dict[str, dict], dict[str, Callable]]:
    context = FakeHadesContext(tmp_path / "knowledge")
    adapter = HadesEngagementAdapter(
        context,
        root_resolver=lambda: context.sedna_knowledge_root,
        **adapter_kwargs,
    )
    context.adapter = adapter
    adapter.register()
    tools = {item["name"]: item for item in context.tools}
    return context, tools, context.hooks


def registered_bound_adapter(tmp_path, **adapter_kwargs):
    context, tools, hooks = registered_adapter(tmp_path, **adapter_kwargs)
    created = create_bound_orion(tools)
    call_tool(
        tools,
        "sedna_record_decision",
        {
            "custom_strategy": "Enumerate exposed services",
            "rationale": "Initial reconnaissance plan",
        },
        **LANE,
    )
    context.created = created
    return context, tools, hooks


def call_tool(tools, name: str, payload: dict, **lane) -> dict:
    tool = tools[name]
    invocation = dict(payload)
    invocation.update({key: value for key, value in lane.items() if value is not None})
    return tool["handler"](**invocation)


def create_payload(**overrides: Any) -> dict:
    payload = {
        "action": "create",
        "display_name": "HTB-Orion",
        "objective": "Obtain the user and root flags",
        "authorization": ("192.0.2.44",),
    }
    payload.update(overrides)
    return payload


def _engagement_path(root: Path, engagement_id) -> Path:
    return root / "engagements" / str(engagement_id)


def load_private_capture(tmp_path, engagement_id) -> tuple[list[Any], bytes]:
    root = _engagement_path(tmp_path / "knowledge", engagement_id)
    events = []
    from sedna.engagement.events import JournalEvent

    for line in (root / "events.jsonl").read_text(encoding="utf-8").splitlines():
        events.append(JournalEvent.model_validate_json(line))
    evidence = b""
    for blob in (root / "evidence").glob("blob-*.bin"):
        evidence = blob.read_bytes()
    return events, evidence


def load_snapshot(tmp_path, engagement_id=None):
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(root) as service:
        if engagement_id is None:
            engagement_id = _first_engagement_id(tmp_path)
        return service.load_snapshot(engagement_id)


def latest_event(tmp_path, event_type: str):
    snapshot = load_snapshot(tmp_path)
    matches = [event for event in snapshot.events if event.type == event_type]
    assert matches, f"no {event_type} event in journal"
    return matches[-1]


def resolve_bound_engagement(tmp_path, session_id: str, task_id: str) -> UUID | None:
    from sedna.engagement.models import ExecutionLaneKey, HostKind

    root = tmp_path / "knowledge"
    lane = ExecutionLaneKey.from_host(
        host_kind=HostKind.HADES, session_id=session_id, task_id=task_id
    )
    with EngagementJournalService.open(root) as service:
        return service.resolve_lane_binding(lane).engagement_id


def create_bound_orion(tools) -> dict:
    return call_tool(
        tools,
        "sedna_manage_engagement",
        create_payload(),
        **LANE,
    )


def stable_hook_identity(prefix: str) -> dict:
    return {
        "session_id": "session-orion",
        "task_id": "task-root",
        "turn_id": f"{prefix}-turn",
        "api_request_id": f"{prefix}-request",
        "api_call_count": 1,
        "tool_call_id": f"{prefix}-call",
    }


def captured_started_tool_names(tmp_path) -> list[str]:
    root = tmp_path / "knowledge"
    names: list[str] = []
    for engagement_dir in (root / "engagements").glob("*"):
        journal = engagement_dir / "events.jsonl"
        if not journal.is_file():
            continue
        for line in journal.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if value["type"] == "tool_call_started":
                names.append(value["payload"]["tool_name"])
    return names


def captured_control_tool_names(tmp_path) -> list[str]:
    root = tmp_path / "knowledge"
    names: list[str] = []
    for engagement_dir in (root / "engagements").glob("*"):
        journal = engagement_dir / "events.jsonl"
        if not journal.is_file():
            continue
        for line in journal.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if value["type"] == "control_tool_invoked":
                names.append(value["payload"]["control_tool"])
    return sorted(names)


def test_adapter_registers_compact_tools_and_required_hooks(tmp_path) -> None:
    context = FakeHadesContext(tmp_path / "knowledge")
    adapter = HadesEngagementAdapter(
        context,
        root_resolver=lambda: context.sedna_knowledge_root,
    )
    adapter.register()

    assert [item["name"] for item in context.tools] == [
        "sedna_manage_engagement",
        "sedna_record_decision",
        "sedna_add_source",
    ]
    assert set(context.hooks) == {
        "pre_tool_call",
        "post_tool_call",
        "pre_llm_call",
        "on_session_start",
        "on_session_end",
        "on_session_finalize",
        "on_session_reset",
        "subagent_start",
        "subagent_stop",
    }
    assert all(
        item["schema"]["parameters"]["additionalProperties"] is False for item in context.tools
    )


def test_pinned_invocation_roots_do_not_cross_concurrent_adapter_calls(tmp_path) -> None:
    roots = iter((tmp_path / "one", tmp_path / "two"))
    adapter = HadesEngagementAdapter(
        FakeHadesContext(tmp_path / "unused"),
        root_resolver=lambda: next(roots),
    )
    first_pinned = threading.Event()
    second_finished = threading.Event()
    observed: list[Path] = []

    def first() -> None:
        adapter._pin_root()
        first_pinned.set()
        assert second_finished.wait(timeout=2)
        with adapter._open_service() as service:
            observed.append(service._repository._knowledge_root)

    def second() -> None:
        assert first_pinned.wait(timeout=2)
        adapter._pin_root()
        with adapter._open_service() as service:
            observed.append(service._repository._knowledge_root)
        second_finished.set()

    threads = (threading.Thread(target=first), threading.Thread(target=second))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert not any(thread.is_alive() for thread in threads)
    assert observed == [tmp_path / "two", tmp_path / "one"]


def test_manage_create_requires_host_lane_and_has_no_per_call_root(tmp_path) -> None:
    context, tools, _ = registered_adapter(tmp_path)
    for definition in tools.values():
        schema = definition["schema"]["parameters"]
        assert "knowledge_root" not in schema.get("properties", {})

    missing = call_tool(tools, "sedna_manage_engagement", create_payload())
    created = call_tool(
        tools,
        "sedna_manage_engagement",
        create_payload(),
        **LANE,
    )
    assert missing == {
        "ok": False,
        "error": {"code": "host_context_required", "retryable": False},
    }
    assert created["ok"] is True
    assert created["engagement"]["display_name"] == "HTB-Orion"


def test_bound_operational_tool_is_recorded_with_original_result(tmp_path) -> None:
    context, tools, hooks = registered_adapter(tmp_path)
    created = create_bound_orion(tools)
    call_tool(
        tools,
        "sedna_record_decision",
        {
            "custom_strategy": "Enumerate exposed services",
            "rationale": "No services are known yet",
        },
        **LANE,
    )
    identity = {
        "session_id": "session-orion",
        "task_id": "task-root",
        "turn_id": "turn-1",
        "api_request_id": "request-1",
        "api_call_count": 1,
        "tool_call_id": "tool-call-1",
    }
    hooks["pre_tool_call"](tool_name="terminal", args={"command": "id"}, **identity)
    hooks["post_tool_call"](
        tool_name="terminal",
        args={"command": "id"},
        result="uid=1000(user) HTB{private-proof}",
        duration_ms=17,
        **identity,
    )

    events, evidence = load_private_capture(tmp_path, created["engagement"]["engagement_id"])
    decision = next(event for event in events if event.type == "decision_recorded")
    started = next(event for event in events if event.type == "tool_call_started")
    assert started.payload.decision_id == decision.payload.decision_id
    assert [event.type for event in events][-2:] == [
        "evidence_attached",
        "tool_call_completed",
    ]
    assert events[-1].payload.technical_status == "returned"
    assert b"HTB{private-proof}" in evidence


def test_only_exact_control_tools_are_skipped_and_legacy_nmap_is_captured(
    tmp_path,
) -> None:
    _, tools, hooks = registered_adapter(tmp_path)
    call_tool(
        tools,
        "sedna_manage_engagement",
        create_payload(),
        **LANE,
    )
    assert CONTROL_TOOL_POLICY_VERSION == "sedna.control-tools.v1"
    assert (
        frozenset(
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
        == CONTROL_TOOL_NAMES
    )
    for control in sorted(CONTROL_TOOL_NAMES):
        hooks["pre_tool_call"](tool_name=control, args={}, **stable_hook_identity(control))
    hooks["pre_tool_call"](
        tool_name="sedna_nmap_tcp_discovery",
        args={"target": "192.0.2.44"},
        **stable_hook_identity("legacy-nmap"),
    )
    assert captured_started_tool_names(tmp_path) == ["sedna_nmap_tcp_discovery"]
    assert set(captured_control_tool_names(tmp_path)) == set(CONTROL_TOOL_NAMES)


def test_incomplete_correlation_is_recorded_without_deduplication(tmp_path) -> None:
    _, tools, hooks = registered_adapter(tmp_path)
    call_tool(
        tools,
        "sedna_manage_engagement",
        create_payload(),
        **LANE,
    )
    hooks["pre_tool_call"](
        tool_name="terminal",
        args={"command": "id"},
        session_id="session-orion",
        task_id="task-root",
    )
    events, _ = load_private_capture(tmp_path, _first_engagement_id(tmp_path))
    assert events[-1].type == "uncertain_correlation"


def _first_engagement_id(tmp_path) -> UUID:
    from uuid import UUID as _UUID

    root = tmp_path / "knowledge" / "engagements"
    for entry in root.iterdir():
        try:
            return _UUID(entry.name)
        except ValueError:
            continue
    raise AssertionError("no published engagement found")


def test_hook_write_failure_surfaces_next_turn_without_raising_from_hook(
    tmp_path, monkeypatch
) -> None:
    context, tools, hooks = registered_adapter(tmp_path)
    call_tool(
        tools,
        "sedna_manage_engagement",
        create_payload(),
        **LANE,
    )

    def raising_journal_failure(*args, **kwargs):
        raise RuntimeError("private failure detail")

    monkeypatch.setattr(EngagementJournalService, "open", raising_journal_failure)
    assert hooks["pre_tool_call"](tool_name="terminal", args={}, **HOOK_ID) is None
    reminder = hooks["pre_llm_call"](
        session_id="session-orion",
        turn_id="turn-2",
        user_message="continue",
        conversation_history=[],
        is_first_turn=False,
        model="fixture",
        platform="cli",
    )
    assert "not reliably journaled" in reminder["context"]
    assert "private failure" not in reminder["context"]


def _tools(context) -> dict:
    return {item["name"]: item for item in context.tools}


# -- Step 3: closing cancellation, abandon, and correlation edge cases -----


def test_new_call_while_closing_appends_cancel_and_start_in_one_batch(
    tmp_path,
) -> None:
    _, tools, hooks = registered_bound_adapter(tmp_path)
    call_tool(
        tools,
        "sedna_manage_engagement",
        {"action": "close", "reason": "proof"},
        **LANE,
    )
    hooks["pre_tool_call"](tool_name="terminal", args={"command": "whoami"}, **HOOK_ID)
    snapshot = load_snapshot(tmp_path)
    assert snapshot.state.status == "active"
    assert [event.type for event in snapshot.events][-3:] == [
        "evidence_attached",
        "closure_cancelled",
        "tool_call_started",
    ]


def test_manage_can_abandon_a_call_left_open_by_host_crash(tmp_path) -> None:
    _, tools, hooks = registered_bound_adapter(tmp_path)
    hooks["pre_tool_call"](
        tool_name="terminal",
        args={"command": "long-running-fixture"},
        **HOOK_ID,
    )
    journal_call_id = latest_event(tmp_path, "tool_call_started").payload.call_id
    assert journal_call_id != HOOK_ID["tool_call_id"]
    call_tool(
        tools,
        "sedna_manage_engagement",
        {"action": "close", "reason": "done"},
        **LANE,
    )
    resolved = call_tool(
        tools,
        "sedna_manage_engagement",
        {
            "action": "resolve_call",
            "call_id": journal_call_id,
            "resolution": "abandoned",
            "reason": "host process exited before post hook",
        },
        **LANE,
    )
    assert resolved["ok"] is True
    assert load_snapshot(tmp_path).state.closure_ready is True


def test_redelivered_stable_pre_before_closing_is_noop_and_does_not_cancel(
    tmp_path,
) -> None:
    _, tools, hooks = registered_bound_adapter(tmp_path)
    hooks["pre_tool_call"](tool_name="terminal", args={"command": "id"}, **HOOK_ID)
    call_tool(
        tools,
        "sedna_manage_engagement",
        {"action": "close", "reason": "done"},
        **LANE,
    )
    before = load_snapshot(tmp_path)
    assert before.state.status == "closing"
    hooks["pre_tool_call"](tool_name="terminal", args={"command": "id"}, **HOOK_ID)
    after = load_snapshot(tmp_path)
    assert after.revision == before.revision
    assert after.state.status == "closing"


def test_stable_duplicate_pre_post_pair_is_idempotent(tmp_path) -> None:
    _, tools, hooks = registered_bound_adapter(tmp_path)
    hooks["pre_tool_call"](tool_name="terminal", args={"command": "id"}, **HOOK_ID)
    hooks["pre_tool_call"](tool_name="terminal", args={"command": "id"}, **HOOK_ID)
    snapshot = load_snapshot(tmp_path)
    relevant = [
        event.type
        for event in snapshot.events
        if event.type
        in {
            "tool_call_started",
            "tool_call_completed",
            "unmatched_tool_completion",
            "uncertain_correlation",
        }
    ]
    assert relevant == ["tool_call_started"]
    hooks["post_tool_call"](
        tool_name="terminal",
        args={"command": "id"},
        result="ok",
        duration_ms=3,
        **HOOK_ID,
    )
    hooks["post_tool_call"](
        tool_name="terminal",
        args={"command": "id"},
        result="ok",
        duration_ms=3,
        **HOOK_ID,
    )
    snapshot = load_snapshot(tmp_path)
    relevant = [
        event.type
        for event in snapshot.events
        if event.type
        in {
            "tool_call_started",
            "tool_call_completed",
            "unmatched_tool_completion",
        }
    ]
    assert relevant == ["tool_call_started", "tool_call_completed"]


def test_uncertain_completion_links_only_when_exactly_one_match(
    tmp_path,
) -> None:
    _, tools, hooks = registered_bound_adapter(tmp_path)
    hooks["pre_tool_call"](
        tool_name="terminal",
        args={"command": "id"},
        session_id="session-orion",
        task_id="task-root",
    )
    hooks["post_tool_call"](
        tool_name="terminal",
        args={"command": "id"},
        result="uid=0",
        duration_ms=5,
        session_id="session-orion",
        task_id="task-root",
    )
    snapshot = load_snapshot(tmp_path)
    terminal = [
        event
        for event in snapshot.events
        if event.type in {"tool_call_completed", "unmatched_tool_completion"}
    ]
    assert len(terminal) == 1
    assert terminal[0].type == "tool_call_completed"


def test_two_uncertain_candidates_emit_sealed_unmatched_audit(tmp_path) -> None:
    _, tools, hooks = registered_bound_adapter(tmp_path)
    for n in range(2):
        hooks["pre_tool_call"](
            tool_name="terminal",
            args={"command": f"cmd-{n}"},
            session_id="session-orion",
            task_id="task-root",
        )
    hooks["post_tool_call"](
        tool_name="terminal",
        args={"command": "other"},
        result="ok",
        duration_ms=3,
        session_id="session-orion",
        task_id="task-root",
    )
    snapshot = load_snapshot(tmp_path)
    unmatched = [event for event in snapshot.events if event.type == "unmatched_tool_completion"]
    assert len(unmatched) == 1
    assert unmatched[0].payload.reason_code == "ambiguous_within_engagement"


def test_zero_or_cross_engagement_candidates_set_health_only(tmp_path) -> None:
    context, tools, hooks = registered_bound_adapter(tmp_path)
    digest = sha256(str(tmp_path / "knowledge").encode()).hexdigest()
    engagement_a = _first_engagement_id(tmp_path)
    # zero candidates: a post with no matching in-flight call
    hooks["post_tool_call"](
        tool_name="terminal",
        args={"command": "orphan"},
        result="late",
        duration_ms=1,
        session_id="session-other",
        task_id="task-other",
    )
    snapshot = load_snapshot(tmp_path)
    assert not [event for event in snapshot.events if event.type == "unmatched_tool_completion"]
    assert context.adapter._health.peek(digest, "session-other") == (
        "unmatched_completion",
        1,
    )

    # cross-engagement candidates: the same lane starts an uncertain call in
    # engagement A, is rebound, then starts another in engagement B
    call_tool(
        tools,
        "sedna_manage_engagement",
        create_payload(display_name="Orion-B"),
        session_id="session-b",
        task_id="task-b",
    )
    engagement_b = resolve_bound_engagement(tmp_path, "session-b", "task-b")
    from sedna.engagement.models import ExecutionLaneKey, HostKind

    lane = ExecutionLaneKey.from_host(
        host_kind=HostKind.HADES,
        session_id="session-orion",
        task_id="task-root",
    )
    # first uncertain call starts while the lane is still bound to engagement A
    hooks["pre_tool_call"](
        tool_name="terminal",
        args={"command": "a"},
        session_id="session-orion",
        task_id="task-root",
    )
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(root) as service:
        service.unbind_lane(engagement_a, lane, reason="rebind")
        service.bind_lane(engagement_b, lane, reason="rebind")
    # second uncertain call starts after the lane was rebound to engagement B
    hooks["pre_tool_call"](
        tool_name="terminal",
        args={"command": "b"},
        session_id="session-orion",
        task_id="task-root",
    )
    hooks["post_tool_call"](
        tool_name="terminal",
        args={"command": "c"},
        result="ok",
        duration_ms=3,
        session_id="session-orion",
        task_id="task-root",
    )
    snapshot_a = load_snapshot(tmp_path, engagement_a)
    snapshot_b = load_snapshot(tmp_path, engagement_b)
    assert not [event for event in snapshot_a.events if event.type == "unmatched_tool_completion"]
    assert not [event for event in snapshot_b.events if event.type == "unmatched_tool_completion"]
    assert context.adapter._health.peek(digest, "session-orion") == (
        "unmatched_completion",
        1,
    )


def test_post_after_abandon_is_unmatched_call_already_terminated(
    tmp_path,
) -> None:
    _, tools, hooks = registered_bound_adapter(tmp_path)
    hooks["pre_tool_call"](tool_name="terminal", args={"command": "slow"}, **HOOK_ID)
    call_id = latest_event(tmp_path, "tool_call_started").payload.call_id
    call_tool(
        tools,
        "sedna_manage_engagement",
        {
            "action": "resolve_call",
            "call_id": call_id,
            "resolution": "abandoned",
            "reason": "timeout",
        },
        **LANE,
    )
    hooks["post_tool_call"](
        tool_name="terminal",
        args={"command": "slow"},
        result="late result",
        duration_ms=999,
        **HOOK_ID,
    )
    snapshot = load_snapshot(tmp_path)
    unmatched = [event for event in snapshot.events if event.type == "unmatched_tool_completion"]
    assert len(unmatched) == 1
    assert unmatched[0].payload.reason_code == "call_already_terminated"


def test_minimum_post_signature_succeeds_without_status_fields(
    tmp_path,
) -> None:
    _, tools, hooks = registered_bound_adapter(tmp_path)
    hooks["pre_tool_call"](tool_name="terminal", args={"command": "id"}, **HOOK_ID)
    hooks["post_tool_call"](
        tool_name="terminal",
        args={"command": "id"},
        result="uid=0",
        task_id="task-root",
        duration_ms=4,
        session_id="session-orion",
        turn_id="turn-1",
        api_request_id="request-1",
        api_call_count=1,
        tool_call_id="tool-call-1",
    )
    snapshot = load_snapshot(tmp_path)
    completed = [event for event in snapshot.events if event.type == "tool_call_completed"]
    assert len(completed) == 1
    assert completed[0].payload.technical_status == "returned"


def test_post_follows_pre_calls_engagement_after_lane_rebind(tmp_path) -> None:
    context, tools, hooks = registered_bound_adapter(tmp_path)
    hooks["pre_tool_call"](tool_name="terminal", args={"command": "id"}, **HOOK_ID)
    engagement_a = _first_engagement_id(tmp_path)
    call_tool(
        tools,
        "sedna_manage_engagement",
        create_payload(display_name="Orion-B"),
        session_id="session-b",
        task_id="task-b",
    )
    engagement_b = resolve_bound_engagement(tmp_path, "session-b", "task-b")
    from sedna.engagement.models import ExecutionLaneKey, HostKind

    lane = ExecutionLaneKey.from_host(
        host_kind=HostKind.HADES,
        session_id="session-orion",
        task_id="task-root",
    )
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(root) as service:
        service.unbind_lane(engagement_a, lane, reason="rebind test")
        service.bind_lane(engagement_b, lane, reason="rebind test")
    hooks["post_tool_call"](
        tool_name="terminal",
        args={"command": "id"},
        result="uid=0",
        duration_ms=2,
        **HOOK_ID,
    )
    snapshot_a = load_snapshot(tmp_path, engagement_a)
    snapshot_b = load_snapshot(tmp_path, engagement_b)
    assert [event.type for event in snapshot_a.events].count("tool_call_completed") == 1
    assert not [event for event in snapshot_b.events if event.type == "tool_call_completed"]


def test_bound_lane_without_decision_emits_unplanned_action(tmp_path) -> None:
    _, tools, hooks = registered_adapter(tmp_path)
    create_bound_orion(tools)
    hooks["pre_tool_call"](tool_name="terminal", args={"command": "id"}, **HOOK_ID)
    snapshot = load_snapshot(tmp_path)
    unplanned = [event for event in snapshot.events if event.type == "unplanned_action"]
    assert len(unplanned) == 1
    assert (
        unplanned[0].payload.call_id == latest_event(tmp_path, "tool_call_started").payload.call_id
    )


def test_unbound_lane_attaches_nothing(tmp_path) -> None:
    _, tools, hooks = registered_adapter(tmp_path)
    hooks["pre_tool_call"](tool_name="terminal", args={"command": "id"}, **HOOK_ID)
    hooks["post_tool_call"](
        tool_name="terminal",
        args={"command": "id"},
        result="x",
        duration_ms=1,
        **HOOK_ID,
    )
    root = tmp_path / "knowledge"
    engagements = root / "engagements"
    published = [entry for entry in engagements.iterdir() if _is_uuid_dir(entry)]
    assert not published


def _is_uuid_dir(entry: Path) -> bool:
    try:
        UUID(entry.name)
    except ValueError:
        return False
    return entry.is_dir()


# -- settlement port test doubles ------------------------------------------


def _assert_no_journal_lock(root: Path, engagement_id) -> None:
    lock_path = root / "engagements" / str(engagement_id) / ".journal.lock"
    fd = os.open(lock_path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise AssertionError("journal lock still held during settlement") from exc
    finally:
        os.close(fd)


class MutatingRecordingSettlementPort(EngagementSettlementPort):
    """Settles by appending a visible user note outside the journal context."""

    def __init__(self, knowledge_root: Path, *, assert_no_journal_lock: bool) -> None:
        self.root = knowledge_root
        self.assert_no_journal_lock = assert_no_journal_lock
        self.calls: list[str] = []

    def settle(self, engagement_id: UUID, *, reason: str) -> EngagementSettlementOutcome:
        self.calls.append(reason)
        if self.assert_no_journal_lock:
            _assert_no_journal_lock(self.root, engagement_id)
        from sedna.engagement.events import JournalEventDraft, UserNotePayload

        with EngagementJournalService.open(self.root) as service:
            service.append_events(
                engagement_id,
                (
                    JournalEventDraft(
                        lane=None,
                        actor="settlement",
                        type="user_note",
                        payload=UserNotePayload(note=f"settled:{reason}"),
                    ),
                ),
            )
        return EngagementSettlementOutcome(status="complete", pending_range_count=0)


class StaticSettlementPortFactory:
    def __init__(self, port: EngagementSettlementPort) -> None:
        self._port = port

    def open(self, resolved_root: Path):
        bind = getattr(self._port, "bind_root", None)
        if bind is not None:
            bind(resolved_root)
        return contextlib.nullcontext(self._port)


class RaisingSettlementPort(EngagementSettlementPort):
    def __init__(self, code: str, *, assert_no_journal_lock: bool) -> None:
        self.code = code
        self.assert_no_journal_lock = assert_no_journal_lock
        self.calls: list[str] = []
        self._root: Path | None = None

    def bind_root(self, root: Path) -> None:
        self._root = root

    def settle(self, engagement_id: UUID, *, reason: str) -> EngagementSettlementOutcome:
        self.calls.append(reason)
        if self.assert_no_journal_lock:
            assert self._root is not None
            _assert_no_journal_lock(self._root, engagement_id)
        return EngagementSettlementOutcome(status="unavailable", safe_code=self.code)


class IncompleteSettlementPort(EngagementSettlementPort):
    """More than 2 MiB of pending evidence after settlement."""

    def __init__(self, knowledge_root: Path) -> None:
        self.root = knowledge_root
        self.calls: list[str] = []

    def settle(self, engagement_id: UUID, *, reason: str) -> EngagementSettlementOutcome:
        self.calls.append(reason)
        _assert_no_journal_lock(self.root, engagement_id)
        return EngagementSettlementOutcome(
            status="incomplete",
            pending_range_count=2,
            next_pending_offset=2_097_153,
            next_pending_subject=f"pending-{'ab' * 32}",
            pending_inventory_sha256="cd" * 32,
            safe_code="evidence_budget_exhausted",
        )


def logbook_authoritative_revision(tmp_path) -> Any:
    """Revision whose sequence/hash prefix is authoritative in the logbook."""
    from sedna.engagement.models import JournalRevision

    snapshot = load_snapshot(tmp_path)
    root = tmp_path / "knowledge"
    logbooks = sorted(
        (root / "engagements").glob("*/evidence/*.md"),
        key=lambda path: path.stat().st_mtime,
    )
    assert logbooks, "no logbook published"
    marker = f"- Revision: {snapshot.revision.sequence}/{snapshot.revision.event_hash[:12]}"
    assert any(marker in path.read_text(encoding="utf-8") for path in logbooks)
    return JournalRevision(
        sequence=snapshot.revision.sequence,
        event_hash=snapshot.revision.event_hash,
    )


def event_with_note(events, note: str):
    for event in events:
        if event.type == "user_note" and event.payload.note == note:
            return event
    raise AssertionError(f"no user_note with note {note!r}")


def latest_event_of_type(events, event_type: str):
    matches = [event for event in events if event.type == event_type]
    assert matches, f"no {event_type} event"
    return matches[-1]


def test_result_prose_does_not_change_technical_status(tmp_path) -> None:
    _, tools, hooks = registered_bound_adapter(tmp_path)
    hooks["pre_tool_call"](tool_name="terminal", args={"command": "run"}, **HOOK_ID)
    hooks["post_tool_call"](
        tool_name="terminal",
        args={"command": "run"},
        result="exit code 1: failed, but success later HTB{flag-shape}",
        duration_ms=1,
        **HOOK_ID,
    )
    completed = latest_event(tmp_path, "tool_call_completed")
    assert completed.payload.technical_status == "returned"
    assert completed.payload.possible_terminal_evidence is True


def test_argument_normalization_failure_emits_capture_role_audit(
    tmp_path,
) -> None:
    _, tools, hooks = registered_bound_adapter(tmp_path)
    cyclic: dict[str, Any] = {}
    cyclic["self"] = cyclic
    hooks["pre_tool_call"](tool_name="terminal", args=cyclic, **HOOK_ID)
    snapshot = load_snapshot(tmp_path)
    failed = [event for event in snapshot.events if event.type == "evidence_capture_failed"]
    assert len(failed) == 1
    assert failed[0].payload.capture_role == "arguments"
    assert failed[0].payload.observed_size is None
    assert failed[0].payload.observed_sha256 is None
    assert snapshot.state.in_flight_call_ids


def test_result_none_emits_unknown_terminal_without_evidence(tmp_path) -> None:
    _, tools, hooks = registered_bound_adapter(tmp_path)
    hooks["pre_tool_call"](tool_name="terminal", args={"command": "id"}, **HOOK_ID)
    hooks["post_tool_call"](
        tool_name="terminal",
        args={"command": "id"},
        result=None,
        duration_ms=1,
        **HOOK_ID,
    )
    snapshot = load_snapshot(tmp_path)
    completed = [event for event in snapshot.events if event.type == "tool_call_completed"]
    assert len(completed) == 1
    assert completed[0].payload.technical_status == "unknown"
    # no result evidence between the start and the lone terminal completion
    assert [event.type for event in snapshot.events][-2:] == [
        "tool_call_started",
        "tool_call_completed",
    ]


# -- Step 4: session and child hooks --------------------------------------


def test_session_start_is_idempotent_and_normalizes_model_platform(
    tmp_path,
) -> None:
    _, tools, hooks = registered_bound_adapter(tmp_path)
    hooks["on_session_start"](
        session_id="session-orion",
        task_id="task-root",
        model="  fixture-model  ",
        platform="cli",
    )
    hooks["on_session_start"](
        session_id="session-orion",
        task_id="task-root",
        model="fixture-model",
        platform="cli",
    )
    snapshot = load_snapshot(tmp_path)
    starts = [event for event in snapshot.events if event.type == "session_started"]
    assert len(starts) == 1
    assert starts[0].payload.model == "fixture-model"
    assert starts[0].payload.platform == "cli"


def test_session_start_before_bind_is_deferred_to_first_bound_operation(
    tmp_path,
) -> None:
    _, tools, hooks = registered_adapter(tmp_path)
    hooks["on_session_start"](session_id="session-orion", task_id="task-root")
    create_bound_orion(tools)
    hooks["pre_tool_call"](tool_name="terminal", args={"command": "id"}, **HOOK_ID)
    snapshot = load_snapshot(tmp_path)
    starts = [event for event in snapshot.events if event.type == "session_started"]
    assert len(starts) == 1
    assert starts[0].sequence < latest_event(tmp_path, "tool_call_started").sequence


def test_session_end_preserves_host_booleans_and_rejects_both_true(
    tmp_path,
) -> None:
    _, tools, hooks = registered_bound_adapter(tmp_path)
    hooks["on_session_end"](
        session_id="session-orion",
        task_id="task-root",
        completed=True,
        interrupted=False,
        reason="finished",
        turn_id="turn-9",
    )
    snapshot = load_snapshot(tmp_path)
    checkpoints = [event for event in snapshot.events if event.type == "session_checkpointed"]
    assert len(checkpoints) == 1
    assert checkpoints[0].payload.completed is True
    assert checkpoints[0].payload.interrupted is False
    assert checkpoints[0].payload.reason == "finished"
    # both true is rejected without appending
    hooks["on_session_end"](
        session_id="session-orion",
        task_id="task-root",
        completed=True,
        interrupted=True,
        turn_id="turn-10",
    )
    snapshot = load_snapshot(tmp_path)
    assert len([event for event in snapshot.events if event.type == "session_checkpointed"]) == 1
    # a redelivered identical callback is a no-op (same turn identity)
    hooks["on_session_end"](
        session_id="session-orion",
        task_id="task-root",
        completed=True,
        interrupted=False,
        reason="finished",
        turn_id="turn-9",
    )
    snapshot = load_snapshot(tmp_path)
    assert len([event for event in snapshot.events if event.type == "session_checkpointed"]) == 1


def test_session_finalize_without_task_id_finalizes_each_engagement_once(
    tmp_path,
) -> None:
    _, tools, hooks = registered_bound_adapter(tmp_path)
    engagement_id = _first_engagement_id(tmp_path)
    from sedna.engagement.models import ExecutionLaneKey, HostKind

    lane2 = ExecutionLaneKey.from_host(
        host_kind=HostKind.HADES,
        session_id="session-orion",
        task_id="task-2",
    )
    with EngagementJournalService.open(tmp_path / "knowledge") as service:
        service.bind_lane(engagement_id, lane2, reason="second task lane")
    hooks["on_session_finalize"](session_id="session-orion")
    snapshot = load_snapshot(tmp_path)
    finalized = [event for event in snapshot.events if event.type == "session_finalized"]
    assert len(finalized) == 1
    expected_lane = min(
        binding.lane.stable_key
        for binding in snapshot.state.bound_lanes
        if binding.lane.session_id == "session-orion"
    )
    assert finalized[0].lane.stable_key == expected_lane
    assert finalized[0].payload.reason == "finalized"
    # duplicate finalize delivery is a no-op
    hooks["on_session_finalize"](session_id="session-orion")
    snapshot = load_snapshot(tmp_path)
    assert len([event for event in snapshot.events if event.type == "session_finalized"]) == 1


def test_session_reset_clears_health_without_mutation(tmp_path) -> None:
    context, tools, hooks = registered_bound_adapter(tmp_path)
    hooks["post_tool_call"](
        tool_name="terminal",
        args={"command": "x"},
        result="y",
        duration_ms=1,
        session_id="session-other",
        task_id="task-other",
    )
    digest = sha256(str(tmp_path / "knowledge").encode()).hexdigest()
    assert context.adapter._health.peek(digest, "session-other") is not None
    before = load_snapshot(tmp_path).revision
    hooks["on_session_reset"](
        session_id="session-other",
        old_session_id="session-orion",
        platform="cli",
    )
    assert context.adapter._health.peek(digest, "session-other") is None
    assert load_snapshot(tmp_path).revision == before


def test_child_session_inherits_only_from_unique_parent_binding(
    tmp_path,
) -> None:
    _, _, hooks = registered_bound_adapter(tmp_path)
    hooks["subagent_start"](
        parent_session_id="session-orion",
        parent_turn_id="turn-1",
        parent_subagent_id=None,
        child_session_id="session-child",
        child_subagent_id="subagent-1",
        child_role="worker",
        child_goal="Inspect the HTTP hypothesis",
    )
    hooks["pre_tool_call"](
        tool_name="terminal",
        args={"command": "true"},
        session_id="session-child",
        task_id="child-task-observed",
        tool_call_id="child-call-1",
        turn_id="child-turn-1",
        api_request_id="child-request-1",
        api_call_count=1,
    )
    assert resolve_bound_engagement(tmp_path, "session-child", "child-task-observed") is not None


@pytest.mark.parametrize(
    ("status", "completed", "interrupted"),
    [
        ("ok", True, False),
        ("timeout", False, True),
        ("interrupted", False, True),
        ("error", False, False),
    ],
)
def test_subagent_stop_maps_child_status(
    tmp_path, status: str, completed: bool, interrupted: bool
) -> None:
    _, _, hooks = registered_bound_adapter(tmp_path)
    hooks["subagent_start"](
        parent_session_id="session-orion",
        child_session_id="session-child",
        child_subagent_id="subagent-1",
    )
    child_identity = {
        "session_id": "session-child",
        "task_id": "task-child",
        "tool_call_id": "c-1",
        "turn_id": "t-1",
        "api_request_id": "r-1",
        "api_call_count": 1,
    }
    hooks["pre_tool_call"](tool_name="terminal", args={"command": "work"}, **child_identity)
    hooks["post_tool_call"](
        tool_name="terminal",
        args={"command": "work"},
        result="done",
        duration_ms=5,
        **child_identity,
    )
    hooks["subagent_stop"](
        parent_session_id="session-orion",
        child_session_id="session-child",
        child_subagent_id="subagent-1",
        child_status=status,
        task_id="task-child",
        duration_ms=1234,
    )
    snapshot = load_snapshot(tmp_path)
    checkpoints = [event for event in snapshot.events if event.type == "session_checkpointed"]
    assert checkpoints[-1].payload.completed is completed
    assert checkpoints[-1].payload.interrupted is interrupted
    # no in-flight calls remain, so the child lane is unbound
    assert resolve_bound_engagement(tmp_path, "session-child", "task-child") is None


def test_subagent_stop_retains_binding_with_in_flight_call(tmp_path) -> None:
    _, _, hooks = registered_bound_adapter(tmp_path)
    hooks["subagent_start"](
        parent_session_id="session-orion",
        child_session_id="session-child",
    )
    hooks["pre_tool_call"](
        tool_name="terminal",
        args={"command": "slow"},
        session_id="session-child",
        task_id="task-child",
        tool_call_id="c-1",
        turn_id="t-1",
        api_request_id="r-1",
        api_call_count=1,
    )
    hooks["subagent_stop"](
        parent_session_id="session-orion",
        child_session_id="session-child",
        child_status="ok",
    )
    snapshot = load_snapshot(tmp_path)
    assert [event.type for event in snapshot.events].count("lane_unbound") == 0
    assert resolve_bound_engagement(tmp_path, "session-child", "task-child") is not None


def test_subagent_stop_unknown_or_unbound_is_noop_with_health(
    tmp_path,
) -> None:
    context, _, hooks = registered_bound_adapter(tmp_path)
    digest = sha256(str(tmp_path / "knowledge").encode()).hexdigest()
    # unbound parent: no checkpoint, only bounded health
    hooks["subagent_stop"](
        parent_session_id="session-ghost",
        child_session_id="session-child-ghost",
        child_status="ok",
    )
    snapshot = load_snapshot(tmp_path)
    assert not [event for event in snapshot.events if event.type == "session_checkpointed"]
    assert context.adapter._health.peek(digest, "session-ghost") is not None
    # unknown child status: mapped checkpoint plus bounded health
    hooks["subagent_start"](
        parent_session_id="session-orion",
        child_session_id="session-child",
    )
    hooks["subagent_stop"](
        parent_session_id="session-orion",
        child_session_id="session-child",
        child_status="bogus",
    )
    assert context.adapter._health.peek(digest, "session-orion") == (
        "unknown_child_status",
        1,
    )


# -- Step 4: settlement port no-lock sequences -----------------------------


def test_resume_and_finalize_call_optional_settlement_port_outside_journal_context(
    tmp_path,
) -> None:
    port = MutatingRecordingSettlementPort(tmp_path / "knowledge", assert_no_journal_lock=True)
    _, tools, hooks = registered_bound_adapter(
        tmp_path,
        settlement_port_factory=StaticSettlementPortFactory(port),
    )
    resumed = call_tool(tools, "sedna_manage_engagement", {"action": "resume"}, **LANE)
    after_resume = load_snapshot(tmp_path)

    assert resumed["engagement"]["revision"] == after_resume.revision.model_dump(mode="json")
    assert after_resume.events[-1].payload.note == "settled:resume"

    hooks["on_session_finalize"](session_id=LANE["session_id"], task_id=LANE["task_id"])
    finalized = load_snapshot(tmp_path)
    settlement_event = event_with_note(finalized.events, "settled:session_finalize")
    final_checkpoint = latest_event_of_type(finalized.events, "session_finalized")

    assert port.calls == ["resume", "session_finalize"]
    assert settlement_event.sequence < final_checkpoint.sequence
    assert final_checkpoint.previous_hash == settlement_event.event_hash
    assert logbook_authoritative_revision(tmp_path) == finalized.revision


def test_settlement_failure_is_typed_without_returning_stale_state(
    tmp_path,
) -> None:
    port = RaisingSettlementPort(code="settlement_unavailable", assert_no_journal_lock=True)
    _, tools, hooks = registered_bound_adapter(
        tmp_path,
        settlement_port_factory=StaticSettlementPortFactory(port),
    )

    resumed = call_tool(tools, "sedna_manage_engagement", {"action": "resume"}, **LANE)
    assert resumed["ok"] is False
    assert resumed["error"]["code"] == "settlement_unavailable"
    assert "engagement" not in resumed
    assert (
        hooks["on_session_finalize"](
            session_id=LANE["session_id"],
            task_id=LANE["task_id"],
        )
        is None
    )
    assert latest_event(tmp_path, "session_finalized").payload.reason == ("settlement_unavailable")

    reminder = hooks["pre_llm_call"](
        session_id=LANE["session_id"],
        task_id=LANE["task_id"],
        turn_id="turn-after-failure",
        user_message="continue",
        conversation_history=[],
        is_first_turn=False,
        model="fixture",
        platform="cli",
    )
    assert "settlement unavailable" in reminder["context"]
    assert "private exception" not in reminder["context"]


def test_resume_with_incomplete_settlement_exposes_no_stale_snapshot(
    tmp_path,
) -> None:
    port = IncompleteSettlementPort(tmp_path / "knowledge")
    _, tools, hooks = registered_bound_adapter(
        tmp_path,
        settlement_port_factory=StaticSettlementPortFactory(port),
    )
    resumed = call_tool(tools, "sedna_manage_engagement", {"action": "resume"}, **LANE)
    assert resumed["ok"] is False
    assert resumed["error"]["code"] == "evidence_budget_exhausted"
    assert resumed["error"]["retryable"] is True
    assert resumed["settlement"]["status"] == "incomplete"
    assert "engagement" not in resumed


def test_finalize_with_incomplete_settlement_records_exact_non_complete(
    tmp_path,
) -> None:
    port = IncompleteSettlementPort(tmp_path / "knowledge")
    _, tools, hooks = registered_bound_adapter(
        tmp_path,
        settlement_port_factory=StaticSettlementPortFactory(port),
    )
    hooks["on_session_finalize"](session_id=LANE["session_id"], task_id=LANE["task_id"])
    snapshot = load_snapshot(tmp_path)
    finalized = latest_event_of_type(snapshot.events, "session_finalized")
    assert finalized.payload.reason == "settlement_incomplete"
    assert finalized.payload.settlement_status == "incomplete"
    assert finalized.payload.pending_range_count == 2
    assert finalized.payload.next_pending_offset == 2_097_153
    assert finalized.payload.safe_code == "evidence_budget_exhausted"
    assert logbook_authoritative_revision(tmp_path) == snapshot.revision


def test_close_with_settlement_port_settles_once_and_cas_after_reload(
    tmp_path,
) -> None:
    port = MutatingRecordingSettlementPort(tmp_path / "knowledge", assert_no_journal_lock=True)
    _, tools, hooks = registered_bound_adapter(
        tmp_path,
        settlement_port_factory=StaticSettlementPortFactory(port),
    )
    closed = call_tool(
        tools,
        "sedna_manage_engagement",
        {"action": "close", "reason": "done"},
        **LANE,
    )
    assert closed["ok"] is True
    assert port.calls == ["close"]
    snapshot = load_snapshot(tmp_path)
    assert snapshot.state.status == "closing"
    assert snapshot.events[-1].type == "closure_requested"


def test_close_with_incomplete_settlement_returns_typed_envelope_without_mutation(
    tmp_path,
) -> None:
    port = IncompleteSettlementPort(tmp_path / "knowledge")
    _, tools, hooks = registered_bound_adapter(
        tmp_path,
        settlement_port_factory=StaticSettlementPortFactory(port),
    )
    closed = call_tool(
        tools,
        "sedna_manage_engagement",
        {"action": "close", "reason": "done"},
        **LANE,
    )
    assert closed["ok"] is False
    assert closed["error"]["code"] == "evidence_budget_exhausted"
    assert closed["error"]["retryable"] is True
    assert closed["settlement"]["status"] == "incomplete"
    assert "engagement" not in closed
    snapshot = load_snapshot(tmp_path)
    assert snapshot.state.status == "active"
    assert not [event for event in snapshot.events if event.type == "closure_requested"]


def test_profile_switch_pins_initial_store_for_whole_invocation(
    tmp_path,
) -> None:
    calls = {"count": 0}

    def resolver() -> Path:
        calls["count"] += 1
        return tmp_path / "knowledge"

    context = FakeHadesContext(tmp_path / "knowledge")
    adapter = HadesEngagementAdapter(context, root_resolver=resolver)
    context.adapter = adapter
    adapter.register()
    tools = {item["name"]: item for item in context.tools}
    call_tool(
        tools,
        "sedna_manage_engagement",
        create_payload(),
        **LANE,
    )
    calls["count"] = 0
    closed = call_tool(
        tools,
        "sedna_manage_engagement",
        {"action": "close", "reason": "done"},
        **LANE,
    )
    assert closed["ok"] is True
    assert calls["count"] == 1
    snapshot = load_snapshot(tmp_path)
    assert snapshot.state.status == "closing"


# -- health map saturation --------------------------------------------------


def test_health_map_rejects_unknown_codes_and_enforces_bounds(tmp_path, monkeypatch) -> None:
    from sedna.engagement.hades_adapter import _HealthMap
    from sedna.engagement.models import (
        MAX_HEALTH_ENTRIES_PER_STORE,
        MAX_HEALTH_ENTRIES_TOTAL,
    )

    health = _HealthMap()
    with pytest.raises(ValueError):
        health.record("store", "session", "bogus_code")

    monkeypatch.setattr("sedna.engagement.hades_adapter.MAX_HEALTH_OCCURRENCES", 5)
    for _ in range(20):
        health.record("store-sat", "session-sat", "journal_unavailable")
    assert health.peek("store-sat", "session-sat") == (
        "journal_unavailable",
        5,
    )

    for index in range(MAX_HEALTH_ENTRIES_PER_STORE + 1):
        health.record("store-a", f"session-{index}", "unbound_lane")
    assert sum(1 for key in health._entries if key[0] == "store-a") == MAX_HEALTH_ENTRIES_PER_STORE

    for index in range(MAX_HEALTH_ENTRIES_TOTAL + 17):
        health.record(f"store-{index % 9}", f"session-{index}", "unmatched_completion")
    assert len(health._entries) == MAX_HEALTH_ENTRIES_TOTAL


def test_health_map_concurrent_insert_purge(tmp_path) -> None:
    import threading

    from sedna.engagement.hades_adapter import _HealthMap
    from sedna.engagement.models import MAX_HEALTH_ENTRIES_TOTAL

    health = _HealthMap()
    errors: list[Exception] = []

    def worker(prefix: str) -> None:
        try:
            for index in range(200):
                session = f"{prefix}-{index % 20}"
                health.record("store", session, "unbound_lane")
                if index % 50 == 0:
                    health.purge("store", session)
        except Exception as exc:  # pragma: no cover - failure reporting only
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"t{n}",)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert len(health._entries) <= MAX_HEALTH_ENTRIES_TOTAL


def test_cyclic_control_argument_produces_uncertain_correlation(
    tmp_path,
) -> None:
    _, tools, hooks = registered_bound_adapter(tmp_path)
    cyclic: dict[str, Any] = {}
    cyclic["self"] = cyclic
    hooks["pre_tool_call"](
        tool_name="sedna_retrieve_knowledge",
        args=cyclic,
        session_id="session-orion",
        task_id="task-root",
        turn_id="t-1",
        api_request_id="r-1",
        api_call_count=1,
    )
    snapshot = load_snapshot(tmp_path)
    invoked = [event for event in snapshot.events if event.type == "control_tool_invoked"]
    assert len(invoked) == 1
    assert invoked[0].payload.correlation.kind.value == "uncertain"
    # no ordinary argument sidecar for the cyclic value
    assert not [event for event in snapshot.events if event.type == "evidence_attached"]


def test_control_call_with_provider_token_leaks_nothing(tmp_path) -> None:
    _, tools, hooks = registered_bound_adapter(tmp_path)
    secret = "provider-token-abc123"
    hooks["pre_tool_call"](
        tool_name="sedna_retrieve_knowledge",
        args={
            "query": "exposed services",
            "provider_token": secret,
            "credential_scope": "provider",
        },
        session_id="session-orion",
        task_id="task-root",
        tool_call_id="ctl-1",
        turn_id="t-1",
        api_request_id="r-1",
        api_call_count=1,
    )
    snapshot = load_snapshot(tmp_path)
    invoked = [event for event in snapshot.events if event.type == "control_tool_invoked"]
    assert len(invoked) == 1
    raw = json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False)
    assert secret not in raw
    assert sha256(secret.encode()).hexdigest() not in raw
