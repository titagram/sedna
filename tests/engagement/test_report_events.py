from __future__ import annotations

from uuid import UUID, uuid5

import pytest

from sedna.engagement import (
    EngagementClosedPayload,
    EngagementStatus,
    JournalEventDraft,
    JournalRevision,
    ReportGeneratedPayload,
    ReportRef,
    SystemCorrelation,
)
from sedna.engagement.reducer import EngagementReplayError, reduce_engagement


def _system_draft(kind: str, payload, operation: str) -> JournalEventDraft:
    return JournalEventDraft(
        actor="system",
        type=kind,
        payload=payload,
        system_correlation=SystemCorrelation(
            source="reporting",
            operation_id=UUID(operation),
        ),
    )


def test_report_events_close_only_with_matching_repository_owned_reference(
    manifest,
    lane,
    initial_drafts,
    closure_requested,
    event_chain,
) -> None:
    opened, bound = initial_drafts(manifest, lane)
    prefix = event_chain(
        manifest,
        opened,
        bound,
        closure_requested(watermark=2, in_flight=()),
    )
    bound_revision = JournalRevision(
        sequence=prefix[-1].sequence,
        event_hash=prefix[-1].event_hash,
    )
    report_id = uuid5(manifest.engagement_id, "report:1")
    report = ReportRef(
        report_id=report_id,
        report_revision=1,
        json_relative_path=f"reports/{report_id}/report.json",
        json_sha256="a" * 64,
        markdown_relative_path=f"reports/{report_id}/report.md",
        markdown_sha256="b" * 64,
        renderer_version="1",
        journal_revision=bound_revision,
    )
    report_draft = _system_draft(
        "report_generated",
        ReportGeneratedPayload(report=report, generation_reason="closure"),
        "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    )
    close_draft = _system_draft(
        "engagement_closed",
        EngagementClosedPayload(
            report_id=report_id,
            report_revision=1,
            closure_request_event_id=prefix[-1].event_id,
            terminal_watermark=2,
        ),
        "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
    )
    events = event_chain(
        manifest,
        opened,
        bound,
        closure_requested(watermark=2, in_flight=()),
        report_draft,
        close_draft,
    )

    state = reduce_engagement(manifest, events)

    assert state.status is EngagementStatus.CLOSED_UNVERIFIED
    assert state.reports == (report,)
    assert state.active_report == report
    assert state.closure is None


def test_reducer_rejects_report_not_bound_to_closure_revision(
    manifest,
    lane,
    initial_drafts,
    closure_requested,
    event_chain,
) -> None:
    opened, bound = initial_drafts(manifest, lane)
    report_id = uuid5(manifest.engagement_id, "report:1")
    forged = ReportRef(
        report_id=report_id,
        report_revision=1,
        json_relative_path=f"reports/{report_id}/report.json",
        json_sha256="a" * 64,
        markdown_relative_path=f"reports/{report_id}/report.md",
        markdown_sha256="b" * 64,
        renderer_version="1",
        journal_revision=JournalRevision(sequence=1, event_hash="c" * 64),
    )
    events = event_chain(
        manifest,
        opened,
        bound,
        closure_requested(watermark=2, in_flight=()),
        _system_draft(
            "report_generated",
            ReportGeneratedPayload(report=forged, generation_reason="closure"),
            "ffffffff-ffff-4fff-8fff-ffffffffffff",
        ),
    )

    with pytest.raises(EngagementReplayError, match="ready closure barrier"):
        reduce_engagement(manifest, events)
