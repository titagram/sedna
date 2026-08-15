"""Physical currentness and case-required acceptance for journal promotions."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from sedna.engagement import ExecutionLaneKey, HostKind
from sedna.engagement.promotion.adapter import PromotionCommitCapability
from sedna.engagement.promotion.models import (
    MAX_PROMOTION_PROVENANCE_BYTES,
    PromotionIndexFailureReceipt,
    PromotionPublicationReceipt,
)
from sedna.engagement.promotion.source import build_nonaccepted_promotion_manifest
from sedna.knowledge.retrieval import (
    AuthorizationScope,
    AuthorizationState,
    RetrievalMaintenanceService,
    ValidatedTarget,
)
from sedna.knowledge.retrieval.sqlite import SQLiteRetrievalIndex
from sedna.knowledge.semantic import SemanticAcceptanceProfile
from tests.engagement.test_journal_promotion_adapter import _claim_request
from tests.engagement.test_promotion_input import _build_verified_journal

from .test_semantic_service import (
    SOURCE_CASES,
    _journal_promotion_prepared,
    _load_responses,
    _prepared_case,
    _service,
)


def _journal_prerequisites():
    next_value = 1

    def uuid_factory() -> UUID:
        nonlocal next_value
        value = UUID(f"00000000-0000-4000-8000-{next_value:012d}")
        next_value += 1
        return value

    scope = AuthorizationScope(
        state=AuthorizationState.AUTHORIZED,
        exact_targets=(ValidatedTarget.parse("192.0.2.44"),),
    )
    lane = ExecutionLaneKey(
        host_kind=HostKind.HADES,
        session_id="session-orion",
        task_id="task-root",
    )
    fixed_time = datetime(2026, 8, 11, 12, 30, tzinfo=UTC)
    return scope, lane, lambda: fixed_time, uuid_factory


def test_journal_promotion_currentness_rehashes_physical_source(tmp_path: Path) -> None:
    with _prepared_case(tmp_path, "reference") as (pipeline, prepared, _path, _raw):
        promoted = _journal_promotion_prepared(pipeline.repository, prepared)
        pipeline.repository.transition_source(promoted.manifest, None)
        service, _host = _service(
            pipeline.repository,
            _load_responses(SOURCE_CASES["hybrid"].fixture_name),
        )
        assert (
            service.compile_and_store(
                promoted,
                acceptance_profile=SemanticAcceptanceProfile.JOURNAL_PROMOTION,
            ).disposition
            == "verified"
        )
        assert service.is_current(promoted)

        (pipeline.repository.root / promoted.manifest.path).write_bytes(b"changed bytes")

        assert not service.is_current(promoted)
        with pytest.raises(Exception, match="physical"):
            pipeline.repository.semantic_bundle_snapshot()


def test_journal_promotion_rejects_reference_only_semantics_and_invalidates_reuse(
    tmp_path: Path,
) -> None:
    with _prepared_case(tmp_path, "reference") as (pipeline, prepared, _path, _raw):
        promoted = _journal_promotion_prepared(pipeline.repository, prepared)
        pipeline.repository.transition_source(promoted.manifest, None)
        service, host = _service(
            pipeline.repository,
            _load_responses(SOURCE_CASES["reference"].fixture_name) * 2,
        )

        failed = service.compile_and_store(
            promoted,
            acceptance_profile=SemanticAcceptanceProfile.JOURNAL_PROMOTION,
        )

        assert failed.disposition == "failed"
        assert failed.failure_code == "required_case_missing"
        assert len(host.calls) == 2
        with pytest.raises(FileNotFoundError):
            pipeline.repository.load_semantic_bundle(promoted.manifest.source_id)

        retry = service.compile_and_store(
            promoted,
            acceptance_profile=SemanticAcceptanceProfile.JOURNAL_PROMOTION,
        )
        assert retry.failure_code == "required_case_missing"
        assert len(host.calls) == 4


def test_journal_promotion_manifest_lineage_rejects_collision_reuse_and_rollback(
    tmp_path: Path,
) -> None:
    with _prepared_case(tmp_path, "reference") as (pipeline, prepared, _path, _raw):
        promoted_v1 = _journal_promotion_prepared(pipeline.repository, prepared)
        legacy = prepared.manifest.model_copy(
            update={
                "source_id": promoted_v1.manifest.source_id,
                "source_namespace": None,
            }
        )
        pipeline.repository.transition_source(legacy, None)

        with pytest.raises(ValueError, match="journal-promotion identity collision"):
            pipeline.repository.transition_source(promoted_v1.manifest, None)
        assert pipeline.repository.load_manifest(legacy.source_id) == legacy

    with _prepared_case(tmp_path / "second", "reference") as (
        pipeline,
        prepared,
        _path,
        _raw,
    ):
        promoted_v1 = _journal_promotion_prepared(pipeline.repository, prepared)
        pipeline.repository.transition_source(promoted_v1.manifest, None)

        changed_same_revision = promoted_v1.manifest.model_copy(update={"sha256": "f" * 64})
        with pytest.raises(ValueError, match="same revision"):
            pipeline.repository.transition_source(changed_same_revision, None)

        promoted_v3 = promoted_v1.manifest.model_copy(
            update={
                "path": promoted_v1.manifest.path.replace("v1.md", "v3.md"),
                "assets": (
                    promoted_v1.manifest.assets[0].model_copy(
                        update={
                            "path": promoted_v1.manifest.assets[0].path.replace(
                                "v1.provenance.json", "v3.provenance.json"
                            )
                        }
                    ),
                ),
            }
        )
        physical_v3 = pipeline.repository.root / promoted_v3.path
        physical_v3.parent.mkdir(parents=True, exist_ok=True)
        physical_v3.write_bytes((pipeline.repository.root / promoted_v1.manifest.path).read_bytes())
        (pipeline.repository.root / promoted_v3.assets[0].path).write_bytes(
            (pipeline.repository.root / promoted_v1.manifest.assets[0].path).read_bytes()
        )
        pipeline.repository.transition_source(promoted_v3, None)
        assert pipeline.repository.load_manifest(promoted_v3.source_id) == promoted_v3

        with pytest.raises(ValueError, match="rollback"):
            pipeline.repository.transition_source(promoted_v1.manifest, None)


def test_journal_promotion_compiles_outside_persistence_then_commits_once(
    tmp_path: Path,
) -> None:
    with _prepared_case(tmp_path, "reference") as (pipeline, prepared, _path, _raw):
        promoted = _journal_promotion_prepared(pipeline.repository, prepared)
        pipeline.repository.transition_source(promoted.manifest, None)
        service, _host = _service(
            pipeline.repository,
            _load_responses(SOURCE_CASES["hybrid"].fixture_name),
        )

        candidate = service.compile_candidate(
            promoted,
            acceptance_profile=SemanticAcceptanceProfile.JOURNAL_PROMOTION,
        )

        assert candidate.disposition == "verified"
        with pytest.raises(FileNotFoundError):
            pipeline.repository.load_semantic_bundle(promoted.manifest.source_id)

        persisted = service.persist_compilation(
            promoted,
            candidate,
            acceptance_profile=SemanticAcceptanceProfile.JOURNAL_PROMOTION,
        )
        assert persisted == candidate
        assert (
            pipeline.repository.load_semantic_bundle(promoted.manifest.source_id)
            == candidate.bundle
        )


def test_journal_promotion_persist_rehashes_physical_bytes_before_mutation(
    tmp_path: Path,
) -> None:
    with _prepared_case(tmp_path, "reference") as (pipeline, prepared, _path, _raw):
        promoted = _journal_promotion_prepared(pipeline.repository, prepared)
        pipeline.repository.transition_source(promoted.manifest, None)
        service, _host = _service(
            pipeline.repository,
            _load_responses(SOURCE_CASES["hybrid"].fixture_name),
        )
        candidate = service.compile_candidate(
            promoted,
            acceptance_profile=SemanticAcceptanceProfile.JOURNAL_PROMOTION,
        )
        assert candidate.disposition == "verified"

        (pipeline.repository.root / promoted.manifest.path).write_bytes(b"changed bytes")
        persisted = service.persist_compilation(
            promoted,
            candidate,
            acceptance_profile=SemanticAcceptanceProfile.JOURNAL_PROMOTION,
        )

        assert persisted.disposition == "failed"
        assert persisted.failure_code == "internal_failure"
        with pytest.raises(FileNotFoundError):
            pipeline.repository.load_semantic_bundle(promoted.manifest.source_id)


def test_journal_promotion_rejects_unissued_valid_compilation_before_mutation(
    tmp_path: Path,
) -> None:
    with _prepared_case(tmp_path, "reference") as (pipeline, prepared, _path, _raw):
        promoted = _journal_promotion_prepared(pipeline.repository, prepared)
        pipeline.repository.transition_source(promoted.manifest, None)
        service, _host = _service(
            pipeline.repository,
            _load_responses(SOURCE_CASES["hybrid"].fixture_name),
        )
        candidate = service.compile_candidate(
            promoted,
            acceptance_profile=SemanticAcceptanceProfile.JOURNAL_PROMOTION,
        )
        forged = candidate.__class__.model_validate(
            candidate.model_dump(mode="json", warnings="error")
        )
        manifest_before = pipeline.repository.load_manifest(promoted.manifest.source_id)
        snapshot_before = pipeline.repository.semantic_bundle_snapshot()

        persisted = service.persist_compilation(
            promoted,
            forged,
            acceptance_profile=SemanticAcceptanceProfile.JOURNAL_PROMOTION,
        )

        assert persisted.disposition == "failed"
        assert persisted.failure_code == "invalid_input"
        assert pipeline.repository.load_manifest(promoted.manifest.source_id) == manifest_before
        assert pipeline.repository.semantic_bundle_snapshot() == snapshot_before
        with pytest.raises(FileNotFoundError):
            pipeline.repository.load_semantic_bundle(promoted.manifest.source_id)


def test_journal_promotion_rejects_profile_substitution_before_mutation(tmp_path: Path) -> None:
    with _prepared_case(tmp_path, "reference") as (pipeline, prepared, _path, _raw):
        promoted = _journal_promotion_prepared(pipeline.repository, prepared)
        pipeline.repository.transition_source(promoted.manifest, None)
        service, _host = _service(
            pipeline.repository,
            _load_responses(SOURCE_CASES["hybrid"].fixture_name),
        )
        candidate = service.compile_candidate(
            promoted,
            acceptance_profile=SemanticAcceptanceProfile.JOURNAL_PROMOTION,
        )
        manifest_before = pipeline.repository.load_manifest(promoted.manifest.source_id)
        snapshot_before = pipeline.repository.semantic_bundle_snapshot()

        persisted = service.persist_compilation(promoted, candidate)

        assert persisted.disposition == "failed"
        assert persisted.failure_code == "invalid_input"
        assert pipeline.repository.load_manifest(promoted.manifest.source_id) == manifest_before
        assert pipeline.repository.semantic_bundle_snapshot() == snapshot_before
        with pytest.raises(FileNotFoundError):
            pipeline.repository.load_semantic_bundle(promoted.manifest.source_id)


@pytest.mark.parametrize(
    "reason",
    (
        "index_retry_exhausted",
        "verification_revoked",
        "unsafe_material",
        "required_case_missing",
        "semantic_quarantined",
        "semantic_failure",
        "promotion_stage_too_large",
        "canonical_unavailable",
        "recovery_conflict",
        "lease_abandoned",
        "promotion_asset_invalid",
    ),
)
def test_nonaccepted_promotion_manifest_preserves_lineage_and_excludes(
    tmp_path: Path,
    reason: str,
) -> None:
    with _prepared_case(tmp_path, "reference") as (pipeline, prepared, _path, _raw):
        current = _journal_promotion_prepared(pipeline.repository, prepared).manifest

        excluded = build_nonaccepted_promotion_manifest(current, reason=reason)

        assert excluded.source_id == current.source_id
        assert excluded.path == current.path
        assert excluded.sha256 == current.sha256
        assert excluded.assets == current.assets
        assert excluded.ingestion_status.value == "excluded"
        assert excluded.quality.value == "unusable"
        assert excluded.quality_reason_codes == (reason,)
        assert not excluded.emitted_artifact_ids
        assert not excluded.quarantine_reasons


def test_receipts_are_minted_by_exact_semantic_canonical_and_index_operations(tmp_path) -> None:
    authorized_scope, lane, fixed_clock, fixed_uuid_factory = _journal_prerequisites()
    manager, journal, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path / "journal", authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    capability = PromotionCommitCapability(journal._repository._issue_promotion_journal_writer())
    claim = capability.claim(
        verified.engagement_id,
        _claim_request(verified.revision, verification_event_id),
        expected_revision=verified.revision,
    )
    assert claim.ownership is not None and claim.attempt is not None
    try:
        with _prepared_case(tmp_path / "canonical", "reference") as (
            pipeline,
            prepared,
            _path,
            _raw,
        ):
            promoted = _journal_promotion_prepared(pipeline.repository, prepared)
            pipeline.repository.transition_source(promoted.manifest, None)
            semantic, _host = _service(
                pipeline.repository,
                _load_responses(SOURCE_CASES["hybrid"].fixture_name),
            )
            candidate = semantic.compile_candidate(
                promoted,
                acceptance_profile=SemanticAcceptanceProfile.JOURNAL_PROMOTION,
            )
            with SQLiteRetrievalIndex(tmp_path / "promotion-index.sqlite") as index:
                receipts = capability._receipt_service(
                    semantic,
                    RetrievalMaintenanceService(pipeline.repository, index),
                )
                persisted, semantic_receipt = receipts.persist_semantic(
                    promoted,
                    candidate,
                    ownership=claim.ownership,
                    promotion_revision=claim.attempt.promotion_revision,
                )
                assert persisted.disposition == "verified"
                assert persisted.bundle is not None
                assert semantic_receipt.operation_nonce is not None
                assert (
                    semantic_receipt.artifact_ids
                    == persisted.bundle.compilation_manifest.emitted_artifact_ids
                )

                pending_receipt = receipts.canonical_currentness(
                    promoted,
                    semantic_receipt,
                    ownership=claim.ownership,
                    promotion_revision=claim.attempt.promotion_revision,
                )
                report, publication_receipt = receipts.rebuild_index(
                    promoted,
                    pending_receipt,
                    ownership=claim.ownership,
                    promotion_revision=claim.attempt.promotion_revision,
                    retry_count=1,
                )

                assert report.succeeded and not report.rebuild_required
                assert isinstance(publication_receipt, PromotionPublicationReceipt)
                assert publication_receipt.operation_nonce is not semantic_receipt.operation_nonce
                assert publication_receipt.case_ids == tuple(
                    item.case_id for item in persisted.bundle.cases
                )

                failure_receipt = receipts._issue(
                    PromotionIndexFailureReceipt,
                    attempt_id=claim.ownership.attempt_id,
                    promotion_revision=claim.attempt.promotion_revision,
                    retry_count=1,
                    reason_code="index_rebuild_failed",
                )
                forged_receipts = (
                    (
                        capability.commit_semantic,
                        replace(semantic_receipt, artifact_ids=("case-forged",)),
                    ),
                    (
                        capability.commit_index_pending,
                        replace(
                            pending_receipt,
                            expected_canonical_revision="f" * 64,
                        ),
                    ),
                    (
                        capability.commit_index_retry,
                        replace(failure_receipt, retry_count=2),
                    ),
                    (
                        capability.commit_promoted,
                        replace(publication_receipt, case_ids=("case-forged",)),
                    ),
                )
                before = journal.load_snapshot(verified.engagement_id)
                for commit, forged_receipt in forged_receipts:
                    with pytest.raises(ValueError, match="receipt payload"):
                        commit(
                            verified.engagement_id,
                            forged_receipt,
                            expected_revision=before.revision,
                        )
                    assert journal.load_snapshot(verified.engagement_id) == before
    finally:
        manager.__exit__(None, None, None)


def test_receipt_chain_revalidates_physical_currentness_before_canonical_attestation(
    tmp_path,
) -> None:
    authorized_scope, lane, fixed_clock, fixed_uuid_factory = _journal_prerequisites()
    manager, journal, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path / "journal", authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    capability = PromotionCommitCapability(journal._repository._issue_promotion_journal_writer())
    claim = capability.claim(
        verified.engagement_id,
        _claim_request(verified.revision, verification_event_id),
        expected_revision=verified.revision,
    )
    assert claim.ownership is not None and claim.attempt is not None
    try:
        with _prepared_case(tmp_path / "canonical", "reference") as (
            pipeline,
            prepared,
            _path,
            _raw,
        ):
            promoted = _journal_promotion_prepared(pipeline.repository, prepared)
            pipeline.repository.transition_source(promoted.manifest, None)
            semantic, _host = _service(
                pipeline.repository,
                _load_responses(SOURCE_CASES["hybrid"].fixture_name),
            )
            candidate = semantic.compile_candidate(
                promoted,
                acceptance_profile=SemanticAcceptanceProfile.JOURNAL_PROMOTION,
            )
            with SQLiteRetrievalIndex(tmp_path / "promotion-index.sqlite") as index:
                receipts = capability._receipt_service(
                    semantic,
                    RetrievalMaintenanceService(pipeline.repository, index),
                )
                _persisted, semantic_receipt = receipts.persist_semantic(
                    promoted,
                    candidate,
                    ownership=claim.ownership,
                    promotion_revision=claim.attempt.promotion_revision,
                )
                (pipeline.repository.root / promoted.manifest.assets[0].path).write_bytes(
                    b"changed provenance"
                )

                with pytest.raises(ValueError, match="physical"):
                    receipts.canonical_currentness(
                        promoted,
                        semantic_receipt,
                        ownership=claim.ownership,
                        promotion_revision=claim.attempt.promotion_revision,
                    )
    finally:
        manager.__exit__(None, None, None)


@pytest.mark.parametrize(
    "physical_change",
    ("malformed", "symlinked", "nonregular", "oversized", "stale", "tampered"),
)
def test_index_rebuild_rejects_noncurrent_physical_promotion_before_index_mutation(
    tmp_path,
    monkeypatch,
    physical_change,
) -> None:
    authorized_scope, lane, fixed_clock, fixed_uuid_factory = _journal_prerequisites()
    manager, journal, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path / "journal", authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    capability = PromotionCommitCapability(journal._repository._issue_promotion_journal_writer())
    claim = capability.claim(
        verified.engagement_id,
        _claim_request(verified.revision, verification_event_id),
        expected_revision=verified.revision,
    )
    assert claim.ownership is not None and claim.attempt is not None
    try:
        with _prepared_case(tmp_path / "canonical", "reference") as (
            pipeline,
            prepared,
            _path,
            _raw,
        ):
            promoted = _journal_promotion_prepared(pipeline.repository, prepared)
            pipeline.repository.transition_source(promoted.manifest, None)
            semantic, _host = _service(
                pipeline.repository,
                _load_responses(SOURCE_CASES["hybrid"].fixture_name),
            )
            candidate = semantic.compile_candidate(
                promoted,
                acceptance_profile=SemanticAcceptanceProfile.JOURNAL_PROMOTION,
            )
            with SQLiteRetrievalIndex(tmp_path / "promotion-index.sqlite") as index:
                receipts = capability._receipt_service(
                    semantic,
                    RetrievalMaintenanceService(pipeline.repository, index),
                )
                _persisted, semantic_receipt = receipts.persist_semantic(
                    promoted,
                    candidate,
                    ownership=claim.ownership,
                    promotion_revision=claim.attempt.promotion_revision,
                )
                pending_receipt = receipts.canonical_currentness(
                    promoted,
                    semantic_receipt,
                    ownership=claim.ownership,
                    promotion_revision=claim.attempt.promotion_revision,
                )
                source_path = pipeline.repository.root / promoted.manifest.path
                provenance_path = pipeline.repository.root / promoted.manifest.assets[0].path
                if physical_change == "malformed":
                    provenance_path.write_bytes(b"{")
                elif physical_change == "symlinked":
                    target = provenance_path.with_name("replacement.provenance.json")
                    target.write_bytes(provenance_path.read_bytes())
                    provenance_path.unlink()
                    provenance_path.symlink_to(target)
                elif physical_change == "nonregular":
                    provenance_path.unlink()
                    provenance_path.mkdir()
                elif physical_change == "oversized":
                    provenance_path.write_bytes(b"x" * (MAX_PROMOTION_PROVENANCE_BYTES + 1))
                elif physical_change == "stale":
                    source_path.write_bytes(source_path.read_bytes() + b"\nstale")
                else:
                    provenance_path.write_bytes(b'{"tampered":true}')

                rebuild_calls = 0
                real_rebuild = SQLiteRetrievalIndex.rebuild

                def track_rebuild(index_self, *args, **kwargs):
                    nonlocal rebuild_calls
                    rebuild_calls += 1
                    return real_rebuild(index_self, *args, **kwargs)

                monkeypatch.setattr(SQLiteRetrievalIndex, "rebuild", track_rebuild)
                before_index = index.snapshot_state()
                with pytest.raises(ValueError, match="physical"):
                    receipts.rebuild_index(
                        promoted,
                        pending_receipt,
                        ownership=claim.ownership,
                        promotion_revision=claim.attempt.promotion_revision,
                        retry_count=1,
                    )
                assert rebuild_calls == 0
                assert index.snapshot_state() == before_index
    finally:
        manager.__exit__(None, None, None)
