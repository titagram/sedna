"""End-to-end orchestration tests for autonomous local learning."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

import sedna.knowledge.learning as learning_module
from sedna.knowledge.inventory import discover_sources
from sedna.knowledge.learning import (
    DocumentLearningService,
    LearningDisposition,
    LearningRunReport,
    LearningSourceOutcome,
)
from sedna.knowledge.pipeline import IngestionPipeline
from sedna.knowledge.repository import CanonicalKnowledgeRepository
from sedna.knowledge.retrieval.maintenance import RetrievalMaintenanceReport
from tests.knowledge.test_semantic_service import (
    SOURCE_CASES,
    _load_responses,
)
from tests.knowledge.test_semantic_service import (
    _service as _semantic_service,
)
from tests.knowledge.test_semantic_version_migration import _seed_verified_compiler_v2

FIXTURES = Path(__file__).parent / "fixtures"


@dataclass(frozen=True, slots=True)
class _SemanticResult:
    disposition: str
    failure_code: str | None = None
    quarantine: object | None = None


class _ScriptedSemanticService:
    def __init__(self, dispositions: dict[str, _SemanticResult] | None = None) -> None:
        self.dispositions = dispositions or {}
        self.calls: list[str] = []
        self.current_source_ids: set[str] = set()

    def is_current(self, prepared: object) -> bool:
        return prepared.manifest.source_id in self.current_source_ids  # type: ignore[attr-defined]

    def compile_and_store(self, prepared: object) -> _SemanticResult:
        source_id = prepared.manifest.source_id  # type: ignore[attr-defined]
        self.calls.append(source_id)
        result = self.dispositions.get(source_id, _SemanticResult("verified"))
        if isinstance(result, BaseException):
            raise result
        if result.disposition == "verified":
            self.current_source_ids.add(source_id)
        return result


class _Maintenance:
    def __init__(self, *, succeeded: bool = True) -> None:
        self.calls = 0
        self.report = RetrievalMaintenanceReport(
            operation="rebuild", succeeded=succeeded, elapsed_seconds=0.0
        )

    def rebuild(self) -> RetrievalMaintenanceReport:
        self.calls += 1
        return self.report


def _source_root(tmp_path: Path, files: dict[str, bytes]) -> Path:
    root = tmp_path / "sources"
    for relative_path, payload in files.items():
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return root


def _lesson() -> bytes:
    return (FIXTURES / "lesson.md").read_bytes()


def _service(
    tmp_path: Path,
    semantic: _ScriptedSemanticService | None = None,
    maintenance: _Maintenance | None = None,
) -> tuple[DocumentLearningService, _ScriptedSemanticService, _Maintenance]:
    semantic = semantic or _ScriptedSemanticService()
    maintenance = maintenance or _Maintenance()
    return (
        DocumentLearningService(
            knowledge_root=tmp_path / "knowledge",
            semantic_service=semantic,  # type: ignore[arg-type]
            maintenance=maintenance,  # type: ignore[arg-type]
        ),
        semantic,
        maintenance,
    )


def test_learn_folder_compiles_verified_sources_then_rebuilds(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path, {"Write-ups/Academy/lesson.md": _lesson()})
    service, semantic, maintenance = _service(tmp_path)

    report = service.learn(source_root)

    assert report.source_path == str(source_root.resolve())
    assert [item.disposition for item in report.outcomes] == [LearningDisposition.VERIFIED]
    assert report.verified_source_count == 1
    assert len(semantic.calls) == maintenance.calls == 1
    assert report.index_report is not None and report.index_report.succeeded


def test_unchanged_folder_run_makes_no_semantic_calls_and_rebuilds(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path, {"Write-ups/Academy/lesson.md": _lesson()})
    service, semantic, maintenance = _service(tmp_path)

    first = service.learn(source_root)
    calls_after_first = len(semantic.calls)
    second = service.learn(source_root)

    assert first.verified_source_count == 1
    assert second.unchanged_source_count == 1
    assert len(semantic.calls) == calls_after_first
    assert maintenance.calls == 2


def test_stale_semantic_state_reprepares_once_for_a_controlled_migration(
    tmp_path: Path,
) -> None:
    source_root = _source_root(tmp_path, {"Write-ups/Academy/lesson.md": _lesson()})
    service, semantic, _ = _service(tmp_path)

    first = service.learn(source_root)
    semantic.current_source_ids.clear()  # Model/compiler version migration marks the bundle stale.
    migrated = service.learn(source_root)
    calls_after_migration = len(semantic.calls)
    unchanged = service.learn(source_root)

    assert first.verified_source_count == migrated.verified_source_count == 1
    assert calls_after_migration == 2
    assert unchanged.unchanged_source_count == 1
    assert len(semantic.calls) == calls_after_migration


def test_compiler_v2_semantics_recompile_once_through_learning_service(tmp_path: Path) -> None:
    case = SOURCE_CASES["reference"]
    source_root = _source_root(tmp_path, {case.relative_path: case.markdown.encode("utf-8")})
    knowledge_root = tmp_path / "knowledge"
    candidate = discover_sources(source_root)[0]
    with IngestionPipeline(source_root, knowledge_root) as pipeline:
        prepared = pipeline.prepare(candidate)
        assert prepared is not None
        _seed_verified_compiler_v2(pipeline.repository, prepared)

    repository = CanonicalKnowledgeRepository(knowledge_root)
    semantic, host = _semantic_service(repository, _load_responses(case.fixture_name))
    service = DocumentLearningService(
        knowledge_root=knowledge_root,
        semantic_service=semantic,
        maintenance=_Maintenance(),  # type: ignore[arg-type]
    )

    migrated = service.learn(source_root)
    calls_after_migration = len(host.calls)
    unchanged = service.learn(source_root)

    assert migrated.verified_source_count == 1
    assert [call["purpose"] for call in host.calls] == [
        "sedna.semantic.extract",
        "sedna.semantic.critic",
    ]
    assert unchanged.unchanged_source_count == 1
    assert len(host.calls) == calls_after_migration
    repository.close()


@pytest.mark.parametrize(
    ("replacement", "expected"),
    ((b"", LearningDisposition.EXCLUDED), (b"\xff", LearningDisposition.FOUNDATION_QUARANTINED)),
)
def test_foundation_terminal_transition_invalidates_old_verified_semantics(
    tmp_path: Path,
    replacement: bytes,
    expected: LearningDisposition,
) -> None:
    case = SOURCE_CASES["reference"]
    source_root = _source_root(tmp_path, {case.relative_path: case.markdown.encode("utf-8")})
    knowledge_root = tmp_path / "knowledge"
    repository = CanonicalKnowledgeRepository(knowledge_root)
    semantic, _ = _semantic_service(repository, _load_responses(case.fixture_name))
    service = DocumentLearningService(
        knowledge_root=knowledge_root,
        semantic_service=semantic,
        maintenance=_Maintenance(),  # type: ignore[arg-type]
    )
    first = service.learn(source_root)
    assert first.verified_source_count == 1
    source_path = source_root / case.relative_path
    source_path.write_bytes(replacement)

    report = service.learn(source_root)

    assert [item.disposition for item in report.outcomes] == [expected]
    with pytest.raises(FileNotFoundError):
        repository.load_semantic_bundle(discover_sources(source_root)[0].source_id)
    repository.close()


def test_reopen_after_crash_between_semantic_and_foundation_journal_cleanup_is_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = SOURCE_CASES["reference"]
    source_root = _source_root(tmp_path, {case.relative_path: case.markdown.encode("utf-8")})
    knowledge_root = tmp_path / "knowledge"
    repository = CanonicalKnowledgeRepository(knowledge_root)
    semantic, _ = _semantic_service(repository, _load_responses(case.fixture_name))
    service = DocumentLearningService(
        knowledge_root=knowledge_root,
        semantic_service=semantic,
        maintenance=_Maintenance(),  # type: ignore[arg-type]
    )
    assert service.learn(source_root).verified_source_count == 1
    source_path = source_root / case.relative_path
    source_path.write_bytes(b"")

    def crash_after_semantic_cleanup(_: str) -> None:
        raise OSError("injected crash")

    monkeypatch.setattr(
        CanonicalKnowledgeRepository,
        "_delete_transition_journal",
        crash_after_semantic_cleanup,
    )
    failed = service.learn(source_root)
    assert failed.failed_source_count == 1
    monkeypatch.undo()
    repository.close()

    reopened = CanonicalKnowledgeRepository(knowledge_root)
    source_id = discover_sources(source_root)[0].source_id
    with pytest.raises(FileNotFoundError):
        reopened.load_semantic_bundle(source_id)
    manifest = reopened.load_manifest(source_id)
    assert manifest.ingestion_status.value == "accepted"
    reopened.close()


def test_force_reprepare_refreshes_only_an_unchanged_accepted_source(tmp_path: Path) -> None:
    source_root = _source_root(
        tmp_path,
        {"Write-ups/Academy/lesson.md": _lesson(), "Write-ups/Academy/empty.md": b""},
    )
    candidates = {candidate.relative_path: candidate for candidate in discover_sources(source_root)}
    accepted = candidates["Write-ups/Academy/lesson.md"]
    excluded = candidates["Write-ups/Academy/empty.md"]

    with IngestionPipeline(source_root, tmp_path / "knowledge") as pipeline:
        assert pipeline.prepare(accepted) is not None
        assert pipeline.prepare(accepted) is None
        assert pipeline.last_outcome == "unchanged"
        assert pipeline.prepare(accepted, force_reprepare=True) is not None
        assert pipeline.prepare(excluded) is None
        assert pipeline.last_outcome == "excluded"
        assert pipeline.prepare(excluded, force_reprepare=True) is None
        assert pipeline.last_outcome == "unchanged"


def test_pipeline_rejects_source_root_nested_under_knowledge_root(tmp_path: Path) -> None:
    knowledge_root = tmp_path / "knowledge"
    source_root = knowledge_root / "manifests"
    source_root.mkdir(parents=True)

    with pytest.raises(ValueError, match="outside the immutable source root"):
        IngestionPipeline(source_root, knowledge_root)


def test_learning_rejects_source_inside_knowledge_root_without_canonical_writes(
    tmp_path: Path,
) -> None:
    knowledge_root = tmp_path / "knowledge"
    source_root = knowledge_root / "manifests"
    source_root.mkdir(parents=True)
    (source_root / "lesson.md").write_bytes(_lesson())
    service, semantic, maintenance = _service(tmp_path)

    report = service.learn(source_root)

    assert report.failed and report.failure_codes == ("source_root_unavailable",)
    assert not semantic.calls
    assert maintenance.calls == 1
    assert not tuple(knowledge_root.rglob("*.json"))


def test_runtime_semantic_exception_is_one_safe_failed_candidate(tmp_path: Path) -> None:
    source_root = _source_root(
        tmp_path,
        {"Write-ups/Academy/a.md": _lesson(), "Write-ups/Academy/z.md": _lesson()},
    )
    service, semantic, maintenance = _service(tmp_path)
    failed_id = discover_sources(source_root)[0].source_id
    semantic.dispositions[failed_id] = RuntimeError("provider secret leaked")  # type: ignore[assignment]

    report = service.learn(source_root)

    assert {item.disposition for item in report.outcomes} == {
        LearningDisposition.FAILED,
        LearningDisposition.VERIFIED,
    }
    assert report.failed_source_count == 1
    assert all("secret" not in message for item in report.outcomes for message in item.messages)
    assert maintenance.calls == 1


def test_empty_directory_is_a_safe_no_sources_failure(tmp_path: Path) -> None:
    source_root = tmp_path / "empty"
    source_root.mkdir()
    service, semantic, maintenance = _service(tmp_path)

    report = service.learn(source_root)

    assert report.failure_codes == ("no_sources",)
    assert report.failed
    assert not semantic.calls
    assert maintenance.calls == 1


def test_nul_path_returns_typed_failed_report_without_raw_path(tmp_path: Path) -> None:
    service, semantic, maintenance = _service(tmp_path)

    report = service.learn(Path("\x00untrusted"))

    assert report.failure_codes == ("invalid_source_path",)
    assert "\x00" not in report.source_path
    assert not semantic.calls
    assert maintenance.calls == 0


def test_source_count_limit_stops_before_processing_or_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _source_root(tmp_path, {"Write-ups/Academy/lesson.md": _lesson()})
    candidate = discover_sources(source_root)[0]
    service, semantic, maintenance = _service(tmp_path)
    monkeypatch.setattr(learning_module, "discover_sources", lambda _: (candidate,) * 100_001)

    report = service.learn(source_root)

    assert report.failure_codes == ("source_count_exceeded",)
    assert not report.outcomes
    assert not semantic.calls
    assert maintenance.calls == 0


def test_one_semantic_failure_does_not_abort_other_documents(tmp_path: Path) -> None:
    source_root = _source_root(
        tmp_path,
        {"Write-ups/Academy/a.md": _lesson(), "Write-ups/Academy/z.md": _lesson()},
    )
    service, semantic, maintenance = _service(tmp_path)
    # Source IDs are stable, so the second source can be selected after discovering its ID.
    failed_id = discover_sources(source_root)[1].source_id
    semantic.dispositions[failed_id] = _SemanticResult("failed", "transport_failure")

    report = service.learn(source_root)

    assert {item.disposition for item in report.outcomes} == {
        LearningDisposition.VERIFIED,
        LearningDisposition.FAILED,
    }
    assert len(semantic.calls) == 2
    assert maintenance.calls == 1


def test_single_file_is_confined_to_parent_and_never_learns_sibling(tmp_path: Path) -> None:
    source_root = _source_root(
        tmp_path,
        {
            "Write-ups/Academy/selected.md": _lesson(),
            "Write-ups/Academy/sibling.md": _lesson(),
        },
    )
    service, semantic, _ = _service(tmp_path)

    report = service.learn(source_root / "Write-ups/Academy/selected.md")

    assert len(report.outcomes) == 1
    assert len(semantic.calls) <= 1
    assert report.source_path == str((source_root / "Write-ups/Academy/selected.md").resolve())


def test_excluded_and_foundation_quarantine_are_reported_without_semantic_calls(
    tmp_path: Path,
) -> None:
    source_root = _source_root(
        tmp_path,
        {"empty.md": b"", "unsupported.pdf": b"not actually a pdf"},
    )
    service, semantic, _ = _service(tmp_path)

    report = service.learn(source_root)

    assert {item.disposition for item in report.outcomes} == {
        LearningDisposition.EXCLUDED,
        LearningDisposition.FOUNDATION_QUARANTINED,
    }
    assert not semantic.calls


def test_invalid_root_and_nonregular_input_return_typed_failed_report(tmp_path: Path) -> None:
    service, semantic, maintenance = _service(tmp_path)
    fifo = tmp_path / "source.fifo"
    os.mkfifo(fifo)

    report = service.learn(fifo)

    assert report.failed is True
    assert report.failure_codes == ("invalid_source_path",)
    assert not report.outcomes
    assert not semantic.calls
    assert maintenance.calls == 0


def test_report_rejects_hidden_counter_mutation_and_sorts_outcomes() -> None:
    report = LearningRunReport(
        source_path="/safe/root",
        outcomes=(
            LearningSourceOutcome(source_id="source-z", disposition="unchanged"),
            LearningSourceOutcome(source_id="source-a", disposition="verified"),
        ),
    )

    assert [item.source_id for item in report.outcomes] == ["source-a", "source-z"]
    assert report.verified_source_count == 1
    with pytest.raises(ValueError):
        report.model_copy(update={"verified_source_count": 99})
    with pytest.raises(ValueError):
        report.model_copy(update={"unexpected": "value"})


def test_rebuild_failure_is_reported_after_source_accounting(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path, {"Write-ups/Academy/lesson.md": _lesson()})
    service, semantic, maintenance = _service(tmp_path, maintenance=_Maintenance(succeeded=False))

    report = service.learn(source_root)

    assert report.verified_source_count == len(semantic.calls) == 1
    assert report.index_report is not None and not report.index_report.succeeded
    assert maintenance.calls == 1
