"""Integration coverage for the host-owned Hades knowledge runtime."""

from __future__ import annotations

from pathlib import Path
from threading import Barrier, Event, Lock, Thread
from typing import Any

import pytest

import sedna.knowledge.semantic.compiler as compiler_module
import sedna.knowledge.semantic.service as semantic_service_module
from sedna.engagement.repository import JournalUnavailableError
from sedna.knowledge.hades_runtime import HadesKnowledgeRuntime
from sedna.knowledge.inventory import discover_sources
from sedna.knowledge.pipeline import IngestionPipeline
from sedna.knowledge.repository import CanonicalKnowledgeRepository
from sedna.knowledge.retrieval import (
    AuthorizationScope,
    AuthorizationState,
    CurrentSituation,
    KnowledgeGapCode,
    RetrievalQuery,
    ValidatedTarget,
)
from sedna.knowledge.retrieval.sqlite import SQLiteRetrievalIndex
from tests.knowledge.test_semantic_service import (
    SOURCE_CASES,
    _load_responses,
    _ScriptedHost,
)


def _source_root(tmp_path: Path) -> Path:
    case = SOURCE_CASES["reference"]
    root = tmp_path / "sources"
    target = root / case.relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(case.markdown, encoding="utf-8")
    return root


def _responses(*, count: int = 1) -> list[object]:
    return _load_responses(SOURCE_CASES["reference"].fixture_name) * count


class _BlockAfterTwoCallsHost(_ScriptedHost):
    """Pause the second compilation after the foundation transition commits."""

    def __init__(self, responses: list[object], entered: Event, release: Event) -> None:
        super().__init__(responses)
        self._entered = entered
        self._release = release

    def complete_structured(self, **kwargs: Any) -> object:
        if len(self.calls) == 2:
            self._entered.set()
            assert self._release.wait(5)
        return super().complete_structured(**kwargs)


def test_runtime_uses_host_structured_facade_without_provider_configuration(tmp_path: Path) -> None:
    """Removing the adapter composition would stop a valid source from being verified."""
    host = _ScriptedHost(_responses())
    runtime = HadesKnowledgeRuntime.create(host, tmp_path / "knowledge")

    try:
        report = runtime.learning.learn(_source_root(tmp_path))

        assert report.verified_source_count == 1
        assert [call["purpose"] for call in host.calls] == [
            "sedna.semantic.extract",
            "sedna.semantic.critic",
        ]
        bundle = runtime._repository.load_semantic_bundle(report.outcomes[0].source_id)
        assert bundle.compilation_manifest.extractor_model_id == "scripted-extract"
        assert bundle.compilation_manifest.critic_model_id == "scripted-critic"
        assert (
            bundle.compilation_manifest.compiler_version
            == compiler_module.SEMANTIC_COMPILER_VERSION
        )
    finally:
        runtime.close()


def test_runtime_close_releases_owned_resources_idempotently(tmp_path: Path) -> None:
    """Omitting either owned close would leave a closed runtime holding its OS resources."""
    runtime = HadesKnowledgeRuntime.create(_ScriptedHost(_responses()), tmp_path / "knowledge")

    runtime.close()
    runtime.close()

    with pytest.raises(RuntimeError):
        runtime._repository.load_manifest("missing")
    with pytest.raises(RuntimeError):
        runtime._index.snapshot_state()


def test_runtime_composes_planning_with_the_same_owned_journal(tmp_path: Path) -> None:
    runtime = HadesKnowledgeRuntime.create(_ScriptedHost([]), tmp_path / "knowledge")

    try:
        assert runtime.planning._journal is runtime._journal
        assert runtime.planning._retrieval is runtime.retrieval
        assert (
            runtime.planning._canonical_revision() == runtime._repository.retrieval_read_revision()
        )
    finally:
        runtime.close()

    with pytest.raises(JournalUnavailableError, match="repository is closed"):
        runtime._journal.list_snapshot_ids()


class _RawCompletionHost:
    def complete(self, **_: Any) -> object:
        return object()


def test_runtime_rejects_raw_host_before_learning_can_inventory_sources(tmp_path: Path) -> None:
    """Replacing structured completion with raw completion must fail before source processing."""
    source_root = _source_root(tmp_path)

    with pytest.raises(TypeError, match="complete_structured"):
        HadesKnowledgeRuntime.create(_RawCompletionHost(), tmp_path / "knowledge")

    assert source_root.exists()
    assert not (tmp_path / "knowledge").exists()


def test_runtime_second_learn_reuses_current_semantics_without_host_calls(tmp_path: Path) -> None:
    """Bypassing semantic currentness would make the unchanged second run call the host again."""
    host = _ScriptedHost(_responses())
    runtime = HadesKnowledgeRuntime.create(host, tmp_path / "knowledge")

    try:
        first = runtime.learning.learn(_source_root(tmp_path))
        calls_after_first = len(host.calls)
        second = runtime.learning.learn(_source_root(tmp_path))

        assert first.verified_source_count == 1
        assert second.unchanged_source_count == 1
        assert len(host.calls) == calls_after_first == 2
    finally:
        runtime.close()


