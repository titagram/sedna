"""Host-derived claim eligibility must reach the observation model."""

from sedna.engagement import EventType, JournalEventDraft, SessionCheckpointedPayload
from sedna.planning.models import InterpretationSubject
from sedna.planning.service import PlanningService
from tests.planning.test_service import (
    FIXED_TIME,
    EmptyObservationLlm,
    attach_text_evidence,
    journal_service,
    lane,
    manifest,
)


def test_observation_subject_may_echo_terminal_event_without_terminal_claims(tmp_path):
    """A model that echoes terminal_tool_event_id must not fail settlement.

    Regression: the instructions always name terminal_tool_event_id, so models
    faithfully echo it even when they raise no terminal claim. The service
    expected None in that case and rejected a schema-valid interpretation with
    observation_subject_mismatch, permanently stalling settlement on a journal
    whose attachment is followed by a completed tool call.
    """
    from sedna.engagement import (
        EvidenceAttachedPayload,
        ToolCallCompletedPayload,
        ToolCallStartedPayload,
        ToolCorrelation,
    )

    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            b"OpenSSH 9.6p1 Ubuntu",
            media_type="text/plain",
            representation="utf-8",
        )
        first = journal.append_hook_events(
            current_manifest.engagement_id,
            (
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type=EventType.TOOL_CALL_STARTED,
                    payload=ToolCallStartedPayload(
                        call_id="research-result",
                        correlation=ToolCorrelation.uncertain("missing_stable_identity"),
                        tool_name="nmap",
                        safe_arguments={"target": "10.0.0.1"},
                    ),
                ),
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type=EventType.EVIDENCE_ATTACHED,
                    payload=EvidenceAttachedPayload(evidence=evidence),
                ),
            ),
            expected_revision=created.snapshot.revision,
        )
        attachment_event_id = next(
            event.event_id
            for event in first.snapshot.events
            if event.type is EventType.EVIDENCE_ATTACHED
        )
        appended = journal.append_hook_events(
            current_manifest.engagement_id,
            (
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type=EventType.TOOL_CALL_COMPLETED,
                    payload=ToolCallCompletedPayload(
                        call_id="research-result",
                        correlation=ToolCorrelation.uncertain("missing_stable_identity"),
                        technical_status="returned",
                        duration_ms=1,
                        evidence_id=evidence.evidence_id,
                        evidence_attachment_event_id=attachment_event_id,
                    ),
                ),
            ),
            expected_revision=first.snapshot.revision,
        )
        events = appended.snapshot.events
        terminal_event_id = next(
            event.event_id for event in events if event.type is EventType.TOOL_CALL_COMPLETED
        )
        # Model echoes the real terminal event but declares no terminal claim.
        llm = EmptyObservationLlm(
            InterpretationSubject(
                attachment_event_id=attachment_event_id,
                evidence_id=evidence.evidence_id,
                terminal_tool_event_id=terminal_event_id,
            )
        )
        result = PlanningService(
            journal=journal, llm=llm, clock=lambda: FIXED_TIME
        ).settle_pending_evidence(current_manifest.engagement_id, reason="plan")

    assert result.status == "settled", result


def test_observation_subject_still_rejects_a_foreign_terminal_event(tmp_path):
    """Echoing an unrelated terminal event must still fail closed."""
    from uuid import UUID

    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        evidence, attached = attach_text_evidence(journal, current_manifest, current_lane)
        llm = EmptyObservationLlm(
            InterpretationSubject(
                attachment_event_id=attached.snapshot.events[-1].event_id,
                evidence_id=evidence.evidence_id,
                terminal_tool_event_id=UUID("00000000-0000-0000-0000-0000000000ff"),
            )
        )
        result = PlanningService(
            journal=journal, llm=llm, clock=lambda: FIXED_TIME
        ).settle_pending_evidence(current_manifest.engagement_id, reason="plan")

    assert result.status == "failed", result
    assert result.failure_code == "invalid_extractor_output"


def test_unpaired_attachment_explicitly_forbids_terminal_claims(tmp_path):
    current_manifest = manifest()
    with journal_service(tmp_path) as journal:
        evidence, attached = attach_text_evidence(journal, current_manifest, lane())
        llm = EmptyObservationLlm(
            InterpretationSubject(
                attachment_event_id=attached.snapshot.events[-1].event_id,
                evidence_id=evidence.evidence_id,
                terminal_tool_event_id=None,
            )
        )
        result = PlanningService(
            journal=journal, llm=llm, clock=lambda: FIXED_TIME
        ).settle_pending_evidence(current_manifest.engagement_id, reason="plan")
        assert result.status == "settled"
        request = llm.calls[0][1]["payload"]
        assert getattr(request, "terminal_claims_allowed", None) is False
        assert "outcomes and objective_proofs must both be empty" in llm.calls[0][1]["instructions"]


def test_settlement_does_not_put_entire_long_journal_in_conversion(tmp_path):
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        evidence, attached = attach_text_evidence(journal, current_manifest, current_lane)
        subject = InterpretationSubject(
            attachment_event_id=attached.snapshot.events[-1].event_id,
            evidence_id=evidence.evidence_id,
            terminal_tool_event_id=None,
        )
        for _ in range(8):
            journal.append_hook_events(
                current_manifest.engagement_id,
                tuple(
                    JournalEventDraft(
                        lane=current_lane,
                        actor="host_agent",
                        type="session_checkpointed",
                        payload=SessionCheckpointedPayload(
                            completed=False, interrupted=False, reason="unrelated history"
                        ),
                    )
                    for _ in range(64)
                ),
            )
        assert len(journal.load_snapshot(current_manifest.engagement_id).events) > 512
        result = PlanningService(
            journal=journal, llm=EmptyObservationLlm(subject), clock=lambda: FIXED_TIME
        ).settle_pending_evidence(current_manifest.engagement_id, reason="plan")
        assert result.status == "settled", result.failure_code
