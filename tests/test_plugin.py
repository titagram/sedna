from __future__ import annotations

import json
from pathlib import Path

import yaml

from sedna.plugin import register

EXPECTED_ENGAGEMENT_HOOKS = {
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


class FakeContext:
    def __init__(self) -> None:
        self.tools: list[dict] = []
        self.hooks: dict = {}

    def register_tool(self, **kwargs) -> None:
        self.tools.append(kwargs)

    def register_hook(self, name, callback) -> None:
        self.hooks[name] = callback


def test_plugin_registers_all_implemented_tools_and_hooks():
    context = FakeContext()

    register(context)

    assert [tool["name"] for tool in context.tools] == [
        "sedna_nmap_tcp_discovery",
        "sedna_nmap_service_scan",
        "sedna_learn_local",
        "sedna_retrieve_knowledge",
        "sedna_get_knowledge_artifact",
        "sedna_knowledge_maintenance",
        "sedna_plan_next",
        "sedna_manage_engagement",
        "sedna_record_decision",
        "sedna_add_source",
    ]
    assert all(tool["toolset"] == "plugin_sedna" for tool in context.tools[:6])
    assert set(context.hooks) == EXPECTED_ENGAGEMENT_HOOKS


def test_plugin_handler_reports_invalid_input_without_running_a_command():
    context = FakeContext()
    register(context)

    response = json.loads(context.tools[0]["handler"]({"target": "--bad"}))

    assert response["ok"] is False
    assert "target" in response["error"]


def test_plugin_manifest_declares_every_registered_tool_and_hook():
    context = FakeContext()
    register(context)
    manifest = yaml.safe_load(
        (Path(__file__).parent.parent / "plugin.yaml").read_text(encoding="utf-8")
    )

    assert manifest["provides_tools"] == [tool["name"] for tool in context.tools]
    assert set(manifest["provides_hooks"]) == set(context.hooks)
