from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from sedna.engagement.lifecycle import TerminalSettlementCoordinator
from sedna.engagement.models import EngagementStatus, JournalRevision
from sedna.engagement.reporting.service import ReportManagementService

ENGAGEMENT_ID = UUID("11111111-1111-4111-8111-111111111111")
REVISION = JournalRevision(sequence=4, event_hash="a" * 64)


class _Journal:
    def __init__(self, snapshot) -> None:
        self.snapshot = snapshot

    def load_snapshot(self, engagement_id: UUID):
        assert engagement_id == ENGAGEMENT_ID
        return self.snapshot


class _ProofClosure:
    def __init__(self, requested_snapshot=None, cancelled_snapshot=None) -> None:
        self.requested_snapshot = requested_snapshot
        self.cancelled_snapshot = cancelled_snapshot
        self.requests = []
        self.cancellations = []

    def request_proof_close(self, engagement_id, *, authoritative_revision, lane, reason):
        self.requests.append((engagement_id, authoritative_revision, lane, reason))
        return SimpleNamespace(snapshot=self.requested_snapshot)

    def cancel_proof_close(self, engagement_id, *, authoritative_revision, reason):
        self.cancellations.append((engagement_id, authoritative_revision, reason))
        return SimpleNamespace(snapshot=self.cancelled_snapshot)


class _Finalizer:
    def __init__(self, snapshot=None) -> None:
        self.snapshot = snapshot
        self.calls = []

    def finalize(self, *, snapshot):
        self.calls.append(snapshot)
        return self.snapshot


def _snapshot(status: EngagementStatus, *, revision=REVISION, closure=None, closure_ready=False):
    return SimpleNamespace(
        engagement_id=ENGAGEMENT_ID,
        revision=revision,
        state=SimpleNamespace(status=status, closure=closure, closure_ready=closure_ready),
    )


def _situation(revision=REVISION):
    return SimpleNamespace(
        engagement_id=ENGAGEMENT_ID,
        authoritative_journal_revision=revision,
        objective_progress=SimpleNamespace(
            requirements=(SimpleNamespace(proof_requirement_id="root-flag"),)
        ),
    )


def test_terminal_coordinator_requests_system_proof_close_for_satisfied_active_state() -> None:
    closing_revision = JournalRevision(sequence=5, event_hash="b" * 64)
    closing = _snapshot(EngagementStatus.CLOSING, revision=closing_revision)
    capability = _ProofClosure(requested_snapshot=closing)
    coordinator = TerminalSettlementCoordinator(
        journal=_Journal(_snapshot(EngagementStatus.ACTIVE)),
        proof_closure=capability,
        finalizer=_Finalizer(),
    )

    result = coordinator.reconcile(
        engagement_id=ENGAGEMENT_ID,
        situation=_situation(),
        requirement_ids=("root-flag",),
        authoritative_revision=REVISION,
        reason="plan",
        all_required_proofs_satisfied=True,
    )

    assert result.action == "proof_close_requested"
    assert result.authoritative_journal_revision == closing_revision
    assert capability.requests == [(ENGAGEMENT_ID, REVISION, None, "plan")]


def test_terminal_coordinator_cancels_only_proof_settlement_barrier() -> None:
    barrier = SimpleNamespace(
        event_id=UUID("22222222-2222-4222-8222-222222222222"),
        origin="proof_settlement",
    )
    active_revision = JournalRevision(sequence=6, event_hash="c" * 64)
    active = _snapshot(EngagementStatus.ACTIVE, revision=active_revision)
    capability = _ProofClosure(cancelled_snapshot=active)
    coordinator = TerminalSettlementCoordinator(
        journal=_Journal(_snapshot(EngagementStatus.CLOSING, closure=barrier)),
        proof_closure=capability,
        finalizer=_Finalizer(),
    )

    result = coordinator.reconcile(
        engagement_id=ENGAGEMENT_ID,
        situation=_situation(),
        requirement_ids=("root-flag",),
        authoritative_revision=REVISION,
        reason="resume",
        all_required_proofs_satisfied=False,
    )

    assert result.action == "proof_close_cancelled"
    assert result.authoritative_journal_revision == active_revision
    assert capability.cancellations == [(ENGAGEMENT_ID, REVISION, "resume")]


def test_terminal_coordinator_finalizes_ready_matching_proof_barrier() -> None:
    barrier = SimpleNamespace(
        event_id=UUID("22222222-2222-4222-8222-222222222222"),
        origin="proof_settlement",
    )
    closing = _snapshot(EngagementStatus.CLOSING, closure=barrier, closure_ready=True)
    closed_revision = JournalRevision(sequence=6, event_hash="d" * 64)
    closed = _snapshot(EngagementStatus.CLOSED_UNVERIFIED, revision=closed_revision)
    finalizer = _Finalizer(closed)
    coordinator = TerminalSettlementCoordinator(
        journal=_Journal(closing),
        proof_closure=_ProofClosure(),
        finalizer=finalizer,
    )

    result = coordinator.reconcile(
        engagement_id=ENGAGEMENT_ID,
        situation=_situation(),
        requirement_ids=("root-flag",),
        authoritative_revision=REVISION,
        reason="session_finalize",
        all_required_proofs_satisfied=True,
    )

    assert result.action == "proof_close_finalized"
    assert result.authoritative_journal_revision == closed_revision
    assert finalizer.calls == [closing]