def test_runtime_asset_only_change_recompiles_once_then_is_unchanged(tmp_path: Path) -> None:
    """Ignoring foundation assets would reuse semantics after their evidence set changed."""
    source_root = _source_root(tmp_path)
    source_path = source_root / SOURCE_CASES["reference"].relative_path
    asset_path = source_path.parent / "evidence.bin"
    asset_path.write_bytes(b"asset revision one")
    host = _ScriptedHost(_responses(count=2))
    runtime = HadesKnowledgeRuntime.create(host, tmp_path / "knowledge")

    try:
        first = runtime.learning.learn(source_root)
        first_bundle = runtime._repository.load_semantic_bundle(first.outcomes[0].source_id)
        calls_after_first = len(host.calls)
        source_sha256 = runtime._repository.load_manifest(first.outcomes[0].source_id).sha256

        asset_path.write_bytes(b"asset revision two")
        changed = runtime.learning.learn(source_root)
        calls_after_change = len(host.calls)
        unchanged = runtime.learning.learn(source_root)
        changed_bundle = runtime._repository.load_semantic_bundle(first.outcomes[0].source_id)

        assert first.verified_source_count == changed.verified_source_count == 1
        assert calls_after_first == 2
        assert calls_after_change == 4
        assert unchanged.unchanged_source_count == 1
        assert len(host.calls) == calls_after_change
        assert changed_bundle.compilation_manifest.foundation_manifest_sha256 != (
            first_bundle.compilation_manifest.foundation_manifest_sha256
        )
        assert (
            runtime._repository.load_manifest(first.outcomes[0].source_id).sha256 == source_sha256
        )
    finally:
        runtime.close()


def test_fresh_runtime_blocks_old_projection_during_asset_only_recompile(
    tmp_path: Path,
) -> None:
    """A fresh runtime must not serve the old projection after an asset-only transition."""
    knowledge_root = tmp_path / "knowledge"
    source_root = _source_root(tmp_path)
    source_path = source_root / SOURCE_CASES["reference"].relative_path
    asset_path = source_path.parent / "evidence.bin"
    asset_path.write_bytes(b"asset revision one")
    entered = Event()
    release = Event()
    host = _BlockAfterTwoCallsHost(_responses(count=2), entered, release)
    runtime = HadesKnowledgeRuntime.create(host, knowledge_root)
    learning_result: list[object] = []

    try:
        first = runtime.learning.learn(source_root)
        source_id = first.outcomes[0].source_id
        artifact_id = runtime._repository.load_semantic_bundle(source_id).references[0].artifact_id
        asset_path.write_bytes(b"asset revision two")

        worker = Thread(target=lambda: learning_result.append(runtime.learning.learn(source_root)))
        worker.start()
        assert entered.wait(5)

        fresh = HadesKnowledgeRuntime.create(_ScriptedHost([]), knowledge_root)
        try:
            assert fresh.retrieval.get_artifact(artifact_id) is None
        finally:
            fresh.close()

        release.set()
        worker.join(5)
        assert not worker.is_alive()
        assert learning_result and learning_result[0].verified_source_count == 1
    finally:
        release.set()
        runtime.close()


def test_live_peer_blocks_old_projection_during_changed_content_recompile(
    tmp_path: Path,
) -> None:
    """A content revision must remove v1 before host-backed v2 compilation starts."""
    knowledge_root = tmp_path / "knowledge"
    source_root = _source_root(tmp_path)
    source_path = source_root / SOURCE_CASES["reference"].relative_path
    entered = Event()
    release = Event()
    host = _BlockAfterTwoCallsHost(_responses(count=2), entered, release)
    runtime = HadesKnowledgeRuntime.create(host, knowledge_root)
    peer: HadesKnowledgeRuntime | None = None

    try:
        first = runtime.learning.learn(source_root)
        source_id = first.outcomes[0].source_id
        artifact_id = runtime._repository.load_semantic_bundle(source_id).references[0].artifact_id
        peer = HadesKnowledgeRuntime.create(_ScriptedHost([]), knowledge_root)
        source_path.write_text(
            source_path.read_text(encoding="utf-8") + "\nChanged source evidence.\n",
            encoding="utf-8",
        )
        worker = Thread(target=lambda: runtime.learning.learn(source_root))
        worker.start()
        assert entered.wait(5)

        assert peer.retrieval.get_artifact(artifact_id) is None

        release.set()
        worker.join(5)
        assert not worker.is_alive()
    finally:
        release.set()
        if peer is not None:
            peer.close()
        runtime.close()


def test_asset_revision_barrier_resumes_after_interrupted_index_invalidation(
    tmp_path: Path,
) -> None:
    """A crash marker must be resumable without leaving the learned index unavailable."""
    knowledge_root = tmp_path / "knowledge"
    source_root = _source_root(tmp_path)
    source_path = source_root / SOURCE_CASES["reference"].relative_path
    asset_path = source_path.parent / "evidence.bin"
    asset_path.write_bytes(b"asset revision one")
    initial = HadesKnowledgeRuntime.create(_ScriptedHost(_responses()), knowledge_root)
    first = initial.learning.learn(source_root)
    source_id = first.outcomes[0].source_id
    artifact_id = initial._repository.load_semantic_bundle(source_id).references[0].artifact_id
    asset_path.write_bytes(b"asset revision two")

    def interrupt_before_index_invalidation(_: str) -> None:
        raise OSError("simulated process interruption")

    try:
        with IngestionPipeline(
            source_root,
            knowledge_root,
            repository=initial._repository,
            before_foundation_revision_change=interrupt_before_index_invalidation,
        ) as pipeline:
            candidate = discover_sources(source_root)[0]
            with pytest.raises(OSError, match="simulated process interruption"):
                pipeline.prepare(candidate)
    finally:
        initial.close()

    resumed = HadesKnowledgeRuntime.create(_ScriptedHost(_responses()), knowledge_root)
    try:
        with pytest.raises(RuntimeError, match="knowledge artifact lookup failed"):
            resumed.retrieval.get_artifact(artifact_id)

        report = resumed.learning.learn(source_root)

        assert report.verified_source_count == 1
        assert report.index_report is not None and report.index_report.succeeded
        assert resumed.retrieval.get_artifact(artifact_id) is not None
        assert not list((knowledge_root / "transactions").glob("*.projection-revision.json"))
    finally:
        resumed.close()


