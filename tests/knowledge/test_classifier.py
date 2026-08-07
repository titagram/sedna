"""Tests for deterministic, explainable source classification."""

from pathlib import Path

import pytest

import sedna.knowledge.classifier as classifier_module
from sedna.knowledge.classifier import ClassificationResult, classify_document
from sedna.knowledge.inventory import SourceCandidate
from sedna.knowledge.schema import (
    DocumentType,
    IngestionStatus,
    KnowledgeRole,
    SourceQuality,
)

FIXTURES = Path(__file__).parent / "fixtures"
REAL_CORPUS = Path(__file__).parents[2] / "raw_src"
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


def test_compact_delimiter_table_with_local_technical_cells_is_a_cheatsheet(
    tmp_path: Path,
) -> None:
    path = tmp_path / "compact-cheatsheet.md"
    text = """# Service reference

| Command | Description |
|-|-|
| `nmap -sV TARGET` | Identify service versions locally |
"""
    path.write_text(text, encoding="utf-8")
    candidate = SourceCandidate(
        source_id="source-compact-cheatsheet",
        path=path,
        relative_path="Write-ups/Academy/Recon/compact-cheatsheet.md",
        suffix=".md",
        sha256="a" * 64,
        size_bytes=len(text.encode()),
        assets=(),
    )

    result = classify_document(candidate, text)

    assert result.document_type == DocumentType.CHEATSHEET_REFERENCE
    assert result.ingestion_status == IngestionStatus.ACCEPTED


@pytest.mark.parametrize(
    "relative_path",
    [
        "Write-ups/Academy/Exploitation/Using CrackMapExec.md",
        "01_information-gathering/Academy/Using CrackMapExec.md",
    ],
)
def test_reference_family_navigation_only_note_is_an_external_stub(
    tmp_path: Path,
    relative_path: str,
) -> None:
    path = tmp_path / "navigation-only.md"
    text = """# Using CrackMapExec

Tags: #🧑‍🎓
Related to:
See also:
Previous: [[HTB Academy]]
"""
    path.write_text(text, encoding="utf-8")
    candidate = SourceCandidate(
        source_id="source-navigation-only",
        path=path,
        relative_path=relative_path,
        suffix=".md",
        sha256="4" * 64,
        size_bytes=len(text.encode()),
        assets=(),
    )

    result = classify_document(candidate, text)

    assert result.document_type == DocumentType.EXTERNAL_STUB
    assert result.quality == SourceQuality.MINIMAL
    assert result.parser_profile == "none"
    assert result.ingestion_status == IngestionStatus.EXCLUDED
    assert "no_local_substance" in result.reasons


def test_wiki_link_scanner_has_a_deterministic_linear_work_bound() -> None:
    scanner = getattr(classifier_module, "_scan_wiki_links", None)
    assert callable(scanner), "classifier must expose instrumentable wiki scanning"
    unmatched = "[" * 40_000

    result = scanner(unmatched)

    assert result.local_text == unmatched
    assert result.reference_link_count == 0
    assert result.scanned_characters <= len(unmatched)


def test_wiki_link_scanner_handles_aliases_surrounding_prose_and_embeds() -> None:
    scanner = getattr(classifier_module, "_scan_wiki_links", None)
    assert callable(scanner), "classifier must expose instrumentable wiki scanning"
    text = (
        "Review [[Academy/Guide|the guide]] before comparing five local observations. "
        "Keep ![[diagram.png]] as an asset."
    )

    result = scanner(text)

    assert result.local_text == (
        "Review  before comparing five local observations. Keep ![[diagram.png]] as an asset."
    )
    assert result.reference_link_count == 1
    assert result.scanned_characters <= len(text)


def test_wiki_link_scanner_preserves_malformed_text_and_recovers_next_line() -> None:
    scanner = getattr(classifier_module, "_scan_wiki_links", None)
    assert callable(scanner), "classifier must expose instrumentable wiki scanning"
    text = "prefix [[unclosed\nsuffix [[valid|label]] end"

    result = scanner(text)

    assert result.local_text == "prefix [[unclosed\nsuffix  end"
    assert result.reference_link_count == 1
    assert result.scanned_characters <= len(text)


