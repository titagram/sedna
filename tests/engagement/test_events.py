"""Closed planning journal-event payload contracts."""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError


def test_outcome_assessed_preserves_attachment_attempt_context() -> None:
    from sedna.engagement import EventType, OutcomeAssessedEventPayload

    payload = OutcomeAssessedEventPayload(
        attachment_event_id=UUID("00000000-0000-0000-0000-000000000002"),
        terminal_tool_event_id=UUID("00000000-0000-0000-0000-000000000003"),
        decision_id=UUID("00000000-0000-0000-0000-000000000004"),
        tool_call_ids=("call-1",),
        category="negative_evidence",
        summary="credentials were rejected",
        strategic_impact="reduce only the tested credential variant",
        evidence_ids=("evidence-sha256-" + "a" * 64,),
        source_event_ids=(UUID("00000000-0000-0000-0000-000000000003"),),
        interpretation_input_digest="b" * 64,
    )

    assert payload.kind == EventType.OUTCOME_ASSESSED.value
    assert payload.attachment_event_id != payload.terminal_tool_event_id
    assert payload.category == "negative_evidence"


def test_missing_information_round_trips_as_settlement_bookkeeping() -> None:
    from sedna.engagement import (
        EventType,
        JournalEventDraft,
        MissingInformationIdentifiedEventPayload,
        SystemCorrelation,
    )

    payload = MissingInformationIdentifiedEventPayload(
        question="which operating system is running?",
        reason="platform-specific strategy selection requires it",
        importance=80,
        related_event_ids=(UUID("00000000-0000-0000-0000-000000000002"),),
        scope_reference_ids=("scope-" + "1" * 32,),
        interpretation_input_digest="b" * 64,
    )
    draft = JournalEventDraft(
        actor="system",
        type=EventType.MISSING_INFORMATION_IDENTIFIED,
        payload=payload,
        system_correlation=SystemCorrelation(
            source="planning",
            operation_id=UUID("00000000-0000-0000-0000-000000000001"),
        ),
    )

    assert draft.payload == payload
    assert payload.importance == 80


def test_hypothesis_formed_round_trips_as_settlement_bookkeeping() -> None:
    from sedna.engagement import (
        EventType,
        HypothesisFormedEventPayload,
        JournalEventDraft,
        SystemCorrelation,
    )

    payload = HypothesisFormedEventPayload(
        statement="the service may be nginx",
        confidence=0.6,
        supporting_event_ids=(UUID("00000000-0000-0000-0000-000000000002"),),
        contradicting_event_ids=(UUID("00000000-0000-0000-0000-000000000003"),),
        scope_reference_ids=("scope-" + "1" * 32,),
        interpretation_input_digest="b" * 64,
    )
    draft = JournalEventDraft(
        actor="system",
        type=EventType.HYPOTHESIS_FORMED,
        payload=payload,
        system_correlation=SystemCorrelation(
            source="planning",
            operation_id=UUID("00000000-0000-0000-0000-000000000001"),
        ),
    )

    assert draft.payload == payload
    assert payload.supporting_event_ids != payload.contradicting_event_ids


def test_observation_extracted_rejects_nested_scope_outside_inventory() -> None:
    from sedna.engagement import ObservationExtractedEventPayload

    with pytest.raises(ValidationError, match="scope_reference_ids"):
        ObservationExtractedEventPayload(
            summary="shell access gained",
            observation={
                "record_kind": "access_state_delta",
                "scope_reference_id": "scope-" + "1" * 32,
                "access_kind": "shell",
                "transition": "gained",
            },
            confidence=0.8,
            evidence_slices=(
                {
                    "evidence_id": "evidence-sha256-" + "a" * 64,
                    "start": 0,
                    "end": 5,
                    "sha256": "a" * 64,
                    "media_type": "text/plain",
                },
            ),
            interpretation_input_digest="b" * 64,
        )


def test_observation_extracted_has_exclusive_planning_append_owner() -> None:
    from sedna.engagement import EventType
    from sedna.engagement.service import EVENT_APPEND_OWNER_BY_TYPE

    assert set(EVENT_APPEND_OWNER_BY_TYPE) == {event_type.value for event_type in EventType}
    assert EVENT_APPEND_OWNER_BY_TYPE["observation_extracted"] == "planning_capability"


def test_observation_extracted_is_settlement_bookkeeping_in_every_status() -> None:
    from sedna.engagement import EngagementStatus, EventType
    from sedna.engagement.reducer import (
        EVENT_LIFECYCLE_EFFECTS,
        STATUS_LIFECYCLE_MATRIX,
        LifecycleEffect,
    )

    effect = EVENT_LIFECYCLE_EFFECTS[EventType.OBSERVATION_EXTRACTED]

    assert effect is LifecycleEffect.SETTLEMENT_BOOKKEEPING
    assert all(effect in STATUS_LIFECYCLE_MATRIX[status] for status in EngagementStatus)


def test_observation_extracted_accepts_typed_incompatibility() -> None:
    from sedna.engagement import ObservationExtractedEventPayload

    payload = ObservationExtractedEventPayload(
        summary="windows technique is incompatible",
        observation={
            "record_kind": "incompatibility",
            "subject_ref": "strategy:windows-only",
            "reason": "target is Linux",
            "scope_reference_ids": ("scope-" + "1" * 32,),
            "event_refs": (UUID("00000000-0000-0000-0000-000000000002"),),
            "knowledge_refs": ("artifact:linux",),
        },
        confidence=0.95,
        evidence_slices=(
            {
                "evidence_id": "evidence-sha256-" + "a" * 64,
                "start": 0,
                "end": 5,
                "sha256": "a" * 64,
                "media_type": "text/plain",
            },
        ),
        scope_reference_ids=("scope-" + "1" * 32,),
        interpretation_input_digest="b" * 64,
    )

    assert payload.observation.record_kind == "incompatibility"
    assert payload.observation.knowledge_refs == ("artifact:linux",)


