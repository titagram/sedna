from __future__ import annotations

from sedna.planning.models import FrontierProposalDraft, PlannerDraft
from sedna.planning.service import PlanningService
from sedna.planning.utility import UtilityInput, expected_utility, select_best_utility


def test_expected_utility_balances_plausibility_information_cost_and_risk() -> None:
    plausible_but_expensive = UtilityInput(
        objective_value=0.80,
        plausibility=0.90,
        information_gain=0.35,
        cost=0.90,
        risk=0.70,
        prerequisites_satisfied=True,
    )
    discriminating_low_cost_test = UtilityInput(
        objective_value=0.78,
        plausibility=0.78,
        information_gain=0.95,
        cost=0.20,
        risk=0.10,
        prerequisites_satisfied=True,
    )

    assert expected_utility(discriminating_low_cost_test) > expected_utility(
        plausible_but_expensive
    )


def test_unsatisfied_prerequisites_make_a_step_unselectable() -> None:
    blocked = UtilityInput(
        objective_value=1.0,
        plausibility=1.0,
        information_gain=1.0,
        cost=0.0,
        risk=0.0,
        prerequisites_satisfied=False,
    )

    assert expected_utility(blocked) == 0.0


def test_service_rejects_frontier_whose_first_step_is_not_maximum_utility() -> None:
    expensive = FrontierProposalDraft(
        family_runtime_key="family-expensive",
        variant_runtime_key="variant-expensive",
        title="Broad invasive probe",
        score=90,
        confidence=60,
        rationale="Try a broad path.",
        commands=(),
    )
    discriminating = FrontierProposalDraft(
        family_runtime_key="family-discriminating",
        variant_runtime_key="variant-discriminating",
        title="Check one discriminating fact",
        score=85,
        confidence=85,
        rationale="Resolve the decisive uncertainty.",
        expected_evidence=("A binary result distinguishes the hypotheses.",),
        stop_conditions=("Stop after one observation.",),
    )

    try:
        PlanningService._validate_expected_utility_selection(
            PlannerDraft(proposals=(expensive, discriminating))
        )
    except ValueError as error:
        assert str(error) == "frontier_not_expected_utility_ordered"
    else:
        raise AssertionError("lower-utility first proposal was accepted")


def test_service_rejects_partially_ordered_frontier_tail() -> None:
    def proposal(key: str, score: int, confidence: int) -> FrontierProposalDraft:
        return FrontierProposalDraft(
            family_runtime_key=f"family-{key}",
            variant_runtime_key=f"variant-{key}",
            title=f"Proposal {key}",
            score=score,
            confidence=confidence,
            rationale=f"Deterministic utility candidate {key}.",
        )

    best = proposal("best", 90, 90)
    middle = proposal("middle", 70, 70)
    worst = proposal("worst", 50, 50)

    try:
        PlanningService._validate_expected_utility_selection(
            PlannerDraft(proposals=(best, worst, middle))
        )
    except ValueError as error:
        assert str(error) == "frontier_not_expected_utility_ordered"
    else:
        raise AssertionError("partially ordered frontier tail was accepted")


def test_selection_is_deterministic_on_equal_utility() -> None:
    same = UtilityInput(
        objective_value=0.7,
        plausibility=0.7,
        information_gain=0.7,
        cost=0.2,
        risk=0.2,
        prerequisites_satisfied=True,
    )

    assert select_best_utility((("variant-b", same), ("variant-a", same))) == "variant-a"


def test_selection_excludes_candidates_with_unsatisfied_prerequisites() -> None:
    blocked = UtilityInput(
        objective_value=1.0,
        plausibility=1.0,
        information_gain=1.0,
        cost=0.0,
        risk=0.0,
        prerequisites_satisfied=False,
    )
    available = blocked.model_copy(
        update={"objective_value": 0.0, "plausibility": 0.0, "prerequisites_satisfied": True}
    )

    assert select_best_utility((("blocked", blocked), ("available", available))) == "available"
    assert select_best_utility((("blocked", blocked),)) is None
