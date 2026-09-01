from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from sedna.planning.belief import (
    BeliefEvidenceUpdate,
    project_hypothesis_beliefs,
    update_belief,
    validate_outcome_score_transition,
)
from sedna.planning.models import OutcomeCategory, SituationHypothesis


def _update(index: int, outcome: OutcomeCategory) -> BeliefEvidenceUpdate:
    return BeliefEvidenceUpdate(
        event_id=UUID(f"00000000-0000-4000-8000-{index:012d}"),
        outcome=outcome,
    )


def test_belief_update_rewards_progress_and_penalizes_negative_evidence() -> None:
    progressed = update_belief(0.5, (_update(1, OutcomeCategory.PROGRESS),))
    contradicted = update_belief(0.5, (_update(2, OutcomeCategory.NEGATIVE_EVIDENCE),))

    assert progressed.posterior == pytest.approx(0.75)
    assert contradicted.posterior == pytest.approx(0.2)


def test_belief_update_does_not_treat_execution_failure_as_counter_evidence() -> None:
    result = update_belief(
        0.61,
        (
            _update(1, OutcomeCategory.EXECUTION_ERROR),
            _update(2, OutcomeCategory.AMBIGUOUS),
        ),
    )

    assert result.posterior == pytest.approx(0.61)


def test_belief_update_deduplicates_evidence_and_applies_bounded_no_effect_penalty() -> None:
    no_effect = _update(1, OutcomeCategory.NO_EFFECT)
    result = update_belief(0.5, (no_effect, no_effect))

    assert result.posterior == pytest.approx(4 / 9)
    assert result.applied_updates == (no_effect,)


def test_journal_projection_links_hypothesis_proposal_decision_and_outcome() -> None:
    hypothesis_event_id = UUID("00000000-0000-4000-8000-000000000101")
    proposal_id = UUID("00000000-0000-4000-8000-000000000102")
    decision_id = UUID("00000000-0000-4000-8000-000000000103")
    outcome_event_id = UUID("00000000-0000-4000-8000-000000000104")
    hypothesis = SituationHypothesis(
        event_ids=(hypothesis_event_id,),
        text="The stored value reaches a privileged renderer.",
        confidence=0.5,
    )
    events = (
        SimpleNamespace(
            event_id=UUID("00000000-0000-4000-8000-000000000110"),
            payload=SimpleNamespace(
                kind="frontier_proposed",
                proposal=SimpleNamespace(
                    proposal_id=proposal_id,
                    event_refs=(hypothesis_event_id,),
                ),
            ),
        ),
        SimpleNamespace(
            event_id=UUID("00000000-0000-4000-8000-000000000111"),
            payload=SimpleNamespace(
                kind="decision_recorded",
                decision_id=str(decision_id),
                proposal_id=proposal_id,
            ),
        ),
        SimpleNamespace(
            event_id=outcome_event_id,
            payload=SimpleNamespace(
                kind="outcome_assessed",
                decision_id=decision_id,
                category=OutcomeCategory.NEGATIVE_EVIDENCE,
            ),
        ),
    )

    (belief,) = project_hypothesis_beliefs((hypothesis,), events)

    assert belief.prior == 0.5
    assert belief.posterior == pytest.approx(0.2)
    assert belief.update_event_ids == (outcome_event_id,)


