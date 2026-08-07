# Sedna Semantic Compiler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compile an accepted `PreparedSource` into automatically verified, provenance-backed technical references, cases, case steps, and decision guidance using the Hades host LLM, then persist the canonical semantic result without human approval.

**Architecture:** Keep the deterministic foundation unchanged through `PreparedSource`. A synchronous semantic compiler sends only retrieval-safe logical segments to the host-owned `ctx.llm.complete_structured` surface, validates the extractor response as typed drafts, runs an isolated critic, permits one bounded repair, materializes exact provenance and deterministic IDs, and commits a source-level canonical bundle plus verification record. The semantic bundle is the M2 source of truth; SQLite retrieval, the user-facing “learn folder” skill, and Event Journal integration remain later milestones.

**Tech Stack:** Python 3.11–3.13, Pydantic 2.13.4, Hades `PluginLlm.complete_structured`, pytest 8, Ruff 0.15.10, descriptor-relative atomic repository writes.

## Global Constraints

- `raw_src/` remains immutable; semantic compilation receives `PreparedSource` and never opens raw source paths.
- Only `LogicalSegment.text`, safe heading paths, safe asset locators, and whitelisted manifest metadata may enter LLM prompts.
- Exact commands remain source evidence; canonical output stores strategic intent and Hades capability references, not tool tutorials.
- Every material artifact and context assertion resolves to one or more exact source spans.
- Missing context is `unknown`, never implicit universal compatibility.
- No final flag or contextual secret may enter drafts, canonical artifacts, verification records, or searchable text.
- Human approval is not required; verification states are `extracted`, `verified`, `corroborated`, `contested`, `deprecated`, and `rejected`.
- The critic is a separate structured LLM call and may use the same host model; this is process isolation, not statistical independence.
- At most one repair call and one post-repair critic call are allowed.
- Provider/model overrides are not requested; Hades retains routing, authentication, timeout, and fallback ownership.
- Canonical IDs and serialized output are deterministic for identical prepared input and identical semantic responses.
- M2 does not add SQLite, embeddings, a vector store, plugin tools, folder traversal, web research, or Event Journal hooks.

---

## File Structure

Create a focused `sedna.knowledge.semantic` package:

```text
src/sedna/knowledge/
├── semantic/
│   ├── __init__.py       # public M2 compiler exports
│   ├── drafts.py         # extractor, critic, repair, and result contracts
│   ├── llm.py            # host LLM protocol/adapter and safe request construction
│   ├── materialize.py    # citation resolution, IDs, canonical model construction
│   ├── prompts.py        # versioned extractor/critic/repair instructions
│   └── compiler.py       # bounded extractor -> critic -> repair state machine
├── schema/
│   ├── context.py        # typed context, extensible facets, epistemic assessment
│   └── semantic.py       # semantic manifest, verification, quarantine, bundle
└── repository.py         # atomic write/load of one semantic bundle and audit records
```

Tests mirror the units:

```text
tests/knowledge/
├── test_semantic_schema.py
├── test_semantic_llm.py
├── test_semantic_materialize.py
├── test_semantic_compiler.py
└── test_semantic_repository.py
```

The source-level `SemanticKnowledgeBundle` is written atomically as
`semantic_bundles/<source_id>.json`. This deliberately narrows the M2 physical layout from the
future reference/case projections in the design: one atomic bundle is the canonical transaction
boundary. M3 may derive `references/*.jsonl`, `cases/*.json`, guidance views, and SQLite rows from
these bundles without asking M2 to coordinate several canonical files.

---

### Task 1: Add Applicability and Epistemic Schemas

**Files:**
- Create: `src/sedna/knowledge/schema/context.py`
- Modify: `src/sedna/knowledge/schema/common.py`
- Modify: `src/sedna/knowledge/schema/__init__.py`
- Modify: `src/sedna/knowledge/__init__.py`
- Create: `tests/knowledge/test_semantic_schema.py`
- Modify: `tests/knowledge/test_schema.py`

