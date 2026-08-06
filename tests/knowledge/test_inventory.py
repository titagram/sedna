"""Tests for deterministic source inventory discovery."""

from pathlib import Path

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


def test_discovery_includes_pdf_and_nested_assets_but_excludes_ds_store(tmp_path: Path) -> None:
    root = tmp_path / "raw_src"
    write_source(root / "Courses" / "lesson.pdf", b"pdf", binary=True)
    write_source(root / "Courses" / "images" / "diagram.png", b"diagram", binary=True)
    write_source(root / "Courses" / ".DS_Store", b"metadata", binary=True)
    write_source(root / "notes.txt", "not a source")

    candidate = discover_sources(root)[0]

    assert candidate.suffix == ".pdf"
    assert [asset.relative_path for asset in candidate.assets] == [
        "Courses/images/diagram.png"
    ]
    assert candidate.assets[0].sha256 == sha256_file(root / "Courses" / "images" / "diagram.png")

