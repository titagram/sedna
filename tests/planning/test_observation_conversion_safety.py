"""Security-critical behavior of the generic observation conversion path."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Literal
from uuid import UUID

from sedna.engagement import (
    EventType,
    EvidenceAttachedPayload,
    JournalEventDraft,
    ToolCallCompletedPayload,
    ToolCallStartedPayload,
    ToolCorrelation,
)
from sedna.planning.models import (
    InterpretationSubject,
    ObservationBatchDraft,
    ObservationDraft,
)
from sedna.planning.service import PlanningService
from tests.planning.test_service import (
    FIXED_TIME,
    EmptyObservationLlm,
    journal_service,
    lane,
    manifest,
)

LITERAL = "Sup3rSecretValue!"


def _call_metadata():
    from sedna.planning.models import PlanningCallMetadata

    return PlanningCallMetadata(
        purpose="observe",
        provider="test",
        model="test",
        agent_id="test",
        prompt_id="sedna-observer",
        prompt_version="2",
        response_schema_version="1",
        input_digest="0" * 64,
        input_tokens=1,
        output_tokens=1,
        elapsed_ms=0,
    )


def _single_attachment_journal(journal, current_manifest, current_lane):
    """Attachment followed by its terminal tool completion, as in production."""
    created = journal.create_from_manifest(current_manifest, lane=current_lane)
    evidence = journal.write_evidence(
        current_manifest.engagement_id,
        b"password=Sup3rSecretValue!",
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
    second = journal.append_hook_events(
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
    return evidence, attachment_event_id, second.snapshot


class _KindsLlm(EmptyObservationLlm):
    """Emit exactly one observation of a chosen kind, carrying a credential."""

    def __init__(
        self, kind: Literal["text", "facet", "access", "secret", "incompatibility"]
    ) -> None:
        super().__init__(
            InterpretationSubject(
                attachment_event_id=UUID(int=0),
                evidence_id="evidence-sha256-" + "0" * 64,
            )
        )
        self.kind = kind

    def complete(self, model_type, **kwargs):  # noqa: ANN003
        request = kwargs["payload"]
        slice_ = request.evidence_slices[0]
        return SimpleNamespace(
            parsed=ObservationBatchDraft(
                subject=InterpretationSubject(
                    attachment_event_id=slice_.event_id,
                    evidence_id=slice_.evidence_id,
                    terminal_tool_event_id=None,
                ),
                observations=(
                    ObservationDraft(
                        kind=self.kind,
                        text=f"password={LITERAL}",
                        event_ids=(slice_.event_id,),
                    ),
                ),
            ),
            provider="test-provider",
            model="test-model",
            agent_id="test-agent",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )


def _journal_raw(journal, engagement_id) -> str:
    return (
        journal._repository._knowledge_root / "engagements" / str(engagement_id) / "events.jsonl"
    ).read_text()


def test_secret_observation_never_journals_the_literal_credential(tmp_path):
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        _single_attachment_journal(journal, current_manifest, current_lane)
        service = PlanningService(
            journal=journal, llm=_KindsLlm("secret"), clock=lambda: FIXED_TIME
        )
        result = service.settle_pending_evidence(current_manifest.engagement_id, reason="plan")
        raw = _journal_raw(journal, current_manifest.engagement_id)

    # The literal must never reach the journal, in any field.
    assert LITERAL not in raw
    # And the settlement must actually have recorded the redacted reference.
    assert result.status == "settled", result
    assert "redacted evidence secret" in raw


def test_text_observation_still_records_its_grounded_wording(tmp_path):
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        _single_attachment_journal(journal, current_manifest, current_lane)
        service = PlanningService(journal=journal, llm=_KindsLlm("text"), clock=lambda: FIXED_TIME)
        service.settle_pending_evidence(current_manifest.engagement_id, reason="plan")
        raw = _journal_raw(journal, current_manifest.engagement_id)

    # A text observation is retained, and the secret-kind redaction is not
    # applied to unrelated kinds.
    assert "password=" in raw


def test_secret_representation_requires_the_referenced_evidence_slice():
    """The secret binding must fail when the draft cites different evidence."""
    from sedna.engagement.events import (
        EvidenceSliceEventRef,
        PrivateValueEventRecord,
        SecretReferenceEventRecord,
    )
    from sedna.planning.journal_events import _source_is_represented_by_authoritative_model
    from sedna.planning.models import (
        EvidenceSliceInput,
        ObservationEventConversion,
        ObservationExtractedSource,
    )

    evidence_id = "evidence-sha256-" + "a" * 64
    other_id = "evidence-sha256-" + "b" * 64
    conversion = ObservationEventConversion(
        batch=ObservationBatchDraft(
            observations=(
                ObservationDraft(
                    kind="secret", text="x", event_ids=(UUID(int=1),)
                ),
            )
        ),
        call_metadata=_call_metadata(),
        interpretation_audits=(),
        local_event_bindings=(),
        valid_event_ids=(),
        # The conversion carries a DIFFERENT evidence than the record references,
        # which is the vulnerable case: the source must not be representable.
        valid_evidence_ids=(other_id,),
        evidence_slices=(
            EvidenceSliceInput(
                evidence_id=other_id, start=0, end=1, media_type="text/plain", content=b"x"
            ),
        ),
        valid_proof_indexes=(),
    )
    slice_ref = EvidenceSliceEventRef(
        evidence_id=evidence_id, start=0, end=1, sha256="a" * 64, media_type="text/plain"
    )
    source = ObservationExtractedSource(
        local_id="observation-0",
        summary="redacted evidence secret",
        observation=SecretReferenceEventRecord(
            secret_ref_id="evidence-secret:0",
            secret_kind="other",
            label="redacted evidence secret",
            value=PrivateValueEventRecord(evidence_slice=slice_ref, value_sha256="a" * 64),
        ),
        confidence=1.0,
        evidence_slices=(slice_ref,),
    )

    assert not _source_is_represented_by_authoritative_model(source, conversion)


def test_facet_representation_requires_exact_text_match():
    """A same-kind facet with different text cannot represent the source."""
    from sedna.engagement.events import EvidenceSliceEventRef, FacetObservationEventRecord
    from sedna.planning.journal_events import _source_is_represented_by_authoritative_model
    from sedna.planning.models import (
        EvidenceSliceInput,
        ObservationEventConversion,
        ObservationExtractedSource,
    )

    facet_ref = EvidenceSliceEventRef(
        evidence_id="evidence-sha256-" + "c" * 64,
        start=0,
        end=1,
        sha256="c" * 64,
        media_type="text/plain",
    )
    conversion = ObservationEventConversion(
        batch=ObservationBatchDraft(
            observations=(
                ObservationDraft(
                    kind="facet",
                    text="different wording",
                    event_ids=(UUID(int=1),),
                ),
            )
        ),
        call_metadata=_call_metadata(),
        interpretation_audits=(),
        local_event_bindings=(),
        valid_event_ids=(),
        evidence_slices=(
            EvidenceSliceInput(
                evidence_id="evidence-sha256-" + "c" * 64,
                start=0,
                end=1,
                media_type="text/plain",
                content=b"x",
            ),
        ),
        valid_evidence_ids=(),
        valid_proof_indexes=(),
    )
    source = ObservationExtractedSource(
        local_id="observation-1",
        summary="expected wording",
        observation=FacetObservationEventRecord(
            dimension="custom", key="k", value="v", relation="observed"
        ),
        confidence=1.0,
        evidence_slices=(facet_ref,),
    )

    assert not _source_is_represented_by_authoritative_model(source, conversion)


def test_incompatibility_observation_fails_closed(tmp_path):
    """incompatibility needs authoritative scope/prior-event grounding."""
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        _single_attachment_journal(journal, current_manifest, current_lane)
        service = PlanningService(
            journal=journal, llm=_KindsLlm("incompatibility"), clock=lambda: FIXED_TIME
        )
        result = service.settle_pending_evidence(current_manifest.engagement_id, reason="plan")

    assert result.status == "failed", result
    assert result.failure_code == "invalid_extractor_output"
