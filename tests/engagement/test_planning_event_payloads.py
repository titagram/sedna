"""Exhaustive tests for the remaining typed planning journal payloads."""

from __future__ import annotations

import json
from hashlib import sha256
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from sedna.engagement import EventPayload

U1 = UUID("00000000-0000-0000-0000-000000000001")
U2 = UUID("00000000-0000-0000-0000-000000000002")
U3 = UUID("00000000-0000-0000-0000-000000000003")
EVIDENCE = "evidence-sha256-" + "a" * 64
DIGEST = "b" * 64
SLICE = {
    "evidence_id": EVIDENCE,
    "start": 0,
    "end": 1,
    "sha256": "a" * 64,
    "media_type": "text/plain",
}
CALL = {
    "purpose": "observe",
    "provider": "provider",
    "model": "model",
    "agent_id": "agent",
    "prompt_id": "prompt:observe",
    "prompt_version": "1",
    "response_schema_version": "1",
    "input_digest": DIGEST,
    "input_tokens": 1,
    "output_tokens": 1,
    "elapsed_ms": 1,
}
ATTEMPTS = {
    "total_count": 0,
    "history_digest": sha256(b"[]").hexdigest(),
}
FAMILY = {
    "record_kind": "strategy_family",
    "family_id": str(U1),
    "stable_key": "family:web",
    "title": "web",
    "strategic_intent": "enumerate web",
    "rationale": "service evidence",
    "score": 80,
    "confidence": 0.8,
    "status": "available",
    "last_material_revision": {"sequence": 1, "event_hash": DIGEST},
}
PROPOSAL = {
    "proposal_id": str(U1),
    "rank": 1,
    "family_id": str(U1),
    "title": "enumerate web",
    "strategic_intent": "inspect service",
    "rationale": "service observed",
    "score": 80,
    "confidence": 0.8,
    "expected_information_gain": "identify technology",
    "event_refs": [str(U2)],
}


REMAINING_CASES = (
    (
        "objective_proof_observed",
        {
            "proof_requirement_id": "root-flag",
            "assessment_generation": 1,
            "assessment": "supported",
            "candidate_value": {"evidence_slice": SLICE, "value_sha256": "a" * 64},
            "confidence": 1.0,
            "evidence_ids": [EVIDENCE],
            "source_event_ids": [str(U2)],
            "interpretation_input_digest": DIGEST,
        },
    ),
    (
        "interpretation_succeeded",
        {
            "interpretation_id": str(U1),
            "attachment_event_id": str(U2),
            "evidence_id": EVIDENCE,
            "covered_slices": [SLICE],
            "emitted_event_ids": [str(U3)],
            "call_metadata": CALL,
            "call_input_digest": DIGEST,
            "call_output_digest": DIGEST,
        },
    ),
    (
        "interpretation_failed",
        {
            "interpretation_id": str(U1),
            "attachment_event_id": str(U2),
            "evidence_id": EVIDENCE,
            "attempted_slices": [SLICE],
            "failure_code": "llm_unavailable",
            "retryable": True,
            "safe_summary": "host unavailable",
            "call_metadata": CALL,
            "call_input_digest": DIGEST,
        },
    ),
    (
        "plan_requested",
        {
            "request_id": str(U1),
            "lane_key": "lane-" + "1" * 32,
            "situation_digest": DIGEST,
            "material_event_revision": {"sequence": 1, "event_hash": DIGEST},
            "input_ledger_digest": DIGEST,
            "canonical_revision": DIGEST,
            "source_registry_digest": DIGEST,
            "max_proposals": 3,
            "request_digest": "66cac7c832d506ebc56618e4b12c24c54cec7003d76fbad9ac6f24c8d6bf919e",
        },
    ),
    (
        "frontier_proposed",
        {
            "request_id": str(U1),
            "frontier_id": str(U2),
            "proposal_ordinal": 1,
            "proposal_count": 1,
            "proposal": PROPOSAL,
            "situation_digest": DIGEST,
            "input_ledger_digest": DIGEST,
            "knowledge_context_digest": DIGEST,
            "draft_digest": DIGEST,
            "call_metadata": CALL,
            "planner_call_digest": DIGEST,
        },
    ),
    (
        "frontier_criticized",
        {
            "request_id": str(U1),
            "frontier_id": str(U2),
            "critic_pass": 1,
            "accepted": True,
            "call_metadata": CALL,
            "call_input_digest": DIGEST,
            "call_output_digest": DIGEST,
        },
    ),
    (
        "frontier_repaired",
        {
            "request_id": str(U1),
            "frontier_id": str(U2),
            "critic_event_id": str(U3),
            "proposal_ordinal": 1,
            "proposal_count": 1,
            "proposal": PROPOSAL,
            "repaired_draft_digest": DIGEST,
            "call_metadata": CALL,
            "call_input_digest": DIGEST,
            "call_output_digest": DIGEST,
        },
    ),
    (
        "frontier_rejected",
        {
            "request_id": str(U1),
            "frontier_id": str(U2),
            "critic_event_ids": [str(U3)],
            "reason_codes": ["material_finding"],
            "rejected_draft_digest": DIGEST,
        },
    ),
    (
        "planning_gap_recorded",
        {
            "request_id": str(U1),
            "code": "critic_rejected",
            "summary": "no frontier",
            "retryable": True,
            "situation_digest": DIGEST,
            "ledger_digest": DIGEST,
        },
    ),
    (
        "strategy_reconciled",
        {
            "request_id": str(U1),
            "frontier_id": str(U2),
            "reconciliation_id": str(U3),
            "item_ordinal": 1,
            "item_count": 1,
            "input_ledger_digest": DIGEST,
            "resulting_ledger_digest": DIGEST,
            "operation": {
                "operation_id": str(U1),
                "operation": "retain",
                "family_id": str(U1),
                "reason": "still applicable",
                "evidence_event_ids": [str(U2)],
            },
            "resulting_snapshot": FAMILY,
            "reconciliation_digest": DIGEST,
        },
    ),
    (
        "strategy_archived",
        {
            "request_id": str(U1),
            "archive_batch_id": str(U2),
            "entry_ordinal": 1,
            "entry_count": 1,
            "archive_record": {
                "archive_entry_id": str(U3),
                "snapshot": FAMILY,
                "archive_reason": "exhausted",
                "archive_summary": "bounded",
                "archived_at_material_revision": {"sequence": 1, "event_hash": DIGEST},
                "source_reconciliation_event_id": str(U2),
                "archive_entry_digest": DIGEST,
            },
            "resulting_archive_digest": DIGEST,
        },
    ),
    (
        "strategy_reactivated",
        {
            "request_id": str(U1),
            "reactivation_batch_id": str(U2),
            "entry_ordinal": 1,
            "entry_count": 1,
            "source_archive_event_id": str(U3),
            "triggering_event_ids": [str(U2)],
            "matched_predicate_ids": ["predicate:1"],
            "prior_archive_entry_digest": DIGEST,
            "resulting_archive_digest": DIGEST,
            "restored_snapshot": FAMILY,
        },
    ),
    (
        "research_query_proposed",
        {
            "query_id": str(U1),
            "normalized_query": "linux nginx",
            "query_digest": sha256(b"linux nginx").hexdigest(),
            "policy_decision": "allowed",
            "policy_version": "policy:1",
            "reason_codes": ["in_scope"],
        },
    ),
    (
        "research_source_consulted",
        {
            "query_id": str(U1),
            "source_id": "source:1",
            "normalized_locator": "https://example.test",
            "locator_digest": sha256(b"https://example.test").hexdigest(),
            "content_digest": DIGEST,
            "media_type": "text/html",
            "evidence_ids": [EVIDENCE],
            "tool_event_ids": [str(U2)],
        },
    ),
    (
        "research_source_assessed",
        {
            "query_id": str(U1),
            "source_id": "source:1",
            "consulted_event_id": str(U2),
            "assessment": "useful",
            "confidence": 0.8,
            "summary": "relevant",
            "related_event_ids": [str(U2)],
            "assessment_digest": "2555dc9c5d0be330fc7aace0a6ca31466cc50617d6827cd4966ac3c29fe99fd5",
            "suggested_registry_status": "useful",
        },
    ),
)


