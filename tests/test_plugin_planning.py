from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

import sedna.plugin as plugin_module
from sedna.engagement import EngagementSettlementOutcome
from sedna.planning import PlanningSettlementAdapter
from sedna.planning.models import (
    FailedSettlementResult,
    IncompleteSettlementResult,
    ObjectiveProgress,
    PendingEvidenceRange,
    PlanningGap,
    PlanningResult,
    ProofProgress,
    SituationProjection,
)
from sedna.plugin import register


class _RegistrationContext:
    def __init__(self, root: Path) -> None:
        self.sedna_knowledge_root = root
        self.tools: list[dict[str, Any]] = []
        self.hooks: dict[str, Any] = {}

    @property
    def llm(self) -> object:
        raise AssertionError("registration must not resolve the host LLM")

    def register_tool(self, **kwargs: Any) -> None:
        self.tools.append(kwargs)

    def register_hook(self, name: str, callback: Any) -> None:
        self.hooks[name] = callback


class _PlanningOperations:
    def __init__(self, result: object) -> None:
        self.result = result

    def settle_pending_evidence(self, engagement_id: UUID, *, reason: str) -> object:
        del engagement_id, reason
        return self.result


def test_registration_declares_rootless_plan_tool_without_opening_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _RegistrationContext(tmp_path / "knowledge")
    monkeypatch.setattr(
        plugin_module.HadesKnowledgeRuntime,
        "create",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("registration must not open a runtime")
        ),
    )

    register(context)

    tool = next(item for item in context.tools if item["name"] == "sedna_plan_next")
    properties = tool["schema"]["parameters"]["properties"]
    assert set(properties) == {"max_proposals"}
    assert properties["max_proposals"]["default"] == 5
    assert properties["max_proposals"]["minimum"] == 3
    assert properties["max_proposals"]["maximum"] == 8
    assert not (tmp_path / "knowledge").exists()


def test_plan_tool_resolves_current_root_and_preserves_exact_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "one"
    second = tmp_path / "two"
    context = _RegistrationContext(first)
    calls: list[tuple[Path, object, int]] = []
    closed: list[Path] = []

    class _Planning:
        def plan_next(self, lane: object, *, max_proposals: int) -> object:
            calls.append((current_root, lane, max_proposals))
            return SimpleNamespace(
                model_dump=lambda **_kwargs: {
                    "status": "failed",
                    "engagement_id": "00000000-0000-0000-0000-000000000001",
                    "current_authoritative_journal_revision": {
                        "sequence": 0,
                        "event_hash": "0" * 64,
                    },
                    "failure_code": "planning_failed",
                }
            )

    current_root = first

    @contextmanager
    def runtime_factory(root: Path) -> Iterator[object]:
        nonlocal current_root
        current_root = root
        try:
            yield SimpleNamespace(planning=_Planning())
        finally:
            closed.append(root)

    monkeypatch.setattr(plugin_module, "_planning_runtime_factory", lambda _ctx: runtime_factory)
    register(context)
    handler = next(item["handler"] for item in context.tools if item["name"] == "sedna_plan_next")

    first_result = json.loads(
        handler({"max_proposals": 3}, session_id="session-a", task_id="task-a")
    )
    context.sedna_knowledge_root = second
    second_result = json.loads(
        handler({"max_proposals": 8}, session_id="session-b", task_id="task-b")
    )

    assert [item[0] for item in calls] == [first, second]
    assert [(item[1].session_id, item[1].task_id, item[2]) for item in calls] == [
        ("session-a", "task-a", 3),
        ("session-b", "task-b", 8),
    ]
    assert closed == [first, second]
    assert first_result["failure_code"] == second_result["failure_code"] == "planning_failed"


def test_plan_tool_rejects_missing_lane_before_resolving_root(tmp_path: Path) -> None:
    context = _RegistrationContext(tmp_path / "knowledge")
    register(context)
    handler = next(item["handler"] for item in context.tools if item["name"] == "sedna_plan_next")

    result = json.loads(handler({}))

    assert result == {"error": "engagement_binding_required", "ok": False}
    assert not (tmp_path / "knowledge").exists()


