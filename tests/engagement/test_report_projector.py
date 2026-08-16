from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

import sedna.engagement.reporting.projector as projector_module
from sedna.engagement import (
    EngagementJournalService,
    EventType,
    EvidenceAttachedPayload,
    EvidenceCaptureFailedPayload,
    EvidenceSlice,
    HostAdaptedCommandRecord,
    JournalEventDraft,
    ProofRequirement,
    SessionFinalizedPayload,
    SessionStartedPayload,
    ToolCallCompletedPayload,
    ToolCallStartedPayload,
    ToolCallTerminatedPayload,
    ToolCorrelation,
)
from sedna.engagement.events import (
    CommandSuggestionEventRecord,
    DecisionRecordedPayload,
    EvidenceSliceEventRef,
    FrontierProposalEventRecord,
    FrontierProposedEventPayload,
    FrontierRepairedEventPayload,
    ObjectiveProofObservedEventPayload,
    PrivateValueEventRecord,
)
from sedna.engagement.reporting.models import ReportSecret
from sedna.engagement.reporting.projector import (
    REPORT_IGNORED_EVENT_TYPES,
    REPORT_PROJECTED_EVENT_TYPES,
    OperationalReportProjector,
)
from sedna.engagement.service import PlanningEventCommitItem


def test_report_event_classification_exhaustively_covers_the_closed_union() -> None:
    assert REPORT_PROJECTED_EVENT_TYPES.isdisjoint(REPORT_IGNORED_EVENT_TYPES)
    assert frozenset(EventType) == REPORT_PROJECTED_EVENT_TYPES | REPORT_IGNORED_EVENT_TYPES
    promotion_transition_types = frozenset(
        {
            EventType.CASE_PROMOTED,
            EventType.CASE_PROMOTION_REVOKED,
            EventType.CASE_PROMOTION_SUPERSEDED,
        }
    )
    saga_internal_event_types = frozenset(
        {
            EventType.PROMOTION_REQUESTED,
            EventType.PROMOTION_CANDIDATE_READY,
            EventType.PROMOTION_SOURCE_COMMITTED,
            EventType.PROMOTION_SEMANTIC_COMMITTED,
            EventType.PROMOTION_INDEX_PENDING,
            EventType.PROMOTION_INDEX_RETRY_FAILED,
            EventType.PROMOTION_ATTEMPT_TERMINATED,
            EventType.PROMOTION_ATTEMPT_CANCELLATION_REQUESTED,
            EventType.PROMOTION_REVOCATION_REQUESTED,
        }
    )
    assert promotion_transition_types <= REPORT_PROJECTED_EVENT_TYPES
    assert saga_internal_event_types <= REPORT_IGNORED_EVENT_TYPES


