# Sedna Ingestion Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic foundation that inventories, classifies, structurally parses, segments, validates, and reports on Sedna source documents before semantic extraction.

**Architecture:** Preserve `raw_src/` unchanged. A source inventory produces stable candidates; a deterministic classifier selects one parser profile; a CommonMark parser produces source-spanned blocks; a segmenter groups blocks into logical units; a repository writes manifests, quarantine records, and ingestion reports atomically. This plan ends at a typed `PreparedSource` boundary consumed by a later semantic-extraction plan.

**Tech Stack:** Python 3.11–3.13, Pydantic 2.13.4, markdown-it-py 3.x, PyYAML 6.0.3, pytest 8.x, Ruff 0.15.10.

## Global Constraints

- `raw_src/` is immutable input; implementation must never rewrite source Markdown, PDFs, images, or archives.
- Sedna owns strategic source preparation; it must not duplicate Hades tool-operation instructions.
- JSON/JSONL are canonical for machine-produced artifacts; YAML is reserved for reviewed decision rules in later phases.
- Every extracted or inferred artifact in later phases must be traceable to a source span, so this phase must preserve line ranges and asset references.
- Empty, flag-only, and external-link-only documents produce manifests but no decision-ready segments.
- Final flags must never appear in prepared searchable text, canonical strategic artifacts, or retrieval indexes.
- Processing is incremental by source hash plus schema, parser, and extractor versions.
- `/Users/gabriele/Dev/sedna` is not an autonomous Git repository and the user declined initialization; omit commit actions and use test/review checkpoints instead.

## Scope Boundary

This is the first of three independently testable implementation plans:

1. **This plan:** deterministic ingestion foundation and golden corpus.
2. **Follow-on:** semantic extraction into references, cases, steps, and draft rules.
3. **Follow-on:** FTS5 indexing, epistemic-lane retrieval packets, and web-research fallback.

This plan intentionally does not call an LLM and does not create approved strategic knowledge. It produces stable contracts that those later subsystems consume.

## File Structure

```text
src/sedna/knowledge/
├── schema/
│   ├── __init__.py          # public schema exports
│   ├── common.py            # shared enums, source spans, extraction metadata
│   ├── manifest.py          # source manifest and asset records
│   ├── reference.py         # reference-artifact contract for phase two
│   ├── case.py              # case and case-step contracts for phase two
│   └── rule.py              # draft/approved decision-rule contract
├── inventory.py             # source discovery, hashing, asset association
├── classifier.py            # deterministic document type and quality rules
├── parsing/
│   ├── __init__.py          # parser exports
│   ├── models.py            # ParsedBlock, ParsedDocument, PreparedSource
│   ├── markdown.py          # CommonMark token conversion with line spans
│   ├── profiles.py          # HTB scrape, Academy/Obsidian, GitHub adapters
│   ├── sanitize.py          # deterministic flag removal from searchable text
│   └── segment.py           # logical segmentation over structural blocks
├── repository.py            # atomic manifest/quarantine/report persistence
└── pipeline.py              # orchestration to PreparedSource

tests/knowledge/
├── fixtures/                # compact representative source samples
├── test_schema.py
├── test_inventory.py
├── test_classifier.py
├── test_markdown_parser.py
├── test_parser_profiles.py
├── test_segmenter.py
├── test_repository.py
└── test_pipeline.py
```

---

### Task 1: Shared Knowledge Schema

**Files:**
- Create: `src/sedna/knowledge/schema/common.py`
- Create: `src/sedna/knowledge/schema/__init__.py`
- Test: `tests/knowledge/test_schema.py`

**Interfaces:**
- Produces: `DocumentType`, `KnowledgeRole`, `ArtifactType`, `Origin`, `ReviewStatus`, `Generalizability`, `SourceQuality`, `IngestionStatus`, `SourceLocation`, `SourceRef`, `ExtractionMetadata`.
- Consumed by: every later task and follow-on plan.

- [ ] **Step 1: Write failing enum and provenance tests**