**Interfaces:**
- Produces: `VerificationStatus`, `ContextRelation`, `ObservedOutcome`, `ContextAssertion`, `ServiceContext`, `TypedContext`, `ContextFacet`, `ApplicabilityContext`, and `EpistemicAssessment`.
- Preserves: importability of legacy `ReviewStatus`; legacy values map explicitly to verification states but are no longer canonical artifact metadata.
- Consumes: existing `Origin`, `Generalizability`, `SourceRef`, and searchable-string validators.

- [ ] **Step 1: Write failing enum and strict-model tests**

Add tests that require the exact new verification vocabulary, confidence bounds, non-empty
assertion provenance, unique context keys, namespaced facets, and immutable models:

```python
def test_context_assertion_requires_provenance_and_bounded_confidence():
    assertion = ContextAssertion(
        value="windows",
        relation=ContextRelation.OBSERVED,
        origin=Origin.EXPLICIT,
        confidence=1.0,
        source_refs=(walkthrough_ref(),),
    )
    assert assertion.value == "windows"
    with pytest.raises(ValidationError):
        ContextAssertion(
            value="windows",
            relation=ContextRelation.OBSERVED,
            origin=Origin.INFERRED,
            confidence=1.01,
            source_refs=(walkthrough_ref(),),
        )


def test_unknown_is_not_a_compatibility_wildcard():
    unknown = ContextAssertion(
        value="unknown",
        relation=ContextRelation.UNKNOWN,
        origin=Origin.INFERRED,
        confidence=0.4,
        source_refs=(walkthrough_ref(),),
    )
    assert unknown.relation is ContextRelation.UNKNOWN
    assert unknown.relation is not ContextRelation.COMPATIBLE
```

- [ ] **Step 2: Run the new schema tests and witness RED**

Run: `pytest -q tests/knowledge/test_semantic_schema.py tests/knowledge/test_schema.py`

Expected: collection fails because the new schema types are not defined.

- [ ] **Step 3: Implement verification enums and context contracts**

Define:

```python
class VerificationStatus(StrEnum):
    EXTRACTED = "extracted"
    VERIFIED = "verified"
    CORROBORATED = "corroborated"
    CONTESTED = "contested"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"


class ContextRelation(StrEnum):
    OBSERVED = "observed"
    REQUIRED = "required"
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"


class ObservedOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    MIXED = "mixed"
    INFORMATIONAL = "informational"
    NOT_APPLICABLE = "not_applicable"
```

`ContextAssertion` contains searchable `value`, `relation`, `origin`, confidence in `[0, 1]`,
and at least one `SourceRef`. `TypedContext` contains optional `ContextAssertion` fields for OS
family/version, CPU architecture, execution environment, system role, identity context, initial
access, network position, and observation date, plus tuples for services, privileges, and security
controls. `ContextFacet` contains searchable `namespace`, `key`, and one assertion.

`ApplicabilityContext` rejects duplicate typed service identities and duplicate
`(namespace, key, value, relation)` facet entries. `EpistemicAssessment` contains:

```python
source_reliability: float = Field(ge=0.0, le=1.0)
extraction_confidence: float = Field(ge=0.0, le=1.0)
generalizability: Generalizability
context_specificity: float = Field(ge=0.0, le=1.0)
verification_status: VerificationStatus
support_count: int = Field(default=1, ge=0)
contradiction_count: int = Field(default=0, ge=0)
observed_outcome: ObservedOutcome
freshness_observed_at: SearchableString | None = None
independence_group: SearchableNonEmptyString
```

- [ ] **Step 4: Preserve and document `ReviewStatus` compatibility**

Keep the enum import for existing callers and add a pure mapping function:

```python
def verification_from_legacy_review(status: ReviewStatus) -> VerificationStatus:
    return {
        ReviewStatus.AUTO_EXTRACTED: VerificationStatus.EXTRACTED,
        ReviewStatus.DRAFT: VerificationStatus.EXTRACTED,
        ReviewStatus.APPROVED: VerificationStatus.VERIFIED,
        ReviewStatus.REJECTED: VerificationStatus.REJECTED,
    }[status]
```

Do not yet remove `review_status` from the existing artifact classes; Task 2 performs the
canonical metadata migration once the new required fields exist.

- [ ] **Step 5: Run schema tests and the existing schema suite**

