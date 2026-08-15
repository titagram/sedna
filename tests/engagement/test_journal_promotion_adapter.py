"""Authoritative event and replay contracts for journal promotion."""

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest

import sedna.engagement.promotion.adapter as promotion_adapter
from sedna.engagement.events import (
    CasePromotedPayload,
    EventPayloadAdapter,
    PromotionAttemptTerminatedPayload,
    PromotionCandidateReadyPayload,
    PromotionIndexPendingPayload,
    PromotionIndexRetryFailedPayload,
    PromotionRequestedPayload,
    PromotionSemanticCommittedPayload,
    PromotionSourceCommittedPayload,
)
from sedna.engagement.models import JournalRevision
from sedna.engagement.promotion.adapter import (
    JournalPromotionAdapter,
    PromotionCommitCapability,
    PromotionResult,
)
from sedna.engagement.promotion.models import (
    PromotionClaimOwnership,
    PromotionClaimRequest,
    PromotionCleanupReceipt,
    PromotionIndexFailureReceipt,
    PromotionIndexPendingReceipt,
    PromotionPublicationReceipt,
    PromotionSecretInventory,
    PromotionSemanticReceipt,
)
from sedna.engagement.promotion.render import (
    PromotionRenderIdentity,
    promotion_source_id,
    render_promotion_source,
)
from sedna.engagement.repository import EngagementJournalRepository, RevisionConflictError
from sedna.engagement.service import EVENT_APPEND_OWNER_BY_TYPE

from .test_promotion_input import _build_verified_journal
from .test_promotion_render import _context, _draft

_ATTEMPT_ID = UUID("00000000-0000-4000-8000-000000000501")
_VERIFICATION_ID = UUID("00000000-0000-4000-8000-000000000502")
_SOURCE_ID = "source-00000000-0000-4000-8000-000000000503"
_DIGEST = sha256(b"promotion-stage").hexdigest()
_REVISION = JournalRevision(sequence=7, event_hash=sha256(b"revision").hexdigest())


def test_journal_promotion_adapter_exposes_closed_result_contract() -> None:
    assert JournalPromotionAdapter.__name__ == "JournalPromotionAdapter"
    result = PromotionResult(
        disposition="in_progress",
        attempt_id=UUID("00000000-0000-0000-0000-000000000001"),
        promotion_revision=1,
    )
    assert result.disposition == "in_progress"
    assert result.case_ids == ()


def test_adapter_returns_unchanged_for_matching_durable_publication() -> None:
    terminal = SimpleNamespace(
        verification_event_id=_VERIFICATION_ID,
        disposition="promoted",
        attempt_id=_ATTEMPT_ID,
        promotion_revision=2,
        source_id=_SOURCE_ID,
        case_ids=("case-1",),
    )
    snapshot = SimpleNamespace(
        revision=_REVISION,
        state=SimpleNamespace(promotion=SimpleNamespace(recent_terminal_attempts=(terminal,))),
    )
    adapter = object.__new__(JournalPromotionAdapter)
    cast(Any, adapter)._capability = SimpleNamespace(
        _writer=SimpleNamespace(load_snapshot=lambda _engagement_id: snapshot)
    )

    result = adapter.promote_verified(
        UUID("00000000-0000-4000-8000-000000000599"),
        expected_revision=JournalRevision(sequence=1, event_hash="0" * 64),
        verification_event_id=_VERIFICATION_ID,
    )

    assert result.disposition == "unchanged"
    assert result.attempt_id == _ATTEMPT_ID
    assert result.case_ids == ("case-1",)


@pytest.mark.parametrize(
    "stage",
    ("candidate_ready", "source_committed", "semantic_committed", "index_pending", "retry_failed"),
)
def test_owned_recovery_reloads_candidate_and_skips_promotion_compiler(monkeypatch, stage) -> None:
    engagement_id = _context().engagement_id
    attempt = SimpleNamespace(
        stage=stage,
        attempt_id=UUID("00000000-0000-4000-8000-000000000101"),
        promotion_revision=1,
    )
    ownership = PromotionClaimOwnership(
        attempt_id=attempt.attempt_id,
        claim_event_id=UUID("00000000-0000-4000-8000-000000000102"),
        _issuer_token=object(),
    )
    committed_source = object()
    loaded: list[object] = []
    adapter = object.__new__(JournalPromotionAdapter)
    cast(Any, adapter)._compiler = SimpleNamespace(
        compile=lambda *_args, **_kwargs: pytest.fail("compiler must not run during recovery")
    )
    cast(Any, adapter)._capability = SimpleNamespace(
        load_candidate=lambda recovered_id, *, ownership: (
            loaded.append((recovered_id, ownership)) or _draft()
        ),
        commit_source=lambda *_args, **_kwargs: SimpleNamespace(source=committed_source),
    )
    monkeypatch.setattr(
        JournalPromotionAdapter,
        "_publish_owned",
        lambda *_args, **_kwargs: PromotionResult(disposition="retrying"),
    )

    result = adapter._run_owned(
        engagement_id,
        _context().verified_revision,
        _context().verification_event_id,
        SimpleNamespace(
            safe_input=_context(),
            inventory=PromotionSecretInventory(),
        ),
        attempt,
        ownership,
        _REVISION,
    )

    assert result.disposition == "retrying"
    assert loaded == [(engagement_id, ownership)]