```python
from pydantic import ValidationError

from sedna.knowledge.schema import (
    DocumentType,
    ExtractionMetadata,
    SourceLocation,
    SourceRef,
)


def test_source_ref_requires_a_precise_location():
    ref = SourceRef(
        source_id="htb-lame",
        path="raw_src/Write-ups/Machines/Lame/walkthrough.md",
        location=SourceLocation(start_line=10, end_line=18),
    )
    assert ref.location.start_line == 10


def test_source_location_rejects_reversed_lines():
    with pytest.raises(ValidationError):
        SourceLocation(start_line=18, end_line=10)


def test_extraction_metadata_records_reproducibility_versions():
    metadata = ExtractionMetadata(
        schema_version="1.0.0",
        parser_id="markdown-it-commonmark",
        parser_version="1",
        extractor_id="deterministic-foundation",
        extractor_version="1",
    )
    assert metadata.schema_version == "1.0.0"
    assert DocumentType.MACHINE_WALKTHROUGH.value == "machine_walkthrough"
```

- [ ] **Step 2: Run the schema test and verify failure**

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_schema.py`

Expected: FAIL because `sedna.knowledge.schema` does not exist.

- [ ] **Step 3: Implement strict shared models**

```python
class SourceLocation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    page: int | None = Field(default=None, ge=1)
    section: str | None = None
    asset_path: str | None = None

    @model_validator(mode="after")
    def validate_location(self) -> "SourceLocation":
        if not any((self.start_line, self.page, self.section, self.asset_path)):
            raise ValueError("at least one source location must be provided")
        if self.start_line and self.end_line and self.end_line < self.start_line:
            raise ValueError("end_line must not precede start_line")
        return self
```

Implement every enum with `StrEnum` and every model with `frozen=True, extra="forbid"`. Re-export public names from `schema/__init__.py`.

- [ ] **Step 4: Run focused tests and lint**

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_schema.py`

Expected: PASS.

Run: `.venv/bin/ruff check src/sedna/knowledge/schema tests/knowledge/test_schema.py`

Expected: PASS.

- [ ] **Step 5: Review checkpoint**

Verify that all enum values exactly match the design vocabulary and that no model accepts unknown fields.

---

### Task 2: Canonical Manifest and Future Artifact Contracts

**Files:**
- Create: `src/sedna/knowledge/schema/manifest.py`
- Create: `src/sedna/knowledge/schema/reference.py`
- Create: `src/sedna/knowledge/schema/case.py`
- Create: `src/sedna/knowledge/schema/rule.py`
- Modify: `src/sedna/knowledge/schema/__init__.py`
- Modify: `tests/knowledge/test_schema.py`

**Interfaces:**
- Consumes: shared enums and provenance types from Task 1.
- Produces: `AssetRef`, `DocumentManifest`, `ReferenceArtifact`, `CaseState`, `CaseHypothesis`, `CaseAction`, `CaseEvidence`, `CaseStep`, `KnowledgeCase`, `DecisionRule`.

- [ ] **Step 1: Add failing manifest and case-validation tests**

```python
def test_manifest_tracks_hash_profile_and_emitted_artifacts():
    manifest = DocumentManifest(
        source_id="htb-lame",
        path="raw_src/Write-ups/Machines/Lame/walkthrough.md",
        sha256="a" * 64,
        title="Lame",
        language="en",
        document_type=DocumentType.MACHINE_WALKTHROUGH,
        knowledge_role=KnowledgeRole.CASE_STUDY,
        quality=SourceQuality.COMPLETE,
        parser_profile="github_walkthrough",
        ingestion_status=IngestionStatus.ACCEPTED,
        extraction=foundation_metadata(),
    )
    assert manifest.sha256 == "a" * 64


def test_case_step_requires_at_least_one_source_reference():
    with pytest.raises(ValidationError):
        CaseStep(
            ordinal=1,
            state_before=CaseState(access="none"),
            observations=("HTTP service exposed",),
            hypotheses=(),
            selected_action=CaseAction(intent="inspect_http"),
            evidence=(),
            state_after=CaseState(access="none"),
            source_refs=(),
        )
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_schema.py`

Expected: FAIL because canonical model classes are undefined.

- [ ] **Step 3: Implement exact canonical contracts**

Use tuples for immutable repeated fields. Validate SHA-256 values with `^[0-9a-f]{64}$`. Require source references on `ReferenceArtifact`, `CaseStep`, and `DecisionRule`. Require case ordinals to be positive and unique inside `KnowledgeCase`.

```python
class CaseHypothesis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    statement: str = Field(min_length=1)
    origin: Origin


class CaseAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: str = Field(min_length=1)
    capability_ref: str | None = None


class CaseStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ordinal: int = Field(ge=1)
    state_before: CaseState
    observations: tuple[str, ...]
    hypotheses: tuple[CaseHypothesis, ...]
    selected_action: CaseAction
    evidence: tuple[CaseEvidence, ...]
    state_after: CaseState
    negative_evidence: tuple[str, ...] = ()
    transfer_conditions: tuple[str, ...] = ()
    case_specific_details: tuple[str, ...] = ()
    requires_validation: bool = True
    source_refs: tuple[SourceRef, ...] = Field(min_length=1)
```

