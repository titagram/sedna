"""Replayable CAS publication saga for verified journal promotion."""

from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import UTC, datetime
from hashlib import sha256
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest

from sedna.engagement.events import PromotionAttemptTerminatedPayload, PromotionRequestedPayload
from sedna.engagement.models import JournalRevision
from sedna.engagement.promotion.adapter import JournalPromotionAdapter
from sedna.engagement.promotion.models import (
    PromotionCleanupReceipt,
    PromotionIndexFailureReceipt,
)
from sedna.engagement.reducer import EngagementReplayError, _Accumulator
from sedna.engagement.repository import EngagementJournalRepository, RevisionConflictError

from .test_journal_promotion_adapter import _install_test_receipt_helpers
from .test_promotion_recovery import _created_repository, _rendered


def test_publication_saga_replays_retry_lineage_to_exactly_one_terminal_case(
    tmp_path, manifest, lane, initial_drafts, fixed_clock, monkeypatch
) -> None:
    _install_test_receipt_helpers(monkeypatch)
    root = tmp_path / "knowledge"
    rendered = _rendered()
    repository, snapshot, capability, ownership = _created_repository(
        root, manifest, lane, initial_drafts, fixed_clock
    )
    with repository:
        source = capability.commit_source(
            manifest.engagement_id,
            rendered,
            ownership=ownership,
            expected_revision=snapshot.revision,
        )
        semantic = capability.commit_semantic(
            manifest.engagement_id,
            capability._seal_semantic_receipt(
                attempt_id=rendered.provenance.attempt_id,
                promotion_revision=1,
                source_id=rendered.source_id,
                foundation_manifest_sha256="3" * 64,
                artifact_ids=("case-1",),
            ),
            expected_revision=source.source.committed_revision,
        )
        pending = capability.commit_index_pending(
            manifest.engagement_id,
            capability._seal_index_pending_receipt(
                attempt_id=rendered.provenance.attempt_id,
                promotion_revision=1,
                source_id=rendered.source_id,
                expected_canonical_revision="4" * 64,
            ),
            expected_revision=semantic.revision,
        )
        retry = capability.commit_index_retry(
            manifest.engagement_id,
            capability._seal_index_failure_receipt(
                attempt_id=rendered.provenance.attempt_id,
                promotion_revision=1,
                retry_count=1,
                reason_code="index_rebuild_failed",
            ),
            expected_revision=pending.revision,
        )
        promoted = capability.commit_promoted(
            manifest.engagement_id,
            capability._seal_publication_receipt(
                attempt_id=rendered.provenance.attempt_id,
                source_id=rendered.source_id,
                promotion_revision=1,
                case_ids=("case-1",),
            ),
            expected_revision=retry.revision,
        )
        state = capability.load_state(manifest.engagement_id)

        assert promoted.revision.sequence == snapshot.revision.sequence + 5
        assert state.active_attempt is None
        assert state.recent_terminal_attempts[-1].stage == "promoted"
        assert state.recent_terminal_attempts[-1].index_retry_count == 1
        assert state.folded_terminal_count == 0
        assert state.folded_terminal_sha256 is None
        assert state.latest_successful_publication.source_id == rendered.source_id
        assert state.latest_successful_publication.case_ids == ("case-1",)

    with EngagementJournalRepository(root) as reopened:
        replayed = reopened.load_snapshot(manifest.engagement_id).state.promotion
        assert replayed == state
        assert (
            sum(
                event.type == "case_promoted"
                for event in reopened.load_snapshot(manifest.engagement_id).events
            )
            == 1
        )


def test_publication_saga_rejects_stale_cas_and_out_of_order_stage_without_mutation(
    tmp_path, manifest, lane, initial_drafts, fixed_clock, monkeypatch
) -> None:
    _install_test_receipt_helpers(monkeypatch)
    root = tmp_path / "knowledge"
    rendered = _rendered()
    repository, snapshot, capability, ownership = _created_repository(
        root, manifest, lane, initial_drafts, fixed_clock
    )
    premature = capability._seal_index_pending_receipt(
        attempt_id=rendered.provenance.attempt_id,
        promotion_revision=1,
        source_id=rendered.source_id,
        expected_canonical_revision="4" * 64,
    )
    with repository:
        with pytest.raises(EngagementReplayError, match="out of order"):
            capability.commit_index_pending(
                manifest.engagement_id,
                premature,
                expected_revision=snapshot.revision,
            )
        assert repository.load_snapshot(manifest.engagement_id).revision == snapshot.revision

        source = capability.commit_source(
            manifest.engagement_id,
            rendered,
            ownership=ownership,
            expected_revision=snapshot.revision,
        )
        with pytest.raises(RevisionConflictError, match="stale"):
            capability.commit_semantic(
                manifest.engagement_id,
                capability._seal_semantic_receipt(
                    attempt_id=rendered.provenance.attempt_id,
                    promotion_revision=1,
                    source_id=rendered.source_id,
                    foundation_manifest_sha256="3" * 64,
                    artifact_ids=("case-1",),
                ),
                expected_revision=snapshot.revision,
            )
        assert (
            repository.load_snapshot(manifest.engagement_id).revision
            == source.source.committed_revision
        )