def test_asset_revision_barrier_resumes_after_canonical_commit_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash before barrier deletion must resume from the committed target revision."""
    knowledge_root = tmp_path / "knowledge"
    source_root = _source_root(tmp_path)
    source_path = source_root / SOURCE_CASES["reference"].relative_path
    asset_path = source_path.parent / "evidence.bin"
    asset_path.write_bytes(b"asset revision one")
    initial = HadesKnowledgeRuntime.create(_ScriptedHost(_responses()), knowledge_root)
    first = initial.learning.learn(source_root)
    source_id = first.outcomes[0].source_id
    artifact_id = initial._repository.load_semantic_bundle(source_id).references[0].artifact_id
    asset_path.write_bytes(b"asset revision two")
    real_delete = initial._repository._delete_projection_revision_barrier
    delete_calls = 0

    def interrupt_before_barrier_delete(failed_source_id: str) -> None:
        nonlocal delete_calls
        delete_calls += 1
        if delete_calls == 1:
            raise OSError("simulated post-commit process interruption")
        real_delete(failed_source_id)

    monkeypatch.setattr(
        initial._repository,
        "_delete_projection_revision_barrier",
        interrupt_before_barrier_delete,
    )
    try:
        with IngestionPipeline(
            source_root,
            knowledge_root,
            repository=initial._repository,
            before_foundation_revision_change=initial.maintenance.barrier_source_revision,
        ) as pipeline:
            candidate = discover_sources(source_root)[0]
            with pytest.raises(OSError, match="post-commit process interruption"):
                pipeline.prepare(candidate)
        assert initial._repository.load_manifest(source_id).assets[0].sha256 == (
            "b2d1dc2bf0d5158905751e117c0ae7a4d3ec6facc2454c0e1e6f5a18627a94e8"
        )
        with pytest.raises(FileNotFoundError):
            initial._repository.load_semantic_bundle(source_id)
    finally:
        initial.close()

    resumed = HadesKnowledgeRuntime.create(_ScriptedHost(_responses()), knowledge_root)
    try:
        report = resumed.learning.learn(source_root)

        assert report.verified_source_count == 1
        assert report.index_report is not None and report.index_report.succeeded
        assert resumed.retrieval.get_artifact(artifact_id) is not None
        assert not list((knowledge_root / "transactions").glob("*.projection-revision.json"))
    finally:
        resumed.close()


def test_live_peer_blocks_reads_after_revision_marker_precedes_index_invalidation(
    tmp_path: Path,
) -> None:
    """A peer opened before the marker must not serve the old source projection."""
    knowledge_root = tmp_path / "knowledge"
    source_root = _source_root(tmp_path)
    source_path = source_root / SOURCE_CASES["reference"].relative_path
    asset_path = source_path.parent / "evidence.bin"
    asset_path.write_bytes(b"asset revision one")
    initial = HadesKnowledgeRuntime.create(_ScriptedHost(_responses()), knowledge_root)
    first = initial.learning.learn(source_root)
    source_id = first.outcomes[0].source_id
    artifact_id = initial._repository.load_semantic_bundle(source_id).references[0].artifact_id
    peer = HadesKnowledgeRuntime.create(_ScriptedHost([]), knowledge_root)
    asset_path.write_bytes(b"asset revision two")

    def interrupt_before_index_invalidation(_: str) -> None:
        raise OSError("simulated pre-invalidation interruption")

    try:
        with (
            IngestionPipeline(
                source_root,
                knowledge_root,
                repository=initial._repository,
                before_foundation_revision_change=interrupt_before_index_invalidation,
            ) as pipeline,
            pytest.raises(OSError, match="pre-invalidation interruption"),
        ):
            pipeline.prepare(discover_sources(source_root)[0])

        assert list((knowledge_root / "transactions").glob("*.projection-revision.json"))
        target = ValidatedTarget.parse("10.10.10.10")
        unavailable = peer.retrieval.retrieve(
            RetrievalQuery(
                situation=CurrentSituation(
                    target=target,
                    authorization=AuthorizationScope(
                        state=AuthorizationState.AUTHORIZED,
                        exact_targets=(target,),
                    ),
                ),
                terms=("evidence",),
            )
        )
        assert unavailable.knowledge_gap is not None
        assert unavailable.knowledge_gap.code is KnowledgeGapCode.RETRIEVAL_UNAVAILABLE
        with pytest.raises(RuntimeError, match="knowledge artifact lookup failed"):
            peer.retrieval.get_artifact(artifact_id)
    finally:
        peer.close()
        initial.close()


@pytest.mark.parametrize(
    ("source_bytes", "terminal_count"),
    (
        (b"", "excluded_source_count"),
        (b"\xff", "foundation_quarantined_source_count"),
    ),
    ids=("excluded", "foundation-quarantined"),
)
def test_nonaccepted_asset_revision_barrier_resumes_after_canonical_commit_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_bytes: bytes,
    terminal_count: str,
) -> None:
    """An unchanged terminal source must clear its committed-target revision barrier."""
    knowledge_root = tmp_path / "knowledge"
    source_root = tmp_path / "sources"
    source_root.mkdir()
    (source_root / "bad.md").write_bytes(source_bytes)
    asset_path = source_root / "evidence.bin"
    asset_path.write_bytes(b"asset revision one")
    initial = HadesKnowledgeRuntime.create(_ScriptedHost([]), knowledge_root)
    first = initial.learning.learn(source_root)
    assert getattr(first, terminal_count) == 1
    source_id = first.outcomes[0].source_id
    first_asset_sha256 = initial._repository.load_manifest(source_id).assets[0].sha256
    asset_path.write_bytes(b"asset revision two")
    real_delete = initial._repository._delete_projection_revision_barrier
    delete_calls = 0

    def interrupt_before_barrier_delete(failed_source_id: str) -> None:
        nonlocal delete_calls
        delete_calls += 1
        if delete_calls == 1:
            raise OSError("simulated terminal post-commit interruption")
        real_delete(failed_source_id)

    monkeypatch.setattr(
        initial._repository,
        "_delete_projection_revision_barrier",
        interrupt_before_barrier_delete,
    )
    try:
        interrupted = initial.learning.learn(source_root)

        assert interrupted.failed_source_count == 1
        assert delete_calls == 1
        assert initial._repository.load_manifest(source_id).assets[0].sha256 != (first_asset_sha256)
        assert list((knowledge_root / "transactions").glob("*.projection-revision.json"))
        with pytest.raises(RuntimeError, match="projection absence could not be proven"):
            initial._repository.resume_nonaccepted_projection_revision(
                source_id,
                before_barrier_clear=lambda _: False,
            )
        assert list((knowledge_root / "transactions").glob("*.projection-revision.json"))
    finally:
        initial.close()

    resumed = HadesKnowledgeRuntime.create(_ScriptedHost([]), knowledge_root)
    try:
        with pytest.raises(RuntimeError, match="knowledge artifact lookup failed"):
            resumed.retrieval.get_artifact("reference-stale")

        report = resumed.learning.learn(source_root)

        assert report.unchanged_source_count == 1
        assert report.index_report is not None and report.index_report.succeeded
        assert not list((knowledge_root / "transactions").glob("*.projection-revision.json"))
        assert resumed._index.snapshot_state().source_states == ()
        assert resumed.retrieval.get_artifact("reference-stale") is None
    finally:
        resumed.close()


def test_runtime_compiler_version_change_reinvokes_host_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ignoring compilation version attribution would prevent a controlled semantic migration."""
    host = _ScriptedHost(_responses(count=2))
    runtime = HadesKnowledgeRuntime.create(host, tmp_path / "knowledge")

    try:
        source_root = _source_root(tmp_path)
        first = runtime.learning.learn(source_root)
        monkeypatch.setattr(compiler_module, "SEMANTIC_COMPILER_VERSION", "runtime-test-v2")
        monkeypatch.setattr(semantic_service_module, "SEMANTIC_COMPILER_VERSION", "runtime-test-v2")
        migrated = runtime.learning.learn(source_root)
        calls_after_migration = len(host.calls)
        unchanged = runtime.learning.learn(source_root)

        assert first.verified_source_count == migrated.verified_source_count == 1
        assert calls_after_migration == 4
        assert unchanged.unchanged_source_count == 1
        assert len(host.calls) == calls_after_migration
    finally:
        runtime.close()