- [ ] **Step 4: Run schema tests and lint**

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_schema.py`

Expected: PASS.

Run: `.venv/bin/ruff check src/sedna/knowledge/schema tests/knowledge/test_schema.py`

Expected: PASS.

- [ ] **Step 5: Review checkpoint**

Confirm that no canonical model contains a `tool_recipe`, raw flag, or mandatory literal target credential field.

---

### Task 3: Source Inventory and Stable Identity

**Files:**
- Create: `src/sedna/knowledge/inventory.py`
- Create: `tests/knowledge/test_inventory.py`

**Interfaces:**
- Consumes: filesystem source root.
- Produces: `SourceCandidate` and `discover_sources(source_root: Path) -> tuple[SourceCandidate, ...]`.
- `SourceCandidate` fields: `source_id`, `path`, `relative_path`, `suffix`, `sha256`, `size_bytes`, `assets`.

- [ ] **Step 1: Write failing discovery, hashing, and stability tests**

```python
def test_discover_sources_returns_stable_sorted_candidates(tmp_path):
    root = tmp_path / "raw_src"
    write_source(root / "Machines" / "Lame" / "walkthrough.md", "# Lame\n")
    write_source(root / "Machines" / "Lame" / "image.png", b"png", binary=True)

    first = discover_sources(root)
    second = discover_sources(root)

    assert first == second
    assert first[0].relative_path == "Machines/Lame/walkthrough.md"
    assert first[0].assets[0].relative_path == "Machines/Lame/image.png"
    assert len(first[0].sha256) == 64
```

- [ ] **Step 2: Run test and verify failure**

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_inventory.py`

Expected: FAIL because `discover_sources` is undefined.

- [ ] **Step 3: Implement deterministic discovery**

Discover `.md` and `.pdf` as source documents. Associate sibling files under the source document's directory as assets, excluding `.DS_Store`. Build stable source IDs with UUID5 over the POSIX relative path, while keeping content SHA-256 separate so edits do not change identity.

```python
def stable_source_id(relative_path: str) -> str:
    return f"source-{uuid5(NAMESPACE_URL, f'sedna:{relative_path}') }"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
```

- [ ] **Step 4: Run inventory tests and lint**

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_inventory.py`

Expected: PASS.

Run: `.venv/bin/ruff check src/sedna/knowledge/inventory.py tests/knowledge/test_inventory.py`

Expected: PASS.

- [ ] **Step 5: Review checkpoint**

Run inventory twice over `raw_src/` and verify 175 Markdown candidates plus 3 PDF candidates, stable ordering, and no source writes.

---

### Task 4: Deterministic Document Classifier

**Files:**
- Create: `src/sedna/knowledge/classifier.py`
- Create: `tests/knowledge/fixtures/lesson.md`
- Create: `tests/knowledge/fixtures/machine-walkthrough.md`
- Create: `tests/knowledge/fixtures/challenge-flag-only.md`
- Create: `tests/knowledge/fixtures/external-stub.md`
- Create: `tests/knowledge/fixtures/empty.md`
- Create: `tests/knowledge/test_classifier.py`

**Interfaces:**
- Consumes: `SourceCandidate`, decoded source text for Markdown or `None` for an unsupported binary format.
- Produces: `ClassificationResult(document_type, knowledge_role, quality, parser_profile, ingestion_status, reasons)`.
- Function: `classify_document(candidate: SourceCandidate, text: str | None) -> ClassificationResult`.

- [ ] **Step 1: Write failing table-driven classification tests**

```python
@pytest.mark.parametrize(
    ("fixture", "expected_type", "expected_status"),
    [
        ("lesson.md", DocumentType.LESSON, IngestionStatus.ACCEPTED),
        ("machine-walkthrough.md", DocumentType.MACHINE_WALKTHROUGH, IngestionStatus.ACCEPTED),
        ("challenge-flag-only.md", DocumentType.EXCLUDED, IngestionStatus.EXCLUDED),
        ("external-stub.md", DocumentType.EXTERNAL_STUB, IngestionStatus.EXCLUDED),
        ("empty.md", DocumentType.EXCLUDED, IngestionStatus.EXCLUDED),
    ],
)
def test_classify_representative_sources(fixture, expected_type, expected_status):
    candidate, text = fixture_candidate(fixture)
    result = classify_document(candidate, text)
    assert result.document_type == expected_type
    assert result.ingestion_status == expected_status
    assert result.reasons