Run: `pytest -q tests/knowledge/test_semantic_schema.py tests/knowledge/test_schema.py`

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/sedna/knowledge/schema/context.py src/sedna/knowledge/schema/common.py \
  src/sedna/knowledge/schema/__init__.py src/sedna/knowledge/__init__.py \
  tests/knowledge/test_semantic_schema.py tests/knowledge/test_schema.py
git commit -m "feat(knowledge): model semantic applicability"
```

---

### Task 2: Migrate Canonical Artifacts and Define Semantic Bundle Records

**Files:**
- Create: `src/sedna/knowledge/schema/semantic.py`
- Modify: `src/sedna/knowledge/schema/common.py`
- Modify: `src/sedna/knowledge/schema/reference.py`
- Modify: `src/sedna/knowledge/schema/case.py`
- Modify: `src/sedna/knowledge/schema/rule.py`
- Modify: `src/sedna/knowledge/schema/__init__.py`
- Modify: `src/sedna/knowledge/__init__.py`
- Modify: `tests/knowledge/test_semantic_schema.py`
- Modify: `tests/knowledge/test_schema.py`

**Interfaces:**
- Produces: canonical artifacts carrying `applicability` and `assessment`, plus `SemanticCallMetadata`, `VerificationFinding`, `SemanticKnowledgeBundle`, `SemanticVerificationRecord`, `SemanticQuarantineRecord`, and `SemanticCompilationManifest`.
- Consumes: Task 1 context and assessment types.
- Preserves: legacy construction using `review_status` through a before-validator mapping to `assessment.verification_status` when no explicit assessment is supplied.

- [ ] **Step 1: Write failing artifact-migration and bundle tests**

Require every canonical artifact to contain applicability and assessment, reject disagreement
between a legacy review state and explicit assessment, and forbid raw LLM text or raw parsed
source fields in the bundle:

```python
def test_semantic_bundle_contains_only_validated_canonical_records():
    bundle = SemanticKnowledgeBundle(
        schema_version="2.0.0",
        source_id="htb-lame",
        source_sha256="a" * 64,
        compilation_manifest=semantic_manifest(),
        references=(reference_artifact(),),
        cases=(),
        guidance=(),
    )
    dumped = bundle.model_dump(mode="json")
    assert "raw_response" not in json.dumps(dumped)
    assert dumped["references"][0]["assessment"]["verification_status"] == "verified"
```

- [ ] **Step 2: Run the focused tests and witness RED**

Run: `pytest -q tests/knowledge/test_semantic_schema.py tests/knowledge/test_schema.py`

Expected: failures for missing semantic bundle and canonical metadata fields.

- [ ] **Step 3: Migrate `CanonicalArtifactMetadata`**

Replace canonical `review_status` and top-level `generalizability` with required:

```python
applicability: ApplicabilityContext
assessment: EpistemicAssessment
```

Expose read-only compatibility properties `review_status` and `generalizability` so existing
callers can inspect old names. Add a `mode="before"` validator that consumes legacy
`review_status` and `generalizability` only when `assessment` is absent, mapping the values with
`verification_from_legacy_review`. The compatibility assessment uses reliability/confidence/
specificity `0.5`, support count `1`, contradiction count `0`, informational outcome, and an
independence group derived from the first `source_ref.source_id`; compatibility applicability is
an empty `TypedContext` plus no facets. Reject payloads that supply contradictory old and new
forms.

- [ ] **Step 4: Extend reference and case semantics**

Add artifact types `constraint`, `evidence_interpretation`, and `exception`. Extend
`ReferenceArtifact` with `subject`, `expected_information_gain`, and
`evidence_interpretation`. Extend `CaseStep` with optional
`expected_information_gain` and a required canonical `step_id`. Replace `KnowledgeCase.platform` and
`KnowledgeCase.operating_system` with the shared applicability context while retaining
read-only compatibility properties.

- [ ] **Step 5: Implement semantic source-level records**

`SemanticCallMetadata` records purpose, provider, model, agent ID, and bounded token counts without
raw prompt or response content. `VerificationFinding` owns the closed finding-code and severity
vocabulary shared by critic output and canonical audit records, preventing a dependency from
`schema` back into `semantic`.

`SemanticCompilationManifest` records source identity/hash, extractor/critic/repair prompt
versions, extractor and critic model identifiers, disposition, repair count constrained to
`0..1`, emitted IDs, and timestamps supplied by the caller. `SemanticVerificationRecord` stores
`VerificationFinding` entries and adjudication outcome without raw model prose. `SemanticQuarantineRecord`
stores reason codes, safe messages, and cited segment indexes. `SemanticKnowledgeBundle` contains
sorted unique references, cases, and guidance and verifies that manifest IDs exactly equal nested
artifact IDs, including every nested case `step_id`.

- [ ] **Step 6: Run all schema tests**

Run: `pytest -q tests/knowledge/test_schema.py tests/knowledge/test_semantic_schema.py`

Expected: PASS, including legacy construction compatibility.

- [ ] **Step 7: Commit Task 2**

```bash
git add src/sedna/knowledge/schema tests/knowledge/test_schema.py \
  tests/knowledge/test_semantic_schema.py src/sedna/knowledge/__init__.py
