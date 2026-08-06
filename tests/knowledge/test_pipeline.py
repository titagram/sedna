"""End-to-end tests for deterministic prepared-source ingestion."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

import sedna.knowledge.pipeline as pipeline_module
from sedna.knowledge import IngestionPipeline as PublicIngestionPipeline
from sedna.knowledge.inventory import SourceCandidate, discover_sources, stable_source_id
from sedna.knowledge.pipeline import (
    CandidateIngestionError,
    IngestionPipeline,
    SourceStructureError,
)
from sedna.knowledge.schema import DocumentType, IngestionStatus

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_PATHS = {
    "machine-walkthrough.md": "Write-ups/Machines/Example/walkthrough.md",
    "challenge-flag-only.md": "Write-ups/Challanges/Example/readme.md",
}


def _fixture_pipeline(tmp_path: Path, fixture: str) -> tuple[IngestionPipeline, SourceCandidate]:
    source_root = tmp_path / "raw_src"
    destination = source_root / FIXTURE_PATHS[fixture]
    destination.parent.mkdir(parents=True)
    destination.write_bytes((FIXTURES / fixture).read_bytes())
    candidate = discover_sources(source_root)[0]
    return IngestionPipeline(source_root, tmp_path / "knowledge"), candidate


def _write_candidate(source_root: Path, relative_path: str, content: bytes) -> SourceCandidate:
    path = source_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return discover_sources(source_root)[0]


def _tree_fingerprint(root: Path) -> tuple[tuple[str, bytes], ...]:
    return tuple(
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def test_prepare_accepted_walkthrough_returns_segments_and_manifest(tmp_path: Path) -> None:
    pipeline, candidate = _fixture_pipeline(tmp_path, "machine-walkthrough.md")

    prepared = pipeline.prepare(candidate)

    assert prepared is not None
    assert prepared.manifest.document_type == DocumentType.MACHINE_WALKTHROUGH
    assert prepared.manifest.ingestion_status == IngestionStatus.ACCEPTED
    assert prepared.segments
    assert pipeline.repository.load_manifest(candidate.source_id) == prepared.manifest
    assert pipeline.last_outcome == "accepted"


def test_pipeline_is_exported_from_public_knowledge_package() -> None:
    assert PublicIngestionPipeline is IngestionPipeline


def test_prepare_flag_only_source_writes_manifest_without_segments(tmp_path: Path) -> None:
    pipeline, candidate = _fixture_pipeline(tmp_path, "challenge-flag-only.md")

    assert pipeline.prepare(candidate) is None

    manifest = pipeline.repository.load_manifest(candidate.source_id)
    assert manifest.ingestion_status == IngestionStatus.EXCLUDED
    assert "flag_only" in manifest.quality_reason_codes
    assert not (pipeline.repository.root / "quarantine" / f"{candidate.source_id}.json").exists()
    assert pipeline.last_outcome == "excluded"


def test_prepare_skips_unchanged_source_with_same_versions(tmp_path: Path) -> None:
    pipeline, candidate = _fixture_pipeline(tmp_path, "machine-walkthrough.md")

    first = pipeline.prepare(candidate)
    second = pipeline.prepare(candidate)

    assert first is not None
    assert second is None
    assert pipeline.last_outcome == "unchanged"


def test_incremental_skip_requires_every_reproducibility_version(tmp_path: Path) -> None:
    pipeline, candidate = _fixture_pipeline(tmp_path, "machine-walkthrough.md")
    prepared = pipeline.prepare(candidate)
    assert prepared is not None
    manifest_path = pipeline.repository.root / "manifests" / f"{candidate.source_id}.json"
    baseline = json.loads(manifest_path.read_text(encoding="utf-8"))

    for field in (
        "schema_version",
        "parser_id",
        "parser_version",
        "extractor_id",
        "extractor_version",
    ):
        changed = json.loads(json.dumps(baseline))
        changed["extraction"][field] = "old"
        manifest_path.write_text(json.dumps(changed), encoding="utf-8")

        assert pipeline.prepare(candidate) is not None
        assert pipeline.last_outcome == "accepted"


def test_stale_candidate_never_skips_changed_source(tmp_path: Path) -> None:
    pipeline, candidate = _fixture_pipeline(tmp_path, "machine-walkthrough.md")
    prepared = pipeline.prepare(candidate)
    assert prepared is not None
    previous_manifest = pipeline.repository.load_manifest(candidate.source_id)
    original = candidate.path.read_bytes()
    candidate.path.write_bytes(original.replace(b"enumeration", b"enumeratioN", 1))

    with pytest.raises(CandidateIngestionError, match="changed since inventory") as error:
        pipeline.prepare(candidate)

    assert error.value.reason_code == "stale_candidate"
    assert pipeline.last_outcome == "failed"
    assert pipeline.repository.load_manifest(candidate.source_id) == previous_manifest


def test_pdf_is_quarantined_as_unsupported_while_preserving_reference_type(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "raw_src"
    candidate = _write_candidate(source_root, "References/Linux-Cheat-Sheet.pdf", b"%PDF-1.4\n")
    pipeline = IngestionPipeline(source_root, tmp_path / "knowledge")

    assert pipeline.prepare(candidate) is None

    manifest = pipeline.repository.load_manifest(candidate.source_id)
    record = json.loads(
        (pipeline.repository.root / "quarantine" / f"{candidate.source_id}.json").read_text()
    )
    assert manifest.document_type == DocumentType.CHEATSHEET_REFERENCE
    assert manifest.ingestion_status == IngestionStatus.QUARANTINED
    assert "unsupported_parser" in manifest.quarantine_reasons
    assert record["reason_codes"] == ["unsupported_parser"]
    assert pipeline.last_outcome == "quarantined"


def test_invalid_utf8_markdown_is_quarantined(tmp_path: Path) -> None:
    source_root = tmp_path / "raw_src"
    candidate = _write_candidate(source_root, "Imported/broken.md", b"# Broken\n\xff\xfe")
    pipeline = IngestionPipeline(source_root, tmp_path / "knowledge")

    assert pipeline.prepare(candidate) is None

    manifest = pipeline.repository.load_manifest(candidate.source_id)
    record = json.loads(
        (pipeline.repository.root / "quarantine" / f"{candidate.source_id}.json").read_text()
    )
    assert manifest.ingestion_status == IngestionStatus.QUARANTINED
    assert manifest.quarantine_reasons == ("invalid_encoding",)
    assert record["reason_codes"] == ["invalid_encoding"]


def test_source_structure_error_is_quarantined_but_value_error_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline, candidate = _fixture_pipeline(tmp_path, "machine-walkthrough.md")

    def invalid_markdown(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise SourceStructureError("source-specific parse failure")

    monkeypatch.setattr(pipeline_module, "parse_markdown", invalid_markdown)
    assert pipeline.prepare(candidate) is None
    manifest = pipeline.repository.load_manifest(candidate.source_id)
    assert manifest.quarantine_reasons == ("structural_parse_error",)

    candidate.path.write_bytes(candidate.path.read_bytes() + b"\n<!-- changed -->\n")
    candidate = discover_sources(pipeline.source_root)[0]

    def broken_parser(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise ValueError("implementation bug")

    monkeypatch.setattr(pipeline_module, "parse_markdown", broken_parser)
    with pytest.raises(CandidateIngestionError, match="structural parser failed") as error:
        pipeline.prepare(candidate)
    assert error.value.reason_code == "parser_failure"
    assert pipeline.last_outcome == "failed"


def test_profile_and_segment_value_errors_are_explicit_implementation_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline, candidate = _fixture_pipeline(tmp_path, "machine-walkthrough.md")

    def broken_profile(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise ValueError("profile bug")

    monkeypatch.setattr(pipeline_module, "apply_profile", broken_profile)
    with pytest.raises(CandidateIngestionError) as profile_error:
        pipeline.prepare(candidate)
    assert profile_error.value.reason_code == "profile_failure"

    monkeypatch.undo()

    def broken_segmenter(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise ValueError("segmenter bug")

    monkeypatch.setattr(pipeline_module, "segment_document", broken_segmenter)
    with pytest.raises(CandidateIngestionError) as segment_error:
        pipeline.prepare(candidate)
    assert segment_error.value.reason_code == "segmenter_failure"


def test_manifest_title_is_sanitized_while_parsed_document_keeps_raw_text(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "raw_src"
    final_hash = "0123456789abcdef0123456789abcdef"
    text = f"""# Root {final_hash} HTB{{secret_value}}

