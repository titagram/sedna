"""Host-derived claim eligibility must reach the observation model."""

from sedna.engagement import JournalEventDraft, SessionCheckpointedPayload
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
