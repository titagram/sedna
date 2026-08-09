"""Atomic persistence tests for canonical semantic compilation results."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import pytest

from sedna.knowledge.parsing import PreparedSource, parse_markdown
from sedna.knowledge.parsing.segment import segment_document
from sedna.knowledge.repository import CanonicalKnowledgeRepository
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

_NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


def _prepared(
    source_id: str = "semantic-source",
    *,
    sha256: str = "a" * 64,
    schema_version: str = "1.1.0",
    parser_id: str = "markdown-it-commonmark",
    parser_version: str = "1",
) -> PreparedSource:
    document = parse_markdown(source_id, f"raw_src/{source_id}.md", "# Service\n\nInspect HTTP.")
    manifest = DocumentManifest(
        source_id=source_id,
        path=f"raw_src/{source_id}.md",
        sha256=sha256,
        title="Semantic source",
        language="en",
        document_type=DocumentType.MACHINE_WALKTHROUGH,
        knowledge_role=KnowledgeRole.CASE_STUDY,
        quality=SourceQuality.COMPLETE,
        parser_profile="github_walkthrough",
        ingestion_status=IngestionStatus.ACCEPTED,
        extraction=ExtractionMetadata(
            schema_version=schema_version,
            parser_id=parser_id,
            parser_version=parser_version,
            extractor_id="deterministic-foundation",
            extractor_version="2",
        ),
    )
    return PreparedSource(manifest=manifest, document=document, segments=segment_document(document))


def _call(purpose: str, model: str) -> SemanticCallMetadata:
    return SemanticCallMetadata(
        purpose=purpose,
        provider="host",
        model=model,
        agent_id="agent",
        input_tokens=10,
        output_tokens=5,
    )


def _verified_result(
    prepared: PreparedSource | None = None,
    *,
    extractor_model: str = "extractor-model",
    critic_model: str = "critic-model",
) -> SemanticCompilationResult:
    prepared = prepared or _prepared()
    extraction = prepared.manifest.extraction
    extractor_call = _call("sedna.semantic.extract", extractor_model)
    critic_call = _call("sedna.semantic.critic", critic_model)
    manifest = SemanticCompilationManifest(
        source_id=prepared.manifest.source_id,
        source_sha256=prepared.manifest.sha256,
        foundation_schema_version=extraction.schema_version,
        foundation_parser_id=extraction.parser_id,
        foundation_parser_version=extraction.parser_version,
        foundation_extraction=extraction,
        foundation_manifest_sha256=foundation_manifest_digest(prepared.manifest),
        compiler_version=SEMANTIC_COMPILER_VERSION,
        extractor_prompt_version=EXTRACTOR_PROMPT_VERSION,
        critic_prompt_version=CRITIC_PROMPT_VERSION,
        repair_prompt_version=REPAIR_PROMPT_VERSION,
        extractor_model_id=extractor_model,
        critic_model_id=critic_model,
        disposition="verified",
        repair_count=0,
        started_at=_NOW,
        completed_at=_NOW,
    )
    bundle = SemanticKnowledgeBundle(
        schema_version=SEMANTIC_SCHEMA_VERSION,
        source_id=prepared.manifest.source_id,
        source_sha256=prepared.manifest.sha256,
        compilation_manifest=manifest,
    )
    verification = SemanticVerificationRecord(
        source_id=prepared.manifest.source_id,
        source_sha256=prepared.manifest.sha256,
        critic_call=critic_call,
        adjudication="verified",
        recorded_at=_NOW,
    )
    return SemanticCompilationResult(
        disposition="verified",
        bundle=bundle,
        verification=verification,
        calls=(extractor_call, critic_call),
    )


def _quarantined_result(prepared: PreparedSource | None = None) -> SemanticCompilationResult:
    prepared = prepared or _prepared()
    critic_call = _call("sedna.semantic.critic", "critic-model")
    finding = VerificationFinding(
        code="unsupported_claim",
        severity="material",
        message="The source does not support the claim.",
        segment_indexes=(0,),
    )
    verification = SemanticVerificationRecord(
        source_id=prepared.manifest.source_id,
        source_sha256=prepared.manifest.sha256,
        critic_call=critic_call,
        findings=(finding,),
        adjudication="quarantined",
        recorded_at=_NOW,
    )
    quarantine = SemanticQuarantineRecord(
        source_id=prepared.manifest.source_id,
        source_sha256=prepared.manifest.sha256,
        reason_codes=("unsupported_claim",),
        messages=("The source does not support the claim.",),
        segment_indexes=(0,),
        recorded_at=_NOW,
    )
    return SemanticCompilationResult(
        disposition="quarantined",
        verification=verification,
        quarantine=quarantine,
        calls=(_call("sedna.semantic.extract", "extractor-model"), critic_call),
    )


def test_verified_semantic_result_round_trips_with_deterministic_bytes(tmp_path: Path) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    result = _verified_result()

    repository.write_semantic_result(result)
    bundle_path = repository.root / "semantic_bundles" / "semantic-source.json"
    verification_path = repository.root / "semantic_verification" / "semantic-source.json"
    first = (bundle_path.read_bytes(), verification_path.read_bytes())
    repository.write_semantic_result(result)

    assert repository.load_semantic_bundle("semantic-source") == result.bundle
    assert repository.load_semantic_verification("semantic-source") == result.verification
    assert tuple(path.read_bytes() for path in (bundle_path, verification_path)) == first
    assert all(payload.endswith(b"\n") for payload in first)
    assert not (repository.root / "semantic_quarantine" / "semantic-source.json").exists()
    assert not list(repository.root.rglob("*.tmp"))


def test_retrieval_snapshot_rejects_semantics_not_bound_to_current_foundation(
    tmp_path: Path,
) -> None:
    """A semantic pair cannot remain retrievable after its accepted manifest changes."""
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    prepared = _prepared()
    repository.write_manifest(prepared.manifest)
    repository.write_semantic_result(_verified_result(prepared))
    changed = prepared.manifest.model_copy(
        update={
            "extraction": prepared.manifest.extraction.model_copy(
                update={"extractor_version": "changed-foundation"}
            )
        }
    )
    repository.write_manifest(changed)

    with pytest.raises(Exception, match="foundation"):
        repository.semantic_bundle_snapshot()
    assert repository.load_current_semantic_result(prepared) is None


def test_stale_failure_cannot_invalidate_newer_same_hash_foundation_semantics(
    tmp_path: Path,
) -> None:
    """A delayed learning fallback must match the whole foundation, not only source bytes."""
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    stale = _prepared()
    current_manifest = stale.manifest.model_copy(
        update={
            "extraction": stale.manifest.extraction.model_copy(
                update={"extractor_version": "new-foundation"}
            )
        }
    )
    current = PreparedSource(
        manifest=current_manifest,
        document=stale.document,
        segments=stale.segments,
    )
    current_result = _verified_result(current)
    repository.write_manifest(current.manifest)
    repository.write_semantic_result(current_result)

    with repository.semantic_compilation_guard(stale.manifest.source_id):
        invalidated = repository.invalidate_failed_semantic_result(stale)

    assert invalidated is False
    assert repository.load_semantic_bundle(stale.manifest.source_id) == current_result.bundle


def test_semantic_dispositions_replace_bundle_and_quarantine_exclusively(tmp_path: Path) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    verified = _verified_result()
    quarantined = _quarantined_result()

    repository.write_semantic_result(verified)
    repository.write_semantic_result(quarantined)

    with pytest.raises(FileNotFoundError):
        repository.load_semantic_bundle("semantic-source")
    assert repository.load_semantic_verification("semantic-source") == quarantined.verification
    assert repository.load_semantic_quarantine("semantic-source") == quarantined.quarantine

    repository.write_semantic_result(verified)
    assert repository.load_semantic_bundle("semantic-source") == verified.bundle
    with pytest.raises(FileNotFoundError):
        repository.load_semantic_quarantine("semantic-source")


def test_failed_semantic_result_does_not_mutate_existing_state(tmp_path: Path) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    repository.write_semantic_result(_verified_result())
    before = {
        path.relative_to(repository.root): path.read_bytes()
        for path in repository.root.rglob("*.json")
    }
    failed = SemanticCompilationResult(
        disposition="failed",
        failure_code="transport_failure",
        failure_message="The host LLM request failed.",
    )

    repository.write_semantic_result(failed)

    after = {
        path.relative_to(repository.root): path.read_bytes()
        for path in repository.root.rglob("*.json")
    }
    assert after == before


@pytest.mark.parametrize(
    "method_name",
    (
        "load_semantic_bundle",
        "load_semantic_verification",
        "load_semantic_quarantine",
    ),
)
@pytest.mark.parametrize(
    "source_id",
    ("", "../escape", "nested/escape", r"nested\escape", ".", ".."),
)
def test_semantic_loaders_reject_unsafe_source_ids(
    tmp_path: Path, method_name: str, source_id: str
) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")

    with pytest.raises(ValueError, match="safe path segment"):
        getattr(repository, method_name)(source_id)
    assert not (repository.root / ".json").exists()
    assert not any(path.name == ".json" for path in repository.root.rglob(".json"))


@pytest.mark.parametrize(
    ("directory", "method_name"),
    (
        ("semantic_bundles", "load_semantic_bundle"),
        ("semantic_verification", "load_semantic_verification"),
        ("semantic_quarantine", "load_semantic_quarantine"),
    ),
)
def test_semantic_loaders_reject_symlinks_and_fifos_without_blocking(
    tmp_path: Path, directory: str, method_name: str
) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    target_directory = repository.root / directory
    target_directory.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    target = target_directory / "semantic-source.json"
    target.symlink_to(outside)

    with pytest.raises(ValueError, match="invalid semantic"):
        getattr(repository, method_name)("semantic-source")

    target.unlink()
    os.mkfifo(target)
    with pytest.raises(ValueError, match="not a regular file"):
        getattr(repository, method_name)("semantic-source")


def test_semantic_loader_rejects_symlinked_canonical_directory_with_scoped_error(
    tmp_path: Path,
) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    outside = tmp_path / "outside"
    outside.mkdir()
    (repository.root / "semantic_bundles").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="invalid semantic bundle.*semantic-source"):
        repository.load_semantic_bundle("semantic-source")

    assert not list(outside.iterdir())


def test_semantic_loaders_reject_corruption_and_cross_record_identity_mismatch(
    tmp_path: Path,
) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    repository.write_semantic_result(_verified_result())
    verification_path = repository.root / "semantic_verification" / "semantic-source.json"
    payload = json.loads(verification_path.read_text(encoding="utf-8"))
    payload["source_sha256"] = "b" * 64
    verification_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="identity"):
        repository.load_semantic_bundle("semantic-source")
    with pytest.raises(ValueError, match="identity"):
        repository.load_semantic_verification("semantic-source")

    verification_path.write_text("{corrupt", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid semantic verification"):
        repository.load_semantic_verification("semantic-source")


@pytest.mark.parametrize(
    ("tamper", "loader_name", "error_pattern"),
    (
        ("manifest_disposition", "load_semantic_bundle", "verified compilation manifest"),
        ("critic_purpose", "load_semantic_verification", "critic call purpose"),
        ("critic_model", "load_semantic_bundle", "critic model identity mismatch"),
    ),
)
def test_semantic_load_and_currentness_reject_tampered_result_invariants(
    tmp_path: Path,
    tamper: str,
    loader_name: str,
    error_pattern: str,
) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    prepared = _prepared()
    repository.write_semantic_result(_verified_result(prepared))
    bundle_path = repository.root / "semantic_bundles" / "semantic-source.json"
    verification_path = repository.root / "semantic_verification" / "semantic-source.json"

    if tamper == "manifest_disposition":
        path = bundle_path
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["compilation_manifest"]["disposition"] = "failed"
    else:
        path = verification_path
        payload = json.loads(path.read_text(encoding="utf-8"))
        if tamper == "critic_purpose":
            payload["critic_call"]["purpose"] = "sedna.semantic.extract"
        else:
            payload["critic_call"]["model"] = "different-critic-model"
    path.write_text(json.dumps(payload), encoding="utf-8")

    if tamper == "critic_model":
        SemanticKnowledgeBundle.model_validate_json(bundle_path.read_bytes())
        SemanticVerificationRecord.model_validate_json(verification_path.read_bytes())
    with pytest.raises(ValueError, match=error_pattern):
        getattr(repository, loader_name)("semantic-source")
    assert not repository.semantic_result_is_current(prepared)


def test_semantic_transition_restores_byte_exact_snapshots_and_original_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    repository.write_semantic_result(_quarantined_result())
    quarantine_path = repository.root / "semantic_quarantine" / "semantic-source.json"
    old_quarantine = b"\xff\xfe\x00old semantic quarantine"
    quarantine_path.write_bytes(old_quarantine)
    old_verification = (
        repository.root / "semantic_verification" / "semantic-source.json"
    ).read_bytes()
    real_write_model = repository._write_model

    def fail_bundle(directory: str, record_id: str, model: object) -> Path:
        if directory == "semantic_bundles":
            raise OSError("original semantic write failure")
        return real_write_model(directory, record_id, model)  # type: ignore[arg-type]

    monkeypatch.setattr(repository, "_write_model", fail_bundle)

    with pytest.raises(OSError, match="original semantic write failure"):
        repository.write_semantic_result(_verified_result())

    assert quarantine_path.read_bytes() == old_quarantine
    assert (
        repository.root / "semantic_verification" / "semantic-source.json"
    ).read_bytes() == old_verification
    assert not (repository.root / "semantic_bundles" / "semantic-source.json").exists()


def test_semantic_transition_rolls_back_if_journal_deletion_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    quarantined = _quarantined_result()
    repository.write_semantic_result(quarantined)
    old_verification = (
        repository.root / "semantic_verification" / "semantic-source.json"
    ).read_bytes()
    old_quarantine = (repository.root / "semantic_quarantine" / "semantic-source.json").read_bytes()
    real_delete = repository._delete_semantic_transition_journal
    calls = 0

    def fail_once(source_id: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("semantic journal fsync failure")
        real_delete(source_id)

    monkeypatch.setattr(repository, "_delete_semantic_transition_journal", fail_once)

    with pytest.raises(OSError, match="semantic journal fsync failure"):
        repository.write_semantic_result(_verified_result())

    assert (
        repository.root / "semantic_verification" / "semantic-source.json"
    ).read_bytes() == old_verification
    assert (
        repository.root / "semantic_quarantine" / "semantic-source.json"
    ).read_bytes() == old_quarantine
    assert not (repository.root / "semantic_bundles" / "semantic-source.json").exists()
    assert repository.load_semantic_verification("semantic-source") == quarantined.verification
    assert repository.load_semantic_quarantine("semantic-source") == quarantined.quarantine
    with pytest.raises(FileNotFoundError):
        repository.load_semantic_bundle("semantic-source")


def test_semantic_transition_serializes_across_repository_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "knowledge"
    first = CanonicalKnowledgeRepository(root)
    second = CanonicalKnowledgeRepository(root)
    entered = Event()
    release = Event()
    second_finished = Event()
    real_write_model = first._write_model

    def pause_first(directory: str, record_id: str, model: object) -> Path:
        if directory == "semantic_bundles":
            entered.set()
            assert release.wait(5)
        return real_write_model(directory, record_id, model)  # type: ignore[arg-type]

    def run_second() -> None:
        second.write_semantic_result(_quarantined_result())
        second_finished.set()

    monkeypatch.setattr(first, "_write_model", pause_first)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(first.write_semantic_result, _verified_result())
        assert entered.wait(5)
        second_future = executor.submit(run_second)
        assert not second_finished.wait(0.2)
        release.set()
        first_future.result(timeout=5)
        second_future.result(timeout=5)

    assert second.load_semantic_quarantine("semantic-source") == _quarantined_result().quarantine
    with pytest.raises(FileNotFoundError):
        second.load_semantic_bundle("semantic-source")


def test_current_semantic_result_is_one_linearizable_pair_across_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "knowledge"
    reader = CanonicalKnowledgeRepository(root)
    writer = CanonicalKnowledgeRepository(root)
    prepared = _prepared()
    reader.write_manifest(prepared.manifest)
    original = _verified_result(
        prepared,
        extractor_model="extractor-original",
        critic_model="critic-original",
    )
    replacement = _verified_result(
        prepared,
        extractor_model="extractor-replacement",
        critic_model="critic-replacement",
    )
    reader.write_semantic_result(original)
    snapshot_loaded = Event()
    release_snapshot = Event()
    writer_started = Event()
    writer_finished = Event()
    real_load_state = reader._load_semantic_state

    def pause_loaded_snapshot(source_id: str) -> object:
        state = real_load_state(source_id)
        snapshot_loaded.set()
        assert release_snapshot.wait(5)
        return state

    def replace_state() -> None:
        writer_started.set()
        writer.write_semantic_result(replacement)
        writer_finished.set()

    monkeypatch.setattr(reader, "_load_semantic_state", pause_loaded_snapshot)
    with ThreadPoolExecutor(max_workers=2) as executor:
        read_future = executor.submit(reader.load_current_semantic_result, prepared)
        assert snapshot_loaded.wait(5)
        write_future = executor.submit(replace_state)
        assert writer_started.wait(5)
        assert not writer_finished.wait(0.2)
        release_snapshot.set()
        unchanged = read_future.result(timeout=5)
        write_future.result(timeout=5)

    assert unchanged is not None
    assert unchanged.disposition == "unchanged"
    assert unchanged.calls == ()
    assert unchanged.bundle == original.bundle
    assert unchanged.verification == original.verification
    assert unchanged.bundle is not None
    assert unchanged.verification is not None
    assert (
        unchanged.bundle.compilation_manifest.critic_model_id
        == unchanged.verification.critic_call.model
        == "critic-original"
    )
    assert reader.load_semantic_bundle("semantic-source") == replacement.bundle


@pytest.mark.parametrize(
    "method_name",
    ("load_current_semantic_result", "semantic_result_is_current"),
)
@pytest.mark.parametrize("corrupt_field", ("document", "segments"))
def test_currentness_deeply_rejects_constructed_prepared_source_before_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    corrupt_field: str,
) -> None:
    """Would fail if currentness read a valid manifest before validating nested input."""
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    prepared = _prepared()
    corruption = {
        "document": {"document": "corrupt", "segments": ()},
        "segments": {"document": prepared.document, "segments": ("corrupt",)},
    }[corrupt_field]
    constructed = PreparedSource.model_construct(
        manifest=prepared.manifest,
        **corruption,
    )
    target_calls = 0
    lock_calls = 0
    real_target = repository._target
    real_lock = repository._source_transition_lock

    def recording_target(*args: object, **kwargs: object) -> object:
        nonlocal target_calls
        target_calls += 1
        return real_target(*args, **kwargs)

    def recording_lock(*args: object, **kwargs: object) -> object:
        nonlocal lock_calls
        lock_calls += 1
        return real_lock(*args, **kwargs)

    monkeypatch.setattr(repository, "_target", recording_target)
    monkeypatch.setattr(repository, "_source_transition_lock", recording_lock)

    with pytest.raises(ValueError, match="prepared source"):
        getattr(repository, method_name)(constructed)

    assert target_calls == lock_calls == 0


def test_currentness_rejects_empty_source_id_without_dot_lock_fallback(tmp_path: Path) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    prepared = _prepared()
    empty = PreparedSource.model_construct(
        manifest=prepared.manifest.model_copy(update={"source_id": ""}),
        document=prepared.document.model_copy(update={"source_id": ""}),
        segments=prepared.segments,
    )

    with pytest.raises(ValueError):
        repository.semantic_result_is_current(empty)

    assert not (repository.root / "transactions" / ".lock").exists()


def test_semantic_compilation_guard_rejects_empty_id_without_dot_lock_fallback(
    tmp_path: Path,
) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")

    with (
        pytest.raises(ValueError, match="safe path segment"),
        repository.semantic_compilation_guard(""),
    ):
        pass

    assert not (repository.root / "semantic_compilation_guards" / ".lock").exists()


def test_semantic_compilation_guard_serializes_only_the_same_safe_source(
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    first = CanonicalKnowledgeRepository(root)
    second = CanonicalKnowledgeRepository(root)
    same_source_started = Event()
    same_source_entered = Event()
    different_source_entered = Event()
    colliding_state_lock_finished = Event()

    def enter_same_source() -> None:
        same_source_started.set()
        with second.semantic_compilation_guard("semantic-source"):
            same_source_entered.set()

    def enter_different_source() -> None:
        with second.semantic_compilation_guard("different-source"):
            different_source_entered.set()

    def write_source_whose_id_matches_the_old_guard_suffix() -> None:
        prepared = _prepared(source_id="semantic-source.semantic-compilation")
        second.write_semantic_result(_verified_result(prepared))
        colliding_state_lock_finished.set()

    with ThreadPoolExecutor(max_workers=3) as executor:
        with first.semantic_compilation_guard("semantic-source"):
            same_future = executor.submit(enter_same_source)
            assert same_source_started.wait(5)
            different_future = executor.submit(enter_different_source)
            state_lock_future = executor.submit(write_source_whose_id_matches_the_old_guard_suffix)
            assert different_source_entered.wait(5)
            assert colliding_state_lock_finished.wait(5)
            assert not same_source_entered.wait(0.2)
            different_future.result(timeout=5)
            state_lock_future.result(timeout=5)
            assert not same_source_entered.is_set()
        same_future.result(timeout=5)
    assert same_source_entered.is_set()


def test_semantic_compilation_guard_rejects_unsafe_ids_and_symlinks(
    tmp_path: Path,
) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")

    with (
        pytest.raises(ValueError, match="safe path segment"),
        repository.semantic_compilation_guard("../escape"),
    ):
        pass

    guard_directory = repository.root / "semantic_compilation_guards"
    guard_directory.mkdir(exist_ok=True)
    outside = tmp_path / "outside.lock"
    outside.write_text("untouched", encoding="utf-8")
    guard_path = guard_directory / "semantic-source.lock"
    guard_path.symlink_to(outside)

    with (
        pytest.raises(ValueError, match="semantic compilation guard"),
        repository.semantic_compilation_guard("semantic-source"),
    ):
        pass

    assert outside.read_text(encoding="utf-8") == "untouched"


def test_semantic_compilation_guard_releases_after_exception(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    first = CanonicalKnowledgeRepository(root)
    second = CanonicalKnowledgeRepository(root)

    with (
        pytest.raises(RuntimeError, match="compile crashed"),
        first.semantic_compilation_guard("semantic-source"),
    ):
        raise RuntimeError("compile crashed")

    with second.semantic_compilation_guard("semantic-source"):
        pass


def test_repository_startup_recovers_interrupted_semantic_journal(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    repository = CanonicalKnowledgeRepository(root)
    repository.write_semantic_result(_verified_result())
    snapshots = {
        directory: repository._read_optional_bytes(directory, "semantic-source")
        for directory in (
            "semantic_bundles",
            "semantic_verification",
            "semantic_quarantine",
        )
    }
    repository._write_semantic_transition_journal("semantic-source", snapshots)
    repository._delete_record("semantic_bundles", "semantic-source")
    repository._write_model(
        "semantic_quarantine", "semantic-source", _quarantined_result().quarantine
    )
    repository.close()

    recovered = CanonicalKnowledgeRepository(root)

    assert recovered.load_semantic_bundle("semantic-source") == _verified_result().bundle
    assert (
        recovered.load_semantic_verification("semantic-source") == _verified_result().verification
    )
    with pytest.raises(FileNotFoundError):
        recovered.load_semantic_quarantine("semantic-source")
    assert not list(root.rglob("*.semantic-transaction.json"))


def test_semantic_result_currentness_covers_all_versions_and_optional_model_pin(
    tmp_path: Path,
) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    prepared = _prepared()
    repository.write_manifest(prepared.manifest)
    repository.write_semantic_result(_verified_result(prepared))

    assert repository.semantic_result_is_current(prepared)
    assert repository.semantic_result_is_current(
        prepared,
        pin_models=True,
        extractor_model_id="extractor-model",
        critic_model_id="critic-model",
    )
    assert repository.semantic_result_is_current(
        prepared,
        extractor_model_id="different",
        critic_model_id="different",
    )
    assert not repository.semantic_result_is_current(
        prepared,
        pin_models=True,
        extractor_model_id="different",
        critic_model_id="critic-model",
    )

    stale_prepared = (
        _prepared(sha256="b" * 64),
        _prepared(schema_version="old"),
        _prepared(parser_id="other-parser"),
        _prepared(parser_version="old"),
    )
    assert all(not repository.semantic_result_is_current(item) for item in stale_prepared)
    version_arguments = (
        {"semantic_schema_version": "old"},
        {"compiler_version": "old"},
        {"extractor_prompt_version": "old"},
        {"critic_prompt_version": "old"},
        {"repair_prompt_version": "old"},
    )
    assert all(
        not repository.semantic_result_is_current(prepared, **arguments)
        for arguments in version_arguments
    )


def test_semantic_currentness_is_false_for_missing_or_quarantined_state(tmp_path: Path) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    prepared = _prepared()

    assert not repository.semantic_result_is_current(prepared)
    repository.write_semantic_result(_quarantined_result(prepared))
    assert not repository.semantic_result_is_current(prepared)