def test_runtime_transport_failure_is_a_failed_source_without_indexed_artifact(
    tmp_path: Path,
) -> None:
    """Letting host failures escape or indexing their output would break failure isolation."""
    host = _ScriptedHost([OSError("host transport failure")])
    runtime = HadesKnowledgeRuntime.create(host, tmp_path / "knowledge")

    try:
        report = runtime.learning.learn(_source_root(tmp_path))

        assert report.failed_source_count == 1
        assert report.verified_source_count == 0
        assert report.index_report is not None and report.index_report.succeeded
        assert runtime._index.snapshot_state().source_states == ()
    finally:
        runtime.close()


def test_runtime_failed_relearn_removes_stale_canonical_and_indexed_knowledge(
    tmp_path: Path,
) -> None:
    """Keeping an old bundle after a changed-source transport failure serves stale knowledge."""
    host = _ScriptedHost([*_responses(), OSError("changed-source transport failure")])
    runtime = HadesKnowledgeRuntime.create(host, tmp_path / "knowledge")

    try:
        source_root = _source_root(tmp_path)
        first = runtime.learning.learn(source_root)
        source_id = first.outcomes[0].source_id
        artifact_id = runtime._repository.load_semantic_bundle(source_id).references[0].artifact_id
        source_path = source_root / SOURCE_CASES["reference"].relative_path
        source_path.write_text(source_path.read_text(encoding="utf-8") + "\nChanged evidence.\n")

        failed = runtime.learning.learn(source_root)

        assert failed.failed_source_count == 1
        assert runtime.maintenance.audit().succeeded
        assert runtime._index.snapshot_state().source_states == ()
        assert runtime.retrieval.get_artifact(artifact_id) is None
        with pytest.raises(FileNotFoundError):
            runtime._repository.load_semantic_bundle(source_id)
    finally:
        runtime.close()


