from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

from sedna.engagement.reporting.service import ReportClosureFinalizer, ReportManagementService
from sedna.engagement.repository import EngagementJournalRepository


def _repository(root: Path, fixed_clock, fixed_uuid_factory):
    return EngagementJournalRepository(
        root,
        clock=fixed_clock,
        uuid_factory=fixed_uuid_factory,
    )


class _EmptyJournalView:
    def __init__(self, revision) -> None:
        self.revision = revision

    def list_evidence_descriptors(self, *args, **kwargs):
        return SimpleNamespace(
            authoritative_revision=self.revision,
            items=(),
            complete=True,
            next_after_sequence=0,
        )

    def read_evidence_slice(self, *args, **kwargs):
        raise AssertionError("empty engagement has no evidence to read")


def test_closure_finalizer_projects_renders_and_commits_without_settlement(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    closure_requested,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    with _repository(tmp_path / "knowledge", fixed_clock, fixed_uuid_factory) as repository:
        opened = repository.create(manifest, initial_drafts(manifest, lane))
        repository.append_batch(
            manifest.engagement_id,
            (closure_requested(watermark=opened.revision.sequence, in_flight=()),),
            expected_revision=opened.revision,
        )
        closing = repository.load_snapshot(manifest.engagement_id)
        finalizer = ReportClosureFinalizer(
            _EmptyJournalView(closing.revision),
            repository._issue_report_commit_capability(),
        )

        closed = finalizer.finalize(snapshot=closing)

    assert closed.state.status.value == "closed_unverified"
    assert len(closed.state.reports) == 1
    assert closed.state.active_report == closed.state.reports[0]


def test_report_management_accepts_settled_planning_result() -> None:
    marker = object()
    snapshot = SimpleNamespace(
        state=SimpleNamespace(status=SimpleNamespace(value="closed_verified"))
    )
    planning = SimpleNamespace(
        settle_pending_evidence=lambda engagement_id, reason: SimpleNamespace(status="settled")
    )
    finalizer = MagicMock(spec=ReportClosureFinalizer)
    finalizer.commit_later_revision.return_value = marker
    service = ReportManagementService(
        journal=SimpleNamespace(load_snapshot=lambda engagement_id: snapshot),
        finalizer=finalizer,
        planning=planning,
    )

    engagement_id = UUID("00000000-0000-4000-8000-000000000099")
    assert service.generate_report_revision(engagement_id, reason="manual_report") is marker
