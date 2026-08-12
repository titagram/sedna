"""Engagement plugin surface: profile isolation, root pinning, and fail-closed input."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import sedna.plugin as plugin_module
from sedna.plugin import register


class HookContext:
    def __init__(self, *, configured_root: Path | None = None) -> None:
        if configured_root is not None:
            self.sedna_knowledge_root = configured_root
        self.tools: list[dict[str, Any]] = []
        self.hooks: dict[str, Any] = {}

    def register_tool(self, **kwargs: Any) -> None:
        self.tools.append(kwargs)

    def register_hook(self, name: str, callback) -> None:
        self.hooks[name] = callback


def install_fake_hermes_home(monkeypatch: pytest.MonkeyPatch, resolver) -> None:
    monkeypatch.setattr(
        plugin_module.importlib,
        "import_module",
        lambda _: SimpleNamespace(get_hermes_home=resolver),
    )


def call_tool(context: HookContext, name: str, payload: dict, **lane) -> dict:
    tool = next(tool for tool in context.tools if tool["name"] == name)
    invocation = dict(payload)
    invocation.update({key: value for key, value in lane.items() if value is not None})
    result = tool["handler"](**invocation)
    assert type(result) is dict
    return result


def create_payload(display_name: str) -> dict:
    return {
        "action": "create",
        "display_name": display_name,
        "objective": "Obtain the user and root flags",
        "authorization": ("192.0.2.44",),
    }


def test_engagement_tools_and_hooks_follow_active_profile_on_every_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = {"home": tmp_path / "profile-a"}
    install_fake_hermes_home(monkeypatch, lambda: active["home"])
    context = HookContext()
    register(context)

    first = call_tool(
        context,
        "sedna_manage_engagement",
        create_payload("Orion-A"),
        session_id="session-a",
        task_id="root-a",
    )
    active["home"] = tmp_path / "profile-b"
    second = call_tool(
        context,
        "sedna_manage_engagement",
        create_payload("Orion-B"),
        session_id="session-b",
        task_id="root-b",
    )

    assert first["ok"] and second["ok"]
    assert (tmp_path / "profile-a" / "knowledge" / "sedna" / "engagements").is_dir()
    assert (tmp_path / "profile-b" / "knowledge" / "sedna" / "engagements").is_dir()
    assert not (tmp_path / "profile-a" / "knowledge" / "sedna" / "sources.md").exists()


def test_registration_resolves_or_creates_no_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def resolve_home() -> Path:
        nonlocal calls
        calls += 1
        return tmp_path / "hades"

    install_fake_hermes_home(monkeypatch, resolve_home)
    context = HookContext()
    register(context)

    assert calls == 0
    assert not (tmp_path / "hades").exists()
    assert {item["name"] for item in context.tools} >= {
        "sedna_manage_engagement",
        "sedna_record_decision",
        "sedna_add_source",
    }
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


def test_context_override_wins_for_engagements(tmp_path: Path) -> None:
    context = HookContext(configured_root=tmp_path / "custom-knowledge")
    register(context)

    created = call_tool(
        context,
        "sedna_manage_engagement",
        create_payload("Orion"),
        session_id="session-a",
        task_id="root-a",
    )

    assert created["ok"] is True
    assert (tmp_path / "custom-knowledge" / "engagements").is_dir()


def test_relative_root_fails_closed(tmp_path: Path) -> None:
    context = HookContext(configured_root=Path("relative/sedna-root"))
    register(context)

    created = call_tool(
        context,
        "sedna_manage_engagement",
        create_payload("Orion"),
        session_id="session-a",
        task_id="root-a",
    )

    assert created["ok"] is False
    assert created["error"]["code"] == "invalid_input"
    assert not Path("relative").exists()


def test_engagement_schemas_contain_no_per_call_root() -> None:
    context = HookContext()
    register(context)

    for definition in context.tools:
        if definition["name"] not in {
            "sedna_manage_engagement",
            "sedna_record_decision",
            "sedna_add_source",
        }:
            continue
        schema = definition["schema"]["parameters"]
        assert "knowledge_root" not in schema.get("properties", {})


def test_invalid_create_input_writes_nothing(tmp_path: Path) -> None:
    context = HookContext(configured_root=tmp_path / "knowledge")
    register(context)

    created = call_tool(
        context,
        "sedna_manage_engagement",
        {
            "action": "create",
            "display_name": "Orion",
            "objective": "Obtain flags",
            "authorization": ("not a valid target",),
        },
        session_id="session-a",
        task_id="root-a",
    )

    assert created["ok"] is False
    assert created["error"]["code"] == "invalid_target"
    assert not (tmp_path / "knowledge" / "engagements").exists()


def test_engagement_hook_binding_uses_pinned_store(tmp_path: Path) -> None:
    context = HookContext(configured_root=tmp_path / "knowledge")
    register(context)
    call_tool(
        context,
        "sedna_manage_engagement",
        create_payload("Orion"),
        session_id="session-a",
        task_id="root-a",
    )

    hook = context.hooks["pre_tool_call"]
    assert hook(
        tool_name="terminal",
        args={"command": "id"},
        session_id="session-a",
        task_id="root-a",
        turn_id="turn-1",
        api_request_id="request-1",
        api_call_count=1,
        tool_call_id="tool-call-1",
    ) is None
    journals = list(
        (tmp_path / "knowledge" / "engagements").glob("*/events.jsonl")
    )
    assert len(journals) == 1
    lines = journals[0].read_text(encoding="utf-8").splitlines()
    assert any(
        json.loads(line)["type"] == "tool_call_started" for line in lines
    )