def test_terminal_coordinator_fails_closed_for_stale_revision_or_requirement_shape() -> None:
    coordinator = TerminalSettlementCoordinator(
        journal=_Journal(_snapshot(EngagementStatus.ACTIVE)),
        proof_closure=_ProofClosure(),
        finalizer=_Finalizer(),
    )
    stale = JournalRevision(sequence=3, event_hash="e" * 64)

    invalid_inputs = (((), REVISION), (("other",), REVISION), (("root-flag",), stale))
    for requirement_ids, supplied_revision in invalid_inputs:
        result = coordinator.reconcile(
            engagement_id=ENGAGEMENT_ID,
            situation=_situation(),
            requirement_ids=requirement_ids,
            authoritative_revision=supplied_revision,
            reason="plan",
            all_required_proofs_satisfied=True,
        )
        assert result.action == "failed"
        assert result.safe_code == "terminal_reconciliation_failed"
        assert result.authoritative_journal_revision == REVISION


def test_report_management_close_settles_and_finalizes_a_ready_barrier() -> None:
    active = _snapshot(EngagementStatus.ACTIVE)
    closing = _snapshot(EngagementStatus.CLOSING, closure_ready=True)
    closed = _snapshot(EngagementStatus.CLOSED_UNVERIFIED)

    class Journal:
        def __init__(self) -> None:
            self.requested = []

        def load_snapshot(self, engagement_id):
            assert engagement_id == ENGAGEMENT_ID
            return active

        def request_close(self, engagement_id, *, lane, reason, expected_revision):
            self.requested.append((engagement_id, lane, reason, expected_revision))
            return SimpleNamespace(snapshot=closing)

    class Planning:
        def __init__(self) -> None:
            self.calls = []

        def settle_pending_evidence(self, engagement_id, *, reason):
            self.calls.append((engagement_id, reason))
            return SimpleNamespace(status="settled")

    journal = Journal()
    planning = Planning()
    finalizer = _Finalizer(closed)
    reporting = ReportManagementService(
        journal=journal,
        planning=planning,
        finalizer=finalizer,
    )

    result = reporting.request_close(ENGAGEMENT_ID, lane="lane", reason="manual close")

    assert result is closed
    assert planning.calls == [(ENGAGEMENT_ID, "report")]
    assert journal.requested == [(ENGAGEMENT_ID, "lane", "manual close", REVISION)]
    assert finalizer.calls == [closing]


def test_report_action_triages_artifacts_without_double_settlement() -> None:
    active_report = SimpleNamespace(report_revision=3)
    snapshot = _snapshot(EngagementStatus.CLOSED_UNVERIFIED)
    snapshot.state.active_report = active_report

    class Journal:
        def __init__(self) -> None:
            self.status = (False, False)

        def load_snapshot(self, engagement_id):
            assert engagement_id == ENGAGEMENT_ID
            return snapshot

        def report_artifact_status(self, engagement_id, report):
            assert (engagement_id, report) == (ENGAGEMENT_ID, active_report)
            return self.status

    class Planning:
        def __init__(self) -> None:
            self.calls = []

        def settle_pending_evidence(self, engagement_id, *, reason):
            self.calls.append((engagement_id, reason))
            return SimpleNamespace(status="settled")

    class Finalizer:
        def __init__(self) -> None:
            self.calls = []

        def commit_later_revision(self, *, snapshot, reason):
            self.calls.append(("revision", snapshot, reason))
            return SimpleNamespace(branch=reason)

        def repair_markdown(self, *, snapshot, report_revision):
            self.calls.append(("markdown", snapshot, report_revision))
            return SimpleNamespace(branch="markdown")

    journal = Journal()
    planning = Planning()
    finalizer = Finalizer()
    reporting = ReportManagementService(journal=journal, planning=planning, finalizer=finalizer)

    assert reporting.report(ENGAGEMENT_ID).branch == "repair_json"
    journal.status = (True, False)
    assert reporting.report(ENGAGEMENT_ID).branch == "markdown"
    journal.status = (True, True)
    assert reporting.report(ENGAGEMENT_ID).branch == "manual_report"
    assert planning.calls == [(ENGAGEMENT_ID, "report")] * 3
    assert [call[0] for call in finalizer.calls] == ["revision", "markdown", "revision"]