def test_plan_tool_does_not_serialize_private_pending_range_evidence() -> None:
    result = PlanningResult(
        status="gap",
        engagement_id=UUID("00000000-0000-0000-0000-000000000001"),
        current_authoritative_journal_revision={"sequence": 1, "event_hash": "1" * 64},
        gap=PlanningGap(
            code="settlement_incomplete",
            summary="Evidence settlement remains incomplete.",
            retryable=True,
            pending_ranges=(
                PendingEvidenceRange(
                    attachment_event_id=UUID("00000000-0000-0000-0000-000000000002"),
                    evidence_id="evidence-sha256-" + "2" * 64,
                    start=2_097_153,
                    end=2_097_154,
                    media_type="text/plain",
                    reason="budget_exhausted",
                ),
            ),
        ),
    )

    serialized = plugin_module._serialize_planning_result(result)

    assert "pending_ranges" not in serialized
    assert "evidence-sha256" not in serialized
    assert "attachment_event_id" not in serialized


def test_settlement_adapter_maps_incomplete_without_planning_private_state() -> None:
    engagement_id = UUID("00000000-0000-0000-0000-000000000001")
    revision = {"sequence": 1, "event_hash": "1" * 64}
    situation = SituationProjection(
        engagement_id=engagement_id,
        authoritative_journal_revision=revision,
        material_event_revision=1,
        state_digest="5" * 64,
        objective_progress=ObjectiveProgress(
            requirements=(
                ProofProgress(
                    proof_requirement_id="user-flag",
                    status="pending",
                    historical_assessment_digest="6" * 64,
                    rejected_value_overflow_digest="7" * 64,
                ),
            )
        ),
    )
    result = IncompleteSettlementResult.model_validate(
        {
            "status": "incomplete",
            "engagement_id": engagement_id,
            "reason": "resume",
            "authoritative_journal_revision": revision,
            "situation": situation,
            "pending_ranges": [
                {
                    "attachment_event_id": "00000000-0000-0000-0000-000000000002",
                    "evidence_id": "evidence-sha256-" + "2" * 64,
                    "start": 2_097_153,
                    "end": 2_097_154,
                    "media_type": "text/plain",
                    "reason": "budget_exhausted",
                }
            ],
            "pending_total_count": 7,
            "next_pending_subject": "pending-" + "3" * 64,
            "pending_inventory_sha256": "4" * 64,
            "incomplete_reason": "budget_exhausted",
            "required_proof_ids": ["user-flag"],
            "all_required_proofs_satisfied": False,
            "possible_terminal_evidence": False,
        }
    )

    outcome = PlanningSettlementAdapter(planning=_PlanningOperations(result)).settle(
        engagement_id, reason="resume"
    )

    assert outcome == EngagementSettlementOutcome(
        status="incomplete",
        pending_range_count=7,
        next_pending_offset=2_097_153,
        next_pending_subject="pending-" + "3" * 64,
        pending_inventory_sha256="4" * 64,
        safe_code="evidence_budget_exhausted",
    )


@pytest.mark.parametrize(
    ("failure_code", "expected"),
    [
        ("journal_unavailable", ("unavailable", "journal_unavailable")),
        ("journal_corrupt", ("unavailable", "journal_corrupt")),
        ("terminal_reconciliation_failed", ("unavailable", "settlement_unavailable")),
        ("extractor_unavailable", ("failed", "interpretation_failed")),
    ],
)
def test_settlement_adapter_uses_closed_host_neutral_failure_codes(
    failure_code: str, expected: tuple[str, str]
) -> None:
    engagement_id = UUID("00000000-0000-0000-0000-000000000001")
    journal_missing = failure_code in {"journal_unavailable", "journal_corrupt"}
    revision = {"sequence": 1, "event_hash": "1" * 64}
    situation = SituationProjection(
        engagement_id=engagement_id,
        authoritative_journal_revision=revision,
        material_event_revision=1,
        state_digest="5" * 64,
        objective_progress=ObjectiveProgress(),
    )
    result = FailedSettlementResult.model_validate(
        {
            "status": "failed",
            "engagement_id": engagement_id,
            "reason": "close",
            "authoritative_journal_revision": None if journal_missing else revision,
            "situation": None if journal_missing else situation,
            "failure_code": failure_code,
            "failure_summary": "private detail",
            "required_proof_ids": [],
            "all_required_proofs_satisfied": False,
            "possible_terminal_evidence": False,
        }
    )

    outcome = PlanningSettlementAdapter(planning=_PlanningOperations(result)).settle(
        engagement_id, reason="close"
    )

    assert (outcome.status, outcome.safe_code) == expected
    assert "private detail" not in outcome.model_dump_json()