def test_adapter_rejects_stale_revision_before_projection_or_claim() -> None:
    snapshot = SimpleNamespace(
        revision=_REVISION,
        state=SimpleNamespace(promotion=SimpleNamespace(recent_terminal_attempts=())),
    )
    adapter = object.__new__(JournalPromotionAdapter)
    cast(Any, adapter)._capability = SimpleNamespace(
        _writer=SimpleNamespace(load_snapshot=lambda _engagement_id: snapshot)
    )

    with pytest.raises(ValueError, match="stale"):
        adapter.promote_verified(
            UUID("00000000-0000-4000-8000-000000000599"),
            expected_revision=JournalRevision(sequence=1, event_hash="0" * 64),
            verification_event_id=_VERIFICATION_ID,
        )


def _install_test_receipt_helpers(monkeypatch) -> None:
    """White-box journal tests may mint holder receipts without a knowledge service."""

    def helper(receipt_type):
        return lambda capability, **values: capability._receipt_ledger.issue(receipt_type, **values)

    for name, receipt_type in (
        ("_seal_semantic_receipt", PromotionSemanticReceipt),
        ("_seal_index_pending_receipt", PromotionIndexPendingReceipt),
        ("_seal_index_failure_receipt", PromotionIndexFailureReceipt),
        ("_seal_publication_receipt", PromotionPublicationReceipt),
    ):
        monkeypatch.setattr(PromotionCommitCapability, name, helper(receipt_type), raising=False)


def test_promotion_event_family_round_trips_and_has_sole_append_authority() -> None:
    payloads = (
        PromotionRequestedPayload(
            attempt_id=_ATTEMPT_ID,
            attempt_ordinal=1,
            promotion_revision=1,
            idempotency_key=_DIGEST,
            verified_revision=_REVISION,
            verification_event_id=_VERIFICATION_ID,
            compiler_version="1",
            extractor_prompt_version="1",
            critic_prompt_version="1",
            repair_prompt_version="1",
            renderer_version="1",
            semantic_compiler_version="1",
            semantic_prompt_versions=("1",),
            claim_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        ),
        PromotionCandidateReadyPayload(
            attempt_id=_ATTEMPT_ID,
            promotion_revision=1,
            candidate_relative_path="engagements/00000000-0000-4000-8000-000000000504/promotion/candidate-case-v1.json",
            candidate_sha256=_DIGEST,
            repair_count=0,
        ),
        PromotionSourceCommittedPayload(
            attempt_id=_ATTEMPT_ID,
            source_id=_SOURCE_ID,
            promotion_revision=1,
            source_relative_path="engagements/00000000-0000-4000-8000-000000000504/promotion/sources/promotion-v1.md",
            source_sha256=_DIGEST,
            provenance_relative_path="engagements/00000000-0000-4000-8000-000000000504/promotion/sources/promotion-v1.provenance.json",
            provenance_sha256=_DIGEST,
            verified_revision=_REVISION,
            verification_event_id=_VERIFICATION_ID,
        ),
        PromotionSemanticCommittedPayload(
            attempt_id=_ATTEMPT_ID,
            promotion_revision=1,
            source_id=_SOURCE_ID,
            foundation_manifest_sha256=_DIGEST,
            artifact_ids=("case-1",),
        ),
        PromotionIndexPendingPayload(
            attempt_id=_ATTEMPT_ID,
            promotion_revision=1,
            source_id=_SOURCE_ID,
            expected_canonical_revision=_DIGEST,
        ),
        PromotionIndexRetryFailedPayload(
            attempt_id=_ATTEMPT_ID,
            promotion_revision=1,
            retry_count=1,
            reason_code="index_rebuild_failed",
        ),
        CasePromotedPayload(
            attempt_id=_ATTEMPT_ID,
            source_id=_SOURCE_ID,
            promotion_revision=1,
            case_ids=("case-1",),
        ),
        PromotionAttemptTerminatedPayload(
            attempt_id=_ATTEMPT_ID,
            promotion_revision=1,
            disposition="failed",
            reason_code="semantic_failure",
        ),
    )

    for payload in payloads:
        assert EventPayloadAdapter.validate_json(payload.model_dump_json()) == payload
        assert EVENT_APPEND_OWNER_BY_TYPE[payload.kind] == "promotion_commit_capability"


