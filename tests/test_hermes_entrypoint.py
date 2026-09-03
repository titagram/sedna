from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


class HermesContext:
    def __init__(self, root: Path) -> None:
        self.sedna_knowledge_root = root
        self.tools: dict[str, dict[str, Any]] = {}
        self.hooks: dict[str, Any] = {}
        self.llm = self

    def register_tool(self, **definition: Any) -> None:
        self.tools[definition["name"]] = definition

    def register_hook(self, name: str, callback: Any) -> None:
        self.hooks[name] = callback

    def complete_structured(self, **kwargs: Any) -> Any:
        raise AssertionError(f"unexpected structured completion: {kwargs.get('purpose')}")


def load_entrypoint() -> ModuleType:
    entrypoint = Path(__file__).resolve().parents[1] / "__init__.py"
    spec = importlib.util.spec_from_file_location("sedna_hermes_entrypoint", entrypoint)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_engagement_handler_accepts_hermes_dispatch_abi(tmp_path: Path) -> None:
    context = HermesContext(tmp_path / "knowledge")
    load_entrypoint().register(context)

    handler = context.tools["sedna_manage_engagement"]["handler"]
    result = handler(
        {"action": "list"},
        session_id="session-1",
        task_id="task-1",
        user_task="inspect engagement state",
    )

    assert isinstance(result, str)
    payload = json.loads(result)
    assert payload["ok"] is True
    assert payload["engagements"] == []


def test_engagement_handler_preserves_hades_keyword_abi(tmp_path: Path) -> None:
    context = HermesContext(tmp_path / "knowledge")
    load_entrypoint().register(context)

    handler = context.tools["sedna_manage_engagement"]["handler"]
    result = handler(action="list", session_id="session-1", task_id="task-1")

    assert isinstance(result, dict)
    assert result["ok"] is True
    assert result["engagements"] == []


def test_dual_abi_handler_sanitizes_metadata_and_serializes_strings() -> None:
    module = load_entrypoint()
    captured: dict[str, Any] = {}

    def hades_handler(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "plain result"

    wrapped = module._dual_abi_handler(hades_handler)
    result = wrapped(
        {
            "action": "list",
            "session_id": "spoofed-session",
            "task_id": "spoofed-task",
            "user_task": "spoofed-user-task",
        },
        session_id="trusted-session",
        task_id="trusted-task",
        user_task="trusted-user-task",
    )

    assert captured == {
        "action": "list",
        "session_id": "trusted-session",
        "task_id": "trusted-task",
    }
    assert json.loads(result) == "plain result"


@pytest.mark.parametrize("invalid_result", [object(), float("nan")])
def test_dual_abi_handler_normalizes_non_json_results(invalid_result: object) -> None:
    module = load_entrypoint()

    def hades_handler(**_: Any) -> object:
        return invalid_result

    result = module._dual_abi_handler(hades_handler)({"action": "list"})

    assert json.loads(result) == {"error": "tool handler returned a non-JSON-serializable result"}
