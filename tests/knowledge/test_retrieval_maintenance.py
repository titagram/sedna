"""Repository-confined rebuild and parity audit tests for disposable retrieval."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sedna.knowledge.repository import (
    CanonicalKnowledgeRepository,
    SemanticBundleEnumerationError,
)
from sedna.knowledge.retrieval import EpistemicLane
from sedna.knowledge.retrieval.maintenance import (
    MaintenanceIssueCode,
    RetrievalMaintenanceService,
)
from sedna.knowledge.retrieval.projection import project_semantic_bundle
from sedna.knowledge.retrieval.sqlite import SQLiteRetrievalIndex
from sedna.knowledge.schema import (
    SemanticCallMetadata,
    SemanticKnowledgeBundle,
    SemanticQuarantineRecord,
    SemanticVerificationRecord,
    VerificationFinding,
)
from sedna.knowledge.semantic import SemanticCompilationResult
from sedna.knowledge.semantic.compiler import (
    SEMANTIC_COMPILER_VERSION,
    SEMANTIC_SCHEMA_VERSION,
)
from sedna.knowledge.semantic.prompts import (
    CRITIC_PROMPT_VERSION,
    EXTRACTOR_PROMPT_VERSION,
    REPAIR_PROMPT_VERSION,
)
from tests.knowledge.test_retrieval_sqlite import _query, _renamed_bundle

_NOW = datetime(2026, 8, 8, tzinfo=UTC)


def _current_bundle(
    source_id: str,
    suffix: str,
    *,
    source_hash: str = "a" * 64,
) -> SemanticKnowledgeBundle:
    payload = _renamed_bundle(source_id, suffix).model_dump(mode="json")
    payload["schema_version"] = SEMANTIC_SCHEMA_VERSION
    payload["source_sha256"] = source_hash
    manifest = payload["compilation_manifest"]
    manifest.update(
        source_sha256=source_hash,
        compiler_version=SEMANTIC_COMPILER_VERSION,
        extractor_prompt_version=EXTRACTOR_PROMPT_VERSION,
        critic_prompt_version=CRITIC_PROMPT_VERSION,
        repair_prompt_version=REPAIR_PROMPT_VERSION,
    )
    return SemanticKnowledgeBundle.model_validate(payload)


def _call(purpose: str, model: str) -> SemanticCallMetadata:
    return SemanticCallMetadata(
        purpose=purpose,
        provider="host",
        model=model,
        agent_id="maintenance-test",
        input_tokens=1,
        output_tokens=1,
    )


def _verified_result(bundle: SemanticKnowledgeBundle) -> SemanticCompilationResult:
    extractor = _call("sedna.semantic.extract", bundle.compilation_manifest.extractor_model_id)
    critic = _call("sedna.semantic.critic", bundle.compilation_manifest.critic_model_id)
    verification = SemanticVerificationRecord(
        source_id=bundle.source_id,
        source_sha256=bundle.source_sha256,
        critic_call=critic,
        adjudication="verified",
        recorded_at=_NOW,
    )
    return SemanticCompilationResult(
        disposition="verified",
        bundle=bundle,
        verification=verification,
        calls=(extractor, critic),
    )


def _quarantined_result(source_id: str) -> SemanticCompilationResult:
    critic = _call("sedna.semantic.critic", "model")
    finding = VerificationFinding(
        code="unsupported_claim",
        severity="material",
        message="The source does not support the claim.",
    )
    verification = SemanticVerificationRecord(
        source_id=source_id,
        source_sha256="c" * 64,
        critic_call=critic,
        findings=(finding,),
        adjudication="quarantined",
        recorded_at=_NOW,
    )
    quarantine = SemanticQuarantineRecord(
        source_id=source_id,
        source_sha256="c" * 64,
        reason_codes=("unsupported_claim",),
        messages=("The source does not support the claim.",),
        recorded_at=_NOW,
    )
    return SemanticCompilationResult(
        disposition="quarantined",
        verification=verification,
        quarantine=quarantine,
        calls=(_call("sedna.semantic.extract", "model"), critic),
    )


def _write_corpus(
    repository: CanonicalKnowledgeRepository,
) -> tuple[SemanticKnowledgeBundle, ...]:
    bundles = (
        _current_bundle("source-a", "a"),
        _current_bundle("source-z", "b", source_hash="b" * 64),
    )
    for bundle in reversed(bundles):
        repository.write_semantic_result(_verified_result(bundle))
    repository.write_semantic_result(_quarantined_result("source-quarantined"))
    return bundles


def test_repository_iterates_only_current_verified_bundles_in_sorted_order(
    tmp_path: Path,
) -> None:
    with CanonicalKnowledgeRepository(tmp_path / "knowledge") as repository:
        expected = _write_corpus(repository)

        actual = tuple(repository.iter_semantic_bundles())

        assert actual == expected
        assert [bundle.source_id for bundle in actual] == ["source-a", "source-z"]


def test_repository_enumeration_fails_closed_on_corrupt_and_stale_records(
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    repository = CanonicalKnowledgeRepository(root)
    bundle = _current_bundle("source-a", "a")
    repository.write_semantic_result(_verified_result(bundle))
    bundle_path = root / "semantic_bundles" / "source-a.json"
    bundle_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(SemanticBundleEnumerationError, match="source-a"):
        tuple(repository.iter_semantic_bundles())

    repository.write_semantic_result(_verified_result(bundle))
    stale_payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    stale_payload["compilation_manifest"]["compiler_version"] = "obsolete"
    bundle_path.write_text(json.dumps(stale_payload), encoding="utf-8")
    with pytest.raises(SemanticBundleEnumerationError, match="not current"):
        tuple(repository.iter_semantic_bundles())
    repository.close()


def test_repository_enumeration_never_follows_symlink_or_blocks_on_fifo(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    repository = CanonicalKnowledgeRepository(root)
    bundle = _current_bundle("source-a", "a")
    repository.write_semantic_result(_verified_result(bundle))
    bundle_path = root / "semantic_bundles" / "source-a.json"
    bundle_path.unlink()
    os.symlink(tmp_path / "outside.json", bundle_path)
    with pytest.raises(SemanticBundleEnumerationError, match="source-a"):
        tuple(repository.iter_semantic_bundles())
    bundle_path.unlink()
    os.mkfifo(bundle_path)
    with pytest.raises(SemanticBundleEnumerationError, match="source-a"):
        tuple(repository.iter_semantic_bundles())
    repository.close()


def test_rebuild_empty_and_populated_repository_is_typed_atomic_and_canonical_immutable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    path = tmp_path / "indexes" / "sedna.sqlite"
    with CanonicalKnowledgeRepository(root) as repository, SQLiteRetrievalIndex(path) as index:
        maintenance = RetrievalMaintenanceService(repository=repository, index=index)
        empty = maintenance.rebuild()
        assert empty.succeeded
        assert empty.canonical_source_count == 0
        assert empty.canonical_artifact_count == 0
        assert empty.index_audit is not None
        assert empty.rebuild_required is False

        bundles = _write_corpus(repository)
        canonical_before = {
            target.relative_to(root): target.read_bytes() for target in root.rglob("*.json")
        }
        rebuilt = maintenance.rebuild()

        assert rebuilt.succeeded
        assert rebuilt.canonical_source_count == 2
        assert rebuilt.canonical_artifact_count == sum(
            len(project_semantic_bundle(bundle)) for bundle in bundles
        )
        assert rebuilt.indexed_source_count == 2
        assert rebuilt.indexed_artifact_count == rebuilt.canonical_artifact_count
        assert rebuilt.elapsed_seconds >= 0.0
        assert rebuilt.issues == ()
        assert {
            target.relative_to(root): target.read_bytes() for target in root.rglob("*.json")
        } == canonical_before


def test_rebuild_corrupt_source_returns_typed_failure_without_partial_index_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    path = tmp_path / "sedna.sqlite"
    with CanonicalKnowledgeRepository(root) as repository, SQLiteRetrievalIndex(path) as index:
        bundles = _write_corpus(repository)
        index.rebuild((bundles[0],))
        before = path.read_bytes()
        (root / "semantic_bundles" / "source-z.json").write_text("{broken", encoding="utf-8")

        report = RetrievalMaintenanceService(repository=repository, index=index).rebuild()

        assert report.succeeded is False
        assert report.rebuild_required
        assert report.issues[0].code is MaintenanceIssueCode.CANONICAL_REPOSITORY_INVALID
        assert report.issues[0].source_ids == ("source-z",)
        assert path.read_bytes() == before
        assert index.get_artifact("reference-http-a") == bundles[0].references[0]


def test_audit_detects_hash_only_stale_missing_and_orphan_source_projections(
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    path = tmp_path / "sedna.sqlite"
    with CanonicalKnowledgeRepository(root) as repository, SQLiteRetrievalIndex(path) as index:
        expected = _write_corpus(repository)
        index.rebuild(expected)
        maintenance = RetrievalMaintenanceService(repository=repository, index=index)
        assert maintenance.audit().rebuild_required is False

        hash_changed = _current_bundle("source-a", "a", source_hash="d" * 64)
        repository.write_semantic_result(_verified_result(hash_changed))
        stale = maintenance.audit()
        assert stale.rebuild_required
        assert stale.stale_source_count == 1
        assert MaintenanceIssueCode.STALE_SOURCE_PROJECTION in {
            issue.code for issue in stale.issues
        }

        index.delete_source("source-z")
        missing = maintenance.audit()
        assert missing.missing_source_count == 1
        assert missing.missing_artifact_count == 6

        orphan = _current_bundle("source-orphan", "c", source_hash="e" * 64)
        index.upsert_bundle(orphan)
        orphaned = maintenance.audit()
        assert orphaned.orphan_source_count == 1
        assert orphaned.orphan_artifact_count == 6


def test_audit_detects_canonical_change_that_occurs_during_index_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "knowledge"
    with (
        CanonicalKnowledgeRepository(root) as repository,
        SQLiteRetrievalIndex(tmp_path / "sedna.sqlite") as index,
    ):
        initial = _current_bundle("source-a", "a")
        repository.write_semantic_result(_verified_result(initial))
        index.rebuild((initial,))
        real_audit = index.audit
        changed = False

        def mutate_canonical_then_audit():
            nonlocal changed
            if not changed:
                changed = True
                repository.write_semantic_result(
                    _verified_result(_current_bundle("source-new", "b"))
                )
            return real_audit()

        monkeypatch.setattr(index, "audit", mutate_canonical_then_audit)

        report = RetrievalMaintenanceService(repository=repository, index=index).audit()

        assert report.rebuild_required
        assert report.missing_source_count == 1
        assert MaintenanceIssueCode.CANONICAL_REPOSITORY_CHANGED in {
            issue.code for issue in report.issues
        }


def test_delete_and_rebuild_is_result_equivalent_even_when_index_bytes_change(
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    path = tmp_path / "sedna.sqlite"
    repository = CanonicalKnowledgeRepository(root)
    _write_corpus(repository)
    index = SQLiteRetrievalIndex(path)
    maintenance = RetrievalMaintenanceService(repository=repository, index=index)
    assert maintenance.rebuild().succeeded
    query = _query("reference-statement-token")
    before = index.search_candidates(query, lane=EpistemicLane.REFERENCE, limit=10)
    before_audit = maintenance.audit().model_copy(update={"elapsed_seconds": 0.0})
    index.close()
    path.unlink()

    replacement = SQLiteRetrievalIndex(path)
    replacement_maintenance = RetrievalMaintenanceService(repository=repository, index=replacement)
    assert replacement_maintenance.rebuild().succeeded
    after = replacement.search_candidates(query, lane=EpistemicLane.REFERENCE, limit=10)
    after_audit = replacement_maintenance.audit().model_copy(update={"elapsed_seconds": 0.0})

    assert after == before
    assert after_audit == before_audit
    replacement.close()
    repository.close()
