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


# --------------------------------------------------------------------------- #
# The same contract, one level up: a completion that ARRIVES but fails its
# schema.
#
# The tests above cover a violation found by _validate_planner_draft, which runs
# on an already-parsed draft and therefore has a draft to repair. A draft that
# fails the adapter's own schema validation never becomes an object, so there is
# nothing to repair -- and before this fix the error escaped `plan_next` as a
# bare PlanningLlmError with zero events recorded.
#
# Worse, the escape was ambiguous in the other direction: the only path that
# publishes `llm_unavailable` was gated on `reason_code == "transport_failure"`,
# so a genuinely unavailable host and a reachable model returning garbage were
# both silent, and the one that DID get published would have claimed "the model
# is unavailable" while a model was answering.
# --------------------------------------------------------------------------- #


class _RaisingLlm:
    """A planning LLM boundary that always fails with a chosen reason code."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def complete(self, *_args: object, **_kwargs: object) -> object:
        raise self._error


def _service_with_failing_llm(error: Exception) -> PlanningService:
    """A PlanningService whose only configured collaborator is a failing LLM.

    `_complete_planning` needs nothing else, so the instance is built without
    running __init__ -- the alternative is standing up a journal, a repository
    and a lane just to reach the boundary under test.
    """
    service = PlanningService.__new__(PlanningService)
    service._llm = _RaisingLlm(error)
    return service


def test_a_schema_invalid_completion_is_a_draft_error_not_a_transport_error() -> None:
    """The model answered; the answer was wrong. That is not an unavailable host."""
    from sedna.planning.llm import PlanningLlmError

    service = _service_with_failing_llm(
        PlanningLlmError("invalid_structured_response", detail="extra_forbidden: strategy")
    )

    with pytest.raises(service_module._PlannerDraftInvalidError) as caught:
        service._complete_planning(object)

    assert not isinstance(caught.value, service_module._PlanningLlmUnavailableError), (
        "a schema-invalid draft must not enter the unavailable-host path: the model "
        "was reachable, so recording 'llm_unavailable' would state a falsehood"
    )
    assert "extra_forbidden" in str(caught.value), "the schema diagnosis must survive"


def test_a_transport_failure_still_means_the_host_is_unavailable() -> None:
    """The unavailable-host meaning must stay attached to transport failures only."""
    from sedna.planning.llm import PlanningLlmError

    service = _service_with_failing_llm(
        PlanningLlmError("transport_failure", detail="OSError: connection refused")
    )

    with pytest.raises(service_module._PlanningLlmUnavailableError):
        service._complete_planning(object)


def test_other_non_transport_reasons_are_draft_errors_too() -> None:
    """Every non-transport reason describes the response, not the transport."""
    from sedna.planning.llm import PlanningLlmError

    for reason in ("missing_parsed_response", "invalid_structured_response"):
        service = _service_with_failing_llm(PlanningLlmError(reason))
        with pytest.raises(service_module._PlannerDraftInvalidError):
            service._complete_planning(object)


def test_plan_next_routes_the_draft_error_to_the_reasoned_gap() -> None:
    """Guard against regression: the marker must be caught, not left to escape."""
    source = inspect.getsource(PlanningService.plan_next)

    assert "_PlannerDraftInvalidError" in source, (
        "_plan_next_once raises _PlannerDraftInvalidError but plan_next does not "
        "catch it, so a schema-invalid draft still escapes as a bare exception"
    )
    assert "_publish_invalid_draft_gap" in source, (
        "the draft-invalid path must publish a reasoned gap, not vanish"
    )
    assert "_publish_llm_unavailable_gap" in source, (
        "the unavailable-host path must stay wired as well"
    )
