from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from tests.planning.simulated_planner import SimulatedPlanner

_FIXTURES = Path(__file__).parent / "fixtures"


def _assert_primary_fixture_contract(tmp_path: Path, scenario: dict[str, object]) -> None:
    result = SimulatedPlanner(tmp_path, scenario).run()

    assert result.strategy_ids == (
        "strategy-ftp-enumeration",
        "strategy-ssh-credential-reuse",
        "strategy-http-reference-discovery",
    )
    assert result.reactivated_strategy_id == "strategy-ssh-credential-reuse"
    assert result.persisted_command_template == "ssh {{source_case_username}}@{{target}}"
    assert result.selected_strategy_id == "strategy-ssh-credential-reuse"
    assert result.host_adapted_command_template == "ssh {{username}}@{{target}}"


@pytest.mark.parametrize(
    "kind",
    (
        "service_observed",
        "strategy_selected",
        "strategy_reactivated",
        "source_command_suggested",
        "host_command_adapted",
    ),
)
def test_primary_fixture_event_removal_breaks_acceptance_contract(
    tmp_path: Path,
    kind: str,
) -> None:
    scenario = json.loads((_FIXTURES / "multi-service-engagement.json").read_text())
    mutated = deepcopy(scenario)
    mutated["events"] = [event for event in mutated["events"] if event["kind"] != kind]

    with pytest.raises((AssertionError, LookupError, StopIteration, ValueError)):
        _assert_primary_fixture_contract(tmp_path, mutated)


def test_adaptive_multi_service_engagement_preserves_strategy_identity_and_proof_order(
    tmp_path: Path,
) -> None:
    scenario = json.loads((_FIXTURES / "multi-service-engagement.json").read_text())
    planner = SimulatedPlanner(tmp_path, scenario)

    result = planner.run()

    assert result.strategy_ids == tuple(scenario["expected_strategy_ids"])
    assert result.reactivated_strategy_id == scenario["reactivated_strategy_id"]
    assert result.selected_strategy_id == "strategy-ssh-credential-reuse"
    assert result.outcome_kinds == (
        "syntax_error",
        "syntax_corrected",
        "credentials_rejected",
        "bounded_wordlist_exhausted",
    )
    assert result.proof_requirement_ids == ("user-flag", "root-flag")
    assert result.terminal_calls == (result.terminal_situation,)
    assert result.terminal_loop_prevented
    assert result.outcomes_have_terminal_completion
    assert result.persisted_command_template == "ssh {{source_case_username}}@{{target}}"
    assert result.host_adapted_command_template == "ssh {{username}}@{{target}}"
    assert result.operational_tool_calls == ()
    assert result.commands_are_placeholder_bound
    assert result.source_credentials_are_unbound
    assert result.research_references_are_exact
    assert result.archive_references_are_exact
    assert result.real_plugin_handlers_called == (
        "sedna_manage_engagement",
        "sedna_plan_next",
        "sedna_record_decision",
    )
    assert "plan_requested" in result.persisted_event_types
    assert "frontier_proposed" in result.persisted_event_types
    assert "strategy_archived" in result.persisted_event_types
    assert "strategy_reactivated" in result.persisted_event_types
    assert result.persisted_event_types.count("objective_proof_observed") == 2
    assert "decision_recorded" in result.persisted_event_types
    assert "closure_requested" in result.persisted_event_types
    assert result.planning_service_calls >= 1


def _assert_adversarial_fixture_contract(tmp_path: Path, scenario: dict[str, object]) -> None:
    result = SimulatedPlanner(tmp_path, scenario).run_adversarial_cases()

    assert result.consulted_source_ids == ("user-source", "alternative-source")
    assert result.hostile_evidence_count == 3
    assert result.false_flag_evidence_observed
    assert result.recovery_steps == (
        "llm_unavailable",
        "unplanned_host_action",
        "planner_recovered",
    )


@pytest.mark.parametrize(
    "field",
    ("sources", "hostile_instructions", "false_flag", "failure_recovery"),
)
def test_adversarial_fixture_removal_breaks_acceptance_contract(
    tmp_path: Path,
    field: str,
) -> None:
    scenario = json.loads((_FIXTURES / "adversarial-evidence.json").read_text())
    mutated = deepcopy(scenario)
    if field == "false_flag":
        mutated.pop(field)
    elif field == "hostile_instructions":
        mutated[field] = mutated[field][:1]
    else:
        mutated[field] = []

    with pytest.raises((AssertionError, LookupError, StopIteration, ValueError)):
        _assert_adversarial_fixture_contract(tmp_path, mutated)


def test_failure_recovery_fixture_order_drives_persisted_recovery_order(
    tmp_path: Path,
) -> None:
    scenario = json.loads((_FIXTURES / "adversarial-evidence.json").read_text())
    permuted = deepcopy(scenario)
    permuted["failure_recovery"] = [
        "unplanned_host_action",
        "llm_unavailable",
        "planner_recovered",
    ]

    original_result = SimulatedPlanner(tmp_path / "original", scenario).run_adversarial_cases()
    permuted_result = SimulatedPlanner(tmp_path / "permuted", permuted).run_adversarial_cases()
    recovery_event_types = {
        "unplanned_action",
        "planning_gap_recorded",
        "plan_requested",
        "frontier_proposed",
    }

    assert tuple(
        event_type
        for event_type in original_result.typed_research_event_types
        if event_type in recovery_event_types
    ) == (
        "planning_gap_recorded",
        "unplanned_action",
        "plan_requested",
        "frontier_proposed",
    )
    assert tuple(
        event_type
        for event_type in permuted_result.typed_research_event_types
        if event_type in recovery_event_types
    ) == (
        "unplanned_action",
        "planning_gap_recorded",
        "plan_requested",
        "frontier_proposed",
    )


def test_adversarial_and_gap_acceptance_remains_bounded_and_closed(tmp_path: Path) -> None:
    scenario = json.loads((_FIXTURES / "adversarial-evidence.json").read_text())
    planner = SimulatedPlanner(tmp_path, scenario)

    result = planner.run_adversarial_cases()

    assert result.rejected_queries == (
        "HTB-Orion walkthrough",
        "HTB-Orion user flag",
        "HTB-Orion root flag",
    )
    assert result.typed_gaps == (
        "unknown_architecture",
        "unsupported_android_adb",
        "llm_unavailable",
    )
    assert result.recovery_after_unplanned_action
    assert result.false_flag_rejected
    assert result.false_flag_evidence_observed
    assert result.hostile_instructions_inert
    assert result.request_sizes_bounded
    assert result.operational_tool_calls == ()
    assert result.consulted_source_ids == ("user-source", "alternative-source")
    assert result.hostile_evidence_count == 3
    assert result.recovery_steps == tuple(scenario["failure_recovery"])
    assert "research_query_proposed" in result.typed_research_event_types
    assert "research_source_consulted" in result.typed_research_event_types
    assert "research_source_assessed" in result.typed_research_event_types
    assert "observation_extracted" in result.typed_research_event_types
    assert "interpretation_succeeded" in result.typed_research_event_types
    assert "unplanned_action" in result.typed_research_event_types
    assert "plan_requested" in result.typed_research_event_types
    assert result.applicability_decisions["windows-only-on-linux"] == "rejected"