git commit -m "feat(knowledge): define canonical semantic bundles"
```

---

### Task 3: Define Extractor, Critic, and Repair Draft Contracts

**Files:**
- Create: `src/sedna/knowledge/semantic/__init__.py`
- Create: `src/sedna/knowledge/semantic/drafts.py`
- Create: `tests/knowledge/test_semantic_drafts.py`

**Interfaces:**
- Produces: `SemanticDraftBundle`, discriminated draft artifact models, `DraftCitation`, `CriticVerdict`, `CompilationDisposition`, and `SemanticCompilationResult`.
- Consumes: schema vocabulary and `VerificationFinding`, but not canonical `SourceRef`; the LLM cites segment indexes only.
- Enforces: draft-local IDs are safe path segments only for cross-reference inside one response and never become canonical IDs.

- [ ] **Step 1: Write failing strict-draft tests**

Cover discriminated unions, duplicate local IDs, invalid segment indexes, unknown origin handling,
critic severity, and the requirement that ignored indexes plus cited indexes remain in the input
segment range when validated by the compiler.

```python
def test_draft_bundle_rejects_duplicate_local_ids():
    with pytest.raises(ValidationError):
        SemanticDraftBundle(
            artifacts=(draft_reference("a"), draft_reference("a")),
            ignored_segment_indexes=(),
        )
```

- [ ] **Step 2: Run draft tests and witness RED**

Run: `pytest -q tests/knowledge/test_semantic_drafts.py`

Expected: collection error for the absent module.

- [ ] **Step 3: Implement strict draft models**

Use a discriminator `draft_type` with values `reference`, `case`, and `guidance`. Draft context
assertions cite one or more non-negative segment indexes and carry `relation`, `origin`, and
confidence. Draft references mirror canonical reference semantics; draft cases contain ordered
draft steps; draft guidance mirrors `DecisionRule`. All models are frozen and `extra="forbid"`.

`CriticVerdict.findings` contains canonical `VerificationFinding` values with:

```python
code: Literal[
    "unsupported_claim", "missing_prerequisite", "missing_exception",
    "context_omission", "overgeneralization", "origin_mismatch",
    "unsafe_material", "lost_negative_evidence", "invalid_provenance",
]
severity: Literal["warning", "material"]
artifact_local_id: str | None
message: SearchableNonEmptyString
segment_indexes: tuple[int, ...]
```

`CriticVerdict.accepted` must be false whenever a material finding exists and true only when no
material finding exists.

`CompilationDisposition` contains `verified`, `quarantined`, `failed`, and `unchanged`.
`SemanticCompilationResult` enforces exactly one payload shape: verified/unchanged has a bundle
and verification record, quarantined has verification plus quarantine and no bundle, and failed
has only a safe reason code/message. `unchanged` is created by the service in Task 8, not by the
compiler state machine.

- [ ] **Step 4: Run draft tests**

Run: `pytest -q tests/knowledge/test_semantic_drafts.py`

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/sedna/knowledge/semantic tests/knowledge/test_semantic_drafts.py
git commit -m "feat(knowledge): define semantic LLM contracts"
```