```

- [ ] **Step 2: Run classifier tests and verify failure**

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_classifier.py`

Expected: FAIL because the classifier is undefined.

- [ ] **Step 3: Implement ordered, explainable rules**

Apply rules in this order:

1. empty or whitespace-only → `excluded` / `empty`;
2. flag pattern plus no procedural signals → `excluded` / `flag_only`;
3. one external walkthrough link plus flags and no procedural signals → `external_stub`;
4. path under `Write-ups/Machines` plus procedural signals → `machine_walkthrough`;
5. path under `Write-ups/Challanges` plus procedural signals → `challenge_walkthrough`;
6. path under Academy or `01_information-gathering` → `lesson` or `cheatsheet_reference` based on narrative-to-table ratio;
7. PDF path and filename identify a technical reference → `cheatsheet_reference` with quarantined status until a PDF parser exists;
8. otherwise → quarantined ambiguous classification.

Procedural signals include at least two headings plus a code block, or explicit action/result language. Final-flag patterns include `HTB{...}` and 32-character hexadecimal flag sections.

- [ ] **Step 4: Run tests and lint**

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_classifier.py`

Expected: PASS.

Run: `.venv/bin/ruff check src/sedna/knowledge/classifier.py tests/knowledge/test_classifier.py`

Expected: PASS.

- [ ] **Step 5: Review checkpoint**

Generate a classification report for all 175 Markdown files and manually inspect every `ambiguous` result plus at least five accepted files from each source family.

---

### Task 5: Structural Parsing Models and CommonMark Dependency

**Files:**
- Modify: `pyproject.toml`
- Create: `src/sedna/knowledge/parsing/models.py`
- Create: `src/sedna/knowledge/parsing/__init__.py`
- Create: `tests/knowledge/test_markdown_parser.py`

**Interfaces:**
- Produces: `BlockKind`, `ParsedBlock`, `ParsedAsset`, `ParsedDocument`, `LogicalSegment`, `PreparedSource`.
- `ParsedBlock` includes `kind`, `text`, `level`, `language`, `start_line`, `end_line`, `metadata`.

- [ ] **Step 1: Add dependency and failing strict-model tests**

Add to `project.dependencies`:

```toml
"markdown-it-py>=3.0,<4",
```

Add tests proving line ranges are required, heading levels are 1–6, and code-block languages remain optional.

- [ ] **Step 2: Install dependencies and verify tests fail for missing models**

Run: `.venv/bin/pip install -e '.[dev]'`

Expected: installation succeeds.

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_markdown_parser.py`

Expected: FAIL because parsing models are undefined.

- [ ] **Step 3: Implement immutable parsing models**

```python
class ParsedBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: BlockKind
    text: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    level: int | None = Field(default=None, ge=1, le=6)
    language: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
```

`PreparedSource` must combine `DocumentManifest`, `ParsedDocument`, and logical segments without semantic artifacts.

- [ ] **Step 4: Run tests and lint**

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_markdown_parser.py`

Expected: PASS for model tests.

Run: `.venv/bin/ruff check src/sedna/knowledge/parsing tests/knowledge/test_markdown_parser.py`

Expected: PASS.

- [ ] **Step 5: Review checkpoint**

Confirm parsing types contain no semantic fields such as hypotheses or decision rules.

---

### Task 6: CommonMark Markdown Parser

**Files:**
- Create: `src/sedna/knowledge/parsing/markdown.py`
- Modify: `src/sedna/knowledge/parsing/__init__.py`
- Modify: `tests/knowledge/test_markdown_parser.py`

**Interfaces:**
- Consumes: source ID, path, Markdown string.
- Produces: `parse_markdown(source_id: str, path: str, markdown: str) -> ParsedDocument`.

- [ ] **Step 1: Write failing structure and source-span tests**

```python
def test_parse_markdown_preserves_structure_and_source_lines():
    parsed = parse_markdown(
        "source-test",
        "sample.md",
        "# Title\n\nObserve HTTP.\n\n```bash\nnmap -sV TARGET_IP\n```\n",
    )
    assert [block.kind for block in parsed.blocks] == [
        BlockKind.HEADING,
        BlockKind.PARAGRAPH,
        BlockKind.CODE,
    ]
    assert parsed.blocks[0].start_line == 1
    assert parsed.blocks[2].language == "bash"
    assert parsed.blocks[2].text == "nmap -sV TARGET_IP"