def test_projector_is_bounded_and_deterministic(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    with EngagementJournalService.open(
        tmp_path / "knowledge", clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        opened = service.create_engagement(
            display_name="Orion",
            objective="Obtain proof",
            scope=authorized_scope,
            lane=lane,
        )
        closing = service.request_close(
            opened.snapshot.engagement_id,
            lane=lane,
            reason="complete",
            expected_revision=opened.snapshot.revision,
        ).snapshot
        projector = OperationalReportProjector()
        kwargs = {
            "snapshot": closing,
            "events": closing.events,
            "evidence": (),
            "evidence_reader": service.read_evidence_slice,
            "report_revision": 1,
            "generated_at": closing.events[-1].occurred_at,
        }
        first = projector.project(**kwargs)
        second = projector.project(**kwargs)

    assert first == second
    assert first.report_revision == 1
    assert first.journal_revision == closing.revision
    assert first.lifecycle_status == "closed_unverified"
    assert first.objective == "Obtain proof"
    assert tuple(item.event_ids for item in first.timeline) == tuple(
        (item.event_id,) for item in closing.events
    )


def test_projector_uses_journal_timestamps_for_session_boundaries(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    with EngagementJournalService.open(
        tmp_path / "knowledge", clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        opened = service.create_engagement(
            display_name="Orion",
            objective="Obtain proof",
            scope=authorized_scope,
            lane=lane,
        )
        session = service.append_hook_events(
            opened.snapshot.engagement_id,
            (
                JournalEventDraft(
                    lane=lane,
                    actor="host_agent",
                    type=EventType.SESSION_STARTED,
                    payload=SessionStartedPayload(model="gpt-test", platform="hades"),
                ),
                JournalEventDraft(
                    lane=lane,
                    actor="host_agent",
                    type=EventType.SESSION_FINALIZED,
                    payload=SessionFinalizedPayload(reason="done"),
                ),
            ),
            expected_revision=opened.snapshot.revision,
        ).snapshot
        closing = service.request_close(
            opened.snapshot.engagement_id,
            lane=lane,
            reason="complete",
            expected_revision=session.revision,
        ).snapshot
        report = OperationalReportProjector().project(
            snapshot=closing,
            events=closing.events,
            evidence=(),
            evidence_reader=service.read_evidence_slice,
            report_revision=1,
            generated_at=closing.events[-1].occurred_at,
        )

    assert report.sessions[0].started_at == session.events[-2].occurred_at
    assert report.sessions[0].ended_at == session.events[-1].occurred_at
    synthetic_events = [
        session.events[-2].model_copy(update={"event_id": UUID(int=index)})
        for index in range(1, 130)
    ]
    chunks = OperationalReportProjector._session_chunks(lane.session_id, synthetic_events)
    assert tuple(len(item.event_ids) for item in chunks) == (128, 1)


def test_projector_preserves_frontier_score_changes() -> None:
    proposal_id = UUID("00000000-0000-4000-8000-000000009010")
    original = FrontierProposalEventRecord.model_construct(
        proposal_id=proposal_id,
        family_id=None,
        variant_id=None,
        rationale="Initial evidence score",
        score=25,
        commands=(),
    )
    repaired = original.model_copy(update={"rationale": "Critic repair", "score": 70})
    proposed_event_id = UUID("00000000-0000-4000-8000-000000009011")
    repaired_event_id = UUID("00000000-0000-4000-8000-000000009012")
    events = (
        SimpleNamespace(
            event_id=proposed_event_id,
            payload=FrontierProposedEventPayload.model_construct(proposal=original),
        ),
        SimpleNamespace(
            event_id=repaired_event_id,
            payload=FrontierRepairedEventPayload.model_construct(proposal=repaired),
        ),
    )

    changes = OperationalReportProjector._frontier_changes(events)

    assert tuple((item.previous_score, item.score) for item in changes) == ((None, 25), (25, 70))
    assert tuple(item.reason for item in changes) == ("Initial evidence score", "Critic repair")
    assert tuple(item.event_ids for item in changes) == ((proposed_event_id,), (repaired_event_id,))


def test_projector_distinguishes_suggested_and_executed_commands() -> None:
    proposal_id = UUID("00000000-0000-4000-8000-000000009020")
    command = CommandSuggestionEventRecord.model_construct(
        rendered_preview="nmap -sV 192.0.2.44",
    )
    proposal = FrontierProposalEventRecord.model_construct(
        proposal_id=proposal_id,
        family_id=None,
        variant_id=None,
        rationale="Enumerate services",
        score=80,
        commands=(command,),
    )
    correlation = ToolCorrelation.uncertain("missing_stable_identity")
    events = (
        SimpleNamespace(
            event_id=UUID("00000000-0000-4000-8000-000000009021"),
            payload=FrontierProposedEventPayload.model_construct(proposal=proposal),
        ),
        SimpleNamespace(
            event_id=UUID("00000000-0000-4000-8000-000000009022"),
            payload=DecisionRecordedPayload(
                decision_id="decision-1",
                proposal_id=proposal_id,
                strategy="Enumerate services",
                rationale="Use target binding",
                host_adapted_command=HostAdaptedCommandRecord(
                    command_template="nmap -sV {target}",
                    placeholder_names=("target",),
                ),
            ),
        ),
        SimpleNamespace(
            event_id=UUID("00000000-0000-4000-8000-000000009023"),
            payload=ToolCallStartedPayload(
                call_id="call-command",
                tool_name="terminal",
                correlation=correlation,
                safe_arguments={},
                decision_id="decision-1",
            ),
        ),
        SimpleNamespace(
            event_id=UUID("00000000-0000-4000-8000-000000009024"),
            payload=ToolCallCompletedPayload(
                call_id="call-command",
                correlation=correlation,
                technical_status="returned",
                duration_ms=1,
            ),
        ),
    )

    execution = OperationalReportProjector._executions(
        UUID("11111111-1111-4111-8111-111111111111"),
        events,
        {},
        lambda *_args, **_kwargs: None,
        failed=False,
    )[0]

    assert execution.suggested_commands == ("nmap -sV 192.0.2.44",)
    assert execution.executed_command == "nmap -sV {target}"


def test_projector_preserves_private_objective_proof_exactly(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    secret = b"FLAG{private-proof-value}"
    with EngagementJournalService.open(
        tmp_path / "knowledge", clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        opened = service.create_engagement(
            display_name="Orion",
            objective="Obtain proof",
            scope=authorized_scope,
            lane=lane,
            required_proofs=(
                ProofRequirement(proof_id="user-flag", kind="flag", description="Recover the flag"),
            ),
        )
        evidence = service.write_evidence(
            opened.snapshot.engagement_id,
            secret,
            media_type="text/plain",
            representation="utf-8",
        )
        attached = service.append_hook_events(
            opened.snapshot.engagement_id,
            (
                JournalEventDraft(
                    lane=lane,
                    actor="host_agent",
                    type="evidence_attached",
                    payload=EvidenceAttachedPayload(evidence=evidence),
                ),
            ),
            expected_revision=opened.snapshot.revision,
        )
        proof_event_id = UUID("00000000-0000-4000-8000-000000009001")
        observed = service._issue_planning_event_commit_capability().commit_planning_events(
            opened.snapshot.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=proof_event_id,
                    idempotency_key="proof:user-flag:1",
                    payload=ObjectiveProofObservedEventPayload(
                        proof_requirement_id="user-flag",
                        assessment_generation=1,
                        assessment="supported",
                        candidate_value=PrivateValueEventRecord(
                            evidence_slice=EvidenceSliceEventRef(
                                evidence_id=evidence.evidence_id,
                                start=0,
                                end=evidence.size,
                                sha256=evidence.sha256,
                                media_type="text/plain",
                            ),
                            value_sha256=evidence.sha256,
                        ),
                        confidence=1.0,
                        evidence_ids=(evidence.evidence_id,),
                        source_event_ids=(attached.created_event_ids[0],),
                        interpretation_input_digest="e" * 64,
                    ),
                ),
            ),
            operation_id=UUID("00000000-0000-4000-8000-000000009002"),
            expected_revision=attached.snapshot.revision,
        )
        closing = service.request_close(
            opened.snapshot.engagement_id,
            lane=lane,
            reason="complete",
            expected_revision=observed.snapshot.revision,
        ).snapshot
        report = OperationalReportProjector().project(
            snapshot=closing,
            events=closing.events,
            evidence=(),
            evidence_reader=service.read_evidence_slice,
            report_revision=1,
            generated_at=closing.events[-1].occurred_at,
        )
        invalid_slices = (
            EvidenceSlice(
                evidence_id=evidence.evidence_id,
                offset=0,
                data=secret,
                complete=False,
            ),
            EvidenceSlice(
                evidence_id="evidence-sha256-" + "0" * 64,
                offset=0,
                data=secret,
                complete=True,
            ),
            EvidenceSlice(
                evidence_id=evidence.evidence_id,
                offset=1,
                data=secret,
                complete=True,
            ),
            EvidenceSlice(
                evidence_id=evidence.evidence_id,
                offset=0,
                data=b"FLAG{forged-proof-value}",
                complete=True,
            ),
        )
        for invalid_slice in invalid_slices:
            with pytest.raises(ValueError, match="evidence slice"):
                OperationalReportProjector().project(
                    snapshot=closing,
                    events=closing.events,
                    evidence=(),
                    evidence_reader=lambda *_args, item=invalid_slice, **_kwargs: item,
                    report_revision=1,
                    generated_at=closing.events[-1].occurred_at,
                )

    assert report.secrets[0].value == secret.decode("utf-8")
    assert report.secrets[0].event_ids == (
        proof_event_id,
        attached.created_event_ids[0],
    )


@pytest.mark.parametrize(
    ("spoofed_evidence_id", "spoofed_offset"),
    (
        ("evidence-sha256-" + "0" * 64, 0),
        (None, 1),
    ),
)
def test_projector_rejects_spoofed_ordinary_evidence_slice_identity(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
    spoofed_evidence_id,
    spoofed_offset,
) -> None:
    captured = b"ordinary tool output"
    with EngagementJournalService.open(
        tmp_path / "knowledge", clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        opened = service.create_engagement(
            display_name="Orion",
            objective="Obtain proof",
            scope=authorized_scope,
            lane=lane,
        )
        evidence = service.write_evidence(
            opened.snapshot.engagement_id,
            captured,
            media_type="text/plain",
            representation="host_text",
        )
        attached = service.append_hook_events(
            opened.snapshot.engagement_id,
            (
                JournalEventDraft(
                    lane=lane,
                    actor="host_agent",
                    type="evidence_attached",
                    payload=EvidenceAttachedPayload(evidence=evidence),
                ),
            ),
            expected_revision=opened.snapshot.revision,
        ).snapshot
        descriptor = service.list_evidence_descriptors(
            attached.engagement_id,
            through_revision=attached.revision,
        ).items[0]
        invalid_slice = EvidenceSlice(
            evidence_id=spoofed_evidence_id or evidence.evidence_id,
            offset=spoofed_offset,
            data=captured,
            complete=True,
        )

        with pytest.raises(ValueError, match="report evidence read"):
            OperationalReportProjector._capture(
                attached.engagement_id,
                descriptor,
                lambda *_args, **_kwargs: invalid_slice,
            )


def test_projector_emits_a_deterministic_overflow_digest_before_exceeding_budget(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory, monkeypatch
) -> None:
    monkeypatch.setattr(projector_module, "MAX_REPORT_JSON_BYTES", 1_000)
    with EngagementJournalService.open(
        tmp_path / "knowledge", clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        opened = service.create_engagement(
            display_name="Orion",
            objective="Obtain proof",
            scope=authorized_scope,
            lane=lane,
        )
        closing = service.request_close(
            opened.snapshot.engagement_id,
            lane=lane,
            reason="complete",
            expected_revision=opened.snapshot.revision,
        ).snapshot
        kwargs = {
            "snapshot": closing,
            "events": closing.events,
            "evidence": (),
            "evidence_reader": service.read_evidence_slice,
            "report_revision": 1,
            "generated_at": closing.events[-1].occurred_at,
        }
        first = OperationalReportProjector().project(**kwargs)
        second = OperationalReportProjector().project(**kwargs)

    assert len(first.model_dump_json().encode("utf-8")) <= 1_000
    assert first.overflow
    assert first.overflow == second.overflow
    assert first.overflow[0].section == "timeline"
    exact_size = len(first.model_dump_json().encode("utf-8"))
    monkeypatch.setattr(projector_module, "MAX_REPORT_JSON_BYTES", exact_size)
    assert OperationalReportProjector._fits_envelopes(first)
    monkeypatch.setattr(projector_module, "MAX_REPORT_JSON_BYTES", exact_size - 1)
    assert not OperationalReportProjector._fits_envelopes(first)
    monkeypatch.setattr(projector_module, "MAX_REPORT_JSON_BYTES", exact_size)
    markdown_size = len(projector_module.render_operational_report(first).encode("utf-8"))
    monkeypatch.setattr(projector_module, "MAX_REPORT_MARKDOWN_BYTES", markdown_size)
    assert OperationalReportProjector._fits_envelopes(first)
    monkeypatch.setattr(projector_module, "MAX_REPORT_MARKDOWN_BYTES", markdown_size - 1)
    assert not OperationalReportProjector._fits_envelopes(first)

    base = first.model_copy(update={"sessions": (), "timeline": (), "secrets": (), "overflow": ()})
    timeline_items = (OperationalReportProjector._timeline(closing.events)[0],) * 40
    secret = ReportSecret(
        kind="flag",
        label="root-flag",
        value="FLAG{" + "x" * 256 + "}",
        event_ids=(closing.events[0].event_id,),
    )
    timeline_overflow = OperationalReportProjector._overflow_summary(
        "timeline",
        timeline_items,
        {item.event_id: item for item in closing.events},
    )
    prioritized = base.model_copy(update={"secrets": (secret,), "overflow": (timeline_overflow,)})
    monkeypatch.setattr(
        projector_module,
        "MAX_REPORT_JSON_BYTES",
        len(prioritized.model_dump_json().encode("utf-8")),
    )
    monkeypatch.setattr(projector_module, "MAX_REPORT_MARKDOWN_BYTES", 10_000_000)

    globally_bounded = OperationalReportProjector._bounded_report(
        base,
        {"timeline": timeline_items, "secrets": (secret,)},
        closing.events,
    )

    assert globally_bounded.secrets == (secret,)
    assert len(globally_bounded.timeline) < len(timeline_items)


def test_projector_rejects_an_unbounded_event_view(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    with EngagementJournalService.open(
        tmp_path / "knowledge", clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        opened = service.create_engagement(
            display_name="Orion",
            objective="Obtain proof",
            scope=authorized_scope,
            lane=lane,
        )
        closing = service.request_close(
            opened.snapshot.engagement_id,
            lane=lane,
            reason="complete",
            expected_revision=opened.snapshot.revision,
        ).snapshot
        try:
            OperationalReportProjector().project(
                snapshot=closing,
                events=closing.events[:-1],
                evidence=(),
                evidence_reader=service.read_evidence_slice,
                report_revision=1,
                generated_at=closing.events[-1].occurred_at,
            )
        except ValueError as error:
            assert "revision" in str(error)
        else:
            raise AssertionError("unbounded report projection unexpectedly succeeded")


@pytest.mark.parametrize(
    ("terminal_kind", "terminal_status", "expected_reason"),
    (
        ("completed", "returned", "host_returned_no_result"),
        ("completed", "blocked", "blocked"),
        ("completed", "cancelled", "cancelled"),
        ("completed", "error", "error"),
        ("completed", "unknown", "host_returned_no_result"),
        ("terminated", "timed_out", "timed_out"),
        ("terminated", "abandoned", "abandoned"),
        ("capture_failed", "returned", "capture_failed"),
    ),
)
def test_projector_preserves_every_terminal_absence_reason(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
    terminal_kind,
    terminal_status,
    expected_reason,
) -> None:
    correlation = ToolCorrelation.uncertain("missing_stable_identity")
    with EngagementJournalService.open(
        tmp_path / "knowledge", clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        opened = service.create_engagement(
            display_name="Orion",
            objective="Obtain proof",
            scope=authorized_scope,
            lane=lane,
        )
        started = JournalEventDraft(
            lane=lane,
            actor="host_agent",
            type="tool_call_started",
            payload=ToolCallStartedPayload(
                call_id="call-absence",
                tool_name="terminal",
                correlation=correlation,
                safe_arguments={},
            ),
        )
        current = service.append_hook_events(
            opened.snapshot.engagement_id,
            (started,),
            expected_revision=opened.snapshot.revision,
        ).snapshot
        if terminal_kind == "terminated":
            terminal = JournalEventDraft(
                lane=lane,
                actor="host_agent",
                type="tool_call_terminated",
                payload=ToolCallTerminatedPayload(
                    call_id="call-absence",
                    resolution=terminal_status,
                    reason="terminal fixture",
                ),
            )
            current = service.append_operational_start(
                current.engagement_id,
                terminal,
                expected_revision=current.revision,
            ).snapshot
        else:
            drafts = []
            if terminal_kind == "capture_failed":
                drafts.append(
                    JournalEventDraft(
                        lane=lane,
                        actor="host_agent",
                        type="evidence_capture_failed",
                        payload=EvidenceCaptureFailedPayload(
                            call_id="call-absence",
                            capture_role="result",
                            reason_code="serialization_failed",
                        ),
                    )
                )
            drafts.append(
                JournalEventDraft(
                    lane=lane,
                    actor="host_agent",
                    type="tool_call_completed",
                    payload=ToolCallCompletedPayload(
                        call_id="call-absence",
                        correlation=correlation,
                        technical_status=terminal_status,
                        duration_ms=1,
                    ),
                )
            )
            current = service.append_hook_events(
                current.engagement_id,
                tuple(drafts),
                expected_revision=current.revision,
            ).snapshot
        closing = service.request_close(
            current.engagement_id,
            lane=lane,
            reason="complete",
            expected_revision=current.revision,
        ).snapshot
        report = OperationalReportProjector().project(
            snapshot=closing,
            events=closing.events,
            evidence=(),
            evidence_reader=service.read_evidence_slice,
            report_revision=1,
            generated_at=closing.events[-1].occurred_at,
        )

    execution = (*report.tool_executions, *report.failed_attempts)[0]
    assert execution.output.absence_reason == expected_reason