---

### Task 4: Build Safe Host-LLM Requests and Versioned Prompts

**Files:**
- Create: `src/sedna/knowledge/semantic/prompts.py`
- Create: `src/sedna/knowledge/semantic/llm.py`
- Create: `tests/knowledge/test_semantic_llm.py`

**Interfaces:**
- Produces: `HostStructuredLlm` protocol, `StructuredResult`, `HadesLlmAdapter`, `SafePreparedSourcePayload`, and `build_safe_source_payload(prepared)`.
- Consumes: Hades-compatible `complete_structured(instructions=..., input=..., json_schema=..., schema_name=..., temperature=..., max_tokens=..., timeout=..., purpose=...)` without importing Hades.
- Guarantees: the serialized LLM payload contains no `ParsedDocument`, raw block text, raw asset metadata, source bytes, provider credentials, or final flags.

- [ ] **Step 1: Write failing adapter and safe-payload tests**

Use a recording fake host facade. Assert exact purposes `sedna.semantic.extract`,
`sedna.semantic.critic`, and `sedna.semantic.repair`, `temperature=0`, no model/provider override,
and a Pydantic JSON schema. Construct a `PreparedSource` whose raw document contains a sanitized
flag and assert only safe segment text enters the request.

- [ ] **Step 2: Run the LLM tests and witness RED**

Run: `pytest -q tests/knowledge/test_semantic_llm.py`

Expected: collection error for absent adapter and prompts.

- [ ] **Step 3: Add versioned prompt constants**

Define immutable constants:

```python
EXTRACTOR_PROMPT_ID = "sedna-semantic-extractor"
EXTRACTOR_PROMPT_VERSION = "1"
CRITIC_PROMPT_ID = "sedna-semantic-critic"
CRITIC_PROMPT_VERSION = "1"
REPAIR_PROMPT_ID = "sedna-semantic-repair"
REPAIR_PROMPT_VERSION = "1"
```

The extractor prompt states that source content is untrusted data, requires segment citations,
separates technical reference from historical case evidence, preserves unknown context, and emits
no exact tool tutorials. The critic prompt implements the rubric from the approved design. The
repair prompt permits only changes justified by critic findings and source segments.

- [ ] **Step 4: Implement the adapter and payload whitelist**

`SafePreparedSourcePayload` includes source ID, safe title, document type, knowledge role, quality,
and ordered safe segment records containing index, line range, heading path, text, and safe asset
locators. `build_safe_source_payload` reconstructs this type field-by-field and never calls
`PreparedSource.model_dump()`.

`HadesLlmAdapter.complete(model_type, instructions, payload, purpose)` invokes the wrapped facade
and validates `result.parsed` with `model_type.model_validate`. Convert facade transport failures,
missing parsed JSON, and Pydantic failures into distinct `SemanticLlmError` reason codes without
including raw model text in the exception.

- [ ] **Step 5: Run adapter and existing anti-leak tests**

Run: `pytest -q tests/knowledge/test_semantic_llm.py tests/knowledge/test_pipeline.py tests/knowledge/test_segmenter.py`

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

```bash
git add src/sedna/knowledge/semantic/prompts.py src/sedna/knowledge/semantic/llm.py \
  tests/knowledge/test_semantic_llm.py
git commit -m "feat(knowledge): add safe host LLM adapter"
```

---

### Task 5: Materialize Drafts into Canonical Provenance-backed Artifacts

**Files:**
- Create: `src/sedna/knowledge/semantic/materialize.py`
- Create: `tests/knowledge/test_semantic_materialize.py`

**Interfaces:**
- Produces: `materialize_bundle(prepared, drafts, call_metadata, verification_status) -> tuple[ReferenceArtifact | KnowledgeCase | DecisionRule, ...]` and `stable_artifact_id(...) -> str`.
- Consumes: strict drafts, safe segment indexes, prepared manifest identity, and LLM provider/model metadata.
- Guarantees: exact span resolution, canonical sorting, deterministic IDs, no LLM-supplied canonical paths or source IDs.

- [ ] **Step 1: Write failing provenance and ID tests**

