"""Tests for deterministic, explainable source classification."""

from pathlib import Path

import pytest

from sedna.knowledge.classifier import ClassificationResult, classify_document
from sedna.knowledge.inventory import SourceCandidate
from sedna.knowledge.schema import (
    DocumentType,
    IngestionStatus,
    KnowledgeRole,
    SourceQuality,
)

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_PATHS = {
    "lesson.md": "Write-ups/Academy/Recon/lesson.md",
    "machine-walkthrough.md": "Write-ups/Machines/Example/walkthrough.md",
    "challenge-flag-only.md": "Write-ups/Challanges/Example/readme.md",
    "external-stub.md": "Write-ups/Machines/External/readme.md",
    "empty.md": "Write-ups/Academy/Empty/empty.md",
}


def fixture_candidate(fixture: str) -> tuple[SourceCandidate, str]:
    """Build a candidate whose taxonomy path represents the fixture family."""
    path = FIXTURES / fixture
    return (
        SourceCandidate(
            source_id=f"source-{fixture}",
            path=path,
            relative_path=FIXTURE_PATHS[fixture],
            suffix=path.suffix,
            sha256="a" * 64,
            size_bytes=path.stat().st_size,
            assets=(),
        ),
        path.read_text(encoding="utf-8"),
    )


@pytest.mark.parametrize(
    ("fixture", "expected_type", "expected_status"),
    [
        ("lesson.md", DocumentType.LESSON, IngestionStatus.ACCEPTED),
        (
            "machine-walkthrough.md",
            DocumentType.MACHINE_WALKTHROUGH,
            IngestionStatus.ACCEPTED,
        ),
        ("challenge-flag-only.md", DocumentType.EXCLUDED, IngestionStatus.EXCLUDED),
        ("external-stub.md", DocumentType.EXTERNAL_STUB, IngestionStatus.EXCLUDED),
        ("empty.md", DocumentType.EXCLUDED, IngestionStatus.EXCLUDED),
    ],
)
def test_classify_representative_sources(
    fixture: str,
    expected_type: DocumentType,
    expected_status: IngestionStatus,
) -> None:
    candidate, text = fixture_candidate(fixture)

    result = classify_document(candidate, text)

    assert isinstance(result, ClassificationResult)
    assert result.document_type == expected_type
    assert result.ingestion_status == expected_status
    assert result.reasons


def test_machine_walkthrough_uses_case_study_lane_and_github_profile() -> None:
    candidate, text = fixture_candidate("machine-walkthrough.md")

    result = classify_document(candidate, text)

    assert result.knowledge_role == KnowledgeRole.CASE_STUDY
    assert result.quality == SourceQuality.COMPLETE
    assert result.parser_profile == "github_walkthrough"


def test_table_dominant_academy_source_is_a_cheatsheet(tmp_path: Path) -> None:
    path = tmp_path / "cheatsheet.md"
    text = """# SQL reference

| Check | Evidence |
| --- | --- |
| Version | Banner |
| Access | Response code |
"""
    path.write_text(text, encoding="utf-8")
    candidate = SourceCandidate(
        source_id="source-cheatsheet",
        path=path,
        relative_path="Write-ups/Academy/SQL/cheatsheet.md",
        suffix=".md",
        sha256="b" * 64,
        size_bytes=len(text.encode()),
        assets=(),
    )

    result = classify_document(candidate, text)

    assert result.document_type == DocumentType.CHEATSHEET_REFERENCE
    assert result.knowledge_role == KnowledgeRole.REFERENCE
    assert result.quality == SourceQuality.PARTIAL
    assert result.parser_profile == "academy_obsidian"


def test_unknown_nonempty_source_is_quarantined_as_ambiguous(tmp_path: Path) -> None:
    path = tmp_path / "unknown.md"
    text = "An isolated observation with no source-family or procedural context."
    path.write_text(text, encoding="utf-8")
    candidate = SourceCandidate(
        source_id="source-unknown",
        path=path,
        relative_path="Imported/unknown.md",
        suffix=".md",
        sha256="c" * 64,
        size_bytes=len(text.encode()),
        assets=(),
    )

    result = classify_document(candidate, text)

    assert result.document_type == DocumentType.EXCLUDED
    assert result.ingestion_status == IngestionStatus.QUARANTINED
    assert result.parser_profile == "none"
    assert "ambiguous" in result.reasons


@pytest.mark.parametrize(
    "text",
    [
        """# Example HTB Challenge - Flag Writeup

[Official challenge](https://app.hackthebox.com/challenges/example)

---

## Challenge Overview

Analyze the supplied material and recover the hidden value.

---

## Final Flag

```
HTB{not_a_procedural_account}
```
""",
        """<!-- archived values
### example.htb
user
```
0123456789abcdef0123456789abcdef
```
root
```
abcdef0123456789abcdef0123456789
```
-->
""",
    ],
)
def test_metadata_separators_and_user_root_values_do_not_imply_procedure(
    tmp_path: Path, text: str
) -> None:
    path = tmp_path / "readme.md"
    path.write_text(text, encoding="utf-8")
    candidate = SourceCandidate(
        source_id="source-flags",
        path=path,
        relative_path="Write-ups/Challanges/Example/readme.md",
        suffix=".md",
        sha256="e" * 64,
        size_bytes=len(text.encode()),
        assets=(),
    )

    result = classify_document(candidate, text)

    assert result.document_type == DocumentType.EXCLUDED
    assert result.ingestion_status == IngestionStatus.EXCLUDED
    assert "flag_only" in result.reasons


def test_technical_reference_pdf_is_quarantined_until_supported(tmp_path: Path) -> None:
    path = tmp_path / "web-cheatsheet.pdf"
    path.write_bytes(b"%PDF-1.4")
    candidate = SourceCandidate(
        source_id="source-pdf",
        path=path,
        relative_path="01_information-gathering/web-cheatsheet.pdf",
        suffix=".pdf",
        sha256="d" * 64,
        size_bytes=8,
        assets=(),
    )

    result = classify_document(candidate, None)

    assert result.document_type == DocumentType.CHEATSHEET_REFERENCE
    assert result.quality == SourceQuality.PARTIAL
    assert result.ingestion_status == IngestionStatus.QUARANTINED
    assert "pdf_parser_unavailable" in result.reasons
