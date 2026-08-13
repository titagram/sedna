from __future__ import annotations

from types import SimpleNamespace
from typing import Literal
from uuid import UUID

import pytest

from sedna.engagement import JournalRevision
from sedna.engagement.events import (
    FrontierCriticizedEventPayload,
    FrontierProposalEventRecord,
    FrontierProposedEventPayload,
    PlanningCallMetadataEventRecord,
    SystemCorrelation,
)
from sedna.planning.frontier import FrontierReducer, FrontierReplayError


def _metadata(*, purpose: Literal["plan", "critic"] = "plan") -> PlanningCallMetadataEventRecord:
    return PlanningCallMetadataEventRecord(
        purpose=purpose,
        provider="test",
        model="test",
        agent_id="test",
        prompt_id="test",
        prompt_version="1",
        response_schema_version="1",
        input_digest="a" * 64,
        input_tokens=1,
        output_tokens=1,
        elapsed_ms=0,
    )


@pytest.mark.parametrize("proposal_count", (1, 2))
def test_frontier_reducer_rejects_incomplete_or_unreconciled_proposal_batch(
    proposal_count: int,
) -> None:
    request_id = UUID("00000000-0000-4000-8000-000000000010")
    frontier_id = UUID("00000000-0000-4000-8000-000000000011")
    proposed = FrontierProposedEventPayload(
        request_id=request_id,
        frontier_id=frontier_id,
        proposal_ordinal=1,
        proposal_count=proposal_count,
        proposal=FrontierProposalEventRecord(
            proposal_id=UUID("00000000-0000-4000-8000-000000000001"),
            rank=1,
            family_id=UUID("00000000-0000-4000-8000-000000000002"),
            variant_id=UUID("00000000-0000-4000-8000-000000000003"),
            title="Proposal 1",
            strategic_intent="Collect discriminating evidence.",
            rationale="The current evidence supports this path.",
            score=99,
            confidence=0.8,
            expected_information_gain="Reduce uncertainty.",
            event_refs=(UUID("00000000-0000-4000-8000-000000000099"),),
        ),
        situation_digest="b" * 64,
        input_ledger_digest="c" * 64,
        knowledge_context_digest="d" * 64,
        draft_digest="e" * 64,
        call_metadata=_metadata(),
        planner_call_digest="f" * 64,
    )
    criticized = FrontierCriticizedEventPayload(
        request_id=request_id,
        frontier_id=frontier_id,
        critic_pass=1,
        accepted=True,
        call_metadata=_metadata(purpose="critic"),
        call_input_digest="a" * 64,
        call_output_digest="b" * 64,
    )
    snapshot = SimpleNamespace(
        engagement_id=UUID("00000000-0000-4000-8000-000000000012"),
        revision=JournalRevision(sequence=2, event_hash="1" * 64),
        events=(
            SimpleNamespace(sequence=1, payload=proposed),
            SimpleNamespace(sequence=2, payload=criticized),
        ),
    )

    with pytest.raises(
        FrontierReplayError, match="atomic operation identity|plan request|ordinal|reconciliation"
    ):
        FrontierReducer.rebuild(snapshot)


def test_frontier_reducer_rejects_non_atomic_attempt_events() -> None:
    request_id = UUID("00000000-0000-4000-8000-000000000010")
    frontier_id = UUID("00000000-0000-4000-8000-000000000011")
    proposed = FrontierProposedEventPayload(
        request_id=request_id,
        frontier_id=frontier_id,
        proposal_ordinal=1,
        proposal_count=1,
        proposal=FrontierProposalEventRecord(
            proposal_id=UUID("00000000-0000-4000-8000-000000000001"),
            rank=1,
            family_id=UUID("00000000-0000-4000-8000-000000000002"),
            variant_id=UUID("00000000-0000-4000-8000-000000000003"),
            title="Proposal 1",
            strategic_intent="Collect discriminating evidence.",
            rationale="The current evidence supports this path.",
            score=99,
            confidence=0.8,
            expected_information_gain="Reduce uncertainty.",
            event_refs=(UUID("00000000-0000-4000-8000-000000000099"),),
        ),
        situation_digest="b" * 64,
        input_ledger_digest="c" * 64,
        knowledge_context_digest="d" * 64,
        draft_digest="e" * 64,
        call_metadata=_metadata(),
        planner_call_digest="f" * 64,
    )
    criticized = FrontierCriticizedEventPayload(
        request_id=request_id,
        frontier_id=frontier_id,
        critic_pass=1,
        accepted=True,
        call_metadata=_metadata(purpose="critic"),
        call_input_digest="a" * 64,
        call_output_digest="b" * 64,
    )
    snapshot = SimpleNamespace(
        engagement_id=UUID("00000000-0000-4000-8000-000000000012"),
        revision=JournalRevision(sequence=2, event_hash="1" * 64),
        events=(
            SimpleNamespace(
                sequence=1,
                payload=proposed,
                system_correlation=SystemCorrelation(
                    source="planning",
                    operation_id=UUID("00000000-0000-4000-8000-000000000020"),
                ),
            ),
            SimpleNamespace(
                sequence=2,
                payload=criticized,
                system_correlation=SystemCorrelation(
                    source="planning",
                    operation_id=UUID("00000000-0000-4000-8000-000000000021"),
                ),
            ),
        ),
    )

    with pytest.raises(FrontierReplayError, match="atomic"):
        FrontierReducer.rebuild(snapshot)


def test_frontier_reducer_rejects_mixed_proposal_knowledge_digest() -> None:
    request_id = UUID("00000000-0000-4000-8000-000000000010")
    frontier_id = UUID("00000000-0000-4000-8000-000000000011")

    def proposed(ordinal: int, knowledge_digest: str) -> FrontierProposedEventPayload:
        return FrontierProposedEventPayload(
            request_id=request_id,
            frontier_id=frontier_id,
            proposal_ordinal=ordinal,
            proposal_count=2,
            proposal=FrontierProposalEventRecord(
                proposal_id=UUID(f"00000000-0000-4000-8000-{ordinal:012d}"),
                rank=ordinal,
                family_id=UUID(f"00000000-0000-4000-8001-{ordinal:012d}"),
                variant_id=UUID(f"00000000-0000-4000-8002-{ordinal:012d}"),
                title=f"Proposal {ordinal}",
                strategic_intent="Collect discriminating evidence.",
                rationale="The current evidence supports this path.",
                score=100 - ordinal,
                confidence=0.8,
                expected_information_gain="Reduce uncertainty.",
                event_refs=(UUID("00000000-0000-4000-8000-000000000099"),),
            ),
            situation_digest="b" * 64,
            input_ledger_digest="c" * 64,
            knowledge_context_digest=knowledge_digest,
            draft_digest="e" * 64,
            call_metadata=_metadata(),
            planner_call_digest="f" * 64,
        )

    with pytest.raises(FrontierReplayError, match="mixed proposal batch digests"):
        FrontierReducer._complete_batch([proposed(1, "d" * 64), proposed(2, "0" * 64)])