Require invalid segment indexes to fail, exact start/end lines to become `SourceRef`, duplicate
semantic artifacts to collapse only when canonical normalized content and citations match, and
IDs to remain stable across draft-local ID changes.

```python
def test_canonical_id_ignores_llm_local_id(prepared_source):
    first = materialize_bundle(prepared_source, bundle_with_local_id("a"), call_meta(), status())
    second = materialize_bundle(prepared_source, bundle_with_local_id("renamed"), call_meta(), status())
    assert first[0].artifact_id == second[0].artifact_id
```

- [ ] **Step 2: Run materialization tests and witness RED**

Run: `pytest -q tests/knowledge/test_semantic_materialize.py`

Expected: collection error for absent materializer.

- [ ] **Step 3: Implement citation resolution**

Resolve every cited segment index to:

```python
SourceRef(
    source_id=prepared.manifest.source_id,
    path=prepared.manifest.path,
    location=SourceLocation(
        start_line=segment.start_line,
        end_line=segment.end_line,
        section=" > ".join(segment.heading_path) or None,
    ),
)
```

Reject indexes outside the prepared segment tuple, empty citations on explicit or inferred
claims, and a draft that neither cites nor explicitly ignores an input segment.

- [ ] **Step 4: Implement deterministic canonical IDs and metadata**

Hash canonical JSON containing source ID, artifact type, semantic content, normalized citations,
and applicability, excluding draft-local IDs, model ID, timestamps, and verification state. Use a
readable prefix and the first 24 hexadecimal SHA-256 characters. Populate `ExtractionMetadata`
with parser versions from the foundation manifest and semantic extractor/prompt/model versions
from the actual host result.

- [ ] **Step 5: Materialize reference, case, step, guidance, context, and assessment records**

Sort semantically set-like tuples, preserve case step order, resolve all context assertion
citations independently, and force the supplied adjudicated verification state rather than
accepting one proposed by the extractor.

- [ ] **Step 6: Run materialization and schema tests**

Run: `pytest -q tests/knowledge/test_semantic_materialize.py tests/knowledge/test_semantic_schema.py tests/knowledge/test_schema.py`

Expected: PASS.

- [ ] **Step 7: Commit Task 5**

```bash
git add src/sedna/knowledge/semantic/materialize.py \
  tests/knowledge/test_semantic_materialize.py
git commit -m "feat(knowledge): materialize semantic artifacts"
```

---

### Task 6: Implement the Bounded Extractor-Critic-Repair Compiler

**Files:**
- Create: `src/sedna/knowledge/semantic/compiler.py`
- Modify: `src/sedna/knowledge/semantic/__init__.py`
- Create: `tests/knowledge/test_semantic_compiler.py`

**Interfaces:**
- Produces: `SemanticCompiler.compile(prepared: PreparedSource) -> SemanticCompilationResult`.
- Constructor: `SemanticCompiler(llm: HadesLlmAdapter, *, clock: Callable[[], datetime])`.
- Consumes: Tasks 3–5 draft, adapter, prompt, and materialization contracts.
- Enforces: exactly one extractor call, one critic call, zero or one repair call, and zero or one post-repair critic call.

- [ ] **Step 1: Write RED state-machine tests**

Cover:

- extractor accepted by critic -> verified bundle;
- warning-only critic -> verified bundle plus findings;
- material finding -> one repair -> accepted -> verified bundle with `repair_count=1`;
- material finding after repair -> semantic quarantine with no artifacts;
- malformed extractor output -> typed failed result;
- timeout -> typed failed result without raw response;
- unsafe canonical material -> quarantine;
- architecture omission found by critic -> repair includes a context assertion;
- exact call count and purpose sequence for every path.

- [ ] **Step 2: Run compiler tests and witness RED**

Run: `pytest -q tests/knowledge/test_semantic_compiler.py`

Expected: collection error for absent compiler.

- [ ] **Step 3: Implement extractor and initial critic path**

Build one safe source payload, call the extractor with `SemanticDraftBundle.model_json_schema()`,
validate segment accounting, then call the critic with safe source payload plus the validated
draft bundle. Never include raw host response text.

- [ ] **Step 4: Implement bounded repair and final adjudication**

