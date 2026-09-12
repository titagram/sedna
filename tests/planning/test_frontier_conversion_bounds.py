"""Planning conversions must stay bounded on long journals."""

import pytest

from sedna.engagement import JournalEventDraft, SessionCheckpointedPayload
from sedna.planning.llm import PlannerDraft
from sedna.planning.models import FrontierProposalDraft
from sedna.planning.service import PlanningService
from tests.planning.test_service import (
    FIXED_TIME,
    GroundedCommandPlannerLlm,
    journal_service,
    lane,
    manifest,
)


def _grow_journal(journal, engagement_id, current_lane, target_events):
    snapshot = journal.load_snapshot(engagement_id)
    remaining = target_events - len(snapshot.events)
    assert remaining > 0
    batch = 64
    for _ in range(0, remaining, batch):
        journal.append_hook_events(
            engagement_id,
            tuple(
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type="session_checkpointed",
                    payload=SessionCheckpointedPayload(
                        completed=False, interrupted=False, reason="unrelated history"
                    ),
                )
                for _ in range(batch)
            ),
        )


def test_plan_next_succeeds_on_journal_longer_than_conversion_index_bound(tmp_path):
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        _grow_journal(journal, current_manifest.engagement_id, current_lane, 600)
        assert len(journal.load_snapshot(current_manifest.engagement_id).events) > 512
        result = PlanningService(
            journal=journal,
            llm=GroundedCommandPlannerLlm(),
            clock=lambda: FIXED_TIME,
            research_aliases=("HTB-Orion", "Orion"),
        ).plan_next(current_lane, max_proposals=3)

    assert result.status == "success", result
    assert result.frontier is not None
    assert len(result.frontier.proposals) == 3


def test_plan_next_rejects_unknown_frontier_reference_before_conversion(tmp_path):
    """A model-cited unknown event ID must fail closed, never index itself."""
    from types import SimpleNamespace
    from uuid import uuid4

    from tests.planning.test_service import AcceptedPlannerLlm

    class UnreferencingPlannerLlm(AcceptedPlannerLlm):
        def complete(self, model_type, **kwargs):
            if model_type is PlannerDraft:
                request = kwargs["payload"]
                parsed = PlannerDraft(
                    proposals=(
                        FrontierProposalDraft(
                            family_runtime_key="family-ghost",
                            variant_runtime_key="variant-ghost",
                            title="Cites a nonexistent event",
                            score=90,
                            confidence=80,
                            rationale="Ground in a fabricated event reference.",
                            event_refs=(uuid4(),),
                        ),
                        FrontierProposalDraft(
                            family_runtime_key="family-real",
                            variant_runtime_key="variant-real",
                            title="Grounded proposal",
                            score=80,
                            confidence=70,
                            rationale="Preserve an independent alternative.",
                            event_refs=(request.recent_event_ids[0],),
                        ),
                        FrontierProposalDraft(
                            family_runtime_key="family-plain",
                            variant_runtime_key="variant-plain",
                            title="Plain proposal",
                            score=70,
                            confidence=60,
                            rationale="Preserve a second alternative.",
                        ),
                    )
                )
                return SimpleNamespace(
                    parsed=parsed,
                    provider="test-provider",
                    model="test-model",
                    agent_id="test-agent",
                    usage=SimpleNamespace(input_tokens=7, output_tokens=3),
                )
            return super().complete(model_type, **kwargs)

    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        service = PlanningService(
            journal=journal,
            llm=UnreferencingPlannerLlm(),
            clock=lambda: FIXED_TIME,
        )
        # Fail-closed: the invented reference must never reach conversion.
        with pytest.raises(ValueError, match="planner_invented_event_reference"):
            service.plan_next(current_lane, max_proposals=3)
        # And nothing may have been journaled by the rejected attempt.
        snapshot = journal.load_snapshot(current_manifest.engagement_id)
    assert not any(event.type.startswith("frontier_") for event in snapshot.events)
