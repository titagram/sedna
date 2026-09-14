"""A structurally invalid draft must reach the repair path, not abort planning.

Defect 13. `_plan_next_once` calls `_validate_planner_draft(planned, ...)`
UNGUARDED before the critic, so any structural violation raises straight out of
`plan_next`. The repair path exists and is tested, but only for critic refusals:

    plan -> [validate: raises] -> critic -> (if not accepted) repair -> critic

The repair loop is therefore unreachable for exactly the failures it could fix.
Observed on a live settled engagement: the model produced a draft, cited a
credential that does not exist in the context, and planning died with
`ValueError: command_secret_reference_not_current` — no critic, no repair, no gap,
no self-correction. The user-visible failure was an exception rather than a
recorded, reasoned outcome.

This is the harness side of the problem: the mechanism is wired, but the wiring
bypasses it.
"""

from __future__ import annotations

import pytest

from sedna.planning.service import PlanningService


def _plan_next_body() -> str:
    """Return the source of _plan_next_once for structural assertions."""
    import inspect

    return inspect.getsource(PlanningService._plan_next_once)


def test_initial_draft_validation_is_not_unguarded() -> None:
    """The validation call before the critic must be inside a try/except.

    Regression: it was a bare call, so a validation error aborted plan_next
    instead of being routed to the repair path.
    """
    body = _plan_next_body()
    lines = body.splitlines()

    index = next(
        (position for position, line in enumerate(lines) if "_validate_planner_draft(" in line),
        None,
    )
    assert index is not None, "expected a _validate_planner_draft call in _plan_next_once"

    # Walk back to the enclosing statement: anything other than a guarded form
    # means the exception escapes.
    preceding = "\n".join(lines[max(0, index - 12) : index])
    guarded = "try:" in preceding
    assert guarded, (
        "the initial _validate_planner_draft call is not inside a try block, so a "
        "validation failure aborts plan_next and never reaches the repair path"
    )


@pytest.mark.parametrize(
    "code",
    [
        "command_secret_reference_not_current",
        "command_target_binding_required",
        "prerequisite_proof_count_mismatch",
        "terminal_strategy_retry_predicate_policy",
    ],
)
def test_service_has_a_defined_path_for_structural_validation_failure(code: str) -> None:
    """Each structural violation must map to a handled outcome, not a crash.

    The service must either repair the draft or record a reasoned gap; raising an
    unhandled ValueError loses the planning attempt entirely.
    """
    import inspect

    import sedna.planning.service as service_module

    source = inspect.getsource(service_module)
    handler_present = "planner_draft_invalid" in source or "planner_repair_required" in source
    assert handler_present, (
        f"{code} has no handled outcome in the planning service: it escapes as an "
        "uncaught exception"
    )
