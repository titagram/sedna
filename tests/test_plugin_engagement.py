"""Engagement plugin surface: profile isolation, root pinning, and fail-closed input."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_type_hints
from uuid import UUID

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
        self.llm = self

    def register_tool(self, **kwargs: Any) -> None:
        self.tools.append(kwargs)

    def register_hook(self, name: str, callback) -> None:
        self.hooks[name] = callback

    def complete_structured(self, **kwargs: Any) -> Any:
        raise AssertionError(f"unexpected structured completion: {kwargs.get('purpose')}")


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


def test_manage_schema_exposes_verified_lifecycle_and_report_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_hermes_home(monkeypatch, lambda: tmp_path / "hades")
    context = HookContext()
    register(context)

    tool = next(item for item in context.tools if item["name"] == "sedna_manage_engagement")
    parameters = tool["schema"]["parameters"]

    actions = set(parameters["properties"]["action"]["enum"])
    assert {"close", "verify", "reject", "reopen", "report"} <= actions
    assert {"regenerate_report", "repair_report"}.isdisjoint(actions)
    assert {
        "verification_kind",
        "verification_reference",
        "flag_event_id",
    } <= parameters["properties"].keys()
    assert "rejected_value_sha256" not in parameters["properties"]


def test_owned_sedna_runtime_protocol_has_exact_typed_task_two_surfaces() -> None:
    from sedna.planning.ports import OwnedSednaRuntime, SednaRuntimeFactory

    annotations = get_type_hints(OwnedSednaRuntime)

    assert set(annotations) == {
        "journal",
        "planning",
        "report_finalizer",
        "reporting",
        "engagements",
    }
    assert all(surface is not Any for surface in annotations.values())
    assert SednaRuntimeFactory is not None


def test_manage_calls_open_and_close_exactly_one_owned_runtime(tmp_path: Path, monkeypatch) -> None:
    from sedna.knowledge.hades_runtime import HadesKnowledgeRuntime

    opened: list[Path] = []
    closed: list[HadesKnowledgeRuntime] = []
    original_create = HadesKnowledgeRuntime.create.__func__
    original_close = HadesKnowledgeRuntime.close

    def create(cls, host_llm, knowledge_root, **kwargs):
        opened.append(Path(knowledge_root))
        return original_create(cls, host_llm, knowledge_root, **kwargs)

    def close(runtime):
        closed.append(runtime)
        return original_close(runtime)

    monkeypatch.setattr(HadesKnowledgeRuntime, "create", classmethod(create))
    monkeypatch.setattr(HadesKnowledgeRuntime, "close", close)
    root = tmp_path / "knowledge"
    context = HookContext(configured_root=root)
    register(context)

    created = call_tool(
        context,
        "sedna_manage_engagement",
        create_payload("Orion"),
        session_id="session-a",
        task_id="root-a",
    )
    inspected = call_tool(
        context,
        "sedna_manage_engagement",
        {"action": "inspect", "engagement_id": created["engagement"]["engagement_id"]},
        session_id="session-a",
        task_id="root-a",
    )

    assert created["ok"] is True
    assert inspected["ok"] is True
    assert opened == [root, root]
    assert len(closed) == 2


def test_registered_resume_uses_the_owned_runtime_planning_service_directly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sedna.planning.ports as planning_ports

    context = HookContext(configured_root=tmp_path / "knowledge")
    register(context)
    created = call_tool(
        context,
        "sedna_manage_engagement",
        create_payload("Orion"),
        session_id="session-a",
        task_id="root-a",
    )

    def forbidden_adapter(*args, **kwargs):
        del args, kwargs
        raise AssertionError("resume must not invoke PlanningSettlementAdapter")

    monkeypatch.setattr(planning_ports, "PlanningSettlementAdapter", forbidden_adapter)
    resumed = call_tool(
        context,
        "sedna_manage_engagement",
        {"action": "resume", "engagement_id": created["engagement"]["engagement_id"]},
        session_id="session-a",
        task_id="root-a",
    )

    assert resumed["ok"] is True


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


def test_registered_manage_lifecycle_matrix_switches_profiles_with_one_runtime_per_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sedna.knowledge.hades_runtime import HadesKnowledgeRuntime

    active = {"home": tmp_path / "profile-a"}
    opened: list[tuple[Path, HadesKnowledgeRuntime]] = []
    closed: list[HadesKnowledgeRuntime] = []
    original_create = HadesKnowledgeRuntime.create.__func__
    original_close = HadesKnowledgeRuntime.close

    def create(cls, host_llm, knowledge_root, **kwargs):
        runtime = original_create(cls, host_llm, knowledge_root, **kwargs)
        opened.append((Path(knowledge_root), runtime))
        return runtime

    def close(runtime):
        closed.append(runtime)
        return original_close(runtime)

    monkeypatch.setattr(HadesKnowledgeRuntime, "create", classmethod(create))
    monkeypatch.setattr(HadesKnowledgeRuntime, "close", close)
    install_fake_hermes_home(monkeypatch, lambda: active["home"])
    context = HookContext()
    register(context)

    for branch in ("a", "b"):
        active["home"] = tmp_path / f"profile-{branch}"
        lane = {"session_id": f"session-{branch}", "task_id": f"root-{branch}"}
        created = call_tool(
            context,
            "sedna_manage_engagement",
            create_payload(f"Orion-{branch.upper()}"),
            **lane,
        )
        engagement_id = created["engagement"]["engagement_id"]
        engagement_uuid = UUID(engagement_id)
        actions = (
            {"action": "resume", "engagement_id": engagement_id},
            {"action": "inspect", "engagement_id": engagement_id},
            {"action": "close", "engagement_id": engagement_id, "reason": "complete"},
            {
                "action": "verify",
                "engagement_id": engagement_id,
                "verification_kind": "platform",
                "verification_reference": f"submission-{branch}",
            },
            {"action": "report", "engagement_id": engagement_id},
        )
        results = [
            call_tool(context, "sedna_manage_engagement", action, **lane) for action in actions
        ]
        root = active["home"] / "knowledge" / "sedna"
        from sedna.engagement.service import EngagementJournalService

        with EngagementJournalService.open(root) as journal:
            before_reopen = journal.load_snapshot(engagement_uuid)
        denied_reopen = call_tool(
            context,
            "sedna_manage_engagement",
            {"action": "reopen", "engagement_id": engagement_id, "reason": "continue"},
            **lane,
        )
        with EngagementJournalService.open(root) as journal:
            after_reopen = journal.load_snapshot(engagement_uuid)

        assert created["ok"] is True
        assert all(result["ok"] is True for result in results)
        assert results[2]["engagement"]["status"] == "closed_unverified"
        assert results[3]["engagement"]["status"] == "closed_verified"
        assert results[4]["report"]["report_revision"] == 2
        assert denied_reopen == {
            "ok": False,
            "error": {"code": "invalid_transition", "retryable": False},
        }
        assert after_reopen == before_reopen

    expected_roots = [
        tmp_path / "profile-a" / "knowledge" / "sedna",
        tmp_path / "profile-b" / "knowledge" / "sedna",
    ]
    assert [root for root, _ in opened] == [expected_roots[0]] * 7 + [expected_roots[1]] * 7
    assert [runtime for _, runtime in opened] == closed
    assert len({id(runtime) for _, runtime in opened}) == 14
    assert all(
        runtime.journal is not None
        and runtime.planning is not None
        and runtime.report_finalizer is not None
        and runtime.reporting is not None
        and runtime.engagements is not None
        for _, runtime in opened
    )


@pytest.mark.parametrize(
    ("artifact_state", "expected_revision"),
    (("missing_json", 2), ("missing_markdown", 1), ("valid", 2)),
)
def test_registered_report_triages_every_immutable_artifact_branch(
    tmp_path: Path,
    artifact_state: str,
    expected_revision: int,
) -> None:
    root = tmp_path / artifact_state / "knowledge"
    context = HookContext(configured_root=root)
    register(context)
    lane = {"session_id": f"session-{artifact_state}", "task_id": "root"}
    created = call_tool(
        context,
        "sedna_manage_engagement",
        create_payload(f"Orion-{artifact_state}"),
        **lane,
    )
    engagement_id = created["engagement"]["engagement_id"]
    closed = call_tool(
        context,
        "sedna_manage_engagement",
        {"action": "close", "engagement_id": engagement_id, "reason": "complete"},
        **lane,
    )
    report_dir = root / "engagements" / engagement_id / "reports"
    if artifact_state == "missing_json":
        (report_dir / "report-v1.json").unlink()
    elif artifact_state == "missing_markdown":
        (report_dir / "report-v1.md").unlink()

    reported = call_tool(
        context,
        "sedna_manage_engagement",
        {"action": "report", "engagement_id": engagement_id},
        **lane,
    )

    assert closed["engagement"]["status"] == "closed_unverified"
    assert reported["ok"] is True
    assert reported["report"]["report_revision"] == expected_revision
    assert (report_dir / f"report-v{expected_revision}.json").is_file()
    assert (report_dir / f"report-v{expected_revision}.md").is_file()


@pytest.mark.parametrize("verified", (False, True), ids=("unverified", "verified"))
def test_registered_reject_reopens_only_unverified_current_proof(
    tmp_path: Path,
    verified: bool,
) -> None:
    from uuid import UUID, uuid4

    from sedna.engagement.events import (
        EventType,
        EvidenceAttachedPayload,
        EvidenceSliceEventRef,
        InterpretationSucceededEventPayload,
        JournalEventDraft,
        ObjectiveProofObservedEventPayload,
        PlanningCallMetadataEventRecord,
        PrivateValueEventRecord,
    )
    from sedna.engagement.models import ExecutionLaneKey, HostKind
    from sedna.engagement.reporting.service import ReportClosureFinalizer
    from sedna.engagement.service import EngagementJournalService, PlanningEventCommitItem
    from sedna.planning.situation import SituationReducer

    root = tmp_path / "knowledge"
    context = HookContext(configured_root=root)
    register(context)
    lane_kwargs = {"session_id": "session-a", "task_id": "root-a"}
    lane = ExecutionLaneKey.from_host(host_kind=HostKind.HADES, **lane_kwargs)
    payload = create_payload("Orion")
    payload["required_proofs"] = (
        {"proof_id": "user-flag", "kind": "flag", "description": "user flag"},
    )
    created = call_tool(context, "sedna_manage_engagement", payload, **lane_kwargs)
    engagement_id = UUID(created["engagement"]["engagement_id"])
    proof_event_id = uuid4()
    interpretation_event_id = uuid4()
    with EngagementJournalService.open(root) as journal:
        snapshot = journal.load_snapshot(engagement_id)
        evidence = journal.write_evidence(
            snapshot.engagement_id,
            b"proof-value",
            media_type="text/plain",
            representation="utf-8",
        )
        attached = journal.append_hook_events(
            snapshot.engagement_id,
            (
                JournalEventDraft(
                    lane=lane,
                    actor="host_agent",
                    type=EventType.EVIDENCE_ATTACHED,
                    payload=EvidenceAttachedPayload(evidence=evidence),
                ),
            ),
            expected_revision=snapshot.revision,
        )
        proof = journal._issue_planning_event_commit_capability().commit_planning_events(
            snapshot.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=proof_event_id,
                    idempotency_key="registered-reject-proof",
                    payload=ObjectiveProofObservedEventPayload(
                        proof_requirement_id=snapshot.manifest.required_proofs[0].proof_id,
                        assessment_generation=1,
                        assessment="supported",
                        candidate_value=PrivateValueEventRecord(
                            evidence_slice=EvidenceSliceEventRef(
                                evidence_id=evidence.evidence_id,
                                start=0,
                                end=evidence.size,
                                sha256=evidence.sha256,
                                media_type=evidence.media_type,
                            ),
                            value_sha256=evidence.sha256,
                        ),
                        confidence=1.0,
                        evidence_ids=(evidence.evidence_id,),
                        source_event_ids=(attached.created_event_ids[0],),
                        interpretation_input_digest="a" * 64,
                    ),
                ),
                PlanningEventCommitItem(
                    event_id=interpretation_event_id,
                    idempotency_key="registered-reject-interpretation",
                    payload=InterpretationSucceededEventPayload(
                        interpretation_id=uuid4(),
                        attachment_event_id=attached.created_event_ids[0],
                        evidence_id=evidence.evidence_id,
                        covered_slices=(
                            EvidenceSliceEventRef(
                                evidence_id=evidence.evidence_id,
                                start=0,
                                end=evidence.size,
                                sha256=evidence.sha256,
                                media_type=evidence.media_type,
                            ),
                        ),
                        emitted_event_ids=(proof_event_id,),
                        call_metadata=PlanningCallMetadataEventRecord(
                            purpose="observe",
                            provider="test",
                            model="scripted",
                            agent_id="agent",
                            prompt_id="planning-observation",
                            prompt_version="1",
                            response_schema_version="1",
                            input_digest="b" * 64,
                            input_tokens=1,
                            output_tokens=1,
                            elapsed_ms=1,
                        ),
                        call_input_digest="b" * 64,
                        call_output_digest="c" * 64,
                    ),
                ),
            ),
            operation_id=uuid4(),
            expected_revision=attached.snapshot.revision,
        )
        closing = journal.request_close(
            snapshot.engagement_id,
            lane=lane,
            reason="complete",
            expected_revision=proof.snapshot.revision,
        )
        closed = ReportClosureFinalizer(
            journal,
            journal._repository._issue_report_commit_capability(),
        ).finalize(snapshot=closing.snapshot)
        SituationReducer.rebuild(closed)

    before_reject = None
    if verified:
        verified_result = call_tool(
            context,
            "sedna_manage_engagement",
            {
                "action": "verify",
                "engagement_id": engagement_id,
                "verification_kind": "platform",
                "verification_reference": "submission-verified",
            },
            **lane_kwargs,
        )
        assert verified_result["ok"] is True
        with EngagementJournalService.open(root) as journal:
            before_reject = journal.load_snapshot(engagement_id)

    rejected = call_tool(
        context,
        "sedna_manage_engagement",
        {
            "action": "reject",
            "engagement_id": engagement_id,
            "flag_event_id": str(proof_event_id),
            "reason": "collect replacement proof",
        },
        **lane_kwargs,
    )

    with EngagementJournalService.open(root) as journal:
        reopened = journal.load_snapshot(engagement_id)
    if verified:
        assert before_reject is not None
        assert rejected == {
            "ok": False,
            "error": {"code": "invalid_transition", "retryable": False},
        }
        assert reopened == before_reject
        return

    assert rejected["ok"] is True, rejected
    assert rejected["engagement"]["status"] == "active"
    assert reopened.revision.sequence == closed.revision.sequence + 2
    assert tuple(event.type for event in reopened.events[-2:]) == (
        "flag_rejected",
        "engagement_reopened",
    )
    assert reopened.events[-2].payload.flag_event_id == proof_event_id
    assert reopened.events[-1].payload.proof_revalidation == "retain_rejections"


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

    assert created["ok"] is True, created
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