def test_academy_external_link_label_alone_is_not_local_substance(
    tmp_path: Path,
) -> None:
    path = tmp_path / "external-course.md"
    text = """# Network enumeration

[Complete course covering network enumeration concepts](https://example.com/course)
"""
    path.write_text(text, encoding="utf-8")
    candidate = SourceCandidate(
        source_id="source-external-course",
        path=path,
        relative_path="Write-ups/Academy/Recon/external-course.md",
        suffix=".md",
        sha256="6" * 64,
        size_bytes=len(text.encode()),
        assets=(),
    )

    result = classify_document(candidate, text)

    assert result.document_type == DocumentType.EXTERNAL_STUB
    assert result.ingestion_status == IngestionStatus.EXCLUDED
    assert "no_local_substance" in result.reasons


@pytest.mark.parametrize(
    "link_only_body",
    [
        (
            "[Complete course covering network enumeration concepts][guide]\n\n"
            "[guide]: https://example.com/course\n"
        ),
        (
            "[Complete course with an escaped \\] label][guide]\n\n"
            "[guide]: https://example.com/course\n"
        ),
        (
            "[Complete course covering network enumeration concepts][]\n\n"
            "[Complete course covering network enumeration concepts]: "
            "https://example.com/course\n"
        ),
        (
            "[Complete course covering network enumeration concepts]\n\n"
            "[Complete course covering network enumeration concepts]: "
            "https://example.com/course\n"
        ),
        (
            '<a title="1 > 0" href="https://example.com/course">'
            "Complete course covering network enumeration concepts"
            "</a>\n"
        ),
    ],
)
def test_academy_reference_or_html_link_label_is_not_local_substance(
    tmp_path: Path,
    link_only_body: str,
) -> None:
    path = tmp_path / "external-reference.md"
    text = f"# Network enumeration\n\n{link_only_body}"
    path.write_text(text, encoding="utf-8")
    candidate = SourceCandidate(
        source_id="source-external-reference",
        path=path,
        relative_path="Write-ups/Academy/Recon/external-reference.md",
        suffix=".md",
        sha256="1" * 64,
        size_bytes=len(text.encode()),
        assets=(),
    )

    result = classify_document(candidate, text)

    assert result.document_type == DocumentType.EXTERNAL_STUB
    assert result.ingestion_status == IngestionStatus.EXCLUDED
    assert "no_local_substance" in result.reasons


def test_academy_table_with_only_external_link_body_is_not_a_cheatsheet(
    tmp_path: Path,
) -> None:
    path = tmp_path / "external-table.md"
    text = """# Network resources

| Resource | Details |
| --- | --- |
|  | ![External catalog](https://example.com/catalog.png) |
"""
    path.write_text(text, encoding="utf-8")
    candidate = SourceCandidate(
        source_id="source-external-table",
        path=path,
        relative_path="Write-ups/Academy/Recon/external-table.md",
        suffix=".md",
        sha256="2" * 64,
        size_bytes=len(text.encode()),
        assets=(),
    )

    result = classify_document(candidate, text)

    assert result.document_type == DocumentType.EXTERNAL_STUB
    assert result.ingestion_status == IngestionStatus.EXCLUDED
    assert "no_local_substance" in result.reasons


@pytest.mark.parametrize(
    "explained_body",
    [
        (
            "Read the [official syntax][guide], then compare its examples against "
            "the response observed in the local lab.\n\n"
            "[guide]: https://example.com/syntax\n"
        ),
        (
            'Read the <a title="1 > 0" href="https://example.com/syntax">'
            "official syntax</a>, "
            "then compare its examples against the response observed locally.\n"
        ),
    ],
)
def test_explanatory_prose_around_reference_or_html_link_remains_substance(
    tmp_path: Path,
    explained_body: str,
) -> None:
    path = tmp_path / "explained-reference.md"
    text = f"# Network enumeration\n\n{explained_body}"
    path.write_text(text, encoding="utf-8")
    candidate = SourceCandidate(
        source_id="source-explained-reference",
        path=path,
        relative_path="Write-ups/Academy/Recon/explained-reference.md",
        suffix=".md",
        sha256="3" * 64,
        size_bytes=len(text.encode()),
        assets=(),
    )

    result = classify_document(candidate, text)

    assert result.document_type == DocumentType.LESSON
    assert result.ingestion_status == IngestionStatus.ACCEPTED


def test_explanatory_text_around_academy_link_remains_local_substance(
    tmp_path: Path,
) -> None:
    path = tmp_path / "explained-link.md"
    text = """# Network enumeration

Read the [official syntax](https://example.com/syntax), then compare its examples
against the response observed in the local lab.
"""
    path.write_text(text, encoding="utf-8")
    candidate = SourceCandidate(
        source_id="source-explained-link",
        path=path,
        relative_path="Write-ups/Academy/Recon/explained-link.md",
        suffix=".md",
        sha256="7" * 64,
        size_bytes=len(text.encode()),
        assets=(),
    )

    result = classify_document(candidate, text)

    assert result.document_type == DocumentType.LESSON
    assert result.ingestion_status == IngestionStatus.ACCEPTED