@pytest.mark.parametrize(("kind", "body"), REMAINING_CASES)
def test_remaining_planning_payloads_round_trip_through_closed_union(
    kind: str, body: dict[str, object]
) -> None:
    payload = TypeAdapter(EventPayload).validate_python({"kind": kind, **body})
    round_tripped = TypeAdapter(EventPayload).validate_json(
        TypeAdapter(EventPayload).dump_json(payload)
    )
    assert round_tripped == payload
    assert payload.kind == kind


def test_frontier_critic_acceptance_requires_no_findings() -> None:
    kind, body = REMAINING_CASES[5]
    with pytest.raises(ValidationError, match="accepted"):
        TypeAdapter(EventPayload).validate_python(
            {"kind": kind, **body, "finding_codes": ["material_finding"]}
        )


def test_research_assessment_digest_covers_all_semantic_fields() -> None:
    kind, body = REMAINING_CASES[14]
    with pytest.raises(ValidationError, match="assessment_digest"):
        TypeAdapter(EventPayload).validate_python(
            {"kind": kind, **body, "assessment_digest": DIGEST}
        )


def test_plan_request_digest_covers_every_field_except_kind_and_itself() -> None:
    kind, body = REMAINING_CASES[3]
    canonical = dict(body)
    canonical.pop("request_digest")
    expected = sha256(
        json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    payload = TypeAdapter(EventPayload).validate_python(
        {"kind": kind, **body, "request_digest": expected}
    )
    assert payload.request_digest == expected
    with pytest.raises(ValidationError, match="request_digest"):
        TypeAdapter(EventPayload).validate_python({"kind": kind, **body, "request_digest": DIGEST})


def test_research_query_digest_is_derived_from_normalized_text() -> None:
    kind, body = REMAINING_CASES[12]
    with pytest.raises(ValidationError, match="query_digest"):
        TypeAdapter(EventPayload).validate_python({"kind": kind, **body, "query_digest": DIGEST})
