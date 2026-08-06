"""Persistence tests for canonical ingestion records."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

import sedna.knowledge.repository as repository_module
from sedna.knowledge.repository import (
    CanonicalKnowledgeRepository,
    IngestionFailure,
    IngestionReport,
    QuarantineRecord,
)
from sedna.knowledge.schema import (
    DocumentManifest,
    DocumentType,
    ExtractionMetadata,
    IngestionStatus,
    KnowledgeRole,
    SourceQuality,
)


def foundation_metadata() -> ExtractionMetadata:
    return ExtractionMetadata(
        schema_version="1.0.0",
        parser_id="markdown-it-commonmark",
        parser_version="1",
        extractor_id="deterministic-foundation",
        extractor_version="1",
    )


def complete_manifest(
    source_id: str = "source-123",
    *,
    title: str = "Café 雪",
) -> DocumentManifest:
    return DocumentManifest(
        source_id=source_id,
        path=f"raw_src/{title}.md",
        sha256="a" * 64,
        title=title,
        language="en",
        document_type=DocumentType.MACHINE_WALKTHROUGH,
        knowledge_role=KnowledgeRole.CASE_STUDY,
        quality=SourceQuality.COMPLETE,
        parser_profile="github_walkthrough",
        ingestion_status=IngestionStatus.ACCEPTED,
        extraction=foundation_metadata(),
    )


def complete_quarantine(source_id: str = "source-123") -> QuarantineRecord:
    return QuarantineRecord(
        quarantine_id=f"quarantine-{source_id}",
        source_id=source_id,
        reason_codes=("unsupported_parser",),
        messages=("PDF parsing is not available.",),
        parser_profile="none",
        extraction=foundation_metadata(),
    )


def complete_report(run_id: str = "run-fixture") -> IngestionReport:
    return IngestionReport(
        run_id=run_id,
        extraction=foundation_metadata(),
        inventoried_source_ids=("source-z", "source-a"),
        accepted_source_ids=("source-a",),
        quarantined_source_ids=("source-z",),
    )


def test_repository_round_trips_manifest_and_preserves_unicode(tmp_path: Path) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    manifest = complete_manifest()

    target = repository.write_manifest(manifest)

    assert repository.load_manifest(manifest.source_id) == manifest
    assert "Café 雪" in target.read_text(encoding="utf-8")


def test_repeated_write_is_byte_identical_and_leaves_no_temporary_file(
    tmp_path: Path,
) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    manifest = complete_manifest()

    target = repository.write_manifest(manifest)
    first = target.read_bytes()
    repository.write_manifest(manifest)

    assert target.read_bytes() == first
    assert first.endswith(b"\n")
    assert not list(repository.root.rglob("*.tmp"))


def test_json_is_sorted_indented_and_uses_json_mode(tmp_path: Path) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")

    target = repository.write_quarantine(complete_quarantine())
    text = target.read_text(encoding="utf-8")

    assert text.startswith('{\n  "extraction": {')
    assert json.loads(text)["reason_codes"] == ["unsupported_parser"]


def test_atomic_replace_failure_preserves_old_target_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    target = repository.write_manifest(complete_manifest(title="First"))
    old_bytes = target.read_bytes()

    def fail_replace(
        source: Path,
        destination: Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        del src_dir_fd, dst_dir_fd
        raise OSError(f"cannot replace {source} with {destination}")

    monkeypatch.setattr(repository_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="cannot replace"):
        repository.write_manifest(complete_manifest(title="Second"))

    assert target.read_bytes() == old_bytes
    assert not list(repository.root.rglob("*.tmp"))


def test_concurrent_writers_to_one_target_never_collide_or_corrupt(tmp_path: Path) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    manifests = tuple(
        complete_manifest("source-shared", title=f"Candidate {index}")
        for index in range(16)
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        targets = tuple(executor.map(repository.write_manifest, manifests))

    assert len(set(targets)) == 1
    assert repository.load_manifest("source-shared") in manifests
    assert not list(repository.root.rglob("*.tmp"))


@pytest.mark.parametrize(
    "source_id",
    (
        "../escape",
        "nested/escape",
        r"nested\escape",
        "/absolute",
        ".",
        "..",
        "nul\x00id",
    ),
)
def test_repository_rejects_unsafe_record_ids(tmp_path: Path, source_id: str) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")

    with pytest.raises(ValueError, match="safe path segment"):
        repository.write_manifest(complete_manifest(source_id))


def test_repository_rejects_symlinked_directory_escape(tmp_path: Path) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    outside = tmp_path / "outside"
    outside.mkdir()
    quarantine_directory = repository.root / "quarantine"
    quarantine_directory.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="outside repository root"):
        repository.write_quarantine(complete_quarantine())

    assert not list(outside.iterdir())


def test_parent_symlink_swap_during_write_cannot_redirect_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    repository.write_manifest(complete_manifest(title="Before"))
    outside = tmp_path / "outside"
    outside.mkdir()
    original_directory = repository.root / "manifests-original"
    real_open = os.open
    swapped = False

    def racing_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if not swapped and os.fspath(path).startswith(".source-123.json."):
            (repository.root / "manifests").rename(original_directory)
            (repository.root / "manifests").symlink_to(
                outside, target_is_directory=True
            )
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(repository_module.os, "open", racing_open)
    repository.write_manifest(complete_manifest(title="After"))

    assert swapped
    assert not list(outside.iterdir())
    payload = json.loads((original_directory / "source-123.json").read_text())
    assert payload["title"] == "After"


def test_target_symlink_swap_during_replace_cannot_modify_outside_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    repository.write_manifest(complete_manifest(title="Before"))
    outside_file = tmp_path / "outside.json"
    outside_file.write_text("outside sentinel", encoding="utf-8")
    real_replace = os.replace

    def racing_replace(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        assert src_dir_fd is not None
        assert dst_dir_fd is not None
        os.unlink(destination, dir_fd=dst_dir_fd)
        os.symlink(outside_file, destination, dir_fd=dst_dir_fd)
        real_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(repository_module.os, "replace", racing_replace)
    repository.write_manifest(complete_manifest(title="After"))

    assert outside_file.read_text(encoding="utf-8") == "outside sentinel"
    assert repository.load_manifest("source-123").title == "After"


def test_root_path_replacement_does_not_redirect_open_repository(
    tmp_path: Path,
) -> None:
    root_path = tmp_path / "knowledge"
    repository = CanonicalKnowledgeRepository(root_path)
    retained_root = tmp_path / "retained-root"
    root_path.rename(retained_root)
    root_path.mkdir()

    repository.write_manifest(complete_manifest())

    assert (retained_root / "manifests" / "source-123.json").is_file()
    assert not list(root_path.rglob("*.json"))


def test_parent_symlink_swap_during_load_cannot_redirect_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    repository.write_manifest(complete_manifest(title="Trusted"))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "source-123.json").write_text(
        repository_module.json.dumps(
            complete_manifest(title="Untrusted").model_dump(mode="json")
        ),
        encoding="utf-8",
    )
    original_directory = repository.root / "manifests-original"
    real_open = os.open
    swapped = False

    def racing_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if not swapped and os.fspath(path) == "source-123.json":
            (repository.root / "manifests").rename(original_directory)
            (repository.root / "manifests").symlink_to(
                outside, target_is_directory=True
            )
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(repository_module.os, "open", racing_open)
    loaded = repository.load_manifest("source-123")

    assert swapped
    assert loaded.title == "Trusted"


def test_target_symlink_swap_during_load_is_rejected_without_reading_outside(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    repository.write_manifest(complete_manifest(title="Trusted"))
    outside_file = tmp_path / "outside.json"
    outside_file.write_text(
        json.dumps(complete_manifest(title="Untrusted").model_dump(mode="json")),
        encoding="utf-8",
    )
    real_open = os.open
    swapped = False

    def racing_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if not swapped and os.fspath(path) == "source-123.json":
            assert dir_fd is not None
            os.unlink(path, dir_fd=dir_fd)
            os.symlink(outside_file, path, dir_fd=dir_fd)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(repository_module.os, "open", racing_open)

    with pytest.raises(ValueError, match="invalid manifest.*source-123"):
        repository.load_manifest("source-123")

    assert swapped


def test_repository_context_manager_closes_descriptor_and_close_is_idempotent(
    tmp_path: Path,
) -> None:
    with CanonicalKnowledgeRepository(tmp_path / "knowledge") as repository:
        repository.write_manifest(complete_manifest())

    repository.close()
    with pytest.raises(RuntimeError, match="repository is closed"):
        repository.load_manifest("source-123")
    with pytest.raises(RuntimeError, match="repository is closed"):
        repository.write_manifest(complete_manifest())


def test_repository_fails_closed_without_descriptor_relative_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repository_module.os, "supports_dir_fd", frozenset())

    with pytest.raises(RuntimeError, match="safe descriptor-relative"):
        CanonicalKnowledgeRepository(tmp_path / "knowledge")


def test_load_manifest_fails_clearly_when_missing_or_invalid(tmp_path: Path) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")

    with pytest.raises(FileNotFoundError, match="source-missing"):
        repository.load_manifest("source-missing")

    invalid = repository.root / "manifests" / "source-invalid.json"
    invalid.parent.mkdir()
    invalid.write_text('{"source_id": "source-invalid"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid manifest.*source-invalid"):
        repository.load_manifest("source-invalid")


def test_load_manifest_rejects_source_id_mismatch(tmp_path: Path) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    target = repository.write_manifest(complete_manifest("source-other"))
    requested_target = target.with_name("source-requested.json")
    target.rename(requested_target)

    with pytest.raises(ValueError, match="invalid manifest.*source-requested"):
        repository.load_manifest("source-requested")


def test_quarantine_contract_is_strict_and_requires_explanation() -> None:
    record = complete_quarantine()
    assert json.dumps(record.model_dump(mode="json"))

    for missing in ("reason_codes", "messages", "parser_profile", "extraction"):
        payload = record.model_dump()
        del payload[missing]
        with pytest.raises(ValidationError):
            QuarantineRecord.model_validate(payload)

    with pytest.raises(ValidationError):
        QuarantineRecord.model_validate(
            {**record.model_dump(), "raw_text": "HTB{must-not-be-stored}"}
        )


def test_ingestion_report_is_deterministic_strict_and_json_serializable() -> None:
    report = IngestionReport(
        run_id="run-fixture",
        extraction=foundation_metadata(),
        inventoried_source_ids=("source-z", "source-a", "source-failed"),
        accepted_source_ids=("source-a",),
        quarantined_source_ids=("source-z",),
        failures=(
            IngestionFailure(
                source_id="source-failed",
                reason_code="invalid_encoding",
                message="Source is not valid UTF-8.",
            ),
        ),
        warnings=("z warning", "a warning"),
    )

    assert report.inventoried_source_ids == (
        "source-a",
        "source-failed",
        "source-z",
    )
    assert report.warnings == ("a warning", "z warning")
    assert json.dumps(report.model_dump(mode="json"))

    with pytest.raises(ValidationError):
        IngestionReport.model_validate(
            {**report.model_dump(), "searchable_text": "sensitive payload"}
        )


def test_repository_writes_deterministic_ingestion_report(tmp_path: Path) -> None:
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    report = complete_report()

    target = repository.write_ingestion_report(report)
    first = target.read_bytes()
    repository.write_ingestion_report(report)

    assert target == repository.root / "ingestion_reports" / "run-fixture.json"
    assert target.read_bytes() == first


def test_report_rejects_overlapping_or_unaccounted_outcomes() -> None:
    with pytest.raises(ValidationError, match="exactly one outcome"):
        IngestionReport(
            run_id="run-invalid",
            extraction=foundation_metadata(),
            inventoried_source_ids=("source-a",),
            accepted_source_ids=("source-a",),
            excluded_source_ids=("source-a",),
        )

    with pytest.raises(ValidationError, match="exactly one outcome"):
        IngestionReport(
            run_id="run-invalid",
            extraction=foundation_metadata(),
            inventoried_source_ids=("source-a", "source-b"),
            accepted_source_ids=("source-a",),
        )