def test_academy_empty_fence_is_not_code_or_local_substance(tmp_path: Path) -> None:
    path = tmp_path / "empty-example.md"
    text = "# Network enumeration\n\n## Example command\n\n```bash\n   \n```\n"
    path.write_text(text, encoding="utf-8")
    candidate = SourceCandidate(
        source_id="source-empty-example",
        path=path,
        relative_path="Write-ups/Academy/Recon/empty-example.md",
        suffix=".md",
        sha256="8" * 64,
        size_bytes=len(text.encode()),
        assets=(),
    )

    result = classify_document(candidate, text)

    assert result.document_type == DocumentType.EXCLUDED
    assert result.ingestion_status == IngestionStatus.EXCLUDED
    assert "no_local_substance" in result.reasons


def test_machine_headings_and_empty_fence_are_not_procedural(tmp_path: Path) -> None:
    path = tmp_path / "empty-walkthrough.md"
    text = """# Example machine

## Enumeration

```text

```
"""
    path.write_text(text, encoding="utf-8")
    candidate = SourceCandidate(
        source_id="source-empty-walkthrough",
        path=path,
        relative_path="Write-ups/Machines/Example/empty-walkthrough.md",
        suffix=".md",
        sha256="9" * 64,
        size_bytes=len(text.encode()),
        assets=(),
    )

    result = classify_document(candidate, text)

    assert result.document_type == DocumentType.EXCLUDED
    assert result.ingestion_status == IngestionStatus.QUARANTINED
    assert "ambiguous" in result.reasons


def test_machine_link_label_does_not_supply_action_and_result_language(
    tmp_path: Path,
) -> None:
    path = tmp_path / "linked-summary.md"
    text = """# Example machine

## External notes

[We ran a scan and we found an exposed service](https://example.com/notes)
"""
    path.write_text(text, encoding="utf-8")
    candidate = SourceCandidate(
        source_id="source-linked-summary",
        path=path,
        relative_path="Write-ups/Machines/Example/linked-summary.md",
        suffix=".md",
        sha256="0" * 64,
        size_bytes=len(text.encode()),
        assets=(),
    )

    result = classify_document(candidate, text)

    assert result.document_type == DocumentType.EXCLUDED
    assert result.ingestion_status == IngestionStatus.QUARANTINED
    assert "ambiguous" in result.reasons


@pytest.mark.skipif(not REAL_CORPUS.is_dir(), reason="real source corpus is unavailable")
def test_real_academy_stubs_are_excluded_without_losing_concise_cheatsheets() -> None:
    stub_paths = (
        "Write-ups/Academy/01. Pre-Engagement/"
        "03a. Introduction to Windows Command Line/"
        "Introduction to Windows Command Line.md",
        "Write-ups/Academy/03. Vulnerability Assessment/"
        "14. Vulnerability Assessment/Vulnerability Assessment.md",
        "Write-ups/Academy/04. Exploitation/21a. Using CrackMapExec/Using CrackMapExec.md",
        "Write-ups/Academy/05. Web Exploitation/26a. Blind SQL Injection/Blind SQL Injection.md",
        "Write-ups/Academy/05. Web Exploitation/"
        "31g. Attacking Authentication Mechanisms/"
        "Attacking Authentication Mechanisms.md",
        "Write-ups/Academy/08. Proof-of-Concept/"
        "34b. Stack-Based Buffer Overflows on Linux x86/"
        "Stack-Based Buffer Overflows on Linux x86.md",
        "Write-ups/Academy/09. Post-Engagement/"
        "35. Documentation & Reporting/Documentation & Reporting.md",
        "Write-ups/Academy/09. Post-Engagement/"
        "36. Attacking Enterprise Networks/Attacking Enterprise Networks.md",
        "Write-ups/Academy/10. Misc/MacOS Fundamentals/MacOS Fundamentals.md",
    )
    cheatsheet_paths = (
        "Write-ups/Academy/04. Exploitation/"
        "21d. Active Directory BloodHound/Active Directory BloodHound.md",
        "Write-ups/Academy/05. Web Exploitation/22. Using Web Proxies/Using Web Proxies.md",
    )

    stub_results = tuple(_classify_real_source(path) for path in stub_paths)
    cheatsheet_results = tuple(_classify_real_source(path) for path in cheatsheet_paths)

    assert len(stub_results) == 9
    assert all(
        result.document_type == DocumentType.EXTERNAL_STUB
        and result.ingestion_status == IngestionStatus.EXCLUDED
        and "no_local_substance" in result.reasons
        for result in stub_results
    )
    assert all(
        result.document_type == DocumentType.CHEATSHEET_REFERENCE
        and result.ingestion_status == IngestionStatus.ACCEPTED
        for result in cheatsheet_results
    )


