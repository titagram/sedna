from __future__ import annotations

import inspect
from types import SimpleNamespace
from uuid import UUID

from sedna.engagement.lifecycle import (
    EngagementLifecycleService,
    PromotionRecoveryPort,
    PromotionRevocationPort,
)
from sedna.engagement.models import ExecutionLaneKey, HostKind, JournalRevision
from sedna.engagement.promotion.adapter import PromotionRecoveryCoordinator


def test_lifecycle_promotion_ports_are_dependency_neutral_and_exact() -> None:
    assert list(inspect.signature(PromotionRecoveryPort.recover_after_verification).parameters) == [
        "self",
        "engagement_id",
    ]
    assert list(inspect.signature(PromotionRevocationPort.revoke_after_settlement).parameters) == [
        "self",
        "engagement_id",
        "lane",
        "expected_revision",
        "operation",
        "reason",
        "proof_rejection",
    ]


def test_revocation_port_keeps_settlement_contract_keyword_only_and_typed() -> None:
    signature = inspect.signature(PromotionRevocationPort.revoke_after_settlement)
    parameters = list(signature.parameters.values())
    assert all(item.kind is inspect.Parameter.KEYWORD_ONLY for item in parameters[2:])
    assert parameters[2].annotation == "ExecutionLaneKey"
    assert parameters[3].annotation == "JournalRevision"
    assert parameters[4].annotation == "Literal['reject', 'reopen']"
    assert parameters[5].annotation == "Annotated[str, Field(min_length=1, max_length=2048)]"
    assert parameters[6].annotation == "SettledProofRejectionReceipt | None"
    assert parameters[6].default is None
    assert signature.return_annotation == "EngagementMutationResult"
    recovery = inspect.signature(PromotionRecoveryPort.recover_after_verification)
    assert recovery.return_annotation == "None"


def test_recovery_coordinator_closes_adapter_failure_without_losing_verified_state() -> None:
    revision = JournalRevision(sequence=7, event_hash="a" * 64)
    event = SimpleNamespace(
        type="engagement_verified",
        event_id=UUID("00000000-0000-4000-8000-000000000007"),
        sequence=revision.sequence,
        event_hash=revision.event_hash,
    )
    snapshot = SimpleNamespace(
        revision=revision,
        events=(event,),
        state=SimpleNamespace(promotion=SimpleNamespace(active_attempt=None)),
    )

    class FailingAdapter:
        def promote_verified(self, *args, **kwargs):
            raise RuntimeError("host unavailable")

    coordinator = PromotionRecoveryCoordinator(
        journal=SimpleNamespace(load_snapshot=lambda _engagement_id: snapshot),
        adapter=FailingAdapter(),  # type: ignore[arg-type]
    )

    result = coordinator.resume_for_engagement(UUID("00000000-0000-4000-8000-000000000008"))

    assert result.disposition == "failed"
    assert result.reason_code == "promotion_recovery_failed"
    assert result.journal_revision == revision


def test_recovery_retry_uses_current_cas_and_preserves_verified_revision() -> None:
    verified_revision = JournalRevision(sequence=7, event_hash="a" * 64)
    current_revision = JournalRevision(sequence=9, event_hash="c" * 64)
    event = SimpleNamespace(
        type="engagement_verified",
        event_id=UUID("00000000-0000-4000-8000-000000000007"),
        sequence=verified_revision.sequence,
        event_hash=verified_revision.event_hash,
    )
    snapshot = SimpleNamespace(
        revision=current_revision,
        events=(event,),
        state=SimpleNamespace(promotion=SimpleNamespace(active_attempt=None)),
    )
    calls = []

    class Adapter:
        def promote_verified(self, engagement_id, **kwargs):
            calls.append((engagement_id, kwargs))
            return SimpleNamespace(disposition="promoted")

    engagement_id = UUID("00000000-0000-4000-8000-000000000008")
    result = PromotionRecoveryCoordinator(
        journal=SimpleNamespace(load_snapshot=lambda _engagement_id: snapshot),
        adapter=Adapter(),  # type: ignore[arg-type]
    ).resume_for_engagement(engagement_id)

    assert result.disposition == "promoted"
    assert calls == [
        (
            engagement_id,
            {
                "expected_revision": current_revision,
                "verification_event_id": event.event_id,
                "verified_revision": verified_revision,
            },
        )
    ]