If material findings exist, call repair once with the validated drafts and typed findings, then
call critic once on the repaired drafts. A second material verdict produces
`CompilationDisposition.QUARANTINED`; it cannot trigger another repair.

- [ ] **Step 5: Implement result metadata and failures**

Capture provider/model identifiers and token usage from each successful host call in safe call
metadata. Use the injected UTC clock for reproducible tests. Transport or schema failures produce
`CompilationDisposition.FAILED` and a safe reason code; source-semantic disagreement produces
`QUARANTINED`; accepted adjudication produces `VERIFIED` and materialized artifacts.

- [ ] **Step 6: Run compiler and materialization tests**

Run: `pytest -q tests/knowledge/test_semantic_compiler.py tests/knowledge/test_semantic_materialize.py tests/knowledge/test_semantic_llm.py`

Expected: PASS.

- [ ] **Step 7: Commit Task 6**

```bash
git add src/sedna/knowledge/semantic/compiler.py src/sedna/knowledge/semantic/__init__.py \
  tests/knowledge/test_semantic_compiler.py
git commit -m "feat(knowledge): compile and verify semantic knowledge"
```

---

### Task 7: Persist Semantic Bundles Atomically

**Files:**
- Modify: `src/sedna/knowledge/repository.py`
- Create: `tests/knowledge/test_semantic_repository.py`

**Interfaces:**
- Produces: `write_semantic_result(result)`, `load_semantic_bundle(source_id)`, `load_semantic_verification(source_id)`, `load_semantic_quarantine(source_id)`, and `semantic_result_is_current(...)` on `CanonicalKnowledgeRepository`.
- Consumes: Task 2 semantic bundle/audit schemas and Task 6 result.
- Storage: `semantic_bundles`, `semantic_verification`, and `semantic_quarantine` directories, each with one `<source_id>.json` record.

- [ ] **Step 1: Write RED persistence, confinement, and idempotency tests**

Test deterministic bytes, strict load validation, safe source IDs, symlink rejection, no FIFO
blocking, bundle/verification identity matching, mutually exclusive bundle/quarantine state,
failed-result non-persistence, stale semantic version detection, and unchanged semantic input.

- [ ] **Step 2: Run repository tests and witness RED**

Run: `pytest -q tests/knowledge/test_semantic_repository.py tests/knowledge/test_repository.py`

Expected: failures for absent directories and methods.

- [ ] **Step 3: Extend the repository directory allowlist and strict loaders**

Reuse `_target`, descriptor-relative directory opening, nonblocking `O_NOFOLLOW` reads, Pydantic
validation, and identity checks. Do not add path-based fallback IO.

- [ ] **Step 4: Add a semantic source transition**

Under the existing source lock, snapshot the three semantic records, write a semantic transition
journal containing raw byte snapshots, then:

- verified result: write verification, write bundle, delete semantic quarantine;
- quarantined result: write verification and quarantine, delete bundle;
- failed result: do not modify canonical semantic state.

On failure restore byte-exact snapshots and preserve the original exception. Delete the journal
only after all directory fsyncs complete. Extend startup recovery to semantic journals without
weakening foundation recovery.

- [ ] **Step 5: Implement currentness checks**

An existing semantic result is current only when source ID/hash, foundation schema/parser
versions, semantic schema version, extractor/critic/repair prompt versions, and configured
compiler version match. Model identity is recorded but does not by itself force re-extraction
unless the caller opts into a model-pinned policy.

- [ ] **Step 6: Run repository and compiler tests**

Run: `pytest -q tests/knowledge/test_semantic_repository.py tests/knowledge/test_repository.py tests/knowledge/test_semantic_compiler.py`

Expected: PASS.

- [ ] **Step 7: Commit Task 7**

```bash
git add src/sedna/knowledge/repository.py tests/knowledge/test_semantic_repository.py
git commit -m "feat(knowledge): persist semantic compilation"
```

---

### Task 8: Add an End-to-End M2 Service and Golden Semantic Fixtures

