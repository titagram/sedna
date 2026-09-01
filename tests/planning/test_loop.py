from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from sedna.planning.loop import has_newer_material_reference, validate_semantic_retry
from sedna.planning.models import OutcomeCategory
from sedna.planning.service import PlanningService


@pytest.mark.parametrize(
    "outcome",
    [
        OutcomeCategory.NO_EFFECT,
        OutcomeCategory.NEGATIVE_EVIDENCE,
        OutcomeCategory.INCOMPATIBLE,
    ],
)
def test_adverse_retry_without_new_material_evidence_is_a_loop(
    outcome: OutcomeCategory,
) -> None:
    with pytest.raises(ValueError, match="semantic_strategy_loop"):
        validate_semantic_retry(latest_outcome=outcome, has_new_material_evidence=False)


@pytest.mark.parametrize(
    "outcome",
    [OutcomeCategory.EXECUTION_ERROR, OutcomeCategory.AMBIGUOUS],
)
def test_non_evidence_outcome_allows_retry(outcome: OutcomeCategory) -> None:
    validate_semantic_retry(latest_outcome=outcome, has_new_material_evidence=False)


def test_new_material_evidence_allows_retry_after_adverse_outcome() -> None:
    validate_semantic_retry(
        latest_outcome=OutcomeCategory.NO_EFFECT,
        has_new_material_evidence=True,
    )


def test_material_reference_must_be_newer_than_outcome_assessment_event() -> None:
    older = UUID("00000000-0000-4000-8000-000000000001")
    outcome = UUID("00000000-0000-4000-8000-000000000002")
    newer = UUID("00000000-0000-4000-8000-000000000003")
    order = {older: 2, outcome: 3, newer: 4}
    event_types = {
        older: "observation_extracted",
        outcome: "outcome_assessed",
        newer: "observation_extracted",
    }

    assert not has_newer_material_reference((older,), outcome, order, event_types)
    assert has_newer_material_reference((newer,), outcome, order, event_types)


def test_newer_planning_event_is_not_material_evidence() -> None:
    outcome = UUID("00000000-0000-4000-8000-000000000011")
    planning = UUID("00000000-0000-4000-8000-000000000012")

    assert not has_newer_material_reference(
        (planning,),
        outcome,
        {outcome: 3, planning: 4},
        {outcome: "outcome_assessed", planning: "planning_requested"},
    )


def test_semantic_loop_guard_covers_variant_absent_from_prior_frontier() -> None:
    variant_id = UUID("00000000-0000-4000-8000-000000000021")
    outcome_id = UUID("00000000-0000-4000-8000-000000000022")
    draft = SimpleNamespace(proposals=(SimpleNamespace(event_refs=()),))
    proposal = SimpleNamespace(variant_id=variant_id)
    attempt = SimpleNamespace(
        outcome=OutcomeCategory.NO_EFFECT,
        outcome_event_id=outcome_id,
    )
    ledger = SimpleNamespace(
        variants=(SimpleNamespace(variant_id=variant_id, recent_attempts=(attempt,)),)
    )

    with pytest.raises(ValueError, match="semantic_strategy_loop"):
        PlanningService._validate_semantic_loops(
            draft,
            (proposal,),
            None,
            ledger,
            event_order={outcome_id: 1},
            event_types={outcome_id: "outcome_assessed"},
        )
