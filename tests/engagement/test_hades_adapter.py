from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from sedna.engagement import (
    CONTROL_TOOL_NAMES,
    CONTROL_TOOL_POLICY_VERSION,
    EngagementJournalService,
)
from sedna.engagement.hades_adapter import HadesEngagementAdapter

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

    def register_tool(self, **definition) -> None:
        self.tools.append(definition)

    def register_hook(self, name, callback) -> None:
        self.hooks[name] = callback


def registered_adapter(tmp_path) -> tuple[FakeHadesContext, dict[str, dict], dict[str, Callable]]:
    context = FakeHadesContext(tmp_path / "knowledge")
    adapter = HadesEngagementAdapter(
        context,
        root_resolver=lambda: context.sedna_knowledge_root,
    )
    adapter.register()
    tools = {item["name"]: item for item in context.tools}
    return context, tools, context.hooks


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


def load_snapshot(tmp_path):
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(root) as service:
        return service.list_engagements()


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
        item["schema"]["parameters"]["additionalProperties"] is False
        for item in context.tools
    )


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

    events, evidence = load_private_capture(
        tmp_path, created["engagement"]["engagement_id"]
    )
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
    assert frozenset(
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
    ) == CONTROL_TOOL_NAMES
    for control in sorted(CONTROL_TOOL_NAMES):
        hooks["pre_tool_call"](
            tool_name=control, args={}, **stable_hook_identity(control)
        )
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