```

Add tests for tables, lists, links, Markdown images, HTML image tags, and Setext headings.

- [ ] **Step 2: Run parser tests and verify failure**

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_markdown_parser.py`

Expected: FAIL because `parse_markdown` is undefined.

- [ ] **Step 3: Convert markdown-it tokens into blocks**

Configure `MarkdownIt("commonmark").enable("table")`. Use each token's `map` field to convert zero-based half-open line ranges into one-based inclusive ranges. Combine inline child text without discarding links or image alt text. Store URL and asset target in metadata.

Do not remove navigation or boilerplate here; profile adapters own cleanup.

- [ ] **Step 4: Run tests and lint**

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_markdown_parser.py`

Expected: PASS.

Run: `.venv/bin/ruff check src/sedna/knowledge/parsing/markdown.py tests/knowledge/test_markdown_parser.py`

Expected: PASS.

- [ ] **Step 5: Review checkpoint**

Parse one large Academy document and one machine walkthrough and inspect that code blocks, tables, headings, and original line spans survive.

---

### Task 7: Source-specific Parser Profiles

**Files:**
- Create: `src/sedna/knowledge/parsing/profiles.py`
- Create: `tests/knowledge/fixtures/htb-scrape.md`
- Create: `tests/knowledge/fixtures/obsidian-lesson.md`
- Create: `tests/knowledge/fixtures/github-walkthrough.md`
- Create: `tests/knowledge/test_parser_profiles.py`

**Interfaces:**
- Consumes: `ParsedDocument`, parser profile string.
- Produces: `apply_profile(document: ParsedDocument, profile: str) -> ParsedDocument`.
- Profiles: `htb_scrape`, `academy_obsidian`, `github_walkthrough`.

- [ ] **Step 1: Write failing profile tests**

```python
def test_htb_scrape_removes_interface_boilerplate_but_keeps_article():
    document = parse_fixture("htb-scrape.md")
    cleaned = apply_profile(document, "htb_scrape")
    text = "\n".join(block.text for block in cleaned.blocks)
    assert "Virtual Hosts" in text
    assert "Dashboard" not in text
    assert "Pwnbox" not in text
    assert "Previous" not in text


def test_obsidian_profile_keeps_wiki_and_asset_relationships():
    cleaned = apply_profile(parse_fixture("obsidian-lesson.md"), "academy_obsidian")
    assert any(asset.target == "logo_footprinting.png" for asset in cleaned.assets)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_parser_profiles.py`

Expected: FAIL because profile adapters are undefined.

- [ ] **Step 3: Implement profile adapters over blocks**

`htb_scrape` keeps content from the first article-level heading until navigation markers such as `Previous`, `Next`, or the next interface section. It removes known UI headings and controls by exact normalized labels.

`academy_obsidian` removes note metadata lines (`Tags:`, `Related to:`, `See also:`, `Previous:`) from searchable body while recording their targets as relationships. It converts `![[asset]]` into `ParsedAsset` without OCR.

`github_walkthrough` removes centered HTML presentation wrappers while retaining their text and image targets. It otherwise preserves chronological blocks.

- [ ] **Step 4: Run profile tests and lint**

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_parser_profiles.py`

Expected: PASS.

Run: `.venv/bin/ruff check src/sedna/knowledge/parsing/profiles.py tests/knowledge/test_parser_profiles.py`

Expected: PASS.

- [ ] **Step 5: Review checkpoint**

Compare cleaned text against the original source for one real file per profile. Confirm cleanup removes only interface or note metadata, never procedural content.

---

### Task 8: Logical Segmenter

**Files:**
- Create: `src/sedna/knowledge/parsing/sanitize.py`
- Create: `src/sedna/knowledge/parsing/segment.py`
- Create: `tests/knowledge/test_segmenter.py`

**Interfaces:**
- Consumes: `ParsedDocument`.
- Produces: `sanitize_searchable_text(text: str, heading_path: tuple[str, ...]) -> str` and `segment_document(document: ParsedDocument, maximum_segment_chars: int = 12_000) -> tuple[LogicalSegment, ...]`.

- [ ] **Step 1: Write failing coherence tests**

