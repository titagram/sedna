from __future__ import annotations

import json
import os
import stat
from uuid import uuid5

import pytest

from sedna.engagement import (
    MAX_JOURNAL_BATCH_EVENTS,
    EngagementClosedPayload,
    EngagementJournalService,
    EventType,
    ReportGeneratedPayload,
    ReportRef,
    SystemCorrelation,
)
from sedna.engagement.events import (
    ControlToolInvokedPayload,
    JournalEventDraft,
    ToolCorrelation,
    UserNotePayload,
)
from sedna.engagement.reporting import models as report_models
from sedna.engagement.reporting.markdown import render_operational_report
from sedna.engagement.reporting.models import MAX_REPORT_TRANSACTION_BYTES
from sedna.engagement.reporting.projector import OperationalReportProjector
from sedna.engagement.repository import JournalUnavailableError, RevisionConflictError
from sedna.engagement.service import EngagementAppendAuthorityError


def _closing_report(service, authorized_scope, lane):
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
    report = OperationalReportProjector().project(
        snapshot=closing,
        events=closing.events,
        evidence=(),
        evidence_reader=service.read_evidence_slice,
        report_revision=1,
        generated_at=closing.events[-1].occurred_at,
    )
    return closing, report


def _strand_report_transaction(service, closing, report) -> None:
    def fault(point: str) -> None:
        if point == "report_before_event_append":
            raise RuntimeError(point)

    service._repository._fault = fault
    with pytest.raises(RuntimeError, match="report_before_event_append"):
        service._repository._issue_report_commit_capability().commit_report_snapshot(
            closing.engagement_id,
            report,
            render_operational_report(report),
            expected_revision=closing.revision,
        )
    service._repository._fault = lambda _point: None