def test_journal_projection_groups_outcomes_from_the_same_attachment() -> None:
    hypothesis_id = UUID("00000000-0000-4000-8000-000000000201")
    proposal_id = UUID("00000000-0000-4000-8000-000000000202")
    decision_id = UUID("00000000-0000-4000-8000-000000000203")
    attachment_id = UUID("00000000-0000-4000-8000-000000000204")
    events = (
        SimpleNamespace(
            event_id=UUID("00000000-0000-4000-8000-000000000210"),
            payload=SimpleNamespace(
                kind="frontier_proposed",
                proposal=SimpleNamespace(proposal_id=proposal_id, event_refs=(hypothesis_id,)),
            ),
        ),
        SimpleNamespace(
            event_id=UUID("00000000-0000-4000-8000-000000000211"),
            payload=SimpleNamespace(
                kind="decision_recorded", decision_id=str(decision_id), proposal_id=proposal_id
            ),
        ),
        *tuple(
            SimpleNamespace(
                event_id=UUID(f"00000000-0000-4000-8000-{index:012d}"),
                payload=SimpleNamespace(
                    kind="outcome_assessed",
                    decision_id=decision_id,
                    category=OutcomeCategory.NEGATIVE_EVIDENCE,
                    attachment_event_id=attachment_id,
                ),
            )
            for index in (212, 213)
        ),
    )
    hypothesis = SituationHypothesis(
        event_ids=(hypothesis_id,), text="Stored input reaches a privileged sink.", confidence=0.5
    )

    (belief,) = project_hypothesis_beliefs((hypothesis,), events)

    assert belief.posterior == pytest.approx(0.2)
    assert len(belief.update_event_ids) == 1


def test_transitively_overlapping_evidence_is_counted_once() -> None:
    updates = (
        BeliefEvidenceUpdate(
            event_id=UUID("00000000-0000-4000-8000-000000000301"),
            outcome=OutcomeCategory.NEGATIVE_EVIDENCE,
            correlation_refs=("attachment:a",),
        ),
        BeliefEvidenceUpdate(
            event_id=UUID("00000000-0000-4000-8000-000000000302"),
            outcome=OutcomeCategory.NEGATIVE_EVIDENCE,
            correlation_refs=("attachment:a", "evidence:b"),
        ),
        BeliefEvidenceUpdate(
            event_id=UUID("00000000-0000-4000-8000-000000000303"),
            outcome=OutcomeCategory.NEGATIVE_EVIDENCE,
            correlation_refs=("evidence:b",),
        ),
    )

    result = update_belief(0.5, updates)

    assert result.posterior == pytest.approx(0.2)
    assert len(result.applied_updates) == 1
    assert result.applied_updates[0].independence_group is not None


def test_correlated_component_outcome_is_order_independent() -> None:
    first = BeliefEvidenceUpdate(
        event_id=UUID("00000000-0000-4000-8000-000000000401"),
        outcome=OutcomeCategory.PROGRESS,
        correlation_refs=("evidence:shared",),
    )
    second = BeliefEvidenceUpdate(
        event_id=UUID("00000000-0000-4000-8000-000000000402"),
        outcome=OutcomeCategory.NEGATIVE_EVIDENCE,
        correlation_refs=("evidence:shared",),
    )

    forward = update_belief(prior=0.5, updates=(first, second))
    reverse = update_belief(prior=0.5, updates=(second, first))

    assert forward == reverse
    assert forward.posterior == 0.5
    assert forward.applied_updates[0].outcome is OutcomeCategory.AMBIGUOUS
    assert forward.applied_updates[0].event_id == first.event_id


def test_outcome_score_policy_requires_adverse_results_to_lower_strategy_weight() -> None:
    validate_outcome_score_transition(
        previous_score=70,
        new_score=60,
        outcomes=(OutcomeCategory.NO_EFFECT,),
    )
    with pytest.raises(ValueError, match="adverse_outcome_requires_lower_score"):
        validate_outcome_score_transition(
            previous_score=70,
            new_score=70,
            outcomes=(OutcomeCategory.NEGATIVE_EVIDENCE,),
        )


def test_outcome_score_policy_does_not_penalize_execution_or_observation_failures() -> None:
    validate_outcome_score_transition(
        previous_score=70,
        new_score=70,
        outcomes=(OutcomeCategory.EXECUTION_ERROR, OutcomeCategory.AMBIGUOUS),
    )
    with pytest.raises(ValueError, match="non_evidence_outcome_cannot_lower_score"):
        validate_outcome_score_transition(
            previous_score=70,
            new_score=60,
            outcomes=(OutcomeCategory.EXECUTION_ERROR,),
        )


def test_incompatibility_nearly_eliminates_but_does_not_destroy_revisable_belief() -> None:
    result = update_belief(0.8, (_update(1, OutcomeCategory.INCOMPATIBLE),))

    assert 0.0 < result.posterior < 0.05