def test_runtime_semantic_write_failure_invalidates_old_canonical_and_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persistence exception after a source change must never leave v1 queryable."""
    host = _ScriptedHost(_responses(count=2))
    runtime = HadesKnowledgeRuntime.create(host, tmp_path / "knowledge")

    try:
        source_root = _source_root(tmp_path)
        first = runtime.learning.learn(source_root)
        source_id = first.outcomes[0].source_id
        artifact_id = runtime._repository.load_semantic_bundle(source_id).references[0].artifact_id
        source_path = source_root / SOURCE_CASES["reference"].relative_path
        source_path.write_text(source_path.read_text(encoding="utf-8") + "\nChanged evidence.\n")

        def fail_semantic_write(_: object) -> None:
            raise OSError("private semantic persistence failure")

        monkeypatch.setattr(runtime._repository, "write_semantic_result", fail_semantic_write)

        failed = runtime.learning.learn(source_root)

        assert failed.failed_source_count == 1
        with pytest.raises(FileNotFoundError):
            runtime._repository.load_semantic_bundle(source_id)
        assert runtime.retrieval.get_artifact(artifact_id) is None
        assert runtime._index.snapshot_state().source_states == ()
        assert "private semantic persistence failure" not in failed.model_dump_json()
    finally:
        runtime.close()


def test_document_learning_unexpected_semantic_exception_invalidates_old_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The learning boundary must fail closed even if semantic orchestration raises."""
    runtime = HadesKnowledgeRuntime.create(_ScriptedHost(_responses()), tmp_path / "knowledge")

    try:
        source_root = _source_root(tmp_path)
        first = runtime.learning.learn(source_root)
        source_id = first.outcomes[0].source_id
        artifact_id = runtime._repository.load_semantic_bundle(source_id).references[0].artifact_id
        source_path = source_root / SOURCE_CASES["reference"].relative_path
        source_path.write_text(source_path.read_text(encoding="utf-8") + "\nChanged again.\n")

        def fail_semantic_orchestration(_: object) -> object:
            raise RuntimeError("private unexpected semantic failure")

        monkeypatch.setattr(
            runtime.learning.semantic_service,
            "compile_and_store",
            fail_semantic_orchestration,
        )

        failed = runtime.learning.learn(source_root)

        assert failed.failed_source_count == 1
        with pytest.raises(FileNotFoundError):
            runtime._repository.load_semantic_bundle(source_id)
        assert runtime.retrieval.get_artifact(artifact_id) is None
        assert runtime._index.snapshot_state().source_states == ()
        assert "private unexpected semantic failure" not in failed.model_dump_json()
    finally:
        runtime.close()


