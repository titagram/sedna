"""Direct guard-level tests: unknown references must fail closed per site."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from sedna.planning.service import PlanningService
from tests.planning.test_frontier_conversion_bounds import _grow_journal
from tests.planning.test_service import (
    FIXED_TIME,
    GroundedCommandPlannerLlm,
    journal_service,
    lane,
    manifest,
)


def test_reconciliation_guard_rejects_unknown_reference_before_indexing(tmp_path):
    """_reconcile_frontier must raise on model-cited IDs absent from the journal."""
    from sedna.planning.ledger import StrategyLedger
    from sedna.planning.llm import PlannerDraft
    from sedna.planning.models import FrontierProposal, FrontierProposalDraft
    from sedna.planning.prompts import PLANNER_PROMPT_ID, PLANNER_PROMPT_VERSION
    from sedna.planning.service import PlanningCallMetadata

    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        snapshot = journal.load_snapshot(current_manifest.engagement_id)
        frontier_id = uuid5(NAMESPACE_URL, "test-ghost-frontier")
        draft = PlannerDraft(
            proposals=(
                FrontierProposalDraft(
                    family_runtime_key="family-ghost",
                    variant_runtime_key="variant-ghost",
                    title="Ghost",
                    score=90,
                    confidence=80,
                    rationale="Fabricated reference.",
                    event_refs=(uuid4(),),
                ),
            )
        )
        proposal = FrontierProposal(
            proposal_id=uuid5(frontier_id, "proposal:1"),
            family_id=uuid5(frontier_id, "family:family-ghost"),
            variant_id=uuid5(frontier_id, "variant:variant-ghost"),
            title="Ghost",
            score=90,
            confidence=80,
            rationale="Fabricated reference.",
        )
        metadata = PlanningCallMetadata(
            purpose="plan",
            provider="test",
            model="test",
            agent_id="test",
            prompt_id=PLANNER_PROMPT_ID,
            prompt_version=PLANNER_PROMPT_VERSION,
            response_schema_version="1",
            input_digest="0" * 64,
            input_tokens=1,
            output_tokens=1,
            elapsed_ms=0,
        )
        with pytest.raises(ValueError, match="reconciliation_reference_not_in_journal"):
            PlanningService._reconcile_frontier(
                StrategyLedger(),
                draft,
                (proposal,),
                request_id=uuid5(frontier_id, "request"),
                frontier_id=frontier_id,
                revision=snapshot.revision,
                event_ref=snapshot.events[0].event_id,
                call_metadata=metadata,
                event_offset=2,
                prior_frontier=None,
                authoritative_event_ids={event.event_id for event in snapshot.events},
            )


def test_referenced_event_ids_walker_collects_reference_fields_only():
    """The walker must collect event-reference fields and ignore other UUIDs."""
    from sedna.planning.journal_events import _referenced_event_ids

    ref_a = uuid4()
    ref_b = uuid4()
    plain = uuid4()
    payload = {
        "attachment_event_id": ref_a,
        "terminal_tool_event_id": ref_b,
        "unrelated_uuid_field": plain,
        "nested": {"critic_event_ids": [ref_a], "other": plain},
    }

    collected = _referenced_event_ids(payload)

    assert collected == {ref_a, ref_b}
    assert plain not in collected


def test_conversion_indexes_respect_the_512_bound(tmp_path):
    """A long-journal plan must produce indexes within the model bound."""
    import sedna.planning.service as service_module

    current_manifest = manifest()
    current_lane = lane()
    captured = []
    original = service_module.payloads_from_planning_attempt

    def recording_converter(conversion):
        captured.append(conversion)
        return original(conversion)

    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        _grow_journal(journal, current_manifest.engagement_id, current_lane, 600)
        service = PlanningService(
            journal=journal,
            llm=GroundedCommandPlannerLlm(),
            clock=lambda: FIXED_TIME,
            research_aliases=("HTB-Orion", "Orion"),
        )
        service_module.payloads_from_planning_attempt = recording_converter
        try:
            result = service.plan_next(current_lane, max_proposals=3)
        finally:
            service_module.payloads_from_planning_attempt = original

    assert result.status == "success", result
    assert captured, "planning attempt conversion was not exercised"
    for conversion in captured:
        assert len(conversion.valid_event_ids) <= 512
