from __future__ import annotations

import json

from sedna.plugin import register


class FakeContext:
    def __init__(self) -> None:
        self.tools: list[dict] = []

    def register_tool(self, **kwargs) -> None:
        self.tools.append(kwargs)


def test_plugin_registers_only_implemented_tools():
    context = FakeContext()

    register(context)

    assert [tool["name"] for tool in context.tools] == [
        "sedna_nmap_tcp_discovery",
        "sedna_nmap_service_scan",
    ]
    assert all(tool["toolset"] == "plugin_sedna" for tool in context.tools)


def test_plugin_handler_reports_invalid_input_without_running_a_command():
    context = FakeContext()
    register(context)

    response = json.loads(context.tools[0]["handler"]({"target": "--bad"}))

    assert response["ok"] is False
    assert "target" in response["error"]