```python
def test_segment_keeps_action_code_and_result_together():
    document = parse_profiled_markdown(
        "## Enumerate SMB\nObserve port 445.\n\n```bash\nsmbclient -L //TARGET_IP\n```\n\nThe output reveals a public share.\n"
    )
    segments = segment_document(document)
    assert len(segments) == 1
    assert "Observe port 445" in segments[0].text
    assert "smbclient" in segments[0].text
    assert "public share" in segments[0].text


def test_long_section_splits_only_between_blocks():
    document = parsed_document_with_three_large_paragraphs()
    segments = segment_document(document, maximum_segment_chars=120)
    assert len(segments) >= 2
    assert all(segment.start_line <= segment.end_line for segment in segments)


def test_segment_redacts_final_flags_from_searchable_text():
    document = parse_profiled_markdown(
        "## Root Flag\n\n```text\nHTB{do_not_index_me}\n```\n"
    )
    segment = segment_document(document)[0]
    assert "HTB{" not in segment.text
    assert "<EXCLUDED_FLAG>" in segment.text
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_segmenter.py`

Expected: FAIL because the segmenter is undefined.

- [ ] **Step 3: Implement heading-aware block grouping**

Start a segment at a heading and keep following blocks until the next heading of equal or higher level. A lower-level heading stays in the current segment. If a segment exceeds `maximum_segment_chars`, split only at a block boundary, preferring a paragraph boundary before a code block and its immediately following explanation.

Before concatenating searchable segment text, replace `HTB{...}` tokens with `<EXCLUDED_FLAG>`. Also replace a standalone 32-character hexadecimal value when its current heading path contains `user flag`, `root flag`, or `final flag`. Preserve the original block text and source span only in the non-searchable parsed document used for provenance review.

Each `LogicalSegment` stores block indices, text, start/end lines, heading path, and asset references.

- [ ] **Step 4: Run segmenter tests and lint**

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_segmenter.py`

Expected: PASS.

Run: `.venv/bin/ruff check src/sedna/knowledge/parsing/segment.py tests/knowledge/test_segmenter.py`

Expected: PASS.

- [ ] **Step 5: Review checkpoint**

Inspect segments for `Lame/walkthrough.md`, `PermX/walkthrough.md`, and the Academy Footprinting lesson. Confirm that commands are not separated from adjacent explanation and result blocks.

---

### Task 9: Canonical Repository and Quarantine

**Files:**
- Create: `src/sedna/knowledge/repository.py`
- Create: `tests/knowledge/test_repository.py`

**Interfaces:**
- Produces: `CanonicalKnowledgeRepository(root: Path)`.
- Methods: `write_manifest(manifest)`, `write_quarantine(record)`, `write_ingestion_report(report)`, `load_manifest(source_id)`.

- [ ] **Step 1: Write failing atomic-write and round-trip tests**

```python
def test_repository_round_trips_manifest(tmp_path):
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    manifest = complete_manifest()
    repository.write_manifest(manifest)
    assert repository.load_manifest(manifest.source_id) == manifest


def test_repository_never_leaves_temporary_file_after_success(tmp_path):
    repository = CanonicalKnowledgeRepository(tmp_path / "knowledge")
    repository.write_manifest(complete_manifest())
    assert not list((tmp_path / "knowledge").rglob("*.tmp"))
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_repository.py`

Expected: FAIL because the repository is undefined.

- [ ] **Step 3: Implement deterministic atomic JSON writes**

Serialize with `model_dump(mode="json")`, UTF-8, sorted keys, two-space indentation, and a final newline. Write to a sibling temporary file, flush, then replace the target path. Reject paths outside the repository root.

Quarantine records must include source ID, reason codes, human-readable messages, parser profile, and extraction metadata.

- [ ] **Step 4: Run repository tests and lint**

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_repository.py`

Expected: PASS.

Run: `.venv/bin/ruff check src/sedna/knowledge/repository.py tests/knowledge/test_repository.py`

Expected: PASS.

- [ ] **Step 5: Review checkpoint**

Confirm generated JSON is stable across two writes and produces no timestamp-only diff for an unchanged manifest.

---

### Task 10: Prepared-source Ingestion Pipeline

**Files:**
- Create: `src/sedna/knowledge/pipeline.py`
- Modify: `src/sedna/knowledge/__init__.py`
- Create: `tests/knowledge/test_pipeline.py`

**Interfaces:**
- Consumes: source root, knowledge root, source candidate.
- Produces: `IngestionPipeline.prepare(candidate: SourceCandidate) -> PreparedSource | None`.
- `None` means excluded or quarantined; a manifest is still persisted.

- [ ] **Step 1: Write failing accepted, excluded, and incremental tests**

```python
def test_prepare_accepted_walkthrough_returns_segments_and_manifest(tmp_path):
    pipeline, candidate = pipeline_for_fixture(tmp_path, "machine-walkthrough.md")
    prepared = pipeline.prepare(candidate)
    assert prepared is not None
    assert prepared.manifest.document_type == DocumentType.MACHINE_WALKTHROUGH
    assert prepared.segments


