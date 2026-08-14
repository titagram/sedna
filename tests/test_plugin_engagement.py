"""Engagement plugin surface: profile isolation, root pinning, and fail-closed input."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import sedna.plugin as plugin_module
from sedna.plugin import register


def test_plugin_imports_in_a_cold_interpreter() -> None:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", "import sedna.plugin; print('cold-start-ok')"],
        cwd=Path(__file__).resolve().parents[1] / "src",
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "cold-start-ok"


@pytest.mark.parametrize("import_order", ("engagement-first", "reporting-first"))
def test_report_contracts_round_trip_in_both_cold_start_import_orders(import_order: str) -> None:
    imports = (
        "import sedna.engagement as engagement\nimport sedna.engagement.reporting as reporting"
        if import_order == "engagement-first"
        else "import sedna.engagement.reporting as reporting\nimport sedna.engagement as engagement"
    )
    script = (
        imports
        + "\n"
        + textwrap.dedent(
            """
        import tempfile
        from pathlib import Path

        import sedna
        from sedna.engagement.events import EventPayloadAdapter
        from sedna.engagement.reporting.markdown import render_operational_report
        from sedna.engagement.reporting.projector import OperationalReportProjector
        from sedna.knowledge.retrieval import (
            AuthorizationScope,
            AuthorizationState,
            ValidatedTarget,
        )

        scope = AuthorizationScope(
            state=AuthorizationState.AUTHORIZED,
            exact_targets=(ValidatedTarget.parse("192.0.2.44"),),
        )
        lane = engagement.ExecutionLaneKey(
            host_kind=engagement.HostKind.HADES,
            session_id="cold-start",
            task_id="report-round-trip",
        )
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            with engagement.EngagementJournalService.open(Path(directory) / "knowledge") as service:
                opened = service.create_engagement(
                    display_name="Orion",
                    objective="Obtain proof",
                    scope=scope,
                    lane=lane,
                )
                closing = service.request_close(
                    opened.snapshot.engagement_id,
                    lane=lane,
                    reason="complete",
                    expected_revision=opened.snapshot.revision,
                ).snapshot
                report = OperationalReportProjector().project(
                    snapshot=closing,
                    events=closing.events,
                    evidence=(),
                    evidence_reader=service.read_evidence_slice,
                    report_revision=1,
                    generated_at=closing.events[-1].occurred_at,
                )
                capability = service._repository._issue_report_commit_capability()
                committed = capability.commit_report_snapshot(
                    closing.engagement_id,
                    report,
                    render_operational_report(report),
                    expected_revision=closing.revision,
                )

        generated = committed.snapshot.events[-2]
        payload = generated.payload.model_dump(mode="json")
        assert EventPayloadAdapter.validate_python(payload) == generated.payload
        draft = engagement.JournalEventDraft.model_validate(
            {
                "actor": generated.actor,
                "type": generated.type,
                "payload": generated.payload.model_dump(mode="json"),
                "system_correlation": generated.system_correlation.model_dump(mode="json"),
            }
        )
        assert draft.payload == generated.payload
        assert engagement.JournalEvent.model_validate_json(generated.model_dump_json()) == generated
        assert engagement.EngagementSnapshot.model_validate_json(
            committed.snapshot.model_dump_json()
        ) == committed.snapshot
        validated_commit = reporting.ReportCommitResult.model_validate_json(
            committed.model_dump_json()
        )
        assert validated_commit == committed
        for contract in (
            engagement.JournalEventDraft,
            engagement.JournalEvent,
            engagement.EngagementSnapshot,
            reporting.OperationalReport,
            reporting.ReportCommitResult,
        ):
            assert contract.model_json_schema()["type"] == "object"
        assert sedna.__version__ == "0.2.0"
        print("report-round-trip-ok")
        """
        )
    )
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1] / "src",
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "report-round-trip-ok"


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
    assert (
        hook(
            tool_name="terminal",
            args={"command": "id"},
            session_id="session-a",
            task_id="root-a",
            turn_id="turn-1",
            api_request_id="request-1",
            api_call_count=1,
            tool_call_id="tool-call-1",
        )
        is None
    )
    journals = list((tmp_path / "knowledge" / "engagements").glob("*/events.jsonl"))
    assert len(journals) == 1
    lines = journals[0].read_text(encoding="utf-8").splitlines()
    assert any(json.loads(line)["type"] == "tool_call_started" for line in lines)
