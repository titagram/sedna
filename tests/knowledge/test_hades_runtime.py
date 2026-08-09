"""Integration coverage for the host-owned Hades knowledge runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import sedna.knowledge.semantic.compiler as compiler_module
import sedna.knowledge.semantic.service as semantic_service_module
from sedna.knowledge.hades_runtime import HadesKnowledgeRuntime
from sedna.knowledge.repository import CanonicalKnowledgeRepository
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

    def __init__(self, path: Path) -> None:
        self.close_calls = 0
        super().__init__(path)
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