def test_prepare_flag_only_source_writes_manifest_without_segments(tmp_path):
    pipeline, candidate = pipeline_for_fixture(tmp_path, "challenge-flag-only.md")
    assert pipeline.prepare(candidate) is None
    manifest = pipeline.repository.load_manifest(candidate.source_id)
    assert manifest.ingestion_status == IngestionStatus.EXCLUDED


def test_prepare_skips_unchanged_source_with_same_versions(tmp_path):
    pipeline, candidate = pipeline_for_fixture(tmp_path, "machine-walkthrough.md")
    first = pipeline.prepare(candidate)
    second = pipeline.prepare(candidate)
    assert first is not None
    assert second is None
    assert pipeline.last_outcome == "unchanged"
```

- [ ] **Step 2: Run pipeline tests and verify failure**

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_pipeline.py`

Expected: FAIL because `IngestionPipeline` is undefined.

- [ ] **Step 3: Implement orchestration**

Pipeline order:

1. classify a non-Markdown candidate from its path and quarantine it with `unsupported_parser` while preserving that document type;
2. decode UTF-8 Markdown, quarantining invalid encoding;
3. classify the Markdown source;
4. build and persist a manifest for excluded sources;
5. select the parser profile;
6. parse Markdown;
7. apply the source profile;
8. segment the document and sanitize searchable text;
9. persist the accepted manifest;
10. return `PreparedSource`.

Skip unchanged sources only when content hash, schema version, parser version, and extractor version all match the stored manifest.

- [ ] **Step 4: Run pipeline tests and lint**

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_pipeline.py`

Expected: PASS.

Run: `.venv/bin/ruff check src/sedna/knowledge/pipeline.py tests/knowledge/test_pipeline.py`

Expected: PASS.

- [ ] **Step 5: Review checkpoint**

Run the pipeline on a temporary copy of representative fixtures and verify it never writes under `raw_src/`.

---

### Task 11: Golden Corpus Integration Test

**Files:**
- Create: `tests/knowledge/golden_manifest.yaml`
- Add compact fixtures under: `tests/knowledge/fixtures/golden/`
- Modify: `tests/knowledge/test_pipeline.py`

**Interfaces:**
- Consumes: 15 fixture paths with expected type, quality, profile, status, and minimum segment count.
- Produces: regression gate for the complete deterministic foundation.

- [ ] **Step 1: Define the exact 15-case golden manifest**

```yaml
cases:
  - path: lesson-narrative.md
    document_type: lesson
    parser_profile: academy_obsidian
    quality: complete
    status: accepted
    minimum_segments: 3
  - path: lesson-cheatsheet.md
    document_type: cheatsheet_reference
    parser_profile: academy_obsidian
    quality: partial
    status: accepted
    minimum_segments: 1
  - path: htb-scrape.md
    document_type: lesson
    parser_profile: htb_scrape
    quality: complete
    status: accepted
    minimum_segments: 2
  - path: obsidian-with-assets.md
    document_type: lesson
    parser_profile: academy_obsidian
    quality: complete
    status: accepted
    minimum_segments: 2
  - path: machine-complete.md
    document_type: machine_walkthrough
    parser_profile: github_walkthrough
    quality: complete
    status: accepted
    minimum_segments: 4
  - path: machine-failed-attempt.md
    document_type: machine_walkthrough
    parser_profile: github_walkthrough
    quality: complete
    status: accepted
    minimum_segments: 3
  - path: machine-html-wrapper.md
    document_type: machine_walkthrough
    parser_profile: github_walkthrough
    quality: partial
    status: accepted
    minimum_segments: 2
  - path: challenge-complete.md
    document_type: challenge_walkthrough
    parser_profile: github_walkthrough
    quality: complete
    status: accepted
    minimum_segments: 3
  - path: challenge-flag-only.md
    document_type: excluded
    parser_profile: none
    quality: unusable
    status: excluded
    minimum_segments: 0
  - path: machine-external-stub.md
    document_type: external_stub
    parser_profile: none
    quality: minimal
    status: excluded
    minimum_segments: 0
  - path: machine-flags-only.md
    document_type: excluded
    parser_profile: none
    quality: unusable
    status: excluded
    minimum_segments: 0
  - path: empty.md
    document_type: excluded
    parser_profile: none
    quality: unusable
    status: excluded
    minimum_segments: 0
  - path: malformed-ambiguous.md
    document_type: excluded
    parser_profile: none
    quality: minimal
    status: quarantined
    minimum_segments: 0
  - path: setext-lesson.md
    document_type: lesson
    parser_profile: htb_scrape
    quality: partial
    status: accepted
    minimum_segments: 2
  - path: reference-cheatsheet.pdf
    document_type: cheatsheet_reference
    parser_profile: none
    quality: partial
    status: quarantined
    minimum_segments: 0