def test_runtime_failed_rebuild_persists_unavailable_across_fresh_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh plugin-style runtime must not trust v1 after canonical v2 failed to rebuild."""
    knowledge_root = tmp_path / "knowledge"
    source_root = _source_root(tmp_path)
    runtime = HadesKnowledgeRuntime.create(_ScriptedHost(_responses(count=2)), knowledge_root)
    artifact_id = ""
    try:
        first = runtime.learning.learn(source_root)
        source_id = first.outcomes[0].source_id
        artifact_id = runtime._repository.load_semantic_bundle(source_id).references[0].artifact_id
        source_path = source_root / SOURCE_CASES["reference"].relative_path
        source_path.write_text(source_path.read_text(encoding="utf-8") + "\nCanonical v2.\n")

        def fail_rebuild(*_: object, **__: object) -> object:
            raise OSError("private rebuild failure")

        monkeypatch.setattr(runtime._index, "rebuild", fail_rebuild)
        changed = runtime.learning.learn(source_root)

        assert changed.verified_source_count == 1
        assert changed.index_report is not None and not changed.index_report.succeeded
        with pytest.raises(RuntimeError, match="retrieval index is unavailable"):
            runtime._index.get_artifact(artifact_id)
    finally:
        runtime.close()

    reopened = HadesKnowledgeRuntime.create(_ScriptedHost([]), knowledge_root)
    try:
        target = ValidatedTarget.parse("10.10.10.10")
        query = RetrievalQuery(
            situation=CurrentSituation(
                target=target,
                authorization=AuthorizationScope(
                    state=AuthorizationState.AUTHORIZED,
                    exact_targets=(target,),
                ),
            ),
            terms=("evidence",),
        )
        unavailable = reopened.retrieval.retrieve(query)

        assert unavailable.knowledge_gap is not None
        assert unavailable.knowledge_gap.code is KnowledgeGapCode.RETRIEVAL_UNAVAILABLE
        with pytest.raises(RuntimeError, match="^knowledge artifact lookup failed$"):
            reopened.retrieval.get_artifact(artifact_id)

        repaired = reopened.maintenance.rebuild()
        assert repaired.succeeded
        current_artifact_id = (
            reopened._repository.load_semantic_bundle(source_id).references[0].artifact_id
        )
        assert reopened.retrieval.get_artifact(current_artifact_id) is not None
        if current_artifact_id != artifact_id:
            assert reopened.retrieval.get_artifact(artifact_id) is None
    finally:
        reopened.close()


def test_runtime_rejects_same_relative_source_from_a_different_learning_root(
    tmp_path: Path,
) -> None:
    """Two roots containing lesson.md must not silently share one canonical source identity."""
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    relative_path = SOURCE_CASES["reference"].relative_path
    source = SOURCE_CASES["reference"].markdown
    (first_root / relative_path).parent.mkdir(parents=True)
    (second_root / relative_path).parent.mkdir(parents=True)
    (first_root / relative_path).write_text(source, encoding="utf-8")
    (second_root / relative_path).write_text(source, encoding="utf-8")
    host = _ScriptedHost(_responses())
    runtime = HadesKnowledgeRuntime.create(host, tmp_path / "knowledge")

    try:
        first = runtime.learning.learn(first_root)
        source_id = first.outcomes[0].source_id
        first_manifest = runtime._repository.load_manifest(source_id)
        second = runtime.learning.learn(second_root)

        assert first.verified_source_count == 1
        assert second.failed_source_count == 1
        assert len(host.calls) == 2
        assert runtime._repository.load_manifest(source_id) == first_manifest
        assert first_manifest.source_namespace is not None
    finally:
        runtime.close()


def test_runtime_learning_uses_retained_repository_after_nominal_root_redirect(
    tmp_path: Path,
) -> None:
    """Learning must write through the retained repository, never a redirected pathname."""
    source_root = _source_root(tmp_path)
    knowledge_root = tmp_path / "external" / "knowledge"
    runtime = HadesKnowledgeRuntime.create(
        _ScriptedHost(_responses()),
        knowledge_root,
        external_source_path=source_root,
    )
    detached = tmp_path / "knowledge-detached"
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    knowledge_root.rename(detached)
    knowledge_root.symlink_to(attacker, target_is_directory=True)

    try:
        report = runtime.learning.learn(source_root)

        assert report.verified_source_count == 1
        assert list((detached / "manifests").glob("*.json"))
        assert not (attacker / "manifests").exists()
        assert not (attacker / "semantic_bundles").exists()
        assert not (attacker / "indexes").exists()
    finally:
        runtime.close()


def test_runtime_failed_relearn_rolls_forward_after_invalidation_journal_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovering an interrupted invalidation must delete stale semantics, never restore them."""
    knowledge_root = tmp_path / "knowledge"
    source_root = _source_root(tmp_path)
    host = _ScriptedHost([*_responses(), OSError("changed-source transport failure")])
    runtime = HadesKnowledgeRuntime.create(host, knowledge_root)
    try:
        first = runtime.learning.learn(source_root)
        source_id = first.outcomes[0].source_id
        artifact_id = runtime._repository.load_semantic_bundle(source_id).references[0].artifact_id
        source_path = source_root / SOURCE_CASES["reference"].relative_path
        source_path.write_text(source_path.read_text(encoding="utf-8") + "\nChanged evidence.\n")
        real_delete = getattr(
            runtime._repository,
            "_delete_semantic_invalidation_journal",
            None,
        )
        calls = 0

        def fail_once(failed_source_id: str) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("injected invalidation journal unlink failure")
            assert real_delete is not None
            real_delete(failed_source_id)

        monkeypatch.setattr(
            runtime._repository,
            "_delete_semantic_invalidation_journal",
            fail_once,
            raising=False,
        )

        failed = runtime.learning.learn(source_root)
        journal = knowledge_root / "transactions" / f"{source_id}.semantic-invalidation.json"

        assert failed.failed_source_count == 1
        assert journal.exists()
        with pytest.raises(FileNotFoundError):
            runtime._repository.load_semantic_bundle(source_id)
        with pytest.raises(RuntimeError, match="retrieval index is unavailable"):
            runtime._index.snapshot_state()
        with pytest.raises(RuntimeError, match="^knowledge artifact lookup failed$"):
            runtime.retrieval.get_artifact(artifact_id)
    finally:
        runtime.close()

    recovered = HadesKnowledgeRuntime.create(_ScriptedHost([]), knowledge_root)
    try:
        report = recovered.maintenance.rebuild()

        assert report.succeeded
        assert report.indexed_source_count == 0
        assert recovered.retrieval.get_artifact(artifact_id) is None
        assert not journal.exists()
        with pytest.raises(FileNotFoundError):
            recovered._repository.load_semantic_bundle(source_id)
    finally:
        recovered.close()