**Files:**
- Create: `src/sedna/knowledge/semantic/service.py`
- Modify: `src/sedna/knowledge/semantic/__init__.py`
- Modify: `src/sedna/knowledge/__init__.py`
- Create: `tests/knowledge/fixtures/semantic/reference-methodology.json`
- Create: `tests/knowledge/fixtures/semantic/windows-walkthrough.json`
- Create: `tests/knowledge/fixtures/semantic/hybrid-reference-case.json`
- Create: `tests/knowledge/fixtures/semantic/context-repair.json`
- Create: `tests/knowledge/test_semantic_service.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `SemanticIngestionService.compile_and_store(prepared: PreparedSource) -> SemanticCompilationResult` and public imports from `sedna.knowledge`.
- Constructor: `SemanticIngestionService(repository, compiler)`.
- Consumes: existing `IngestionPipeline.prepare`, Task 6 compiler, and Task 7 repository.
- Does not register a Hades tool or traverse folders; M4 will compose this service.

- [ ] **Step 1: Write end-to-end RED tests**

Use the real deterministic parser/segmenter, a scripted host LLM facade, the real semantic
compiler, and a temporary canonical repository. Cover a technical lesson, Windows walkthrough,
hybrid document, repair for missing architecture, second-pass semantic unchanged, and semantic
recompile after prompt-version change.

- [ ] **Step 2: Run the service tests and witness RED**

Run: `pytest -q tests/knowledge/test_semantic_service.py`

Expected: collection error for absent service.

- [ ] **Step 3: Implement compile-and-store orchestration**

`compile_and_store` checks repository currentness before an LLM call. If current, it returns a
typed `UNCHANGED` result loaded from canonical state. Otherwise it compiles once and persists only
verified or quarantined outcomes using the semantic transition. Failed outcomes remain run-local.
This makes repeated semantic ingestion idempotent for unchanged source and compiler versions.

- [ ] **Step 4: Add deterministic golden LLM responses**

Fixture JSON represents structured model output, not mocked canonical artifacts. The Windows
fixture must include explicit Windows applicability and an incompatible Linux condition; the
hybrid fixture must emit both a reference and a case; the context-repair fixture must show the
critic finding and corrected response.

- [ ] **Step 5: Document the M2 boundary**

Update README to show:

```text
PreparedSource -> host LLM extractor -> critic -> bounded repair
               -> canonical SemanticKnowledgeBundle
```

State explicitly that M2 can compile one prepared source through a supplied Hades LLM facade but
does not yet expose “learn folder” or retrieval tools.

- [ ] **Step 6: Run focused and full verification**

Run:

```bash
pytest -q tests/knowledge
pytest -q
ruff check src/sedna/knowledge tests/knowledge
git diff --name-only 85aac46 -- '*.py' | xargs ruff format --check
git diff --check
```

Expected: all tests pass, Ruff reports no violations, format check passes, and diff check is empty.

- [ ] **Step 7: Audit the security boundary**

Serialize every semantic fixture result and assert no raw or recursively URL/HTML-decoded HTB
flag marker or contextual root/user 32-hex value appears. Assert the raw source fixture hash is
unchanged before and after compilation.

- [ ] **Step 8: Commit Task 8**

```bash
git add README.md src/sedna/knowledge tests/knowledge
git commit -m "feat(knowledge): complete semantic compiler milestone"
```

---

## Final Verification and Handoff

- [ ] Run the complete suite, scoped Ruff checks, `ruff format --check` on Python files changed
  since `85aac46`, and `git diff --check` again from the final commit. Eleven unchanged foundation
  files fail the repository-wide format check at baseline and must not be mechanically reformatted
  as part of M2.
- [ ] Inspect `git status --short` and confirm no raw corpus, index, virtual environment, or model
  response dump is tracked.
- [ ] Compare public exports against the M2 interfaces in this plan.
- [ ] Confirm the Hades adapter never requests provider/model/profile overrides.
- [ ] Confirm every verified artifact has exact provenance and every inferred context assertion
  is labeled.
- [ ] Confirm the one-repair limit with call-count tests.
- [ ] Record observed test counts and any intentional deviations in the final handoff.

M3 begins only after M2 is reviewed. Its plan will derive lane-aware FTS5 and relational facet
projections from `SemanticKnowledgeBundle`; it will not change the M2 canonical transaction format.