def _claim_request(revision: JournalRevision, verification_event_id: UUID) -> PromotionClaimRequest:
    return PromotionClaimRequest(
        verified_revision=revision,
        verification_event_id=verification_event_id,
        compiler_version="1",
        extractor_prompt_version="1",
        critic_prompt_version="1",
        repair_prompt_version="1",
        renderer_version="1",
        semantic_compiler_version="9",
        semantic_prompt_versions=("2",),
    )


def _expected_claim_idempotency_key(
    engagement_id: UUID,
    request: PromotionClaimRequest,
    attempt_ordinal: int,
) -> str:
    identity = request.model_dump(mode="json", warnings="error")
    identity["engagement_id"] = str(engagement_id)
    identity["attempt_ordinal"] = attempt_ordinal
    canonical = json.dumps(
        identity,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def test_repository_authors_atomic_claim_and_coalesces_without_transferring_lease(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    first_holder = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    second_holder = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    request = _claim_request(verified.revision, verification_event_id)
    try:
        created = first_holder.claim(
            verified.engagement_id, request, expected_revision=verified.revision
        )
        coalesced = second_holder.claim(
            verified.engagement_id, request, expected_revision=verified.revision
        )
        snapshot = service.load_snapshot(verified.engagement_id)

        assert created.disposition == "created"
        assert created.ownership is not None
        assert coalesced.disposition == "existing"
        assert coalesced.ownership is None
        assert coalesced.attempt == created.attempt
        assert coalesced.claim_event_id == created.claim_event_id
        assert created.attempt.attempt_ordinal == 1
        assert created.attempt.promotion_revision == 1
        assert created.attempt.verified_revision == verified.revision
        assert created.attempt.verification_event_id == verification_event_id
        assert sum(event.type == "promotion_requested" for event in snapshot.events) == 1
    finally:
        manager.__exit__(None, None, None)


def test_fresh_repository_runtime_authenticates_matching_live_claim_for_recovery(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    request = _claim_request(verified.revision, verification_event_id)
    original = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    created = original.claim(verified.engagement_id, request, expected_revision=verified.revision)
    assert created.ownership is not None
    manager.__exit__(None, None, None)

    with EngagementJournalRepository(
        tmp_path / "knowledge", clock=fixed_clock
    ) as recovered_repository:
        recovered_capability = PromotionCommitCapability(
            recovered_repository._issue_promotion_journal_writer()
        )
        recovered = recovered_capability.claim(
            verified.engagement_id,
            request,
            expected_revision=created.revision,
        )

        assert recovered.disposition == "resumed"
        assert recovered.attempt == created.attempt
        assert recovered.claim_event_id == created.claim_event_id
        assert recovered.ownership is not None
        assert recovered.ownership.claim_event_id == created.claim_event_id


def test_fresh_repository_runtime_rejects_expired_persisted_claim_without_mutation(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    request = _claim_request(verified.revision, verification_event_id)
    original = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    created = original.claim(verified.engagement_id, request, expected_revision=verified.revision)
    assert created.attempt is not None
    expires_at = created.attempt.claim_expires_at
    assert expires_at is not None
    manager.__exit__(None, None, None)

    root = tmp_path / "knowledge"
    before = tuple(
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )
    with EngagementJournalRepository(
        root, clock=lambda: expires_at + timedelta(seconds=1)
    ) as recovered_repository:
        recovered_capability = PromotionCommitCapability(
            recovered_repository._issue_promotion_journal_writer()
        )
        with pytest.raises(ValueError, match="expired"):
            recovered_capability.claim(
                verified.engagement_id,
                request,
                expected_revision=created.revision,
            )

    after = tuple(
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )
    assert after == before


def test_parallel_repository_cannot_take_over_a_live_persisted_claim(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    request = _claim_request(verified.revision, verification_event_id)
    owner = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    created = owner.claim(verified.engagement_id, request, expected_revision=verified.revision)
    assert created.ownership is not None
    try:
        with EngagementJournalRepository(
            tmp_path / "knowledge", clock=fixed_clock
        ) as parallel_repository:
            parallel = PromotionCommitCapability(
                parallel_repository._issue_promotion_journal_writer()
            ).claim(
                verified.engagement_id,
                request,
                expected_revision=created.revision,
            )

            assert parallel.disposition == "existing"
            assert parallel.ownership is None
            assert parallel.attempt == created.attempt
    finally:
        manager.__exit__(None, None, None)


def test_cleanup_receipt_is_consumed_only_after_terminal_journal_cas_succeeds(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch,
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    capability = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    claim = capability.claim(
        verified.engagement_id,
        _claim_request(verified.revision, verification_event_id),
        expected_revision=verified.revision,
    )
    assert claim.attempt is not None and claim.ownership is not None
    receipt = capability._receipt_ledger.issue(
        PromotionCleanupReceipt,
        attempt_id=claim.attempt.attempt_id,
        promotion_revision=claim.attempt.promotion_revision,
        source_id=promotion_source_id(verified.engagement_id),
        canonical_revision="c" * 64,
    )
    calls = 0

    def terminate_once_then_succeed(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RevisionConflictError("forced terminal CAS failure")
        return SimpleNamespace(
            events=(SimpleNamespace(event_id=UUID(int=999)),),
            revision=JournalRevision(sequence=99, event_hash="d" * 64),
        )

    monkeypatch.setattr(service._repository, "_terminate_promotion", terminate_once_then_succeed)
    values = {
        "attempt_id": claim.attempt.attempt_id,
        "promotion_revision": claim.attempt.promotion_revision,
        "disposition": "failed",
        "reason_code": "index_retry_exhausted",
        "cleanup_receipt": receipt,
        "ownership": claim.ownership,
        "expected_revision": claim.revision,
    }
    try:
        with pytest.raises(RevisionConflictError, match="forced terminal CAS failure"):
            capability.terminate(verified.engagement_id, **values)

        mutation = capability.terminate(verified.engagement_id, **values)
        assert mutation.revision.sequence == 99
        assert calls == 2
        with pytest.raises(ValueError, match="invalid promotion cleanup receipt"):
            capability.terminate(verified.engagement_id, **values)
        assert calls == 2
    finally:
        manager.__exit__(None, None, None)


def test_fresh_runtime_reloads_event_bound_candidate_through_sealed_capability(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    request = _claim_request(verified.revision, verification_event_id)
    original = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    claim = original.claim(verified.engagement_id, request, expected_revision=verified.revision)
    assert claim.attempt is not None and claim.ownership is not None
    committed = original.commit_candidate(
        verified.engagement_id,
        attempt_id=claim.attempt.attempt_id,
        promotion_revision=claim.attempt.promotion_revision,
        draft=_draft(),
        repair_count=0,
        ownership=claim.ownership,
        expected_revision=claim.revision,
    )
    manager.__exit__(None, None, None)

    with EngagementJournalRepository(
        tmp_path / "knowledge", clock=fixed_clock
    ) as recovered_repository:
        recovered_capability = PromotionCommitCapability(
            recovered_repository._issue_promotion_journal_writer()
        )
        resumed = recovered_capability.claim(
            verified.engagement_id, request, expected_revision=committed.revision
        )

        assert resumed.ownership is not None
        assert (
            recovered_capability.load_candidate(verified.engagement_id, ownership=resumed.ownership)
            == _draft()
        )


def test_caller_cannot_forge_cleanup_proof_from_claim_token(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    capability = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    claim = capability.claim(
        verified.engagement_id,
        _claim_request(verified.revision, verification_event_id),
        expected_revision=verified.revision,
    )
    assert claim.attempt is not None and claim.ownership is not None
    forged = PromotionCleanupReceipt(
        attempt_id=claim.attempt.attempt_id,
        promotion_revision=claim.attempt.promotion_revision,
        source_id="promotion-forged",
        canonical_revision="f" * 64,
        _issuer_token=claim.ownership._issuer_token,
    )
    before = service.load_snapshot(verified.engagement_id)
    try:
        with pytest.raises(ValueError, match="cleanup receipt"):
            capability.terminate(
                verified.engagement_id,
                attempt_id=claim.attempt.attempt_id,
                promotion_revision=claim.attempt.promotion_revision,
                disposition="failed",
                reason_code="index_retry_exhausted",
                cleanup_receipt=forged,
                ownership=claim.ownership,
                expected_revision=claim.revision,
            )
        assert service.load_snapshot(verified.engagement_id) == before
    finally:
        manager.__exit__(None, None, None)


def test_candidate_commit_rejects_forged_and_cross_holder_ownership_without_mutation(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    owner = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    other = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    try:
        claim = owner.claim(
            verified.engagement_id,
            _claim_request(verified.revision, verification_event_id),
            expected_revision=verified.revision,
        )
        assert claim.ownership is not None
        before = service.load_snapshot(verified.engagement_id)
        forged = PromotionClaimOwnership(
            attempt_id=claim.attempt.attempt_id,
            claim_event_id=claim.claim_event_id,
            _issuer_token=object(),
        )

        for capability, ownership in ((owner, forged), (other, claim.ownership)):
            with pytest.raises(ValueError, match="ownership"):
                capability.commit_candidate(
                    verified.engagement_id,
                    attempt_id=claim.attempt.attempt_id,
                    promotion_revision=claim.attempt.promotion_revision,
                    draft=_draft(),
                    repair_count=0,
                    ownership=ownership,
                    expected_revision=before.revision,
                )
            assert service.load_snapshot(verified.engagement_id) == before
    finally:
        manager.__exit__(None, None, None)


def _assert_promotion_rejection_preserves_snapshot(service, engagement_id, before) -> None:
    after = service.load_snapshot(engagement_id)
    assert after.revision == before.revision
    assert tuple((event.sequence, event.event_id, event.type) for event in after.events) == tuple(
        (event.sequence, event.event_id, event.type) for event in before.events
    )
    assert after.state == before.state
    assert after == before


def test_second_capability_cannot_terminate_claim_holders_active_attempt(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    owner = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    other = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    try:
        claim = owner.claim(
            verified.engagement_id,
            _claim_request(verified.revision, verification_event_id),
            expected_revision=verified.revision,
        )
        assert claim.attempt is not None and claim.ownership is not None
        before = service.load_snapshot(verified.engagement_id)

        with pytest.raises(ValueError, match="ownership"):
            other.terminate(
                verified.engagement_id,
                attempt_id=claim.attempt.attempt_id,
                promotion_revision=claim.attempt.promotion_revision,
                disposition="failed",
                reason_code="transport_failure",
                cleanup_receipt=None,
                ownership=claim.ownership,
                expected_revision=claim.revision,
            )

        _assert_promotion_rejection_preserves_snapshot(service, verified.engagement_id, before)
    finally:
        manager.__exit__(None, None, None)


@pytest.mark.parametrize(
    "boundary",
    ("candidate", "source", "semantic", "canonical", "index", "publication", "terminal"),
)
def test_expired_claim_rejects_every_promotion_transition_before_mutation(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch,
    boundary,
) -> None:
    _install_test_receipt_helpers(monkeypatch)
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    repository = service._repository
    capability = PromotionCommitCapability(repository._issue_promotion_journal_writer())
    try:
        claim = capability.claim(
            verified.engagement_id,
            _claim_request(verified.revision, verification_event_id),
            expected_revision=verified.revision,
        )
        assert claim.attempt is not None and claim.ownership is not None
        attempt = claim.attempt
        revision = claim.revision
        context = _context().model_copy(
            update={
                "engagement_id": verified.engagement_id,
                "verified_revision": verified.revision,
                "verification_event_id": verification_event_id,
            }
        )
        rendered = render_promotion_source(
            _draft(),
            context=context,
            inventory=PromotionSecretInventory(),
            identity=PromotionRenderIdentity(
                engagement_id=verified.engagement_id,
                attempt_id=attempt.attempt_id,
                verification_event_id=verification_event_id,
                verified_revision=verified.revision,
                source_id=promotion_source_id(verified.engagement_id),
                promotion_revision=attempt.promotion_revision,
            ),
        )

        if boundary != "candidate":
            revision = capability.commit_candidate(
                verified.engagement_id,
                attempt_id=attempt.attempt_id,
                promotion_revision=attempt.promotion_revision,
                draft=_draft(),
                repair_count=0,
                ownership=claim.ownership,
                expected_revision=revision,
            ).revision
        if boundary not in {"candidate", "source", "terminal"}:
            revision = capability.commit_source(
                verified.engagement_id,
                rendered,
                ownership=claim.ownership,
                expected_revision=revision,
            ).source.committed_revision
        if boundary in {"canonical", "index", "publication"}:
            revision = capability.commit_semantic(
                verified.engagement_id,
                capability._seal_semantic_receipt(
                    attempt_id=attempt.attempt_id,
                    promotion_revision=attempt.promotion_revision,
                    source_id=rendered.source_id,
                    foundation_manifest_sha256="3" * 64,
                    artifact_ids=("case-1",),
                ),
                expected_revision=revision,
            ).revision
        if boundary in {"index", "publication"}:
            revision = capability.commit_index_pending(
                verified.engagement_id,
                capability._seal_index_pending_receipt(
                    attempt_id=attempt.attempt_id,
                    promotion_revision=attempt.promotion_revision,
                    source_id=rendered.source_id,
                    expected_canonical_revision="4" * 64,
                ),
                expected_revision=revision,
            ).revision

        before = service.load_snapshot(verified.engagement_id)
        expires_at = before.state.promotion.active_attempt.claim_expires_at
        assert expires_at is not None
        monkeypatch.setattr(repository, "_clock", lambda: expires_at + timedelta(seconds=1))

        with pytest.raises(ValueError, match="expired"):
            if boundary == "candidate":
                capability.commit_candidate(
                    verified.engagement_id,
                    attempt_id=attempt.attempt_id,
                    promotion_revision=attempt.promotion_revision,
                    draft=_draft(),
                    repair_count=0,
                    ownership=claim.ownership,
                    expected_revision=revision,
                )
            elif boundary == "source":
                capability.commit_source(
                    verified.engagement_id,
                    rendered,
                    ownership=claim.ownership,
                    expected_revision=revision,
                )
            elif boundary == "semantic":
                capability.commit_semantic(
                    verified.engagement_id,
                    capability._seal_semantic_receipt(
                        attempt_id=attempt.attempt_id,
                        promotion_revision=attempt.promotion_revision,
                        source_id=rendered.source_id,
                        foundation_manifest_sha256="3" * 64,
                        artifact_ids=("case-1",),
                    ),
                    expected_revision=revision,
                )
            elif boundary == "canonical":
                capability.commit_index_pending(
                    verified.engagement_id,
                    capability._seal_index_pending_receipt(
                        attempt_id=attempt.attempt_id,
                        promotion_revision=attempt.promotion_revision,
                        source_id=rendered.source_id,
                        expected_canonical_revision="4" * 64,
                    ),
                    expected_revision=revision,
                )
            elif boundary == "index":
                capability.commit_index_retry(
                    verified.engagement_id,
                    capability._seal_index_failure_receipt(
                        attempt_id=attempt.attempt_id,
                        promotion_revision=attempt.promotion_revision,
                        retry_count=1,
                        reason_code="index_rebuild_failed",
                    ),
                    expected_revision=revision,
                )
            elif boundary == "publication":
                capability.commit_promoted(
                    verified.engagement_id,
                    capability._seal_publication_receipt(
                        attempt_id=attempt.attempt_id,
                        promotion_revision=attempt.promotion_revision,
                        source_id=rendered.source_id,
                        case_ids=("case-1",),
                    ),
                    expected_revision=revision,
                )
            else:
                capability.terminate(
                    verified.engagement_id,
                    attempt_id=attempt.attempt_id,
                    promotion_revision=attempt.promotion_revision,
                    disposition="failed",
                    reason_code="transport_failure",
                    cleanup_receipt=None,
                    ownership=claim.ownership,
                    expected_revision=revision,
                )

        _assert_promotion_rejection_preserves_snapshot(service, verified.engagement_id, before)
    finally:
        manager.__exit__(None, None, None)


def test_claim_owner_commits_candidate_and_replay_coalesces_exact_artifact(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    capability = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    try:
        claim = capability.claim(
            verified.engagement_id,
            _claim_request(verified.revision, verification_event_id),
            expected_revision=verified.revision,
        )
        assert claim.ownership is not None
        created = capability.commit_candidate(
            verified.engagement_id,
            attempt_id=claim.attempt.attempt_id,
            promotion_revision=claim.attempt.promotion_revision,
            draft=_draft(),
            repair_count=0,
            ownership=claim.ownership,
            expected_revision=claim.revision,
        )
        replay = capability.commit_candidate(
            verified.engagement_id,
            attempt_id=claim.attempt.attempt_id,
            promotion_revision=claim.attempt.promotion_revision,
            draft=_draft(),
            repair_count=0,
            ownership=claim.ownership,
            expected_revision=claim.revision,
        )
        snapshot = service.load_snapshot(verified.engagement_id)
        assert created.created is True
        assert replay.created is False
        assert replay.event_id == created.event_id
        assert snapshot.state.promotion.active_attempt.stage == "candidate_ready"
        assert snapshot.state.promotion.active_attempt.candidate_relative_path.endswith(".json")
        assert sum(event.type == "promotion_candidate_ready" for event in snapshot.events) == 1
    finally:
        manager.__exit__(None, None, None)


@pytest.mark.parametrize(
    "fault_point",
    (
        "promotion_candidate_after_intent",
        "promotion_candidate_after_artifact",
        "promotion_candidate_before_event_append",
        "promotion_candidate_after_event_append",
    ),
)
def test_candidate_commit_recovers_every_declared_crash_boundary(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch,
    fault_point,
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    repository = service._repository
    capability = PromotionCommitCapability(repository._issue_promotion_journal_writer())
    try:
        claim = capability.claim(
            verified.engagement_id,
            _claim_request(verified.revision, verification_event_id),
            expected_revision=verified.revision,
        )
        assert claim.attempt is not None and claim.ownership is not None
        fired = False

        def crash(point: str) -> None:
            nonlocal fired
            if point == fault_point and not fired:
                fired = True
                raise RuntimeError("simulated candidate crash")

        monkeypatch.setattr(repository, "_fault", crash)
        with pytest.raises(RuntimeError, match="simulated candidate crash"):
            capability.commit_candidate(
                verified.engagement_id,
                attempt_id=claim.attempt.attempt_id,
                promotion_revision=claim.attempt.promotion_revision,
                draft=_draft(),
                repair_count=0,
                ownership=claim.ownership,
                expected_revision=claim.revision,
            )
        monkeypatch.setattr(repository, "_fault", lambda _point: None)

        recovered = capability.commit_candidate(
            verified.engagement_id,
            attempt_id=claim.attempt.attempt_id,
            promotion_revision=claim.attempt.promotion_revision,
            draft=_draft(),
            repair_count=0,
            ownership=claim.ownership,
            expected_revision=claim.revision,
        )
        loaded = service.load_snapshot(verified.engagement_id)
        assert recovered.created is (fault_point != "promotion_candidate_after_event_append")
        assert sum(item.type == "promotion_candidate_ready" for item in loaded.events) == 1
        intent = (
            tmp_path
            / "knowledge"
            / "engagements"
            / str(verified.engagement_id)
            / "promotion"
            / "candidates"
            / ".candidate-transaction.json"
        )
        assert not intent.exists()
    finally:
        manager.__exit__(None, None, None)


def test_three_terminal_attempts_exhaust_retry_without_fourth_claim_mutation(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    capability = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    request = _claim_request(verified.revision, verification_event_id)
    revision = verified.revision
    try:
        for ordinal in range(1, 4):
            claim = capability.claim(verified.engagement_id, request, expected_revision=revision)
            assert claim.attempt.attempt_ordinal == ordinal
            terminated = capability.terminate(
                verified.engagement_id,
                attempt_id=claim.attempt.attempt_id,
                promotion_revision=claim.attempt.promotion_revision,
                disposition="failed",
                reason_code="transport_failure",
                cleanup_receipt=None,
                ownership=claim.ownership,
                expected_revision=claim.revision,
            )
            revision = terminated.revision

        before = service.load_snapshot(verified.engagement_id)
        exhausted = capability.claim(verified.engagement_id, request, expected_revision=revision)
        assert exhausted.disposition == "retry_exhausted"
        assert exhausted.attempt is None
        assert service.load_snapshot(verified.engagement_id) == before
    finally:
        manager.__exit__(None, None, None)


def test_claim_idempotency_identity_coalesces_one_attempt_and_distinguishes_retries(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    capability = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    observer = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    request = _claim_request(verified.revision, verification_event_id)
    revision = verified.revision
    keys: list[str] = []
    try:
        for ordinal in range(1, 4):
            claim = capability.claim(verified.engagement_id, request, expected_revision=revision)
            coalesced = observer.claim(
                verified.engagement_id,
                request,
                expected_revision=revision,
            )

            assert claim.attempt is not None and claim.ownership is not None
            assert claim.attempt.attempt_ordinal == ordinal
            assert claim.attempt.promotion_revision == ordinal
            assert coalesced.attempt == claim.attempt
            assert coalesced.claim_event_id == claim.claim_event_id
            assert claim.attempt.idempotency_key == _expected_claim_idempotency_key(
                verified.engagement_id,
                request,
                ordinal,
            )
            keys.append(claim.attempt.idempotency_key)

            terminated = capability.terminate(
                verified.engagement_id,
                attempt_id=claim.attempt.attempt_id,
                promotion_revision=claim.attempt.promotion_revision,
                disposition="failed",
                reason_code="transport_failure",
                cleanup_receipt=None,
                ownership=claim.ownership,
                expected_revision=claim.revision,
            )
            revision = terminated.revision

        assert len(set(keys)) == 3
    finally:
        manager.__exit__(None, None, None)


def test_publication_transitions_require_same_holder_sealed_receipts_without_mutation(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory, monkeypatch
) -> None:
    _install_test_receipt_helpers(monkeypatch)
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    capability = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    other = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    try:
        claim = capability.claim(
            verified.engagement_id,
            _claim_request(verified.revision, verification_event_id),
            expected_revision=verified.revision,
        )
        assert claim.attempt is not None and claim.ownership is not None
        candidate = capability.commit_candidate(
            verified.engagement_id,
            attempt_id=claim.attempt.attempt_id,
            promotion_revision=claim.attempt.promotion_revision,
            draft=_draft(),
            repair_count=0,
            ownership=claim.ownership,
            expected_revision=claim.revision,
        )
        context = _context().model_copy(
            update={
                "engagement_id": verified.engagement_id,
                "verified_revision": verified.revision,
                "verification_event_id": verification_event_id,
            }
        )
        rendered = render_promotion_source(
            _draft(),
            context=context,
            inventory=PromotionSecretInventory(),
            identity=PromotionRenderIdentity(
                engagement_id=verified.engagement_id,
                attempt_id=claim.attempt.attempt_id,
                verification_event_id=verification_event_id,
                verified_revision=verified.revision,
                source_id=promotion_source_id(verified.engagement_id),
                promotion_revision=claim.attempt.promotion_revision,
            ),
        )
        source = capability.commit_source(
            verified.engagement_id,
            rendered,
            ownership=claim.ownership,
            expected_revision=candidate.revision,
        )
        forged = PromotionSemanticReceipt(
            attempt_id=claim.attempt.attempt_id,
            promotion_revision=claim.attempt.promotion_revision,
            source_id=rendered.source_id,
            foundation_manifest_sha256="3" * 64,
            artifact_ids=("case-1",),
            _issuer_token=object(),
        )
        before = service.load_snapshot(verified.engagement_id)
        for holder in (capability, other):
            with pytest.raises(ValueError, match="receipt"):
                holder.commit_semantic(
                    verified.engagement_id,
                    forged,
                    expected_revision=source.source.committed_revision,
                )
            assert service.load_snapshot(verified.engagement_id) == before

        semantic_receipt = capability._seal_semantic_receipt(
            attempt_id=claim.attempt.attempt_id,
            promotion_revision=claim.attempt.promotion_revision,
            source_id=rendered.source_id,
            foundation_manifest_sha256="3" * 64,
            artifact_ids=("case-1",),
        )
        semantic = capability.commit_semantic(
            verified.engagement_id,
            semantic_receipt,
            expected_revision=source.source.committed_revision,
        )
        pending_receipt = capability._seal_index_pending_receipt(
            attempt_id=claim.attempt.attempt_id,
            promotion_revision=claim.attempt.promotion_revision,
            source_id=rendered.source_id,
            expected_canonical_revision="4" * 64,
        )
        pending = capability.commit_index_pending(
            verified.engagement_id, pending_receipt, expected_revision=semantic.revision
        )
        failure_receipt = capability._seal_index_failure_receipt(
            attempt_id=claim.attempt.attempt_id,
            promotion_revision=claim.attempt.promotion_revision,
            retry_count=1,
            reason_code="index_rebuild_failed",
        )
        retry = capability.commit_index_retry(
            verified.engagement_id, failure_receipt, expected_revision=pending.revision
        )
        publication_receipt = capability._seal_publication_receipt(
            attempt_id=claim.attempt.attempt_id,
            promotion_revision=claim.attempt.promotion_revision,
            source_id=rendered.source_id,
            case_ids=("case-1",),
        )
        promoted = capability.commit_promoted(
            verified.engagement_id, publication_receipt, expected_revision=retry.revision
        )
        assert promoted.revision.sequence == verified.revision.sequence + 7
        assert service.load_snapshot(verified.engagement_id).state.promotion.active_attempt is None
    finally:
        manager.__exit__(None, None, None)


def test_capability_exposes_no_caller_value_receipt_minting_helpers(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    manager, service, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    capability = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    try:
        assert not any(
            hasattr(capability, name)
            for name in (
                "_seal_semantic_receipt",
                "_seal_index_pending_receipt",
                "_seal_index_failure_receipt",
                "_seal_publication_receipt",
            )
        )
    finally:
        manager.__exit__(None, None, None)


def test_capability_holder_has_no_public_receipt_service_or_issuer_constructor(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    manager, service, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    capability = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    try:
        assert not hasattr(capability, "receipt_service")
        assert not hasattr(promotion_adapter, "PromotionReceiptService")
    finally:
        manager.__exit__(None, None, None)
