"""Repository-confined rebuild and parity audit tests for disposable retrieval."""

from __future__ import annotations

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import pytest

from sedna.knowledge.repository import (
    CanonicalKnowledgeRepository,
    SemanticBundleEnumerationError,
    SemanticSnapshotChangedError,
)
from sedna.knowledge.retrieval import EpistemicLane, IndexAudit, IndexStateSnapshot
from sedna.knowledge.retrieval.maintenance import (
    MaintenanceIssueCode,
    RetrievalMaintenanceService,
)
from sedna.knowledge.retrieval.projection import project_semantic_bundle, project_source_state
from sedna.knowledge.retrieval.sqlite import SQLiteRetrievalIndex
from sedna.knowledge.schema import (
    DocumentManifest,
    DocumentType,
    ExtractionMetadata,
    IngestionStatus,
    KnowledgeRole,
    SemanticCallMetadata,
    SemanticCompilationManifest,
    SemanticKnowledgeBundle,
    SemanticQuarantineRecord,
    SemanticVerificationRecord,
    SourceQuality,
    VerificationFinding,
    foundation_manifest_digest,
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
    foundation_extraction = ExtractionMetadata(
        schema_version="1.2.0",
        parser_id="markdown-it-commonmark",
        parser_version="1",
        extractor_id="deterministic-foundation",
        extractor_version="4",
    )
    manifest.update(
        source_sha256=source_hash,
        foundation_schema_version=foundation_extraction.schema_version,
        foundation_parser_id=foundation_extraction.parser_id,
        foundation_parser_version=foundation_extraction.parser_version,
        foundation_extraction=foundation_extraction.model_dump(mode="json"),
        compiler_version=SEMANTIC_COMPILER_VERSION,
        extractor_prompt_version=EXTRACTOR_PROMPT_VERSION,
        critic_prompt_version=CRITIC_PROMPT_VERSION,
        repair_prompt_version=REPAIR_PROMPT_VERSION,
    )
    current_manifest = SemanticCompilationManifest.model_validate(manifest)
    manifest["foundation_manifest_sha256"] = foundation_manifest_digest(
        _foundation_manifest(source_id, source_hash, current_manifest)
    )
    return SemanticKnowledgeBundle.model_validate(payload)


def _empty_current_bundle(source_id: str, source_hash: str) -> SemanticKnowledgeBundle:
    payload = _current_bundle(source_id, "a", source_hash=source_hash).model_dump(mode="json")
    payload["references"] = []
    payload["cases"] = []
    payload["guidance"] = []
    payload["compilation_manifest"]["emitted_artifact_ids"] = []
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


def _foundation_manifest(
    source_id: str,
    source_sha256: str,
    compilation_manifest: SemanticCompilationManifest,
) -> DocumentManifest:
    extraction = compilation_manifest.foundation_extraction
    assert extraction is not None
    return DocumentManifest(
        source_id=source_id,
        source_namespace="source-root-maintenance-tests",
        path=f"fixtures/{source_id}.md",
        sha256=source_sha256,
        title=f"Fixture {source_id}",
        language="en",
        document_type=DocumentType.LESSON,
        knowledge_role=KnowledgeRole.REFERENCE,
        quality=SourceQuality.COMPLETE,
        parser_profile="generic_markdown",
        ingestion_status=IngestionStatus.ACCEPTED,
        extraction=extraction,
    )


def _write_verified(
    repository: CanonicalKnowledgeRepository,
    bundle: SemanticKnowledgeBundle,
) -> None:
    repository.write_manifest(
        _foundation_manifest(bundle.source_id, bundle.source_sha256, bundle.compilation_manifest)
    )
    repository.write_semantic_result(_verified_result(bundle))


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
    compilation_manifest = _empty_current_bundle(
        source_id, "c" * 64
    ).compilation_manifest.model_copy(update={"disposition": "quarantined"})
    quarantine = SemanticQuarantineRecord(
        source_id=source_id,
        source_sha256="c" * 64,
        reason_codes=("unsupported_claim",),
        messages=("The source does not support the claim.",),
        recorded_at=_NOW,
        compilation_manifest=compilation_manifest,
        semantic_schema_version=SEMANTIC_SCHEMA_VERSION,
    )
    return SemanticCompilationResult(
        disposition="quarantined",
        verification=verification,
        quarantine=quarantine,
        calls=(_call("sedna.semantic.extract", "model"), critic),
    )


def _write_quarantined(repository: CanonicalKnowledgeRepository, source_id: str) -> None:
    result = _quarantined_result(source_id)
    assert result.quarantine is not None
    manifest = result.quarantine.compilation_manifest
    assert manifest is not None
    repository.write_manifest(_foundation_manifest(source_id, "c" * 64, manifest))
    repository.write_semantic_result(result)


def _write_corpus(
    repository: CanonicalKnowledgeRepository,
) -> tuple[SemanticKnowledgeBundle, ...]:
    bundles = (
        _current_bundle("source-a", "a"),
        _current_bundle("source-z", "b", source_hash="b" * 64),
    )
    for bundle in reversed(bundles):
        _write_verified(repository, bundle)
    _write_quarantined(repository, "source-quarantined")
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
    _write_verified(repository, bundle)
    bundle_path = root / "semantic_bundles" / "source-a.json"
    bundle_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(SemanticBundleEnumerationError, match="source-a"):
        tuple(repository.iter_semantic_bundles())

    _write_verified(repository, bundle)
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
    _write_verified(repository, bundle)
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


def test_repository_enumeration_rejects_orphan_and_unsafe_quarantine_records(
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    repository = CanonicalKnowledgeRepository(root)
    _write_quarantined(repository, "source-quarantined")
    verification = root / "semantic_verification" / "source-quarantined.json"
    verification.unlink()
    with pytest.raises(SemanticBundleEnumerationError, match="source-quarantined"):
        tuple(repository.iter_semantic_bundles())

    quarantine = root / "semantic_quarantine" / "source-quarantined.json"
    quarantine.unlink()
    os.symlink(tmp_path / "outside.json", quarantine)
    with pytest.raises(SemanticBundleEnumerationError, match="source-quarantined"):
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
        with pytest.raises(RuntimeError, match="retrieval index is unavailable"):
            index.get_artifact("reference-http-a")


def test_rebuild_fails_closed_on_pending_semantic_crash_state_then_recovers(
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    path = tmp_path / "sedna.sqlite"
    repository = CanonicalKnowledgeRepository(root)
    original = _current_bundle("source-a", "a", source_hash="a" * 64)
    replacement = _current_bundle("source-a", "b", source_hash="b" * 64)
    prior = _current_bundle("source-prior", "c", source_hash="c" * 64)
    _write_verified(repository, original)
    snapshots = {
        directory: repository._read_optional_bytes(directory, original.source_id)
        for directory in repository._SEMANTIC_DIRECTORIES
    }
    index = SQLiteRetrievalIndex(path)
    index.rebuild((prior,))
    before = path.read_bytes()

    repository._write_semantic_transition_journal(original.source_id, snapshots)
    replacement_result = _verified_result(replacement)
    repository._write_model(
        "semantic_verification",
        replacement.source_id,
        replacement_result.verification,
    )
    repository._write_model("semantic_bundles", replacement.source_id, replacement)

    report = RetrievalMaintenanceService(repository=repository, index=index).rebuild()

    assert report.succeeded is False
    assert report.issues[0].code is MaintenanceIssueCode.CANONICAL_REPOSITORY_INVALID
    assert report.issues[0].source_ids == ("source-a",)
    assert path.read_bytes() == before
    with pytest.raises(RuntimeError, match="retrieval index is unavailable"):
        index.get_artifact("reference-http-c")

    repository.close()
    index.close()
    recovered = CanonicalKnowledgeRepository(root)
    assert recovered.load_semantic_bundle("source-a") == original
    repaired_index = SQLiteRetrievalIndex(path)
    rebuilt = RetrievalMaintenanceService(repository=recovered, index=repaired_index).rebuild()
    assert rebuilt.succeeded
    assert repaired_index.get_artifact("reference-http-a") == original.references[0]
    assert repaired_index.get_artifact("reference-http-b") is None
    recovered.close()
    repaired_index.close()


@pytest.mark.parametrize(
    "journal_name",
    ("source-a.transaction.json", "source-a.semantic-transaction.json"),
)
def test_repository_snapshot_rejects_each_pending_transaction_journal_type(
    tmp_path: Path,
    journal_name: str,
) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    transactions = repository.root / "transactions"
    transactions.mkdir(exist_ok=True)
    (transactions / journal_name).write_text("{}", encoding="utf-8")

    with pytest.raises(SemanticBundleEnumerationError, match="source-a"):
        tuple(repository.iter_semantic_bundles())
    repository.close()


def test_repository_snapshot_rejects_malformed_symlink_and_fifo_pending_journal(
    tmp_path: Path,
) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    transactions = repository.root / "transactions"
    transactions.mkdir(exist_ok=True)
    journal = transactions / "source-a.semantic-transaction.json"
    journal.write_text("{broken", encoding="utf-8")
    with pytest.raises(SemanticBundleEnumerationError, match="source-a"):
        tuple(repository.iter_semantic_bundles())

    journal.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    journal.symlink_to(outside)
    with pytest.raises(SemanticBundleEnumerationError, match="source-a"):
        tuple(repository.iter_semantic_bundles())

    journal.unlink()
    os.mkfifo(journal)
    with pytest.raises(SemanticBundleEnumerationError, match="source-a"):
        tuple(repository.iter_semantic_bundles())
    journal.unlink()
    repository.close()


def test_repository_snapshot_detects_transaction_journal_appearing_during_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    _write_verified(repository, _current_bundle("source-a", "a"))
    journal = repository.root / "transactions" / "source-a.semantic-transaction.json"
    real_inventory = repository._semantic_inventory_entries
    created = False

    def inventory_then_create_journal():
        nonlocal created
        entries = real_inventory()
        if not created:
            created = True
            journal.write_text("{}", encoding="utf-8")
        return entries

    monkeypatch.setattr(repository, "_semantic_inventory_entries", inventory_then_create_journal)

    with pytest.raises(SemanticBundleEnumerationError, match="source-a"):
        tuple(repository.iter_semantic_bundles())
    repository.close()


def test_repository_snapshot_rejects_journal_before_it_can_disappear_during_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    _write_verified(repository, _current_bundle("source-a", "a"))
    journal = repository.root / "transactions" / "source-a.semantic-transaction.json"
    journal.write_text("{}", encoding="utf-8")
    real_inventory = repository._semantic_inventory_entries

    def remove_journal_then_inventory():
        journal.unlink(missing_ok=True)
        return real_inventory()

    monkeypatch.setattr(repository, "_semantic_inventory_entries", remove_journal_then_inventory)

    with pytest.raises(SemanticBundleEnumerationError, match="source-a"):
        tuple(repository.iter_semantic_bundles())
    repository.close()


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
        _write_verified(repository, hash_changed)
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
        _write_verified(repository, initial)
        index.rebuild((initial,))
        real_snapshot = index.snapshot_state
        changed = False

        def mutate_canonical_then_snapshot():
            nonlocal changed
            if not changed:
                changed = True
                _write_verified(repository, _current_bundle("source-new", "b"))
            return real_snapshot()

        monkeypatch.setattr(index, "snapshot_state", mutate_canonical_then_snapshot)

        report = RetrievalMaintenanceService(repository=repository, index=index).audit()

        assert report.rebuild_required
        assert report.missing_source_count == 1
        assert MaintenanceIssueCode.CANONICAL_REPOSITORY_CHANGED in {
            issue.code for issue in report.issues
        }


def test_audit_cannot_mix_two_index_generations_across_a_101_source_page_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "knowledge"
    old_hash = "a" * 64
    new_hash = "b" * 64
    source_ids = tuple(f"source-{index:03d}" for index in range(101))
    canonical = tuple(
        _empty_current_bundle(
            source_id,
            old_hash if index < 100 else new_hash,
        )
        for index, source_id in enumerate(source_ids)
    )
    old_generation = tuple(_empty_current_bundle(source_id, old_hash) for source_id in source_ids)
    new_generation = tuple(_empty_current_bundle(source_id, new_hash) for source_id in source_ids)

    with (
        CanonicalKnowledgeRepository(root) as repository,
        SQLiteRetrievalIndex(tmp_path / "sedna.sqlite") as index,
    ):
        for bundle in canonical:
            _write_verified(repository, bundle)
        index.rebuild(old_generation)
        writer = SQLiteRetrievalIndex(tmp_path / "sedna.sqlite")
        real_audit_connection = index._audit_connection
        writer_attempting = Event()
        snapshot_entered = Event()

        def observe_snapshot(connection: sqlite3.Connection):
            snapshot_entered.set()
            writer_attempting.wait(timeout=5)
            return real_audit_connection(connection)

        def rebuild_concurrently() -> None:
            snapshot_entered.wait(timeout=5)
            writer_attempting.set()
            writer.rebuild(new_generation)

        monkeypatch.setattr(index, "_audit_connection", observe_snapshot)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(rebuild_concurrently)
            report = RetrievalMaintenanceService(repository=repository, index=index).audit()
            future.result(timeout=10)

        assert report.rebuild_required
        assert report.stale_source_count > 0
        writer.close()


def test_rebuild_aborts_before_install_when_canonical_source_is_added_during_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "knowledge"
    path = tmp_path / "sedna.sqlite"
    with CanonicalKnowledgeRepository(root) as repository, SQLiteRetrievalIndex(path) as index:
        prior = _current_bundle("source-prior", "c")
        initial = _current_bundle("source-a", "a")
        added = _current_bundle("source-new", "b")
        index.rebuild((prior,))
        before = path.read_bytes()
        _write_verified(repository, initial)
        real_insert = index._insert_projection_rows
        changed = False

        def add_source_during_candidate_build(*args: object, **kwargs: object) -> None:
            nonlocal changed
            real_insert(*args, **kwargs)
            if not changed:
                changed = True
                _write_verified(repository, added)

        monkeypatch.setattr(index, "_insert_projection_rows", add_source_during_candidate_build)

        report = RetrievalMaintenanceService(repository=repository, index=index).rebuild()

        assert changed
        assert report.succeeded is False
        assert report.rebuild_required
        assert path.read_bytes() == before
        with pytest.raises(RuntimeError, match="retrieval index is unavailable"):
            index.get_artifact("reference-http-c")


def test_repository_root_lock_cannot_be_split_by_replacing_obsolete_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "knowledge"
    first = CanonicalKnowledgeRepository(root)
    second = CanonicalKnowledgeRepository(root)
    initial = _empty_current_bundle("source-a", "a" * 64)
    added = _empty_current_bundle("source-b", "b" * 64)
    _write_verified(first, initial)
    second.write_manifest(
        _foundation_manifest(added.source_id, added.source_sha256, added.compilation_manifest)
    )
    snapshot = first.semantic_bundle_snapshot()
    obsolete_sidecar = root / "transactions" / ".semantic-inventory.lock"
    obsolete_sidecar.write_bytes(b"")
    lock_attempted = Event()
    lock_acquired = Event()
    real_lock = second._semantic_inventory_lock

    @contextmanager
    def observed_lock(*, exclusive: bool):
        lock_attempted.set()
        with real_lock(exclusive=exclusive):
            lock_acquired.set()
            yield

    monkeypatch.setattr(second, "_semantic_inventory_lock", observed_lock)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with first.semantic_snapshot_guard(snapshot.revision):
            obsolete_sidecar.unlink()
            obsolete_sidecar.write_bytes(b"")
            writer = executor.submit(second.write_semantic_result, _verified_result(added))
            assert lock_attempted.wait(timeout=2)
            assert not lock_acquired.wait(timeout=0.1)
        assert lock_acquired.wait(timeout=2)
        writer.result(timeout=5)

    assert tuple(bundle.source_id for bundle in first.iter_semantic_bundles()) == (
        "source-a",
        "source-b",
    )
    first.close()
    second.close()


def test_startup_semantic_recovery_waits_for_repository_snapshot_guard(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    repository = CanonicalKnowledgeRepository(root)
    initial = _empty_current_bundle("source-a", "a" * 64)
    _write_verified(repository, initial)
    snapshot = repository.semantic_bundle_snapshot()
    snapshots = {
        directory: repository._read_optional_bytes(directory, "source-a")
        for directory in repository._SEMANTIC_DIRECTORIES
    }
    recovery_attempted = Event()
    recovery_finished = Event()

    def recover_repository() -> CanonicalKnowledgeRepository:
        recovery_attempted.set()
        recovered = CanonicalKnowledgeRepository(root)
        recovery_finished.set()
        return recovered

    with ThreadPoolExecutor(max_workers=1) as executor:
        with (
            pytest.raises(SemanticSnapshotChangedError),
            repository.semantic_snapshot_guard(snapshot.revision),
        ):
            repository._write_semantic_transition_journal("source-a", snapshots)
            repository._delete_record("semantic_bundles", "source-a")
            future = executor.submit(recover_repository)
            assert recovery_attempted.wait(timeout=2)
            assert not recovery_finished.wait(timeout=0.1)
        recovered = future.result(timeout=5)

    assert recovered.load_semantic_bundle("source-a") == initial
    recovered.close()
    repository.close()


def test_rebuild_rolls_back_when_canonical_bytes_change_after_guard_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "knowledge"
    path = tmp_path / "sedna.sqlite"
    with CanonicalKnowledgeRepository(root) as repository, SQLiteRetrievalIndex(path) as index:
        prior = _current_bundle("source-prior", "c")
        initial = _current_bundle("source-a", "a")
        index.rebuild((prior,))
        before = path.read_bytes()
        _write_verified(repository, initial)
        verification_path = root / "semantic_verification" / "source-a.json"
        real_verify = index._verify_retained_sibling
        changed = False

        def change_after_guard_entry(*args: object, **kwargs: object) -> None:
            nonlocal changed
            real_verify(*args, **kwargs)
            if not changed:
                changed = True
                verification_path.write_bytes(verification_path.read_bytes() + b" \n")

        monkeypatch.setattr(index, "_verify_retained_sibling", change_after_guard_entry)

        report = RetrievalMaintenanceService(repository=repository, index=index).rebuild()

        assert changed
        assert report.succeeded is False
        assert report.issues[0].code is MaintenanceIssueCode.CANONICAL_REPOSITORY_CHANGED
        assert path.read_bytes() == before
        with pytest.raises(RuntimeError, match="retrieval index is unavailable"):
            index.get_artifact("reference-http-c")


def test_audit_derives_missing_artifact_count_from_live_rows_not_source_assertions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    path = tmp_path / "sedna.sqlite"
    repository = CanonicalKnowledgeRepository(root)
    bundle = _current_bundle("source-a", "a")
    _write_verified(repository, bundle)
    index = SQLiteRetrievalIndex(path)
    index.rebuild((bundle,))
    index.close()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "DELETE FROM artifact_fts WHERE artifact_id = ?",
            ("reference-http-a",),
        )
        connection.execute(
            "DELETE FROM artifacts WHERE artifact_id = ?",
            ("reference-http-a",),
        )

    reopened = SQLiteRetrievalIndex(path)
    report = RetrievalMaintenanceService(repository=repository, index=reopened).audit()

    assert report.canonical_artifact_count == 6
    assert report.indexed_artifact_count == 5
    assert report.stale_source_count == 1
    assert report.missing_artifact_count == 1
    reopened.close()
    repository.close()


@pytest.mark.parametrize(
    ("statement", "parameters", "expected_stale_artifacts"),
    (
        (
            "UPDATE indexed_sources SET projection_digest = ? WHERE source_id = ?",
            ("f" * 64, "source-a"),
            0,
        ),
        (
            "UPDATE artifacts SET projection_digest = ? WHERE artifact_id = ?",
            ("f" * 64, "reference-http-a"),
            1,
        ),
    ),
)
def test_audit_counts_exact_stale_source_and_artifacts_for_digest_tampering(
    tmp_path: Path,
    statement: str,
    parameters: tuple[str, str],
    expected_stale_artifacts: int,
) -> None:
    root = tmp_path / "knowledge"
    path = tmp_path / "sedna.sqlite"
    repository = CanonicalKnowledgeRepository(root)
    bundle = _current_bundle("source-a", "a")
    _write_verified(repository, bundle)
    index = SQLiteRetrievalIndex(path)
    index.rebuild((bundle,))
    index.close()
    with sqlite3.connect(path) as connection:
        connection.execute(statement, parameters)

    with SQLiteRetrievalIndex(path) as reopened:
        report = RetrievalMaintenanceService(repository=repository, index=reopened).audit()

    assert report.succeeded
    assert report.stale_source_count == 1
    assert report.stale_artifact_count == expected_stale_artifacts
    assert MaintenanceIssueCode.INDEX_INTEGRITY_FAILURE in {issue.code for issue in report.issues}
    repository.close()


def test_malformed_backend_audit_hidden_state_maps_to_typed_unavailable(tmp_path: Path) -> None:
    state = project_source_state(_empty_current_bundle("source-hidden", "a" * 64))
    valid = IndexStateSnapshot(
        generation=0,
        audit=IndexAudit(source_count=1),
        source_states=(state,),
    )
    malformed_snapshots = (
        valid.model_copy(update={"hidden": "discarded"}),
        valid.model_copy(
            update={
                "audit": valid.audit.model_copy(update={"hidden": "discarded"}),
            }
        ),
        valid.model_copy(
            update={
                "source_states": (state.model_copy(update={"hidden": "discarded"}),),
            }
        ),
    )
    with CanonicalKnowledgeRepository(tmp_path / "knowledge") as repository:
        for malformed in malformed_snapshots:

            class MalformedIndex:
                def __init__(self, snapshot: object) -> None:
                    self._snapshot = snapshot

                def snapshot_state(self):
                    return self._snapshot

            report = RetrievalMaintenanceService(
                repository=repository,
                index=MalformedIndex(malformed),  # type: ignore[arg-type]
            ).audit()

            assert report.succeeded is False
            assert report.issues[0].code is MaintenanceIssueCode.INDEX_UNAVAILABLE


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