def _classify_real_source(relative_path: str) -> ClassificationResult:
    path = REAL_CORPUS / relative_path
    text = path.read_text(encoding="utf-8")
    candidate = SourceCandidate(
        source_id=f"real-{path.stem}",
        path=path,
        relative_path=relative_path,
        suffix=path.suffix,
        sha256="5" * 64,
        size_bytes=path.stat().st_size,
        assets=(),
    )
    return classify_document(candidate, text)


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


def test_atx_user_root_hash_sections_are_flag_only_not_procedural(tmp_path: Path) -> None:
    path = tmp_path / "flags.md"
    text = """# Minimal machine record

## User

```
0123456789abcdef0123456789abcdef
```

## Root

```
abcdef0123456789abcdef0123456789
```
"""
    path.write_text(text, encoding="utf-8")
    candidate = SourceCandidate(
        source_id="source-atx-flags",
        path=path,
        relative_path="Write-ups/Machines/Example/flags.md",
        suffix=".md",
        sha256="f" * 64,
        size_bytes=len(text.encode()),
        assets=(),
    )

    result = classify_document(candidate, text)

    assert result.document_type == DocumentType.EXCLUDED
    assert result.ingestion_status == IngestionStatus.EXCLUDED
    assert "flag_only" in result.reasons


def test_unclosed_fence_at_eof_is_a_procedural_code_block(tmp_path: Path) -> None:
    path = tmp_path / "unclosed.md"
    text = """# Example machine

## Enumeration

The service response was recorded for the next investigative step.

```text
80/tcp open http
"""
    path.write_text(text, encoding="utf-8")
    candidate = SourceCandidate(
        source_id="source-unclosed-fence",
        path=path,
        relative_path="Write-ups/Machines/Example/unclosed.md",
        suffix=".md",
        sha256="1" * 64,
        size_bytes=len(text.encode()),
        assets=(),
    )

    result = classify_document(candidate, text)

    assert result.document_type == DocumentType.MACHINE_WALKTHROUGH
    assert result.ingestion_status == IngestionStatus.ACCEPTED
    assert "procedural_signals" in result.reasons


def test_genuine_challenge_method_is_a_challenge_walkthrough(tmp_path: Path) -> None:
    path = tmp_path / "challenge.md"
    text = """# Decoder challenge

## Inspect the encoding

We decoded the outer representation before interpreting its contents.

## Interpret the result

The command returned a structured payload that confirmed the hypothesis.
"""
    path.write_text(text, encoding="utf-8")
    candidate = SourceCandidate(
        source_id="source-challenge-method",
        path=path,
        relative_path="Write-ups/Challanges/Decoder/readme.md",
        suffix=".md",
        sha256="2" * 64,
        size_bytes=len(text.encode()),
        assets=(),
    )

    result = classify_document(candidate, text)

    assert result.document_type == DocumentType.CHALLENGE_WALKTHROUGH
    assert result.knowledge_role == KnowledgeRole.CASE_STUDY
    assert result.ingestion_status == IngestionStatus.ACCEPTED


def test_procedural_walkthrough_with_final_flag_remains_accepted(tmp_path: Path) -> None:
    path = tmp_path / "walkthrough-with-flag.md"
    text = """# Example machine

## Enumeration

We ran an initial check and the scan returned an exposed web service.

```text
80/tcp open http
```

## Exploitation

The observed response confirmed that the selected path succeeded.

```text
shell obtained
```

## Final Flag

```text
HTB{case_outcome_must_not_override_procedure}
```
"""
    path.write_text(text, encoding="utf-8")
    candidate = SourceCandidate(
        source_id="source-walkthrough-with-flag",
        path=path,
        relative_path="Write-ups/Machines/Example/walkthrough.md",
        suffix=".md",
        sha256="3" * 64,
        size_bytes=len(text.encode()),
        assets=(),
    )

    result = classify_document(candidate, text)

    assert result.document_type == DocumentType.MACHINE_WALKTHROUGH
    assert result.knowledge_role == KnowledgeRole.CASE_STUDY
    assert result.ingestion_status == IngestionStatus.ACCEPTED


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