## Enumeration

We ran a scan against the service and recorded the response.

```text
nmap target
```

## Result

The scan showed that HTTP was reachable.
""".encode()
    candidate = _write_candidate(source_root, "Write-ups/Machines/FlagTitle/walkthrough.md", text)
    pipeline = IngestionPipeline(source_root, tmp_path / "knowledge")

    prepared = pipeline.prepare(candidate)

    assert prepared is not None
    assert "HTB{" not in prepared.manifest.title
    assert final_hash not in prepared.manifest.title
    assert "HTB{secret_value}" in prepared.document.blocks[0].text
    assert all("HTB{secret_value}" not in segment.text for segment in prepared.segments)
    persisted = (pipeline.repository.root / "manifests" / f"{candidate.source_id}.json").read_text()
    assert "HTB{secret_value}" not in persisted


def test_manifest_contains_stable_inventory_asset_refs(tmp_path: Path) -> None:
    pipeline, _ = _fixture_pipeline(tmp_path, "machine-walkthrough.md")
    asset = pipeline.source_root / "Write-ups/Machines/Example/images/evidence.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"image bytes")
    candidate = discover_sources(pipeline.source_root)[0]

    prepared = pipeline.prepare(candidate)

    assert prepared is not None
    assert [(item.path, item.sha256) for item in prepared.manifest.assets] == [
        (
            "Write-ups/Machines/Example/images/evidence.png",
            hashlib.sha256(b"image bytes").hexdigest(),
        )
    ]


def test_segment_assets_are_retrieval_safe_while_parsed_assets_remain_raw(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "raw_src"
    text = b"""# Example machine