def test_third_index_failure_is_durably_recorded_then_terminates_without_fourth_retry(
    tmp_path, manifest, lane, initial_drafts, fixed_clock
) -> None:
    root = tmp_path / "knowledge"
    rendered = _rendered()
    repository, snapshot, real_capability, ownership = _created_repository(
        root, manifest, lane, initial_drafts, fixed_clock
    )
    with repository:
        committed = real_capability.commit_source(
            manifest.engagement_id,
            rendered,
            ownership=ownership,
            expected_revision=snapshot.revision,
        ).source

    retry_revision = JournalRevision(sequence=10, event_hash="a" * 64)
    terminal_revision = JournalRevision(sequence=11, event_hash="b" * 64)
    attempt = SimpleNamespace(
        attempt_id=ownership.attempt_id,
        promotion_revision=1,
        stage="retry_failed",
        index_retry_count=2,
        artifact_ids=("case-1",),
    )
    failure = PromotionIndexFailureReceipt(
        attempt_id=ownership.attempt_id,
        promotion_revision=1,
        retry_count=3,
        reason_code="index_rebuild_failed",
        _issuer_token=object(),
    )
    calls: list[tuple[str, object]] = []

    class Capability:
        def active_attempt(self, *_args, **_kwargs):
            return attempt, committed.committed_revision

        def commit_index_retry(self, _engagement_id, receipt, *, expected_revision):
            calls.append(("retry", (receipt.retry_count, expected_revision)))
            return SimpleNamespace(revision=retry_revision)

        def terminate(self, _engagement_id, **values):
            calls.append(("terminate", values))
            return SimpleNamespace(revision=terminal_revision)

    cleanup = PromotionCleanupReceipt(
        attempt_id=ownership.attempt_id,
        promotion_revision=1,
        source_id=rendered.source_id,
        canonical_revision="c" * 64,
        _issuer_token=object(),
    )
    adapter = object.__new__(JournalPromotionAdapter)
    cast(Any, adapter)._capability = Capability()
    cast(Any, adapter)._receipts = SimpleNamespace(
        recover_semantic=lambda *_args, **_kwargs: object(),
        canonical_currentness=lambda *_args, **_kwargs: object(),
        rebuild_index=lambda *_args, **_kwargs: (object(), failure),
        exclude_foundation=lambda *_args, **_kwargs: cleanup,
    )
    cast(Any, adapter)._semantic = SimpleNamespace(
        _repository=SimpleNamespace(promotion_publication_guard=lambda _source_id: nullcontext())
    )

    result = adapter._publish_owned(
        manifest.engagement_id,
        SimpleNamespace(inventory=object()),
        attempt,
        ownership,
        committed,
    )

    assert result.disposition == "failed"
    assert result.reason_code == "index_retry_exhausted"
    assert calls[0] == ("retry", (3, committed.committed_revision))
    assert calls[1][0] == "terminate"
    assert cast(dict, calls[1][1])["expected_revision"] == retry_revision
    assert len(calls) == 2