def test_secret_reference_rejects_value_digest_mismatch() -> None:
    from sedna.engagement import ObservationExtractedEventPayload

    with pytest.raises(ValidationError, match="private value digest"):
        ObservationExtractedEventPayload(
            summary="password candidate observed",
            observation={
                "record_kind": "secret_reference",
                "secret_ref_id": "secret:ssh:alice",
                "secret_kind": "password",
                "label": "alice ssh password",
                "value": {
                    "evidence_slice": {
                        "evidence_id": "evidence-sha256-" + "a" * 64,
                        "start": 0,
                        "end": 5,
                        "sha256": "c" * 64,
                        "media_type": "text/plain",
                    },
                    "value_sha256": "d" * 64,
                },
            },
            confidence=0.8,
            evidence_slices=(
                {
                    "evidence_id": "evidence-sha256-" + "a" * 64,
                    "start": 0,
                    "end": 5,
                    "sha256": "c" * 64,
                    "media_type": "text/plain",
                },
            ),
            interpretation_input_digest="b" * 64,
        )


def test_observation_extracted_accepts_grounded_secret_reference() -> None:
    from sedna.engagement import ObservationExtractedEventPayload

    payload = ObservationExtractedEventPayload(
        summary="password candidate observed",
        observation={
            "record_kind": "secret_reference",
            "secret_ref_id": "secret:ssh:alice",
            "secret_kind": "password",
            "label": "alice ssh password",
            "value": {
                "evidence_slice": {
                    "evidence_id": "evidence-sha256-" + "a" * 64,
                    "start": 0,
                    "end": 5,
                    "sha256": "c" * 64,
                    "media_type": "text/plain",
                },
                "value_sha256": "c" * 64,
            },
            "scope_reference_ids": ("scope-" + "1" * 32,),
            "origin": "engagement_evidence",
        },
        confidence=0.8,
        evidence_slices=(
            {
                "evidence_id": "evidence-sha256-" + "a" * 64,
                "start": 0,
                "end": 5,
                "sha256": "c" * 64,
                "media_type": "text/plain",
            },
        ),
        scope_reference_ids=("scope-" + "1" * 32,),
        interpretation_input_digest="b" * 64,
    )

    assert payload.observation.record_kind == "secret_reference"
    assert payload.observation.value.value_sha256 == "c" * 64


def test_observation_extracted_accepts_typed_access_delta() -> None:
    from sedna.engagement import ObservationExtractedEventPayload

    payload = ObservationExtractedEventPayload(
        summary="shell access gained",
        observation={
            "record_kind": "access_state_delta",
            "scope_reference_id": "scope-" + "1" * 32,
            "access_kind": "shell",
            "transition": "gained",
            "principal_label": "www-data",
        },
        confidence=0.8,
        evidence_slices=(
            {
                "evidence_id": "evidence-sha256-" + "a" * 64,
                "start": 0,
                "end": 5,
                "sha256": "a" * 64,
                "media_type": "text/plain",
            },
        ),
        scope_reference_ids=("scope-" + "1" * 32,),
        interpretation_input_digest="b" * 64,
    )

    assert payload.observation.record_kind == "access_state_delta"
    assert payload.observation.access_kind == "shell"


def test_observation_extracted_accepts_typed_facet_record() -> None:
    from sedna.engagement import ObservationExtractedEventPayload

    payload = ObservationExtractedEventPayload(
        summary="linux was observed",
        observation={
            "record_kind": "facet",
            "dimension": "os_family",
            "key": "operating_system",
            "value": "linux",
            "relation": "observed",
        },
        confidence=0.9,
        evidence_slices=(
            {
                "evidence_id": "evidence-sha256-" + "a" * 64,
                "start": 0,
                "end": 5,
                "sha256": "a" * 64,
                "media_type": "text/plain",
            },
        ),
        interpretation_input_digest="b" * 64,
    )

    assert payload.observation.record_kind == "facet"
    assert payload.observation.dimension == "os_family"


def test_observation_extracted_round_trips_as_a_planning_system_event() -> None:
    from sedna.engagement import (
        EventType,
        JournalEventDraft,
        ObservationExtractedEventPayload,
        SystemCorrelation,
    )

    payload = ObservationExtractedEventPayload(
        summary="nginx was observed",
        observation={
            "record_kind": "text_fact",
            "subject": "web_server",
            "value": "nginx",
        },
        confidence=1.0,
        evidence_slices=(
            {
                "evidence_id": "evidence-sha256-" + "a" * 64,
                "start": 0,
                "end": 5,
                "sha256": "a" * 64,
                "media_type": "text/plain",
            },
        ),
        interpretation_input_digest="b" * 64,
    )
    draft = JournalEventDraft(
        actor="system",
        type=EventType.OBSERVATION_EXTRACTED,
        payload=payload,
        system_correlation=SystemCorrelation(
            source="planning",
            operation_id=UUID("00000000-0000-0000-0000-000000000001"),
        ),
    )

    assert draft.type.value == "observation_extracted"
    assert draft.payload.model_dump(mode="json") == payload.model_dump(mode="json")