def test_runtime_poisoned_index_masks_combined_invalidation_delete_and_close_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Logical poisoning must not rely on successful deletion or physical index cleanup."""
    knowledge_root = tmp_path / "knowledge"
    source_root = _source_root(tmp_path)
    host = _ScriptedHost([*_responses(), OSError("changed-source transport failure")])
    runtime = HadesKnowledgeRuntime.create(host, knowledge_root)
    try:
        first = runtime.learning.learn(source_root)
        source_id = first.outcomes[0].source_id
        artifact_id = runtime._repository.load_semantic_bundle(source_id).references[0].artifact_id
        target = ValidatedTarget.parse("10.10.10.10")
        query = RetrievalQuery(
            situation=CurrentSituation(
                target=target,
                authorization=AuthorizationScope(
                    state=AuthorizationState.AUTHORIZED,
                    exact_targets=(target,),
                ),
            ),
            terms=("evidence",),
        )
        assert runtime.retrieval.get_artifact(artifact_id) is not None
        assert any(
            hit.artifact_id == artifact_id for hit in runtime.retrieval.retrieve(query).references
        )
        source_path = source_root / SOURCE_CASES["reference"].relative_path
        source_path.write_text(source_path.read_text(encoding="utf-8") + "\nChanged evidence.\n")
        real_delete_journal = runtime._repository._delete_semantic_invalidation_journal
        real_delete_source = runtime._index.delete_source
        real_close = runtime._index.close
        journal_delete_calls = 0
        source_delete_calls = 0
        close_calls = 0

        def fail_journal_delete_once(failed_source_id: str) -> None:
            nonlocal journal_delete_calls
            journal_delete_calls += 1
            if journal_delete_calls == 1:
                raise OSError("private invalidation journal unlink failure")
            real_delete_journal(failed_source_id)

        def fail_source_delete_once(failed_source_id: str) -> None:
            nonlocal source_delete_calls
            source_delete_calls += 1
            if source_delete_calls == 1:
                raise OSError("private projection deletion failure")
            real_delete_source(failed_source_id)

        def fail_physical_close_once() -> None:
            nonlocal close_calls
            close_calls += 1
            if close_calls == 1:
                raise OSError("private physical close failure")
            real_close()

        monkeypatch.setattr(
            runtime._repository,
            "_delete_semantic_invalidation_journal",
            fail_journal_delete_once,
        )
        monkeypatch.setattr(runtime._index, "delete_source", fail_source_delete_once)
        monkeypatch.setattr(runtime._index, "close", fail_physical_close_once)

        failed = runtime.learning.learn(source_root)
        journal = knowledge_root / "transactions" / f"{source_id}.semantic-invalidation.json"
        unavailable = runtime.retrieval.retrieve(query)

        assert failed.failed_source_count == 1
        assert failed.index_report is not None and not failed.index_report.succeeded
        assert journal.exists()
        assert journal_delete_calls == close_calls == 1
        assert source_delete_calls == 2
        assert unavailable.knowledge_gap is not None
        assert unavailable.knowledge_gap.code is KnowledgeGapCode.RETRIEVAL_UNAVAILABLE
        rendered = unavailable.model_dump_json()
        assert "private invalidation journal unlink failure" not in rendered
        assert "private projection deletion failure" not in rendered
        assert "private physical close failure" not in rendered
        with pytest.raises(RuntimeError, match="^knowledge artifact lookup failed$") as failure:
            runtime.retrieval.get_artifact(artifact_id)
        assert "private" not in str(failure.value)
        with pytest.raises(RuntimeError, match="retrieval index is unavailable"):
            runtime._index.get_artifact(artifact_id)
        with pytest.raises(RuntimeError, match="retrieval index is unavailable"):
            runtime._index.snapshot_state()
        with pytest.raises(RuntimeError, match="retrieval index is unavailable"):
            runtime._index.rebuild(())
    finally:
        runtime.close()

    reopened = HadesKnowledgeRuntime.create(_ScriptedHost([]), knowledge_root)
    try:
        unavailable = reopened.retrieval.retrieve(query)

        assert unavailable.knowledge_gap is not None
        assert unavailable.knowledge_gap.code is KnowledgeGapCode.RETRIEVAL_UNAVAILABLE
        with pytest.raises(RuntimeError, match="^knowledge artifact lookup failed$"):
            reopened.retrieval.get_artifact(artifact_id)

        repaired = reopened.maintenance.rebuild()

        assert repaired.succeeded
        assert reopened.retrieval.get_artifact(artifact_id) is None
    finally:
        reopened.close()


def test_runtime_rebuilds_a_disposed_index_from_canonical_records(tmp_path: Path) -> None:
    """Treating the index as canonical state would make recreation lose verified knowledge."""
    knowledge_root = tmp_path / "knowledge"
    runtime = HadesKnowledgeRuntime.create(_ScriptedHost(_responses()), knowledge_root)
    try:
        learned = runtime.learning.learn(_source_root(tmp_path))
        assert learned.verified_source_count == 1
    finally:
        runtime.close()

    index_path = knowledge_root / "indexes" / "retrieval.sqlite"
    index_path.unlink()
    rebuilt = HadesKnowledgeRuntime.create(_ScriptedHost([]), knowledge_root)
    try:
        report = rebuilt.maintenance.rebuild()

        assert report.succeeded
        assert report.indexed_source_count == 1
        assert index_path.exists()
    finally:
        rebuilt.close()


def test_runtime_rejects_root_replacement_during_index_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opening the index by a replaced pathname would split it from the retained repository."""
    import sedna.knowledge.hades_runtime as runtime_module

    real_index = runtime_module.SQLiteRetrievalIndex
    knowledge_root = tmp_path / "knowledge"
    replacement = tmp_path / "knowledge-replaced"

    def replace_root_while_opening_index(path: Path, **kwargs: object) -> SQLiteRetrievalIndex:
        knowledge_root.rename(replacement)
        knowledge_root.mkdir()
        return real_index(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(runtime_module, "SQLiteRetrievalIndex", replace_root_while_opening_index)

    with pytest.raises(RuntimeError, match="changed"):
        HadesKnowledgeRuntime.create(_ScriptedHost(_responses()), knowledge_root)

    assert replacement.is_dir()
    assert knowledge_root.is_dir()


class _CloseProbe:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.calls = 0
        self.entered = Event()
        self.release = Event()
        self._lock = Lock()

    def close(self) -> None:
        with self._lock:
            self.calls += 1
            call = self.calls
        if self.fail_first and call == 1:
            self.entered.set()
            assert self.release.wait(5)
            raise OSError("injected first close failure")


def test_runtime_close_serializes_threads_and_retries_only_failed_resource() -> None:
    """Marking the runtime closed before a failed close blocks recovery and races callers."""
    index = _CloseProbe(fail_first=True)
    repository = _CloseProbe()
    journal_repository = _CloseProbe()
    runtime = HadesKnowledgeRuntime(  # type: ignore[arg-type]
        learning=object(),
        retrieval=object(),
        maintenance=object(),
        planning=object(),
        _repository=repository,
        _index=index,
        _journal=object(),
        _journal_repository=journal_repository,
    )
    start = Barrier(3)
    failures: list[BaseException] = []

    def close_from_thread() -> None:
        start.wait()
        try:
            runtime.close()
        except BaseException as error:
            failures.append(error)

    first = Thread(target=close_from_thread)
    second = Thread(target=close_from_thread)
    first.start()
    second.start()
    start.wait()
    assert index.entered.wait(5)
    index.release.set()
    first.join(5)
    second.join(5)

    assert not first.is_alive() and not second.is_alive()
    assert len(failures) == 1 and isinstance(failures[0], OSError)
    assert index.calls == 2
    assert repository.calls == 1
    assert journal_repository.calls == 1
    runtime.close()
    assert index.calls == 2
    assert repository.calls == 1
    assert journal_repository.calls == 1


class _FlippingStructuredHost:
    def __init__(self) -> None:
        self.delegate = _ScriptedHost(_responses())
        self.lookups = 0

    @property
    def complete_structured(self) -> object:
        self.lookups += 1
        if self.lookups == 1:
            return self.delegate.complete_structured
        raise RuntimeError("host descriptor changed after preflight")


def test_runtime_binds_structured_host_callable_at_preflight(tmp_path: Path) -> None:
    """Looking up a mutable host descriptor during learning turns a valid preflight into failure."""
    host = _FlippingStructuredHost()
    runtime = HadesKnowledgeRuntime.create(host, tmp_path / "knowledge")

    try:
        report = runtime.learning.learn(_source_root(tmp_path))

        assert report.verified_source_count == 1
        assert host.lookups == 1
        assert len(host.delegate.calls) == 2
    finally:
        runtime.close()


def test_guarded_runtime_retains_external_root_identity_through_repository_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacing an opened root ancestor must not redirect repository/index writes to source."""
    import sedna.knowledge.hades_runtime as runtime_module

    source_root = _source_root(tmp_path)
    external_parent = tmp_path / "external"
    external_parent.mkdir()
    detached_parent = tmp_path / "external-detached"
    knowledge_root = external_parent / "knowledge"
    swapped = False

    class _SwappingRepository(CanonicalKnowledgeRepository):
        def __init__(self, root: Path, *, root_fd: int | None = None) -> None:
            nonlocal swapped
            external_parent.rename(detached_parent)
            external_parent.symlink_to(source_root, target_is_directory=True)
            swapped = True
            super().__init__(root, root_fd=root_fd)

    monkeypatch.setattr(runtime_module, "CanonicalKnowledgeRepository", _SwappingRepository)

    with pytest.raises(OSError):
        HadesKnowledgeRuntime.create(
            _ScriptedHost(_responses()),
            knowledge_root,
            external_source_path=source_root,
        )

    assert swapped
    assert not (source_root / "knowledge").exists()
    assert not (source_root / "knowledge" / "indexes" / "retrieval.sqlite").exists()


def test_guarded_runtime_rejects_source_rename_before_creating_knowledge_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moving the retained source into the external path must fail before mkdir."""
    import sedna.knowledge.hades_runtime as runtime_module

    source_root = _source_root(tmp_path)
    external_parent = tmp_path / "external"
    external_parent.mkdir()
    detached_parent = tmp_path / "external-detached"
    knowledge_root = external_parent / "knowledge"
    real_open_root = runtime_module._open_or_create_directory
    swapped = False

    def move_source_then_open(path: Path, **kwargs: object) -> int:
        nonlocal swapped
        external_parent.rename(detached_parent)
        source_root.rename(external_parent)
        swapped = True
        return real_open_root(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(runtime_module, "_open_or_create_directory", move_source_then_open)

    with pytest.raises((ValueError, RuntimeError)):
        HadesKnowledgeRuntime.create(
            _ScriptedHost(_responses()),
            knowledge_root,
            external_source_path=source_root,
        )

    assert swapped
    assert not (external_parent / "knowledge").exists()


class _TrackingRepository(CanonicalKnowledgeRepository):
    instances: list[_TrackingRepository] = []

    def __init__(self, root: Path) -> None:
        self.close_calls = 0
        super().__init__(root)
        self.instances.append(self)

    def close(self) -> None:
        self.close_calls += 1
        super().close()


class _TrackingIndex(SQLiteRetrievalIndex):
    instances: list[_TrackingIndex] = []

    def __init__(self, path: Path, *, parent_fd: int | None = None) -> None:
        self.close_calls = 0
        super().__init__(path, parent_fd=parent_fd)
        self.instances.append(self)

    def close(self) -> None:
        self.close_calls += 1
        super().close()


def test_runtime_constructor_failure_closes_each_already_owned_resource_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returning after a later constructor failure must not leak the repository or index."""
    import sedna.knowledge.hades_runtime as runtime_module

    _TrackingRepository.instances.clear()
    _TrackingIndex.instances.clear()

    class _FailingLearningService:
        def __init__(self, **_: object) -> None:
            raise RuntimeError("injected constructor failure")

    monkeypatch.setattr(runtime_module, "CanonicalKnowledgeRepository", _TrackingRepository)
    monkeypatch.setattr(runtime_module, "SQLiteRetrievalIndex", _TrackingIndex)
    monkeypatch.setattr(runtime_module, "DocumentLearningService", _FailingLearningService)

    with pytest.raises(RuntimeError, match="injected constructor failure"):
        HadesKnowledgeRuntime.create(_ScriptedHost(_responses()), tmp_path / "knowledge")

    assert [_repository.close_calls for _repository in _TrackingRepository.instances] == [1]
    assert [_index.close_calls for _index in _TrackingIndex.instances] == [1]