## Enumeration

We ran a scan and recorded the result.

![HTB{alt_secret}](HTB{target_secret}.png \"HTB{title_secret}\")

```text
nmap target
```

## Result

The scan showed HTTP was reachable.
"""
    candidate = _write_candidate(
        source_root,
        "Write-ups/Machines/AssetLeak/walkthrough.md",
        text,
    )
    pipeline = IngestionPipeline(source_root, tmp_path / "knowledge")

    prepared = pipeline.prepare(candidate)

    assert prepared is not None
    raw_asset = prepared.document.assets[0]
    assert "HTB%7B" in raw_asset.target
    assert "HTB{" in (raw_asset.alt_text or "")
    assert "HTB{" in (raw_asset.title or "")
    safe_assets = tuple(asset for segment in prepared.segments for asset in segment.assets)
    assert safe_assets
    serialized = json.dumps(
        [asset.model_dump(mode="json") for asset in safe_assets],
        sort_keys=True,
    )
    assert "HTB{" not in serialized
    assert "HTB%7B" not in serialized
    assert all(
        set(type(asset).model_fields) == {"asset_index", "target", "start_line", "end_line"}
        for asset in safe_assets
    )


def test_corrupt_existing_manifest_fails_without_overwriting_it(tmp_path: Path) -> None:
    pipeline, candidate = _fixture_pipeline(tmp_path, "machine-walkthrough.md")
    manifest_path = pipeline.repository.root / "manifests" / f"{candidate.source_id}.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_bytes(b"{corrupt")

    with pytest.raises(ValueError, match="invalid manifest"):
        pipeline.prepare(candidate)

    assert manifest_path.read_bytes() == b"{corrupt"
    assert pipeline.last_outcome == "failed"


@pytest.mark.parametrize("escape_kind", ("file", "parent"))
def test_source_symlink_escape_is_rejected(tmp_path: Path, escape_kind: str) -> None:
    source_root = tmp_path / "raw_src"
    source_root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside", encoding="utf-8")
    if escape_kind == "file":
        relative_path = "link.md"
        (source_root / relative_path).symlink_to(outside)
    else:
        outside_directory = tmp_path / "outside"
        outside_directory.mkdir()
        (outside_directory / "link.md").write_text("# Outside", encoding="utf-8")
        (source_root / "linked").symlink_to(outside_directory, target_is_directory=True)
        relative_path = "linked/link.md"
    path = source_root / relative_path
    content = path.read_bytes()
    candidate = SourceCandidate(
        source_id=stable_source_id(relative_path),
        path=path,
        relative_path=relative_path,
        suffix=".md",
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        assets=(),
    )
    pipeline = IngestionPipeline(source_root, tmp_path / "knowledge")

    with pytest.raises(CandidateIngestionError, match="safe regular file"):
        pipeline.prepare(candidate)

    assert pipeline.last_outcome == "failed"
    assert not list((tmp_path / "knowledge").rglob("*.json"))


def test_candidate_identity_must_match_confined_relative_path(tmp_path: Path) -> None:
    pipeline, candidate = _fixture_pipeline(tmp_path, "machine-walkthrough.md")
    escaped = replace(
        candidate,
        source_id=stable_source_id("../outside.md"),
        relative_path="../outside.md",
        path=tmp_path / "outside.md",
    )

    with pytest.raises(CandidateIngestionError, match="safe relative path"):
        pipeline.prepare(escaped)


def test_source_root_alias_keeps_inventory_candidates_valid(tmp_path: Path) -> None:
    real_source_root = tmp_path / "real-raw"
    _write_candidate(
        real_source_root,
        FIXTURE_PATHS["machine-walkthrough.md"],
        (FIXTURES / "machine-walkthrough.md").read_bytes(),
    )
    source_alias = tmp_path / "raw-alias"
    source_alias.symlink_to(real_source_root, target_is_directory=True)
    candidate = discover_sources(source_alias)[0]
    pipeline = IngestionPipeline(source_alias, tmp_path / "knowledge")

    prepared = pipeline.prepare(candidate)

    assert prepared is not None
    assert pipeline.last_outcome == "accepted"


def test_pipeline_context_closes_source_and_repository_descriptors(tmp_path: Path) -> None:
    pipeline, candidate = _fixture_pipeline(tmp_path, "machine-walkthrough.md")

    with pipeline:
        assert pipeline.prepare(candidate) is not None

    pipeline.close()
    with pytest.raises(RuntimeError, match="pipeline is closed"):
        pipeline.prepare(candidate)
    with pytest.raises(RuntimeError, match="repository is closed"):
        pipeline.repository.load_manifest(candidate.source_id)


def test_pipeline_never_writes_to_source_tree(tmp_path: Path) -> None:
    pipeline, candidate = _fixture_pipeline(tmp_path, "machine-walkthrough.md")
    before = _tree_fingerprint(pipeline.source_root)

    prepared = pipeline.prepare(candidate)

    assert prepared is not None
    assert _tree_fingerprint(pipeline.source_root) == before
    assert pipeline.source_root not in pipeline.repository.root.parents


def test_pipeline_rejects_a_knowledge_root_inside_the_immutable_source_tree(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "raw_src"
    source_root.mkdir()

    with pytest.raises(ValueError, match="outside the immutable source root"):
        IngestionPipeline(source_root, source_root / "knowledge")

    assert not (source_root / "knowledge").exists()


def test_reprocessing_an_old_quarantine_removes_stale_record(tmp_path: Path) -> None:
    source_root = tmp_path / "raw_src"
    relative_path = "Write-ups/Machines/Example/walkthrough.md"
    candidate = _write_candidate(source_root, relative_path, b"ambiguous prose")
    pipeline = IngestionPipeline(source_root, tmp_path / "knowledge")
    assert pipeline.prepare(candidate) is None
    quarantine_path = pipeline.repository.root / "quarantine" / f"{candidate.source_id}.json"
    assert quarantine_path.is_file()

    content = (FIXTURES / "machine-walkthrough.md").read_bytes()
    candidate.path.write_bytes(content)
    new_candidate = discover_sources(source_root)[0]

    assert new_candidate.source_id == candidate.source_id

    prepared = pipeline.prepare(new_candidate)

    assert prepared is not None
    assert not quarantine_path.exists()


def test_new_associated_asset_makes_original_candidate_stale_before_skip(
    tmp_path: Path,
) -> None:
    pipeline, candidate = _fixture_pipeline(tmp_path, "machine-walkthrough.md")
    assert pipeline.prepare(candidate) is not None
    new_asset = candidate.path.parent / "new.png"
    new_asset.write_bytes(b"new asset")

    with pytest.raises(CandidateIngestionError) as error:
        pipeline.prepare(candidate)

    assert error.value.source_id == candidate.source_id
    assert error.value.reason_code == "stale_asset_inventory"
    assert pipeline.last_outcome == "failed"


@pytest.mark.parametrize(
    "corruption",
    ("invalid_json", "reason_codes", "parser_profile", "extraction"),
)
def test_unchanged_quarantine_requires_a_strict_matching_record(
    tmp_path: Path,
    corruption: str,
) -> None:
    source_root = tmp_path / "raw_src"
    candidate = _write_candidate(source_root, "Imported/source.md", b"ambiguous prose")
    pipeline = IngestionPipeline(source_root, tmp_path / "knowledge")
    assert pipeline.prepare(candidate) is None
    quarantine_path = pipeline.repository.root / "quarantine" / f"{candidate.source_id}.json"
    if corruption == "invalid_json":
        quarantine_path.write_text("{corrupt", encoding="utf-8")
    elif corruption == "reason_codes":
        payload = json.loads(quarantine_path.read_text(encoding="utf-8"))
        payload["reason_codes"] = ["wrong_reason"]
        quarantine_path.write_text(json.dumps(payload), encoding="utf-8")
    elif corruption == "parser_profile":
        payload = json.loads(quarantine_path.read_text(encoding="utf-8"))
        payload["parser_profile"] = "wrong_profile"
        quarantine_path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        payload = json.loads(quarantine_path.read_text(encoding="utf-8"))
        payload["extraction"]["extractor_version"] = "old"
        quarantine_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CandidateIngestionError) as error:
        pipeline.prepare(candidate)

    assert error.value.reason_code == "canonical_state_mismatch"
    assert pipeline.last_outcome == "failed"


def test_stale_asset_candidate_is_rejected_before_manifest_skip(tmp_path: Path) -> None:
    pipeline, _ = _fixture_pipeline(tmp_path, "machine-walkthrough.md")
    asset = pipeline.source_root / "Write-ups/Machines/Example/image.png"
    asset.write_bytes(b"old")
    candidate = discover_sources(pipeline.source_root)[0]
    assert pipeline.prepare(candidate) is not None
    asset.write_bytes(b"new")

    with pytest.raises(CandidateIngestionError, match="asset changed since inventory") as error:
        pipeline.prepare(candidate)

    assert error.value.source_id == candidate.source_id
    assert error.value.reason_code == "stale_asset"


def test_close_racing_with_source_open_cannot_invalidate_inflight_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, candidate = _fixture_pipeline(tmp_path, "machine-walkthrough.md")
    real_open = pipeline_module.os.open
    child_open_started = threading.Event()
    allow_child_open = threading.Event()
    source_fd = pipeline._source_fd

    def blocking_open(
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if path == "Write-ups" and dir_fd is not None:
            child_open_started.set()
            assert allow_child_open.wait(timeout=5)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(pipeline_module.os, "open", blocking_open)
    result: list[bytes | BaseException] = []

    def reader() -> None:
        try:
            result.append(
                pipeline._read_relative_file(candidate.relative_path, candidate.source_id)
            )
        except BaseException as error:
            result.append(error)

    thread = threading.Thread(target=reader)
    thread.start()
    assert child_open_started.wait(timeout=5)
    pipeline.close()
    assert source_fd is not None
    allow_child_open.set()
    thread.join(timeout=5)

    assert result == [candidate.path.read_bytes()]
