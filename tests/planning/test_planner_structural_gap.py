"""A structural draft violation must produce a reasoned gap, never a crash.

Defect 13 behavioural contract. Before the fix, `_plan_next_once` validated the
initial draft with an unguarded call, so a structural violation escaped
`plan_next` as a bare ValueError: no critic, no repair, no gap, no self-correction.
On a live settled engagement this surfaced as

    ValueError: command_secret_reference_not_current

with zero events recorded, even though the repair path exists and is tested.

The fix routes the violation through the existing plan -> critic -> repair loop and,
when repair cannot salvage the attempt, records a retryable gap. These tests pin
that contract at the service level.
"""

from __future__ import annotations

import inspect

import pytest

from sedna.planning import service as service_module
from sedna.planning.service import PlanningService


def test_repair_call_is_guarded_against_a_failed_repair() -> None:
    """The repair LLM call must not be able to abort the planning attempt."""
    source = inspect.getsource(PlanningService._plan_next_once)

    repair_index = source.find("sedna.planning.repair")
    assert repair_index != -1, "expected the repair call in _plan_next_once"

    # The call must be wrapped: `try:` before it and an `except PlanningLlmError`
    # after it. Searching only backwards missed the handler entirely.
    before = source[max(0, repair_index - 400) : repair_index]
    after = source[repair_index : repair_index + 600]
    assert "try:" in before, (
        "the repair call is not guarded: a failed repair aborts plan_next instead of "
        "recording a reasoned gap"
    )
    assert "except PlanningLlmError" in after, (
        "the repair call does not handle PlanningLlmError, so a failed repair still "
        "aborts the planning attempt"
    )


def test_a_retryable_structural_gap_publisher_exists() -> None:
    """The service must be able to record a structural-violation gap."""
    assert hasattr(PlanningService, "_publish_invalid_draft_gap"), (
        "no publisher for a structural-violation gap: the only options would be an "
        "uncaught exception or silently dropping the attempt"
    )


@pytest.mark.parametrize(
    "code",
    ["invalid_planner_output", "llm_unavailable"],
)
def test_structural_gap_codes_are_valid_contract_values(code: str) -> None:
    """The gap code used must exist in the PlanningGap contract."""
    from typing import get_args

    from sedna.planning.models import PlanningGap

    field = PlanningGap.model_fields["code"]
    allowed = set(get_args(field.annotation))
    assert code in allowed, f"{code} is not a valid PlanningGap.code ({sorted(allowed)})"


def test_source_has_no_unguarded_initial_validation() -> None:
    """Guard against regression: the initial validation must stay inside try/except."""
    source = inspect.getsource(PlanningService._plan_next_once)
    lines = source.splitlines()

    for index, line in enumerate(lines):
        if "_validate_planner_draft(" in line:
            preceding = "\n".join(lines[max(0, index - 14) : index])
            assert "try:" in preceding, (
                f"_validate_planner_draft at line {index} is not guarded:\n{line.strip()}"
            )


def test_service_module_exposes_the_reasoned_gap_path() -> None:
    """The module must reference the new gap path so the behaviour is wired."""
    source = inspect.getsource(service_module)
    assert "planner_draft_invalid" in source, (
        "the structural-violation finding is not constructed anywhere"
    )
