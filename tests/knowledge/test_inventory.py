"""Tests for deterministic source inventory discovery."""

from pathlib import Path

import pytest

import sedna.knowledge.inventory as inventory_module
from sedna.knowledge.inventory import discover_sources, sha256_file, stable_source_id


def write_source(path: Path, content: str | bytes, *, binary: bool = False) -> None:
    """Write a source fixture, creating its parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if binary:
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
    else:
        path.write_text(content if isinstance(content, str) else content.decode(), encoding="utf-8")


def test_discover_sources_returns_stable_sorted_candidates(tmp_path: Path) -> None:
    root = tmp_path / "raw_src"
    write_source(root / "Machines" / "Lame" / "walkthrough.md", "# Lame\n")
    write_source(root / "Machines" / "Lame" / "image.png", b"png", binary=True)
    write_source(root / "Machines" / "Bashed" / "walkthrough.md", "# Bashed\n")

    first = discover_sources(root)
    second = discover_sources(root)

    assert first == second
    assert [candidate.relative_path for candidate in first] == [
        "Machines/Bashed/walkthrough.md",
        "Machines/Lame/walkthrough.md",
    ]
    assert first[1].assets[0].relative_path == "Machines/Lame/image.png"
    assert len(first[1].sha256) == 64


def test_discovery_keeps_identity_stable_when_content_changes(tmp_path: Path) -> None:
    root = tmp_path / "raw_src"
    document = root / "Machines" / "Lame" / "walkthrough.md"
    write_source(document, "# Lame\nfirst version")

    before = discover_sources(root)[0]
    write_source(document, "# Lame\nsecond version")
    after = discover_sources(root)[0]

    assert before.source_id == after.source_id == stable_source_id("Machines/Lame/walkthrough.md")
    assert before.sha256 != after.sha256


def test_discovery_namespaces_same_relative_path_by_resolved_source_root(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    relative_path = Path("Machines") / "Lame" / "walkthrough.md"
    write_source(first_root / relative_path, "# First origin\n")
    write_source(second_root / relative_path, "# Second origin\n")

    first = discover_sources(first_root)[0]
    repeated = discover_sources(first_root)[0]
    second = discover_sources(second_root)[0]

    assert first.source_id == repeated.source_id == second.source_id
    assert first.source_namespace == repeated.source_namespace
    assert first.source_namespace is not None
    assert first.source_namespace != second.source_namespace


def test_discovery_includes_pdf_and_nested_assets_but_excludes_ds_store(tmp_path: Path) -> None:
    root = tmp_path / "raw_src"
    write_source(root / "Courses" / "lesson.pdf", b"pdf", binary=True)
    write_source(root / "Courses" / "images" / "diagram.png", b"diagram", binary=True)
    write_source(root / "Courses" / ".DS_Store", b"metadata", binary=True)
    write_source(root / "notes.txt", "not a source")

    candidate = discover_sources(root)[0]

    assert candidate.suffix == ".pdf"
    assert [asset.relative_path for asset in candidate.assets] == ["Courses/images/diagram.png"]
    assert candidate.assets[0].sha256 == sha256_file(root / "Courses" / "images" / "diagram.png")


def test_discovery_ignores_symlinked_file_without_hashing_its_external_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "raw_src"
    root.mkdir()
    external = tmp_path / "external.md"
    external.write_text("external secret", encoding="utf-8")
    linked_source = root / "linked.md"
    linked_source.symlink_to(external)
    real_sha256_file = inventory_module.sha256_file

    def reject_link_hash(path: Path) -> str:
        if path == linked_source:
            raise AssertionError("inventory tried to hash a symlink target")
        return real_sha256_file(path)

    monkeypatch.setattr(inventory_module, "sha256_file", reject_link_hash)

    assert discover_sources(root) == ()


def test_discovery_does_not_descend_into_symlinked_external_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "raw_src"
    root.mkdir()
    external = tmp_path / "external"
    write_source(external / "nested" / "outside.md", "external secret")
    (root / "linked-directory").symlink_to(external, target_is_directory=True)
    real_sha256_file = inventory_module.sha256_file

    def reject_external_hash(path: Path) -> str:
        if external in path.parents:
            raise AssertionError("inventory descended outside the source root")
        return real_sha256_file(path)

    monkeypatch.setattr(inventory_module, "sha256_file", reject_external_hash)

    assert discover_sources(root) == ()