def test_recovered_third_index_failure_terminates_without_another_rebuild(monkeypatch) -> None:
    revision = JournalRevision(sequence=10, event_hash="a" * 64)
    terminal_revision = JournalRevision(sequence=11, event_hash="b" * 64)
    ownership = SimpleNamespace(attempt_id=UUID(int=101), claim_event_id=UUID(int=102))
    attempt = SimpleNamespace(
        attempt_id=ownership.attempt_id,
        promotion_revision=1,
        stage="retry_failed",
        index_retry_count=3,
        artifact_ids=("case-1",),
    )
    committed = SimpleNamespace(
        source_id="source-00000000-0000-4000-8000-000000000101",
    )
    cleanup = PromotionCleanupReceipt(
        attempt_id=ownership.attempt_id,
        promotion_revision=1,
        source_id=committed.source_id,
        canonical_revision="c" * 64,
        _issuer_token=object(),
    )
    calls: list[str] = []

    class Capability:
        def active_attempt(self, *_args, **_kwargs):
            return attempt, revision

        def terminate(self, _engagement_id, **values):
            calls.append("terminate")
            assert values["expected_revision"] == revision
            return SimpleNamespace(revision=terminal_revision)

    adapter = object.__new__(JournalPromotionAdapter)
    cast(Any, adapter)._capability = Capability()
    cast(Any, adapter)._receipts = SimpleNamespace(
        recover_semantic=lambda *_args, **_kwargs: pytest.fail(
            "terminal recovery must not require accepted semantics"
        ),
        rebuild_index=lambda *_args, **_kwargs: pytest.fail(
            "a recovered durable retry count of three must not rebuild again"
        ),
        exclude_foundation=lambda *_args, **_kwargs: calls.append("cleanup") or cleanup,
    )
    cast(Any, adapter)._semantic = SimpleNamespace(
        _repository=SimpleNamespace(promotion_publication_guard=lambda _source_id: nullcontext())
    )
    monkeypatch.setattr(
        "sedna.engagement.promotion.adapter.build_promotion_prepared_source",
        lambda _source: SimpleNamespace(manifest=SimpleNamespace(source_id=committed.source_id)),
    )

    result = adapter._publish_owned(
        UUID(int=100),
        SimpleNamespace(inventory=object()),
        attempt,
        cast(Any, ownership),
        cast(Any, committed),
    )

    assert result.disposition == "failed"
    assert result.reason_code == "index_retry_exhausted"
    assert calls == ["cleanup", "terminate"]


def test_promotion_replay_folds_terminal_history_to_a_fixed_bound(
    tmp_path, manifest, lane, initial_drafts, fixed_clock
) -> None:
    repository = EngagementJournalRepository(tmp_path / "knowledge", clock=fixed_clock)
    snapshot = repository.create(manifest, initial_drafts(manifest, lane))
    revision = snapshot.revision
    folded_sha256 = None
    with repository:
        for ordinal in range(1, 71):
            before = repository.load_snapshot(manifest.engagement_id).state.promotion
            if len(before.recent_terminal_attempts) == 64:
                evicted = before.recent_terminal_attempts[0]
                terminal_record = json.dumps(
                    evicted.model_dump(mode="json", warnings="error"),
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
                previous = (folded_sha256 or "").encode("ascii")
                folded_sha256 = sha256(previous + b"\x00" + terminal_record).hexdigest()
            attempt_id = UUID(int=10_000 + ordinal)
            verification_ordinal = (ordinal - 1) // 3
            requested = repository._append_promotion_event(
                manifest.engagement_id,
                PromotionRequestedPayload(
                    attempt_id=attempt_id,
                    attempt_ordinal=((ordinal - 1) % 3) + 1,
                    promotion_revision=ordinal,
                    idempotency_key=f"{ordinal:064x}",
                    verified_revision=snapshot.revision,
                    verification_event_id=UUID(int=20_000 + verification_ordinal),
                    compiler_version="1",
                    extractor_prompt_version="1",
                    critic_prompt_version="1",
                    repair_prompt_version="1",
                    renderer_version="1",
                    semantic_compiler_version="1",
                    semantic_prompt_versions=("1",),
                    claim_expires_at=datetime(2026, 8, 15, tzinfo=UTC),
                ),
                expected_revision=revision,
            )
            terminated = repository._append_promotion_event(
                manifest.engagement_id,
                PromotionAttemptTerminatedPayload(
                    attempt_id=attempt_id,
                    promotion_revision=ordinal,
                    disposition="failed",
                    reason_code="transport_failure",
                ),
                expected_revision=requested.revision,
            )
            revision = terminated.revision

        promotion = repository.load_snapshot(manifest.engagement_id).state.promotion
        assert len(promotion.recent_terminal_attempts) == 64
        assert promotion.recent_terminal_attempts[0].promotion_revision == 7
        assert promotion.folded_terminal_count == 6
        assert promotion.folded_terminal_sha256 == folded_sha256

    with EngagementJournalRepository(tmp_path / "knowledge") as reopened:
        assert reopened.load_snapshot(manifest.engagement_id).state.promotion == promotion


def test_promotion_replay_uses_only_a_bounded_current_verification_ordinal_index() -> None:
    fields = _Accumulator.__dataclass_fields__

    assert "promotion_attempt_ordinal_by_verification" not in fields
    assert "promotion_verification_event_id" in fields
    assert "promotion_attempt_ordinal" in fields
