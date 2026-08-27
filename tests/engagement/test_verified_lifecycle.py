from __future__ import annotations

from types import SimpleNamespace
from typing import get_args
from uuid import UUID

import pytest

from sedna.engagement import ExecutionLaneKey, HostKind, SettlementReason
from sedna.engagement.lifecycle import EngagementLifecycleService


class _Planning:
    def __init__(self, status: str = "nothing_pending") -> None:
        self.status = status
        self.reasons: list[str] = []

    def settle_pending_evidence(self, engagement_id: UUID, *, reason: str):
        del engagement_id
        self.reasons.append(reason)
        return SimpleNamespace(status=self.status)


class _Journal:
    def __init__(self, snapshot) -> None:
        self.snapshot = snapshot
        self.requested = False

    def load_snapshot(self, engagement_id: UUID):
        del engagement_id
        return self.snapshot

    def request_close(self, engagement_id: UUID, *, lane, reason: str, expected_revision):
        del engagement_id, lane, reason, expected_revision
        self.requested = True
        return SimpleNamespace(snapshot=self.snapshot)


class _Finalizer:
    def __init__(self, snapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def finalize(self, *, snapshot):
        del snapshot
        self.calls += 1
        return self.snapshot


class _Commits:
    def __init__(self) -> None:
        self.reopen_calls = 0
        self.rejection_calls = 0
        self.receipt_calls = 0

    def commit_verified(self, *args, **kwargs):
        return SimpleNamespace(args=args, kwargs=kwargs)

    def commit_reopen(self, *args, **kwargs):
        del args, kwargs
        self.reopen_calls += 1
        return SimpleNamespace()

    def rejection_receipt(self, *args, **kwargs):
        del args, kwargs
        self.receipt_calls += 1
        return SimpleNamespace(rejected_value_sha256="a" * 64)

    def commit_rejection_and_reopen(self, *args, **kwargs):
        del args, kwargs
        self.rejection_calls += 1
        return SimpleNamespace()


class _Recovery:
    def __init__(self) -> None:
        self.engagement_ids: list[UUID] = []

    def recover_after_verification(self, engagement_id: UUID) -> None:
        self.engagement_ids.append(engagement_id)


def _snapshot(*, status: str, closure_ready: bool = False, active_report=None, events=()):
    return SimpleNamespace(
        engagement_id=UUID("11111111-1111-4111-8111-111111111111"),
        revision=SimpleNamespace(sequence=4),
        state=SimpleNamespace(
            status=SimpleNamespace(value=status),
            closure_ready=closure_ready,
            active_report=active_report,
        ),
        events=events,
    )


def test_terminal_settlement_reason_set_remains_authoritative() -> None:
    assert get_args(SettlementReason) == (
        "plan",
        "close",
        "verify",
        "reject",
        "reopen",
        "report",
        "resume",
        "session_finalize",
    )


def test_close_stops_before_mutation_when_settlement_is_incomplete() -> None:
    snapshot = _snapshot(status="active")
    planning = _Planning("incomplete")
    journal = _Journal(snapshot)
    lifecycle = EngagementLifecycleService(
        journal=journal,
        planning=planning,
        closure_finalizer=_Finalizer(snapshot),
        lifecycle_commits=_Commits(),
    )

    with pytest.raises(ValueError, match="terminal_settlement_incomplete"):
        lifecycle.close(snapshot.engagement_id, lane=object(), reason="done")

    assert planning.reasons == ["close"]
    assert journal.requested is False


def test_verify_settles_then_requires_a_closed_report() -> None:
    snapshot = _snapshot(status="active")
    planning = _Planning()
    lifecycle = EngagementLifecycleService(
        journal=_Journal(snapshot),
        planning=planning,
        closure_finalizer=_Finalizer(snapshot),
        lifecycle_commits=_Commits(),
    )

    with pytest.raises(ValueError, match="verification_requires_closed_report"):
        lifecycle.verify(
            snapshot.engagement_id,
            verification_kind="platform",
            verification_reference="submission-42",
        )

    assert planning.reasons == ["verify"]


def test_repeated_exact_verification_recovers_without_a_second_commit(monkeypatch) -> None:
    event_id = UUID("22222222-2222-4222-8222-222222222222")
    verified_event = SimpleNamespace(
        event_id=event_id,
        type="engagement_verified",
        payload=SimpleNamespace(
            verification_kind="platform",
            verification_reference="submission-42",
        ),
    )
    snapshot = _snapshot(
        status="closed_verified",
        active_report=object(),
        events=(verified_event,),
    )
    commits = _Commits()
    planning = _Planning()
    recovery = _Recovery()
    monkeypatch.setattr(
        "sedna.engagement.lifecycle.EngagementMutationResult",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    lifecycle = EngagementLifecycleService(
        journal=_Journal(snapshot),
        planning=planning,
        closure_finalizer=_Finalizer(snapshot),
        lifecycle_commits=commits,
        promotion_recovery=recovery,
    )

    result = lifecycle.verify(
        snapshot.engagement_id,
        verification_kind="platform",
        verification_reference="submission-42",
    )

    assert result.existing_event_ids == (event_id,)
    assert recovery.engagement_ids == [snapshot.engagement_id]
    assert planning.reasons == []


@pytest.mark.parametrize("action", ("reopen", "reject"))
def test_closed_verified_reopen_paths_require_canonical_revocation_before_commit(
    action: str,
) -> None:
    snapshot = _snapshot(status="closed_verified", active_report=object())
    planning = _Planning()
    commits = _Commits()
    lane = ExecutionLaneKey(host_kind=HostKind.HADES, session_id="session", task_id="task")
    lifecycle = EngagementLifecycleService(
        journal=_Journal(snapshot),
        planning=planning,
        closure_finalizer=_Finalizer(snapshot),
        lifecycle_commits=commits,
    )

    with pytest.raises(ValueError, match="canonical_revocation_required"):
        if action == "reopen":
            lifecycle.reopen(snapshot.engagement_id, lane=lane, reason="continue")
        else:
            lifecycle.reject_flag(
                snapshot.engagement_id,
                lane=lane,
                flag_event_id=UUID("22222222-2222-4222-8222-222222222222"),
                reason="replace proof",
            )

    assert planning.reasons == [action]
    assert commits.reopen_calls == 0
    assert commits.rejection_calls == 0
    assert commits.receipt_calls == 0