def test_recovery_coordinator_finishes_durable_revocation_intent_instead_of_promoting() -> None:
    engagement_id = UUID("00000000-0000-4000-8000-000000000008")
    verification_id = UUID("00000000-0000-4000-8000-000000000007")
    request_id = UUID("00000000-0000-4000-8000-000000000009")
    revision = JournalRevision(sequence=8, event_hash="b" * 64)
    lane = ExecutionLaneKey(host_kind=HostKind.HADES, session_id="session", task_id="task")
    intent = SimpleNamespace(operation="reopen", lane=lane, reopen_reason="correct evidence")
    verification = SimpleNamespace(
        type="engagement_verified",
        event_id=verification_id,
        sequence=7,
        event_hash="a" * 64,
    )
    request = SimpleNamespace(
        type="promotion_attempt_cancellation_requested",
        event_id=request_id,
        payload=SimpleNamespace(lifecycle_intent=intent),
    )
    active = SimpleNamespace(
        stage="cancellation_requested",
        attempt_id=UUID("00000000-0000-4000-8000-000000000006"),
        promotion_revision=1,
        source_id=None,
        verification_event_id=verification_id,
        verified_revision=JournalRevision(sequence=7, event_hash="a" * 64),
        cancellation_request_event_id=request_id,
        revocation_request_event_id=None,
    )
    snapshot = SimpleNamespace(
        revision=revision,
        events=(verification, request),
        state=SimpleNamespace(promotion=SimpleNamespace(active_attempt=active)),
    )
    calls = []

    class RevokingAdapter:
        def revoke_after_settlement(self, *args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(
                snapshot=SimpleNamespace(revision=JournalRevision(sequence=10, event_hash="c" * 64))
            )

        def promote_verified(self, *args, **kwargs):
            raise AssertionError("durable revocation recovery must not restart promotion")

    coordinator = PromotionRecoveryCoordinator(
        journal=SimpleNamespace(load_snapshot=lambda _engagement_id: snapshot),
        adapter=RevokingAdapter(),  # type: ignore[arg-type]
    )

    result = coordinator.resume_for_engagement(engagement_id)

    assert result.disposition == "revoked"
    assert result.journal_revision.sequence == 10
    assert calls == [
        (
            (engagement_id,),
            {
                "lane": lane,
                "expected_revision": revision,
                "operation": "reopen",
                "reason": "correct evidence",
                "proof_rejection": None,
            },
        )
    ]


def test_verified_reopen_routes_through_revocation_port_after_settlement() -> None:
    engagement_id = UUID("00000000-0000-4000-8000-000000000008")
    revision = JournalRevision(sequence=7, event_hash="a" * 64)
    snapshot = SimpleNamespace(
        revision=revision,
        state=SimpleNamespace(status=SimpleNamespace(value="closed_verified")),
    )
    expected = object()
    calls = []

    class Revocation:
        def revoke_after_settlement(self, *args, **kwargs):
            calls.append((args, kwargs))
            return expected

    service = EngagementLifecycleService(
        journal=SimpleNamespace(load_snapshot=lambda _engagement_id: snapshot),
        planning=SimpleNamespace(
            settle_pending_evidence=lambda _engagement_id, reason: SimpleNamespace(status="settled")
        ),
        closure_finalizer=object(),
        lifecycle_commits=object(),
        promotion_revocation=Revocation(),  # type: ignore[arg-type]
    )
    lane = ExecutionLaneKey(host_kind=HostKind.HADES, session_id="session", task_id="task")

    result = service.reopen(engagement_id, lane=lane, reason="correct evidence")

    assert result is expected
    assert calls == [
        (
            (engagement_id,),
            {
                "lane": lane,
                "expected_revision": revision,
                "operation": "reopen",
                "reason": "correct evidence",
                "proof_rejection": None,
            },
        )
    ]