```

Create exactly these 15 compact fixtures. Markdown fixtures are purpose-written excerpts rather than full copied corpus documents. `reference-cheatsheet.pdf` is a minimal valid test PDF used only to verify the unsupported-parser quarantine path.

- [ ] **Step 2: Write the failing parametrized integration test**

The test loads `golden_manifest.yaml`, runs every fixture through the pipeline, and compares document type, parser profile, status, and segment count.

- [ ] **Step 3: Run the integration test and record mismatches**

Run: `.venv/bin/python -m pytest -q tests/knowledge/test_pipeline.py -k golden`

Expected: FAIL only for classifier/profile behaviors not yet matching the reviewed expectations.

- [ ] **Step 4: Correct deterministic rules without fixture-specific path hacks**

Adjust classifier and parser rules using structural signals shared by a source family. Do not add conditions that match a single fixture filename unless the filename expresses an actual corpus taxonomy path.

- [ ] **Step 5: Run the full knowledge test suite**

Run: `.venv/bin/python -m pytest -q tests/knowledge`

Expected: PASS.

- [ ] **Step 6: Review checkpoint**

Review every golden result manually and record the accepted expected manifest as the regression baseline.

---

### Task 12: Legacy Compatibility, Documentation, and Full Verification

**Files:**
- Modify: `src/sedna/knowledge/ingest.py`
- Modify: `src/sedna/knowledge/__init__.py`
- Modify: `README.md`
- Modify: `tests/test_ingest.py`

**Interfaces:**
- Preserves: `ingest_markdown(...) -> list[KnowledgeChunk]` during migration.
- Documents: `IngestionPipeline` as the new foundation entry point.

- [ ] **Step 1: Add a failing compatibility test**

```python
def test_legacy_ingest_markdown_remains_available_during_migration(tmp_path):
    assert callable(ingest_markdown)
    assert callable(IngestionPipeline)
```

- [ ] **Step 2: Run the full suite before compatibility changes**

Run: `.venv/bin/python -m pytest -q`

Expected: existing tests pass; the new compatibility import test fails until exports are updated.

- [ ] **Step 3: Export the new pipeline without redirecting legacy behavior**

Keep `ingest_markdown` unchanged except for a deprecation docstring explaining that it creates legacy retrieval chunks and is not used by the strategic pipeline. Export `IngestionPipeline` and schema types from `sedna.knowledge`.

- [ ] **Step 4: Document the foundation flow**

Add a README section containing:

```text
raw source -> inventory -> classification -> structural parser
           -> logical segments -> PreparedSource
```

Document that manifests are canonical, `raw_src/` is immutable, semantic extraction is a follow-on phase, and no direct flags enter prepared searchable text.

- [ ] **Step 5: Run complete verification**

Run: `.venv/bin/python -m pytest -q`

Expected: PASS.

Run: `.venv/bin/ruff check src tests`

Expected: PASS.

- [ ] **Step 6: Verify real-corpus preparation report**

Run the foundation over `/Users/gabriele/Dev/sedna/raw_src` with output directed to a temporary knowledge directory. Confirm:

- 175 Markdown files and 3 PDF files are inventoried;
- every source has a manifest outcome;
- excluded files have reason codes;
- ambiguous files are quarantined;
- no file beneath `raw_src/` changes;
- no extracted prepared text contains an HTB flag pattern.

- [ ] **Step 7: Final review checkpoint**

Compare implemented interfaces against the design spec, record any intentional deviation in the README, and do not start semantic extraction until the golden corpus is accepted.