def test_report_commit_authority_is_repository_issued_and_not_public(
    tmp_path, fixed_clock, fixed_uuid_factory
) -> None:
    with EngagementJournalService.open(
        tmp_path / "knowledge", clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        repository = service._repository

        assert not hasattr(repository, "commit_report_snapshot")
        capability = repository._issue_report_commit_capability()
        assert callable(capability.commit_report_snapshot)


def test_report_commit_capability_cannot_be_retargeted_across_repository_holders(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    with (
        EngagementJournalService.open(
            first_root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
        ) as first,
        EngagementJournalService.open(
            second_root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
        ) as second,
    ):
        closing, report = _closing_report(second, authorized_scope, lane)
        capability = first._repository._issue_report_commit_capability()
        capability._repository = second._repository

        with pytest.raises(ValueError, match="invalid report capability holder"):
            capability.commit_report_snapshot(
                closing.engagement_id,
                report,
                render_operational_report(report),
                expected_revision=closing.revision,
            )

        assert second.load_snapshot(closing.engagement_id).revision == closing.revision

    assert not (second_root / "engagements" / str(closing.engagement_id) / "reports").exists()


def test_generic_repository_append_cannot_forge_report_bound_closure(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        closing, report = _closing_report(service, authorized_scope, lane)
        correlation = SystemCorrelation(
            source="reporting",
            operation_id=fixed_uuid_factory(),
        )
        report_ref = ReportRef(
            report_id=report.report_id,
            report_revision=report.report_revision,
            json_relative_path=f"engagements/{closing.engagement_id}/reports/report-v1.json",
            json_sha256="a" * 64,
            markdown_relative_path=f"engagements/{closing.engagement_id}/reports/report-v1.md",
            markdown_sha256="b" * 64,
            renderer_version="1",
            journal_revision=closing.revision,
        )

        with pytest.raises(EngagementAppendAuthorityError, match="repository-owned"):
            service._repository.append_batch(
                closing.engagement_id,
                (
                    JournalEventDraft(
                        actor="system",
                        type=EventType.REPORT_GENERATED,
                        payload=ReportGeneratedPayload(
                            report=report_ref,
                            generation_reason="closure",
                        ),
                        system_correlation=correlation,
                    ),
                    JournalEventDraft(
                        actor="system",
                        type=EventType.ENGAGEMENT_CLOSED,
                        payload=EngagementClosedPayload(
                            report_id=report.report_id,
                            report_revision=report.report_revision,
                            closure_request_event_id=closing.state.closure.event_id,
                            terminal_watermark=closing.state.closure.terminal_watermark,
                        ),
                        system_correlation=correlation,
                    ),
                ),
                expected_revision=closing.revision,
            )

        assert service.load_snapshot(closing.engagement_id).revision == closing.revision

    assert not (root / "engagements" / str(closing.engagement_id) / "reports").exists()


def test_report_commit_rejects_symlinked_reports_directory(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    root = tmp_path / "knowledge"
    outside = tmp_path / "outside"
    outside.mkdir()
    with EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        closing, report = _closing_report(service, authorized_scope, lane)
        report_dir = root / "engagements" / str(closing.engagement_id) / "reports"
        report_dir.symlink_to(outside, target_is_directory=True)

        with pytest.raises(JournalUnavailableError, match="reports directory"):
            service._repository._issue_report_commit_capability().commit_report_snapshot(
                closing.engagement_id,
                report,
                render_operational_report(report),
                expected_revision=closing.revision,
            )

    assert tuple(outside.iterdir()) == ()


def test_report_commit_is_immutable_and_idempotent(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        closing, report = _closing_report(service, authorized_scope, lane)
        markdown = render_operational_report(report)
        capability = service._repository._issue_report_commit_capability()
        first = capability.commit_report_snapshot(
            closing.engagement_id,
            report,
            markdown,
            expected_revision=closing.revision,
        )
        second = capability.commit_report_snapshot(
            closing.engagement_id,
            report,
            markdown,
            expected_revision=closing.revision,
        )

    report_dir = root / "engagements" / str(closing.engagement_id) / "reports"
    assert first == second
    assert stat.S_IMODE(report_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((report_dir / "report-v1.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((report_dir / "report-v1.md").stat().st_mode) == 0o600
    assert not (report_dir / ".report-transaction.json").exists()


def test_retry_after_event_append_fault_recovers_exact_commit(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        closing, report = _closing_report(service, authorized_scope, lane)
        markdown = render_operational_report(report)

        def fault(point: str) -> None:
            if point == "report_after_event_append":
                raise RuntimeError(point)

        service._repository._fault = fault
        capability = service._repository._issue_report_commit_capability()
        with pytest.raises(RuntimeError, match="report_after_event_append"):
            capability.commit_report_snapshot(
                closing.engagement_id,
                report,
                markdown,
                expected_revision=closing.revision,
            )
        service._repository._fault = lambda _point: None
        recovered = capability.commit_report_snapshot(
            closing.engagement_id,
            report,
            markdown,
            expected_revision=closing.revision,
        )

    assert recovered.snapshot.state.active_report == recovered.report
    assert [item.type.value for item in recovered.snapshot.events[-2:]] == [
        "report_generated",
        "engagement_closed",
    ]
    assert not (
        root / "engagements" / str(closing.engagement_id) / "reports" / ".report-transaction.json"
    ).exists()


def test_conflicting_retry_preserves_existing_report_transaction_intent(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        closing, report = _closing_report(service, authorized_scope, lane)
        capability = service._repository._issue_report_commit_capability()

        def fault(point: str) -> None:
            if point == "report_before_event_append":
                raise RuntimeError(point)

        service._repository._fault = fault
        with pytest.raises(RuntimeError, match="report_before_event_append"):
            capability.commit_report_snapshot(
                closing.engagement_id,
                report,
                render_operational_report(report),
                expected_revision=closing.revision,
            )
        service._repository._fault = lambda _point: None
        intent_path = (
            root
            / "engagements"
            / str(closing.engagement_id)
            / "reports"
            / ".report-transaction.json"
        )
        original_intent = intent_path.read_bytes()
        conflicting = report.model_copy(update={"objective": "conflicting retry content"})

        with pytest.raises(JournalUnavailableError, match="conflicting report transaction intent"):
            capability.commit_report_snapshot(
                closing.engagement_id,
                conflicting,
                render_operational_report(conflicting),
                expected_revision=closing.revision,
            )

    assert intent_path.read_bytes() == original_intent


def test_old_report_retry_preserves_newer_transaction_intent(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        closing, report_v1 = _closing_report(service, authorized_scope, lane)
        capability = service._repository._issue_report_commit_capability()
        committed = capability.commit_report_snapshot(
            closing.engagement_id,
            report_v1,
            render_operational_report(report_v1),
            expected_revision=closing.revision,
        ).snapshot
        report_v2 = OperationalReportProjector().project(
            snapshot=committed,
            events=committed.events,
            evidence=(),
            evidence_reader=service.read_evidence_slice,
            report_revision=2,
            generated_at=committed.events[-1].occurred_at,
        )

        def fault(point: str) -> None:
            if point == "report_before_event_append":
                raise RuntimeError(point)

        service._repository._fault = fault
        with pytest.raises(RuntimeError, match="report_before_event_append"):
            capability.commit_report_revision(
                closing.engagement_id,
                report_v2,
                render_operational_report(report_v2),
                generation_reason="manual_report",
                expected_revision=committed.revision,
            )
        service._repository._fault = lambda _point: None
        intent_path = (
            root
            / "engagements"
            / str(closing.engagement_id)
            / "reports"
            / ".report-transaction.json"
        )
        retained = intent_path.read_bytes()

        with pytest.raises(JournalUnavailableError, match="conflicting report transaction intent"):
            capability.commit_report_snapshot(
                closing.engagement_id,
                report_v1,
                render_operational_report(report_v1),
                expected_revision=closing.revision,
            )

    assert intent_path.read_bytes() == retained


def test_completed_report_retry_verifies_event_bound_files(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        closing, report = _closing_report(service, authorized_scope, lane)
        markdown = render_operational_report(report)
        capability = service._repository._issue_report_commit_capability()
        capability.commit_report_snapshot(
            closing.engagement_id,
            report,
            markdown,
            expected_revision=closing.revision,
        )
        markdown_path = (
            root / "engagements" / str(closing.engagement_id) / "reports" / "report-v1.md"
        )
        markdown_path.write_text("corrupt", encoding="utf-8")

        with pytest.raises(JournalUnavailableError, match="Markdown digest mismatch"):
            capability.commit_report_snapshot(
                closing.engagement_id,
                report,
                markdown,
                expected_revision=closing.revision,
            )


def test_markdown_derivative_can_be_regenerated_from_valid_json(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        closing, report = _closing_report(service, authorized_scope, lane)
        markdown = render_operational_report(report)
        capability = service._repository._issue_report_commit_capability()
        committed = capability.commit_report_snapshot(
            closing.engagement_id, report, markdown, expected_revision=closing.revision
        )
        markdown_path = (
            root / "engagements" / str(closing.engagement_id) / "reports" / "report-v1.md"
        )
        markdown_path.write_text("corrupt", encoding="utf-8")
        repaired = capability.repair_markdown(
            closing.engagement_id, 1, expected_revision=committed.snapshot.revision
        )

    assert repaired == committed.report
    assert markdown_path.read_text(encoding="utf-8") == markdown


@pytest.mark.parametrize(
    "abandon_fault_point",
    ["report_abandon_after_event_append", "report_abandon_after_json_move"],
)
def test_unrelated_append_abandons_unbound_report_transaction_atomically(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
    abandon_fault_point,
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        closing, report = _closing_report(service, authorized_scope, lane)
        capability = service._repository._issue_report_commit_capability()

        def fault(point: str) -> None:
            if point == "report_before_event_append":
                raise RuntimeError(point)

        service._repository._fault = fault
        with pytest.raises(RuntimeError, match="report_before_event_append"):
            capability.commit_report_snapshot(
                closing.engagement_id,
                report,
                render_operational_report(report),
                expected_revision=closing.revision,
            )

        def abandon_fault(point: str) -> None:
            if point == abandon_fault_point:
                raise RuntimeError(point)

        service._repository._fault = abandon_fault
        with pytest.raises(RuntimeError, match=abandon_fault_point):
            service._repository.append_batch(
                closing.engagement_id,
                (
                    JournalEventDraft(
                        actor="user",
                        type=EventType.USER_NOTE,
                        payload=UserNotePayload(note="new work invalidated the report"),
                    ),
                ),
                expected_revision=closing.revision,
            )
        service._repository._fault = lambda _point: None
        result = service._repository.append_batch(
            closing.engagement_id,
            (
                JournalEventDraft(
                    actor="user",
                    type=EventType.USER_NOTE,
                    payload=UserNotePayload(note="new work invalidated the report"),
                ),
            ),
            expected_revision=closing.revision,
        )
        events = service._repository.load_events(closing.engagement_id)

    report_dir = root / "engagements" / str(closing.engagement_id) / "reports"
    assert [item.type.value for item in events[-2:]] == [
        "report_commit_abandoned",
        "user_note",
    ]
    assert result.events == (events[-1],)
    assert not (report_dir / ".report-transaction.json").exists()
    assert not (report_dir / "report-v1.json").exists()
    assert not (report_dir / "report-v1.md").exists()
    assert len(tuple((report_dir / "orphans").iterdir())) == 1


@pytest.mark.parametrize(
    ("field", "tampered_value"),
    [
        ("intent_id", "00000000-0000-0000-0000-000000000001"),
        ("report_id", "00000000-0000-0000-0000-000000000002"),
        ("generation_reason", "spoofed"),
        ("renderer_version", "2"),
    ],
)
def test_unrelated_append_rejects_semantically_tampered_unbound_intent_before_mutation(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
    field,
    tampered_value,
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        closing, report = _closing_report(service, authorized_scope, lane)
        capability = service._repository._issue_report_commit_capability()

        def fault(point: str) -> None:
            if point == "report_before_event_append":
                raise RuntimeError(point)

        service._repository._fault = fault
        with pytest.raises(RuntimeError, match="report_before_event_append"):
            capability.commit_report_snapshot(
                closing.engagement_id,
                report,
                render_operational_report(report),
                expected_revision=closing.revision,
            )
        service._repository._fault = lambda _point: None
        report_dir = root / "engagements" / str(closing.engagement_id) / "reports"
        intent_path = report_dir / ".report-transaction.json"
        intent = json.loads(intent_path.read_bytes())
        intent[field] = tampered_value
        tampered = json.dumps(
            intent,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        intent_path.write_bytes(tampered)
        events_before = service._repository.load_events(closing.engagement_id)
        json_before = (report_dir / "report-v1.json").read_bytes()
        markdown_before = (report_dir / "report-v1.md").read_bytes()

        with pytest.raises(JournalUnavailableError, match="invalid report transaction intent"):
            service._repository.append_batch(
                closing.engagement_id,
                (
                    JournalEventDraft(
                        actor="user",
                        type=EventType.USER_NOTE,
                        payload=UserNotePayload(note="must not authenticate forged intent"),
                    ),
                ),
                expected_revision=closing.revision,
            )

        assert service._repository.load_events(closing.engagement_id) == events_before
        assert intent_path.read_bytes() == tampered
        assert (report_dir / "report-v1.json").read_bytes() == json_before
        assert (report_dir / "report-v1.md").read_bytes() == markdown_before
        assert not (report_dir / "orphans").exists()


def test_unrelated_append_rejects_consistent_closure_to_manual_report_forgery_before_mutation(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        closing, report = _closing_report(service, authorized_scope, lane)
        capability = service._repository._issue_report_commit_capability()

        def fault(point: str) -> None:
            if point == "report_before_event_append":
                raise RuntimeError(point)

        service._repository._fault = fault
        with pytest.raises(RuntimeError, match="report_before_event_append"):
            capability.commit_report_snapshot(
                closing.engagement_id,
                report,
                render_operational_report(report),
                expected_revision=closing.revision,
            )

        report_dir = root / "engagements" / str(closing.engagement_id) / "reports"
        intent_path = report_dir / ".report-transaction.json"
        intent = json.loads(intent_path.read_bytes())
        intent["generation_reason"] = "manual_report"
        intent["intent_id"] = str(
            uuid5(
                closing.engagement_id,
                f"report-intent:{intent['report_revision']}:manual_report:"
                f"{intent['json_sha256']}:{intent['markdown_sha256']}",
            )
        )
        forged_intent = json.dumps(
            intent,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        intent_path.write_bytes(forged_intent)
        events_before = service._repository.load_events(closing.engagement_id)
        json_before = (report_dir / "report-v1.json").read_bytes()
        markdown_before = (report_dir / "report-v1.md").read_bytes()
        mutation_points: list[str] = []
        service._repository._fault = mutation_points.append

        with pytest.raises(JournalUnavailableError, match="invalid report transaction intent"):
            service._repository.append_batch(
                closing.engagement_id,
                (
                    JournalEventDraft(
                        actor="user",
                        type=EventType.USER_NOTE,
                        payload=UserNotePayload(note="must not recover forged report reason"),
                    ),
                ),
                expected_revision=closing.revision,
            )

        assert mutation_points == []
        assert service._repository.load_events(closing.engagement_id) == events_before
        assert intent_path.read_bytes() == forged_intent
        assert (report_dir / "report-v1.json").read_bytes() == json_before
        assert (report_dir / "report-v1.md").read_bytes() == markdown_before
        assert not (report_dir / "orphans").exists()


@pytest.mark.parametrize(
    ("caller_count", "accepted"),
    [
        (MAX_JOURNAL_BATCH_EVENTS - 1, True),
        (MAX_JOURNAL_BATCH_EVENTS, False),
    ],
)
def test_report_abandonment_reserves_one_atomic_batch_slot(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
    caller_count,
    accepted,
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        closing, report = _closing_report(service, authorized_scope, lane)
        capability = service._repository._issue_report_commit_capability()

        def fault(point: str) -> None:
            if point == "report_before_event_append":
                raise RuntimeError(point)

        service._repository._fault = fault
        with pytest.raises(RuntimeError, match="report_before_event_append"):
            capability.commit_report_snapshot(
                closing.engagement_id,
                report,
                render_operational_report(report),
                expected_revision=closing.revision,
            )
        service._repository._fault = lambda _point: None
        drafts = tuple(
            JournalEventDraft(
                actor="user",
                type=EventType.USER_NOTE,
                payload=UserNotePayload(note=f"displaced note {index}"),
            )
            for index in range(caller_count)
        )

        if accepted:
            result = service._repository.append_batch(
                closing.engagement_id,
                drafts,
                expected_revision=closing.revision,
            )
            assert len(result.events) == caller_count
        else:
            with pytest.raises(ValueError, match="leaves no room for caller batch"):
                service._repository.append_batch(
                    closing.engagement_id,
                    drafts,
                    expected_revision=closing.revision,
                )

    report_dir = root / "engagements" / str(closing.engagement_id) / "reports"
    if accepted:
        assert not (report_dir / ".report-transaction.json").exists()
        assert len(tuple((report_dir / "orphans").iterdir())) == 1
    else:
        assert (report_dir / ".report-transaction.json").is_file()
        assert (report_dir / "report-v1.json").is_file()
        assert (report_dir / "report-v1.md").is_file()
        assert not (report_dir / "orphans").exists()


def test_stale_unrelated_append_does_not_mutate_unbound_report_transaction(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        closing, report = _closing_report(service, authorized_scope, lane)
        capability = service._repository._issue_report_commit_capability()

        def fault(point: str) -> None:
            if point == "report_before_event_append":
                raise RuntimeError(point)

        service._repository._fault = fault
        with pytest.raises(RuntimeError, match="report_before_event_append"):
            capability.commit_report_snapshot(
                closing.engagement_id,
                report,
                render_operational_report(report),
                expected_revision=closing.revision,
            )
        service._repository._fault = lambda _point: None
        events_before = service._repository.load_events(closing.engagement_id)
        stale = closing.revision.model_copy(update={"sequence": closing.revision.sequence - 1})

        with pytest.raises(RevisionConflictError, match="expected revision is stale"):
            service._repository.append_batch(
                closing.engagement_id,
                (
                    JournalEventDraft(
                        actor="user",
                        type=EventType.USER_NOTE,
                        payload=UserNotePayload(note="stale work"),
                    ),
                ),
                expected_revision=stale,
            )

        assert service._repository.load_events(closing.engagement_id) == events_before

    report_dir = root / "engagements" / str(closing.engagement_id) / "reports"
    assert (report_dir / ".report-transaction.json").is_file()
    assert (report_dir / "report-v1.json").is_file()
    assert (report_dir / "report-v1.md").is_file()
    assert not (report_dir / "orphans").exists()


def test_unrelated_append_never_orphans_event_bound_report_files(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        closing, report = _closing_report(service, authorized_scope, lane)
        capability = service._repository._issue_report_commit_capability()

        def fault(point: str) -> None:
            if point == "report_after_event_append":
                raise RuntimeError(point)

        service._repository._fault = fault
        with pytest.raises(RuntimeError, match="report_after_event_append"):
            capability.commit_report_snapshot(
                closing.engagement_id,
                report,
                render_operational_report(report),
                expected_revision=closing.revision,
            )
        service._repository._fault = lambda _point: None
        committed = service._repository.load_snapshot(closing.engagement_id)
        service._repository.append_batch(
            closing.engagement_id,
            (
                JournalEventDraft(
                    lane=lane,
                    actor="host_agent",
                    type=EventType.CONTROL_TOOL_INVOKED,
                    payload=ControlToolInvokedPayload(
                        control_tool="sedna_manage_engagement",
                        correlation=ToolCorrelation.uncertain("missing_stable_identity"),
                    ),
                ),
            ),
            expected_revision=committed.revision,
        )
        events = service._repository.load_events(closing.engagement_id)

    report_dir = root / "engagements" / str(closing.engagement_id) / "reports"
    assert [item.type.value for item in events].count("report_commit_abandoned") == 0
    assert (report_dir / "report-v1.json").is_file()
    assert (report_dir / "report-v1.md").is_file()
    assert not (report_dir / ".report-transaction.json").exists()
    assert not (report_dir / "orphans").exists()


@pytest.mark.parametrize(
    "artifact_kind",
    ("malformed", "exact_limit_malformed", "oversized", "symlink", "fifo"),
)
def test_unrelated_append_rejects_unsafe_transaction_intent_artifacts_without_mutation(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
    artifact_kind,
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        closing, report = _closing_report(service, authorized_scope, lane)
        _strand_report_transaction(service, closing, report)
        report_dir = root / "engagements" / str(closing.engagement_id) / "reports"
        intent_path = report_dir / ".report-transaction.json"
        intent_path.unlink()
        if artifact_kind == "symlink":
            outside = tmp_path / "outside-intent.json"
            outside.write_bytes(b"{}")
            intent_path.symlink_to(outside)
        elif artifact_kind == "fifo":
            os.mkfifo(intent_path)
        elif artifact_kind == "oversized":
            intent_path.write_bytes(b"x" * (MAX_REPORT_TRANSACTION_BYTES + 1))
        elif artifact_kind == "exact_limit_malformed":
            intent_path.write_bytes(b"x" * MAX_REPORT_TRANSACTION_BYTES)
        else:
            intent_path.write_bytes(b"{")
        events_before = service._repository.load_events(closing.engagement_id)
        json_before = (report_dir / "report-v1.json").read_bytes()
        markdown_before = (report_dir / "report-v1.md").read_bytes()
        mutation_points: list[str] = []
        service._repository._fault = mutation_points.append

        with pytest.raises(JournalUnavailableError, match="report transaction intent"):
            service._repository.append_batch(
                closing.engagement_id,
                (
                    JournalEventDraft(
                        actor="user",
                        type=EventType.USER_NOTE,
                        payload=UserNotePayload(note="must remain uncommitted"),
                    ),
                ),
                expected_revision=closing.revision,
            )

        assert mutation_points == []
        assert service._repository.load_events(closing.engagement_id) == events_before
        assert (report_dir / "report-v1.json").read_bytes() == json_before
        assert (report_dir / "report-v1.md").read_bytes() == markdown_before
        assert not (report_dir / "orphans").exists()


@pytest.mark.parametrize(
    "fault_point",
    (
        "report_after_intent",
        "report_after_json",
        "report_after_markdown",
        "report_after_directory_fsync",
        "append_after_journal_fsync",
        "append_after_head_replace",
        "report_after_event_append",
        "report_after_intent_clear",
    ),
)
def test_report_commit_recovers_deterministically_from_every_authoritative_crash_window(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
    fault_point,
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        closing, report = _closing_report(service, authorized_scope, lane)
        markdown = render_operational_report(report)

        def fault(point: str) -> None:
            if point == fault_point:
                raise RuntimeError(point)

        service._repository._fault = fault
        with pytest.raises(RuntimeError, match=fault_point):
            service._repository._issue_report_commit_capability().commit_report_snapshot(
                closing.engagement_id,
                report,
                markdown,
                expected_revision=closing.revision,
            )

    with EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as recovered_service:
        capability = recovered_service._repository._issue_report_commit_capability()
        recovered = capability.commit_report_snapshot(
            closing.engagement_id,
            report,
            markdown,
            expected_revision=closing.revision,
        )
        repeated = capability.commit_report_snapshot(
            closing.engagement_id,
            report,
            markdown,
            expected_revision=closing.revision,
        )

        assert repeated == recovered
        assert [item.type.value for item in recovered.snapshot.events].count(
            "report_generated"
        ) == 1
        assert [item.type.value for item in recovered.snapshot.events].count(
            "engagement_closed"
        ) == 1

    report_dir = root / "engagements" / str(closing.engagement_id) / "reports"
    assert not (report_dir / ".report-transaction.json").exists()


def test_over_budget_report_snapshot_is_rejected_before_filesystem_or_journal_mutation(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch,
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        closing, report = _closing_report(service, authorized_scope, lane)
        events_before = service._repository.load_events(closing.engagement_id)
        mutation_points: list[str] = []
        service._repository._fault = mutation_points.append
        monkeypatch.setattr(report_models, "MAX_REPORT_JSON_BYTES", 1)

        with pytest.raises(ValueError, match="immutable report budget"):
            service._repository._issue_report_commit_capability().commit_report_snapshot(
                closing.engagement_id,
                report,
                render_operational_report(report),
                expected_revision=closing.revision,
            )

        assert mutation_points == []
        assert service._repository.load_events(closing.engagement_id) == events_before
        report_dir = root / "engagements" / str(closing.engagement_id) / "reports"
        assert not report_dir.exists()


def test_over_budget_report_revision_is_rejected_before_filesystem_or_journal_mutation(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch,
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        closing, report = _closing_report(service, authorized_scope, lane)
        capability = service._repository._issue_report_commit_capability()
        committed = capability.commit_report_snapshot(
            closing.engagement_id,
            report,
            render_operational_report(report),
            expected_revision=closing.revision,
        )
        revised = OperationalReportProjector().project(
            snapshot=committed.snapshot,
            events=committed.snapshot.events,
            evidence=(),
            evidence_reader=service.read_evidence_slice,
            report_revision=2,
            generated_at=fixed_clock(),
        )
        report_dir = root / "engagements" / str(closing.engagement_id) / "reports"
        files_before = {
            path.name: path.read_bytes() for path in report_dir.iterdir() if path.is_file()
        }
        events_before = service._repository.load_events(closing.engagement_id)
        mutation_points: list[str] = []
        service._repository._fault = mutation_points.append
        monkeypatch.setattr(report_models, "MAX_REPORT_JSON_BYTES", 1)

        with pytest.raises(ValueError, match="immutable report budget"):
            capability.commit_report_revision(
                closing.engagement_id,
                revised,
                render_operational_report(revised),
                generation_reason="manual_report",
                expected_revision=committed.snapshot.revision,
            )

        assert mutation_points == []
        assert service._repository.load_events(closing.engagement_id) == events_before
        assert {
            path.name: path.read_bytes() for path in report_dir.iterdir() if path.is_file()
        } == files_before
