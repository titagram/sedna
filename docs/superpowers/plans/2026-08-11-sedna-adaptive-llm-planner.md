# Sedna Adaptive LLM Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the M6B adaptive planner that settles private engagement evidence, reconstructs a
source-cited situation, retrieves applicable Sedna experience, and returns a critic-validated,
LLM-scored strategic frontier with safe source-backed or model-generated command suggestions.

**Architecture:** M6B consumes the M6A append-only engagement facade and never owns operational
tool execution. Canonical `ExecutionExample` records are compiled atomically inside their source
bundle and exposed only through an ID-only drill-down projection; runtime evidence is converted
into authoritative typed observation events, while situation, strategy ledger, archive, and
frontier remain rebuildable projections. A host-owned structured LLM performs observation
extraction and planner/critic/one-repair reasoning outside all repository locks; deterministic code
only validates identity, references, authorization, limits, ordering, cache keys, and optimistic
commit revisions.

**Tech Stack:** Python 3.11–3.13, Pydantic 2.13.4, standard-library `hashlib`, `ipaddress`,
`json`, `re`, `shlex`, and `sqlite3`/FTS5, Hades/Hermes `complete_structured`, pytest 8,
pytest-asyncio, Ruff 0.15.10, and the M6A descriptor-confined engagement repository.

## Completion Reconciliation — 2026-08-14

- Tasks 1–14 are implemented, independently reviewed where required, and committed on `main`
  through `e7a5010`.
- The post-integration verification on `e7a5010` passed 1,641 tests, `ruff check src tests`, and
  `git diff --check`; the repository was clean and local/remote `main` matched before this
  documentation-only reconciliation.
- Task-step and behavioral final-verification checkboxes below are marked complete from the
  corresponding RED/GREEN evidence, dedicated commits, review verdicts, and final regression gate.
- The exact repository-wide `ruff format --check src tests` command remains a pre-existing baseline
  failure on 30 legacy files. M6B used clean touched-file format gates and deliberately avoided a
  broad unrelated formatter rewrite. Therefore Task 14 Step 8 and the combined final formatting
  checkbox remain open and explicitly document this accepted baseline deviation.
- M6C report/case promotion is a separate successor plan, not unfinished M6B implementation.

## Global Constraints

- M6A is a hard prerequisite. The public package `sedna.engagement` must provide
  `ExecutionLaneKey`, `EngagementSnapshot`, `EngagementStatus`, `ProofRequirement`, `ScopeReference`,
  `JournalEventDraft`, `SettlementReason`, `EngagementSettlementOutcome`,
  `EngagementSettlementPort`, and
  `EngagementJournalService` with `load_snapshot`, `append_events`, `read_evidence_slice`,
  `load_projection`, `commit_projection`, `resolve_lane_binding`, and `load_active_decision`.
- M6B must not import or reuse the legacy `sedna.models.Engagement` or `SednaStore` records.
- Hades or Hermes remains the final operator. Sedna suggests strategic intent and optional
  commands; it never invokes an operational tool and never bypasses Hades authorization,
  sandboxing, approval, or `/learn` validation.
- Private engagement evidence may contain flags and engagement credentials. Planning models for
  private state must not use `SearchableString`, which deliberately rejects final-flag material.
- Authoritative private proof/secret records persist only a candidate-only evidence range plus its
  SHA-256. A trusted report/promotion projector may recover the exact bytes only through M6A's
  bounded `read_evidence_slice`, must recheck range/digest, and must symbolize/redact them before
  constructing or serializing any learning/promotion input.
- Provider credentials, host-runtime secrets unrelated to the engagement, and raw provider
  exceptions never enter journal events, prompts, projections, or tool responses.
- All source, evidence, web, registry, canonical, prior-model, and command content is untrusted
  data. It is serialized in bounded structured payload fields and never interpolated into system
  instructions.
- `ExecutionExample` command text is bundle-owned, non-searchable, and committed, replaced,
  loaded, audited, quarantined, and deleted atomically with its semantic source.
- Every current-target value in a command suggestion is represented by a typed placeholder and is
  resolved only from an engagement `ScopeReference`. A raw IP, CIDR, URL, or dotted hostname in a
  command template is rejected.
- Source-case usernames, passwords, hashes, tokens, and keys remain symbolic
  `source_case_credential` placeholders and are never auto-bound to the current engagement.
- The LLM assigns scores and semantic outcomes. Deterministic code does not implement a domain
  scoring formula or per-tool exception table.
- Strategy family, execution variant, and attempt IDs are runtime-owned and immutable. The model
  emits bounded keys and must echo existing IDs; changing prose cannot replace identity.
- The hot ledger is bounded to 32 families, 64 variants, eight recent attempts per variant, and
  256 recent attempts in total. Older attempts remain authoritative in journal events and survive
  only as deterministic aggregates/archive summaries. Each call receives at most 16 archive
  reactivation candidates and a 16 KiB archive summary. No entry may disappear silently.
- Planner input contains at most 64 recent events and 64 KiB of recent-event text. The complete
  canonical JSON planner request is capped at 512 KiB; an input that cannot fit after deterministic
  priority packing produces a typed gap instead of silent truncation.
- Evidence settlement uses exact 32 KiB slices, at most 64 slices and 2 MiB per invocation. It
  returns `incomplete` with the remaining byte ranges still pending when that cap is reached.
- A normal visible frontier contains three to eight proposals, while fewer are allowed when the
  validated situation is genuinely constrained.
- Planner delivery follows `planner -> critic -> optional one repair -> final critic`. A second
  rejection produces a typed planning gap and no new validated frontier.
- No engagement, canonical-repository, or SQLite lock may be held across evidence reading, a host
  LLM call, canonical retrieval, or command rendering.
- A frontier commit uses optimistic engagement revision and canonical revision checks. A stale
  result is never published.
- `<engagement>/engagement-state.json` remains the M6A lifecycle projection. M6B's
  `<engagement>/state.json` contains only a `SituationProjection`; neither file may embed the
  other model. `strategy-archive.jsonl` is a separate descriptor-confined M6A projection surface
  with its own compare-and-swap revision envelope, never a caller-selected generic path.
- The planner cache key includes situation digest/material revision, verified resulting-ledger
  digest, canonical corpus revision, shared-source-registry digest, requested proposal bound, and
  all relevant prompt/schema/policy versions. Input-ledger digest remains separate audit data.
- Retrieval lane scores remain lane-local evidence. M6B never copies them into frontier scores or
  compares a reference score with a case, negative-evidence, or guidance score.
- `raw_src/` remains immutable. Corpus migration is progressive: a legacy `2.4.0` bundle remains
  strategically projectable and retrievable with `execution_examples=()`, while relearning its
  original source recompiles it exactly once through the normal learning path. No canonical JSON
  is rewritten in place.
- Learning currentness and retrieval compatibility are separate decisions. The learning service
  treats the exact `2.4.0`/compiler-8/prompt-v1 contract as stale, but repository projection
  accepts that exact legacy contract as strategic-only knowledge; no other stale or unknown
  bundle version is implicitly accepted.
- SQLite v5 projects legacy bundles with zero execution-example locator rows and records their
  semantic/execution-example capability version in indexed source state. Drill-down returns a
  typed `legacy_bundle_without_examples` coverage gap, never a fabricated command or an empty
  result indistinguishable from a current bundle that legitimately has no examples.
- A source whose original bytes are unavailable remains strategically usable indefinitely. It
  cannot produce, or be cited as the origin of, a source-backed command until it is relearned into
  the execution-example schema; model-generated suggestions must remain labeled as such.
- The semantic migration versions are exact: schema `2.4.0 -> 2.5.0`, compiler `8 -> 9`, semantic
  extractor/critic/repair prompt `1 -> 2`, execution-example schema `1`, source projection
  `canonical-projection-v2 -> canonical-projection-v3`, and SQLite schema `4 -> 5`.
- M6A establishes product/plugin version `0.2.0`; M6B and M6C keep that version and only verify all
  package/plugin declarations remain synchronized.
- Tests never invoke Nmap, a browser, a remote target, or another real security tool.
- Objective proof is always tied to an explicit `EngagementManifest.required_proofs` entry by
  stable requirement ID. An empty requirement list does not mean success and disables automatic
  closure; satisfying only a subset never closes the engagement.
- `SharedSourceRegistry` exposes only bounded, deeply validated managed records to planning.
  Registry entries, research queries, and consulted-page extracts remain structured untrusted
  data; manual prose outside managed `sources.md` blocks is preserved but not injected into a
  prompt.

---

### Task 1: Define Canonical and Draft Execution Examples

**Files:**
- Create: `src/sedna/knowledge/schema/execution.py`
- Modify: `src/sedna/knowledge/schema/__init__.py`
- Modify: `src/sedna/knowledge/__init__.py`
- Modify: `src/sedna/knowledge/semantic/drafts.py`
- Modify: `src/sedna/knowledge/semantic/__init__.py`
- Create: `tests/knowledge/test_execution_examples.py`
- Modify: `tests/knowledge/test_semantic_drafts.py`

**Interfaces:**
- Consumes: `DraftLocalId`, `DraftCitation`, `SourceRef`, and `ExtractionMetadata`.
- Produces: `ExecutionPlaceholder`, `ExecutionExample`, `DraftExecutionPlaceholder`,
  `ExecutionCondition`, `ExecutionPlatformConstraint`, `DraftExecutionExample`, and
  `SemanticDraftBundle.execution_examples`.
- Guarantees: examples cite exactly one valid reference/case-step parent, every template token has
  one typed placeholder, prerequisites/applicability/platform constraints are independently
  source-cited, final flags are rejected, and source-case credentials cannot request automatic
  binding.
- [x] **Step 1: Write failing canonical command-template tests**
Add tests requiring immutable strict models, deterministic placeholder ordering, exact placeholder
coverage, `requires_validation=True`, at least one source reference, and the source-case credential
policy. Require source refs on every prerequisite and platform constraint, and reject an asserted
OS/architecture/execution environment hidden only in prose:

```python
def test_execution_example_requires_typed_template_placeholders():
    example = ExecutionExample(
        schema_version="1",
        example_id="execution-example-http-probe",
        parent_artifact_id="case_step-http-enumeration",
        command_template="curl -i {{target}}",
        placeholders=(
            ExecutionPlaceholder(
                name="target",
                kind="target",
                binding_policy="authorized_scope",
                role="authorized HTTP target",
            ),
        ),
        capability_hint="http.inspect",
        purpose="Inspect HTTP response metadata.",
        observed_role="This invocation gathered response evidence in the source case.",
        prerequisites=(
            ExecutionCondition(
                statement="An authorized HTTP target is available.",
                source_refs=(source_ref(),),
            ),
        ),
        applicability=ApplicabilityContext(),
        platform_constraints=(
            ExecutionPlatformConstraint(
                dimension="execution_environment",
                relation="compatible",
                value="network-reachable HTTP service",
                source_refs=(source_ref(),),
            ),
        ),
        source_refs=(source_ref(),),
        extraction=extraction_metadata(),
        requires_validation=True,
    )

    assert example.command_template == "curl -i {{target}}"
    assert example.placeholders[0].binding_policy == "authorized_scope"


def test_source_case_credential_can_never_auto_bind():
    with pytest.raises(ValidationError, match="source-case credentials"):
        ExecutionPlaceholder(
            name="source_password",
            kind="source_case_credential",
            binding_policy="authorized_scope",
            role="password observed only in the source case",
        )
```
- [x] **Step 2: Write failing draft-parent and local-ID tests**
Construct a draft bundle containing one reference, one case step, and two execution examples.
Assert that a parent may reference the reference or nested step local ID, but not a case parent,
guidance item, missing local ID, or another execution example. Assert all artifact, step, and
execution-example local IDs are globally unique within the response.
- [x] **Step 3: Run focused tests and witness RED**
Run:

```bash
pytest -q tests/knowledge/test_execution_examples.py \
  tests/knowledge/test_semantic_drafts.py -x
```
Expected: collection fails because execution-example models and the draft bundle field do not yet
exist.
- [x] **Step 4: Implement the canonical execution contracts**
Define the exact closed vocabulary and strict records in `schema/execution.py`:

```python
class PlaceholderKind(StrEnum):
    TARGET = "target"
    PORT = "port"
    USERNAME = "username"
    CREDENTIAL_REF = "credential_ref"
    SOURCE_CASE_CREDENTIAL = "source_case_credential"
    WORDLIST = "wordlist"
    PATH = "path"
    VALUE = "value"


class PlaceholderBindingPolicy(StrEnum):
    AUTHORIZED_SCOPE = "authorized_scope"
    HOST_SUPPLIED = "host_supplied"
    NEVER_AUTO_BIND = "never_auto_bind"


class ExecutionPlaceholder(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    kind: PlaceholderKind
    binding_policy: PlaceholderBindingPolicy
    role: SearchableNonEmptyString
```

`ExecutionPlaceholder` enforces `TARGET -> AUTHORIZED_SCOPE` and
`SOURCE_CASE_CREDENTIAL -> NEVER_AUTO_BIND`. `ExecutionExample` uses an 8,192-character command
bound, rejects NUL/control characters and final-flag material, sorts placeholders by name, rejects
duplicates, and requires the template token set `{{name}}` to equal the declared placeholder set.
Define source-backed `ExecutionCondition` plus `ExecutionPlatformConstraint` with dimensions
`os_family`, `os_version`, `cpu_architecture`, and `execution_environment`, and closed relations
`required`, `compatible`, and `incompatible`. `ExecutionExample.prerequisites`, `.applicability`
(`ApplicabilityContext`), and `.platform_constraints` are explicit and independently cited; they
do not inherit applicability silently from the parent. Cross-check duplicate/conflicting
constraints and require every assertion/condition to cite source spans from the same bundle.
- [x] **Step 5: Implement draft examples and bundle validation**
Add `DraftExecutionPlaceholder` with the same name/kind/policy/role fields and
source-cited draft condition/platform-constraint counterparts. Add `DraftExecutionExample` with
`local_id`, `parent_local_id`, command metadata, explicit `DraftApplicabilityContext`, source-cited
prerequisites/platform constraints, citations, and literal `requires_validation=True`. Extend
`SemanticDraftBundle`:

```python
class SemanticDraftBundle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    artifacts: tuple[DraftArtifact, ...] = ()
    execution_examples: tuple[DraftExecutionExample, ...] = ()
    ignored_segment_indexes: tuple[int, ...] = ()
```

Update its validator, `_segment_indexes`, and public exports. Validate parent type using the
response-local reference and nested case-step maps rather than prose or ordinal position.
- [x] **Step 6: Run schema and draft tests GREEN**
Run:

```bash
pytest -q tests/knowledge/test_execution_examples.py \
  tests/knowledge/test_semantic_drafts.py \
  tests/knowledge/test_semantic_schema.py
```
Expected: PASS.
- [x] **Step 7: Commit Task 1**
```bash
git add -- src/sedna/knowledge/schema/execution.py src/sedna/knowledge/schema/__init__.py src/sedna/knowledge/__init__.py src/sedna/knowledge/semantic/drafts.py src/sedna/knowledge/semantic/__init__.py tests/knowledge/test_execution_examples.py tests/knowledge/test_semantic_drafts.py
git commit -m "feat(knowledge): model source execution examples"
```

---

### Task 2: Materialize and Critic-Verify Execution Examples

**Files:**
- Modify: `src/sedna/knowledge/semantic/materialize.py`
- Modify: `src/sedna/knowledge/semantic/compiler.py`
- Modify: `src/sedna/knowledge/semantic/prompts.py`
- Modify: `src/sedna/knowledge/schema/semantic.py`
- Modify: `tests/knowledge/test_semantic_materialize.py`
- Modify: `tests/knowledge/test_semantic_compiler.py`
- Modify: `tests/knowledge/test_semantic_llm.py`
- Modify: `tests/knowledge/fixtures/semantic/windows-walkthrough.json`
- Modify: `tests/knowledge/fixtures/semantic/hybrid-reference-case.json`

**Interfaces:**
- Consumes: Task 1 drafts and canonical models.
- Produces: `MaterializedSemanticContent`, `materialize_semantic_content(...)`, bundle-owned
  `execution_examples`, compiler/prompt version 2.5.0/9/2, and exact manifest example coverage.
- Preserves: `materialize_bundle(...) -> tuple[CanonicalArtifact, ...]` for existing callers.
- [x] **Step 1: Write failing deterministic materialization tests**
Test that two identical prepared sources and draft responses produce byte-identical example IDs;
different parent, cited span, template, or placeholder role changes the ID. Verify draft local IDs
never cross the boundary and the canonical parent is the materialized reference or step ID. Also
assert that changing a prerequisite, applicability assertion, platform constraint, or any of their
citations changes the example ID.
- [x] **Step 2: Write failing compiler attribution and quarantine tests**
Script an extractor response containing `nmap -sV {{target}}` and an accepting critic. Assert the
verified bundle contains the example, its source span and extractor metadata, and separate
`emitted_execution_example_ids`. Script examples containing `HTB{final}` or a non-parameterized
source-case password and assert deterministic materialization quarantine or a material critic
finding; neither value may enter a verified bundle.
- [x] **Step 3: Run semantic tests and witness RED**
Run:

```bash
pytest -q tests/knowledge/test_semantic_materialize.py \
  tests/knowledge/test_semantic_compiler.py \
  tests/knowledge/test_semantic_llm.py -x
```
Expected: failures because examples are not materialized or included in the call contract.
- [x] **Step 4: Add a compatibility-preserving materialization result**
Implement:

```python
@dataclass(frozen=True, slots=True)
class MaterializedSemanticContent:
    artifacts: tuple[CanonicalArtifact, ...]
    execution_examples: tuple[ExecutionExample, ...]


def materialize_semantic_content(
    prepared: PreparedSource,
    drafts: SemanticDraftBundle,
    call_metadata: SemanticCallMetadata,
    verification_status: VerificationStatus,
) -> MaterializedSemanticContent:
    prepared = validate_prepared_source(prepared)
    drafts = SemanticDraftBundle.model_validate(drafts.model_dump(mode="json"))
    call_metadata = SemanticCallMetadata.model_validate(call_metadata.model_dump(mode="json"))
    artifacts, local_to_canonical = _materialize_artifacts_with_local_ids(
        prepared,
        drafts,
        call_metadata,
        verification_status,
    )
    examples = _materialize_execution_examples(
        prepared,
        drafts.execution_examples,
        local_to_canonical,
        call_metadata,
    )
    return MaterializedSemanticContent(
        artifacts=artifacts,
        execution_examples=examples,
    )
```

Implement the two helpers with these exact private signatures so canonical ID assignment and
execution-example materialization remain independently testable:

```python
def _materialize_artifacts_with_local_ids(
    prepared: PreparedSource,
    drafts: SemanticDraftBundle,
    call_metadata: SemanticCallMetadata,
    verification_status: VerificationStatus,
) -> tuple[tuple[CanonicalArtifact, ...], Mapping[str, str]]:


def _materialize_execution_examples(
    prepared: PreparedSource,
    drafts: tuple[DraftExecutionExample, ...],
    local_to_canonical: Mapping[str, str],
    call_metadata: SemanticCallMetadata,
) -> tuple[ExecutionExample, ...]:
```

Keep `materialize_bundle` as a wrapper returning `.artifacts`. Include example citations in segment
accounting. Generate `execution-example-<24 hex>` IDs from source ID, canonical parent ID,
semantic fields, sorted source refs, and placeholders. Do not include examples in parent artifact
identity so adding an example does not rename the source-backed strategy. The canonical ID material
must also contain normalized prerequisites, full applicability context, platform constraints, and
their sorted source refs.
- [x] **Step 5: Extend the bundle and manifest validators**
Add legacy-readable defaults:

```python
class SemanticCompilationManifest(BaseModel):
    execution_example_schema_version: NonEmptyString | None = None
    emitted_execution_example_ids: tuple[NonEmptyString, ...] = ()


class SemanticKnowledgeBundle(BaseModel):
    execution_examples: tuple[ExecutionExample, ...] = ()
```

Require sorted unique examples, exact manifest coverage, unique IDs across artifacts/steps/rules/
examples, bundle-source provenance on each example, and parent membership in the bundle's
references or nested case steps.
- [x] **Step 6: Replace the semantic no-command prompt boundary**
Advance all three prompt versions to `"2"`. The extractor must keep strategic artifacts free of
tool tutorials while emitting source-backed commands only in `execution_examples`; it must
parameterize targets and source-case credentials and extract explicit source-cited prerequisites,
applicability, OS, architecture, and execution-environment constraints. The critic independently
checks source text, parent type, citations, placeholder completeness, every applicability/platform
claim, prerequisite completeness, credential parameterization, `requires_validation`, and absence
of flags/provider secrets. An unsupported or missing material constraint is a material finding;
the repair prompt may change an example only when source evidence and critic findings justify it.
- [x] **Step 7: Update compiler versions and bundle assembly**
Set:

```python
SEMANTIC_SCHEMA_VERSION = "2.5.0"
SEMANTIC_COMPILER_VERSION = "9"
EXECUTION_EXAMPLE_SCHEMA_VERSION = "1"
```

Use `materialize_semantic_content` in `_verified`, record exact example IDs and schema version in
the manifest, and pass examples into `SemanticKnowledgeBundle`. Preserve the existing two-call or
four-call state machine and safe failure vocabulary.
- [x] **Step 8: Run semantic tests GREEN**
Run:

```bash
pytest -q tests/knowledge/test_execution_examples.py \
  tests/knowledge/test_semantic_materialize.py \
  tests/knowledge/test_semantic_compiler.py \
  tests/knowledge/test_semantic_llm.py \
  tests/knowledge/test_semantic_schema.py
```
Expected: PASS, with exactly two calls for accepted extraction and four for repaired extraction.
- [x] **Step 9: Commit Task 2**
```bash
git add -- src/sedna/knowledge/semantic/materialize.py src/sedna/knowledge/semantic/compiler.py src/sedna/knowledge/semantic/prompts.py src/sedna/knowledge/schema/semantic.py tests/knowledge/test_semantic_materialize.py tests/knowledge/test_semantic_compiler.py tests/knowledge/test_semantic_llm.py tests/knowledge/fixtures/semantic/windows-walkthrough.json tests/knowledge/fixtures/semantic/hybrid-reference-case.json
git commit -m "feat(knowledge): compile verified execution examples"
```

---

### Task 3: Make Execution-Example Migration Atomic and Progressively Compatible

**Files:**
- Modify: `src/sedna/knowledge/repository.py`
- Modify: `src/sedna/knowledge/semantic/service.py`
- Create: `tests/knowledge/test_execution_example_migration.py`
- Modify: `tests/knowledge/test_semantic_repository.py`
- Modify: `tests/knowledge/test_semantic_service.py`
- Modify: `tests/knowledge/test_semantic_version_migration.py`

**Interfaces:**
- Consumes: semantic schema/compiler/prompt versions from Task 2.
- Produces: separate learning-currentness and retrieval-compatibility checks, atomic stale-example
  replacement, and strict bundle drill-down loading by source and parent.
- [x] **Step 1: Write failing old-bundle migration test**
Seed a valid legacy `2.4.0` bundle with compiler `8`, semantic prompt versions `1`, no example
schema, and a current foundation manifest. Before relearning, assert repository iteration accepts
the bundle as strategic-only knowledge with `execution_examples=()`. Learn the same source with a
scripted v2 response. Assert one extractor/critic sequence produces a `2.5.0` bundle with examples,
and the next identical learning call returns `unchanged` with zero additional host calls.
- [x] **Step 2: Write failing atomic replacement/recovery tests**
Persist v1 with two examples, recompile changed source to v2 with one different example, inject a
failure at each semantic transaction phase, reopen the repository, and assert recovery yields
either the complete old bundle or complete new bundle. No stale example may survive outside its
own bundle because no separately committed command file exists.
- [x] **Step 3: Run repository migration tests and witness RED**
Run:

```bash
pytest -q tests/knowledge/test_execution_example_migration.py \
  tests/knowledge/test_semantic_repository.py \
  tests/knowledge/test_semantic_version_migration.py -x
```
Expected: old state is incorrectly considered fully current, or the repository rejects the
otherwise valid legacy strategic bundle.
- [x] **Step 4: Split learning currentness from retrieval compatibility**
Add `execution_example_schema_version` to `load_current_semantic_result`,
`semantic_result_is_current`, `_require_current_retrieval_bundle`, and
`_require_current_retrieval_quarantine`. `semantic_result_is_current` and
`load_current_semantic_result` require exact `2.5.0`/compiler-9/prompt-v2/example-schema-1 values,
so encountering a legacy source through `learn` recompiles it once. Retrieval validation accepts
either that current contract or exactly `2.4.0`/compiler-8/prompt-v1 with no example schema and an
empty example tuple. Keep this compatibility allowlist local and explicit; malformed bundles,
unknown versions, legacy bundles containing example records, and stale foundation manifests still
fail closed. Quarantine follows the same version-specific validation rules as its owning bundle.
- [x] **Step 5: Add strict bundle-owned example loading**
Implement:

```python
def load_execution_examples(
    self,
    source_id: str,
    *,
    parent_artifact_id: str,
    example_ids: tuple[str, ...] | None = None,
) -> tuple[ExecutionExample, ...]:
```

The method uses the existing descriptor-relative bundle loader, validates safe IDs, filters only
the requested parent, sorts by example ID, and when `example_ids` is supplied requires an exact
set match. It never searches command text or falls back to filesystem paths.
- [x] **Step 6: Run migration and repository tests GREEN**
Run:

```bash
pytest -q tests/knowledge/test_execution_example_migration.py \
  tests/knowledge/test_semantic_repository.py \
  tests/knowledge/test_semantic_service.py \
  tests/knowledge/test_semantic_version_migration.py
```
Expected: PASS.
- [x] **Step 7: Commit Task 3**
```bash
git add -- src/sedna/knowledge/repository.py src/sedna/knowledge/semantic/service.py tests/knowledge/test_execution_example_migration.py tests/knowledge/test_semantic_repository.py tests/knowledge/test_semantic_service.py tests/knowledge/test_semantic_version_migration.py
git commit -m "feat(knowledge): migrate execution example bundles"
```

---

### Task 4: Add Non-searchable Execution-Example Lookup and Audit

**Files:**
- Modify: `src/sedna/knowledge/retrieval/models.py`
- Modify: `src/sedna/knowledge/retrieval/projection.py`
- Modify: `src/sedna/knowledge/retrieval/sqlite.py`
- Modify: `src/sedna/knowledge/retrieval/service.py`
- Modify: `src/sedna/knowledge/retrieval/maintenance.py`
- Modify: `src/sedna/knowledge/retrieval/__init__.py`
- Modify: `src/sedna/knowledge/hades_runtime.py`
- Modify: `tests/knowledge/test_retrieval_models.py`
- Modify: `tests/knowledge/test_retrieval_projection.py`
- Modify: `tests/knowledge/test_retrieval_sqlite.py`
- Modify: `tests/knowledge/test_retrieval_service.py`
- Modify: `tests/knowledge/test_retrieval_maintenance.py`

**Interfaces:**
- Consumes: `CanonicalKnowledgeRepository.load_execution_examples`.
- Produces: `ExecutionExampleLocator`, `ExecutionExampleCoverageGap`,
  `ExecutionExampleDrilldown`, `RetrievalIndex.get_execution_example_locators`, and
  `KnowledgeRetrievalService.get_execution_examples`.
- Guarantees: SQLite contains example/parent/source IDs only; command templates cannot match FTS.
- [x] **Step 1: Write failing projection and SQLite secrecy tests**
Index a bundle whose command contains a unique marker `sedna-command-not-searchable-7f31`. Assert
the marker does not occur in any `artifact_fts` column, artifact `canonical_json`, or
`execution_example_lookup` row, while the lookup returns the correct example/parent/source IDs.
- [x] **Step 2: Write failing source-state parity tests**
Delete, duplicate, re-parent, and orphan lookup rows. Assert audit reports exact execution-example
counts and requires rebuild. Assert source projection digests change when example IDs or parent
relationships change even if ordinary artifact rows are identical.
- [x] **Step 3: Write failing drill-down revision tests**
Assert `KnowledgeRetrievalService.get_execution_examples(parent_id)`:

- performs ID lookup and canonical bundle load under the same before/after canonical revision;
- rejects locator/bundle source, parent, or example-ID disagreement;
- returns a typed runtime failure when canonical state changes during drill-down;
- returns examples with no gap for a current bundle;
- distinguishes a current parent with no examples from a legacy parent whose bundle could not have
  represented them, using a typed `legacy_bundle_without_examples` coverage gap.
- [x] **Step 4: Run focused retrieval tests and witness RED**
Run:

```bash
pytest -q tests/knowledge/test_retrieval_projection.py \
  tests/knowledge/test_retrieval_sqlite.py \
  tests/knowledge/test_retrieval_service.py \
  tests/knowledge/test_retrieval_maintenance.py -x
```
Expected: failures for missing locator projection and service method.
- [x] **Step 5: Extend backend-neutral state and projection**
Define:

```python
class ExecutionExampleLocator(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    example_id: Reason
    parent_artifact_id: Reason
    source_id: Annotated[SearchableNonEmptyString, Field(max_length=512)]


class IndexedExecutionExampleState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    example_id: Reason
    parent_artifact_id: Reason


class ExecutionExampleCoverageCode(StrEnum):
    LEGACY_BUNDLE_WITHOUT_EXAMPLES = "legacy_bundle_without_examples"
    SOURCE_EXAMPLES_UNAVAILABLE = "source_examples_unavailable"


class ExecutionExampleCoverageGap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: ExecutionExampleCoverageCode
    source_id: Annotated[SearchableNonEmptyString, Field(max_length=512)]
    semantic_schema_version: NonEmptyString
    explanation: Reason


class ExecutionExampleDrilldown(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    parent_artifact_id: Reason
    examples: tuple[ExecutionExample, ...] = ()
    coverage_gap: ExecutionExampleCoverageGap | None = None
```

Add sorted unique example states plus `semantic_schema_version` and nullable
`execution_example_schema_version` to `IndexedSourceState`; include all three in
`source_projection_digest`. Add `project_execution_example_locators(bundle)` and set
`SOURCE_PROJECTION_VERSION = "canonical-projection-v3"`. A valid legacy bundle projects its
strategic artifacts, zero locators, and null example capability; a current bundle with no examples
projects example schema `1`, which makes the two states unambiguous.
- [x] **Step 6: Add the ID-only SQLite table atomically**
Advance `_SCHEMA_VERSION` to `5` and add:

```sql
CREATE TABLE execution_example_lookup (
    example_id TEXT PRIMARY KEY,
    parent_artifact_id TEXT NOT NULL,
    owner_source_id TEXT NOT NULL,
    FOREIGN KEY(parent_artifact_id) REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
    FOREIGN KEY(owner_source_id) REFERENCES indexed_sources(source_id) ON DELETE CASCADE
);

CREATE INDEX execution_example_parent_idx
ON execution_example_lookup(parent_artifact_id, example_id);
```

Insert/delete lookup rows inside the same source transaction as artifacts. Extend schema audit,
orphan checks, source-state enumeration, capability-version parity, and rebuild parity. Do not add
an FTS trigger or command column. Schema-v5 rebuild must accept exact legacy strategic bundles and
create zero lookup rows for them rather than dropping their artifacts.
- [x] **Step 7: Add revision-guarded drill-down**
Extend `RetrievalIndex` with the locator method. Extend `KnowledgeRetrievalService` with optional
`execution_example_loader`, and implement:

```python
def get_execution_examples(
    self,
    parent_artifact_id: str,
) -> ExecutionExampleDrilldown:
```

Cross-validate canonical IDs and revisions. Return `legacy_bundle_without_examples` from indexed
source capability metadata without calling the loader; return `SOURCE_EXAMPLES_UNAVAILABLE` when
canonical drill-down is unavailable. Only a current schema-1 source can return `examples=()` with
`coverage_gap=None`. In `HadesKnowledgeRuntime.create`, inject
`repository.load_execution_examples` into the retrieval service.
- [x] **Step 8: Run retrieval tests GREEN**
Run:

```bash
pytest -q tests/knowledge/test_retrieval_models.py \
  tests/knowledge/test_retrieval_projection.py \
  tests/knowledge/test_retrieval_sqlite.py \
  tests/knowledge/test_retrieval_service.py \
  tests/knowledge/test_retrieval_maintenance.py
```
Expected: PASS, and the unique command marker is absent from every searchable projection.
- [x] **Step 9: Commit Task 4**
```bash
git add -- src/sedna/knowledge/retrieval/models.py src/sedna/knowledge/retrieval/projection.py src/sedna/knowledge/retrieval/sqlite.py src/sedna/knowledge/retrieval/service.py src/sedna/knowledge/retrieval/maintenance.py src/sedna/knowledge/retrieval/__init__.py src/sedna/knowledge/hades_runtime.py tests/knowledge/test_retrieval_models.py tests/knowledge/test_retrieval_projection.py tests/knowledge/test_retrieval_sqlite.py tests/knowledge/test_retrieval_service.py tests/knowledge/test_retrieval_maintenance.py
git commit -m "feat(knowledge): add execution example drilldown"
```

---

### Task 5: Define Strict Planning, Situation, Ledger, and Frontier Contracts

**Files:**
- Create: `src/sedna/planning/__init__.py`
- Create: `src/sedna/planning/models.py`
- Create: `src/sedna/planning/ports.py`
- Create: `tests/planning/test_models.py`

**Interfaces:**
- Consumes: M6A `ExecutionLaneKey`, `ScopeReference`, event/evidence IDs, retrieval artifact IDs,
  and canonical `ExecutionExample` IDs.
- Produces: all immutable structured request/response, situation, ledger, archive, frontier,
  critic, settlement, terminal-reconciliation, and planning-result contracts used by Tasks 6–14.
- [x] **Step 1: Write failing observation/situation tests**
Require private evidence text to retain a sample flag, facts and hypotheses to carry non-empty
event references, outcome categories to match the seven-value design vocabulary, sorted unique
IDs, and finite confidence. `SituationProjection.authoritative_journal_revision` tracks the CAS
head, while `material_event_revision` is the last event that changes situation semantics and the
SHA-256 state digest is bound to that material revision and explicitly excludes
`authoritative_journal_revision`. Require every
`ObjectiveProofDraft` to name one existing `ProofRequirement.proof_id`; reject an unknown or
duplicate requirement ID. `ObjectiveProgress` represents every explicit manifest requirement as
`pending`, `supported`, or `contradicted`, cites its events, and never invents a default flag.
Require observation drafts to use the closed text/facet/access/secret/incompatibility union and
private proof/secret values to resolve to one exact candidate-only evidence slice plus a locally
derived digest; an LLM-supplied inline value is never itself authoritative. Advance a proof
generation after rejecting digest A and assert a supported proof with the same grounded digest A
is invalid, while distinct grounded digest B may support. Add 35 unique rejections and assert the
newest 32 exact digests, three-entry folded overflow count/digest, and identical reconstruction from
events after deleting the projection.
- [x] **Step 2: Write failing ledger identity and limit tests**
Construct 32 families, 64 variants, and 16 archive candidates successfully; reject the 33rd,
65th, and 17th. Retain at most eight recent attempts per variant and 256 across the hot ledger;
assert older attempt events are represented by deterministic counters/digests rather than dropped
from authority. Reject duplicate runtime IDs/keys, variant ancestry mismatch, silent removal from
a reconciliation, invalid score ranges, and archive summaries over 16 KiB.
- [x] **Step 3: Write failing frontier/critic/result-shape tests**
Require runtime proposal IDs, exact state/ledger/cache binding, score and confidence in 0–100,
three-to-eight normal proposals, fewer only with a constrained rationale, stable score ordering,
valid event/knowledge/scope refs, and mutually exclusive `validated`, `cached`, and
`planning_gap` results. Require critic acceptance to be false exactly when a material finding
exists. `FrontierProjection` records both `input_ledger_digest` used by the LLM and
`resulting_ledger_digest` produced by the accepted reconciliation; `PlanningResult` separately
reports the current authoritative journal revision so a cache hit can rebind authority without
rewriting the stored frontier provenance.

Round-trip all four `SettlementResult` discriminator variants and assert their exact invariants:
`settled` and `nothing_pending` have an exact situation and no pending ranges/failure;
`incomplete` has an exact situation plus at least one sorted pending range; `failed` has a closed
safe failure code and may omit the situation/revision only for `journal_unavailable` or
`journal_corrupt`. Reject a revision that differs from
`situation.authoritative_journal_revision`, overlapping/duplicate pending ranges, a true
`all_required_proofs_satisfied` for an empty requirement list or any non-supported requirement,
and any raw exception/provider-response field. Assert lifecycle-compatible gating accepts only
`settled`/`nothing_pending`; `incomplete`/`failed` must stop M6C mutation even when the failed
variant retains a situation.
- [x] **Step 4: Run model tests and witness RED**
Run:

```bash
pytest -q tests/planning/test_models.py -x
```
Expected: collection fails because the planning package does not exist.
- [x] **Step 5: Implement closed enums and bounded primitives**
Define these exact vocabularies:

```python
class OutcomeCategory(StrEnum):
    PROGRESS = "progress"
    PARTIAL_PROGRESS = "partial_progress"
    NO_EFFECT = "no_effect"
    NEGATIVE_EVIDENCE = "negative_evidence"
    INCOMPATIBLE = "incompatible"
    EXECUTION_ERROR = "execution_error"
    AMBIGUOUS = "ambiguous"


class StrategyStatus(StrEnum):
    AVAILABLE = "available"
    DEFERRED = "deferred"
    BLOCKED = "blocked"
    EXHAUSTED = "exhausted"
    COMPLETED = "completed"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"


class RetryPredicateKind(StrEnum):
    FACT_PRESENT = "fact_present"
    FACT_CHANGED = "fact_changed"
    PREREQUISITE_SATISFIED = "prerequisite_satisfied"
    EVIDENCE_CATEGORY_PRESENT = "evidence_category_present"
    CREDENTIAL_AVAILABLE = "credential_available"
    STATE_REVISION_AFTER = "state_revision_after"
```

Import the single M6A-owned `SettlementReason` with exact ordered values `plan`, `close`,
`verify`, `reject`, `reopen`, `report`, `resume`, and `session_finalize`; do not redeclare it in
planning.
Also import M6A's authoritative `PendingSubjectCursor`, `MAX_REQUIRED_PROOFS`,
`MAX_HOST_RESULT_BYTES`, `MAX_SETTLEMENT_PENDING_RANGES`, `MAX_JOURNAL_EVENT_BYTES`, and `MAX_JOURNAL_BATCH_EVENTS`. Planning may define a smaller safety
margin, but it must not redefine the cursor or any hard repository/proof limit.
Lifecycle operations must settle before both reject and reopen.
Define these initial limits as exported constants and bind every model validator to them:

```python
ProofRequirementId = Annotated[
    str,
    Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$"),
]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ShortText = Annotated[str, Field(min_length=1, max_length=2048)]
MediaType = Annotated[str, Field(min_length=1, max_length=255)]

MAX_ATTEMPTS_PER_VARIANT = 8
MAX_HOT_ATTEMPTS = 256
MAX_RECENT_EVENTS = 64
MAX_RECENT_EVENT_TEXT_BYTES = 64 * 1024
MAX_PLANNER_REQUEST_BYTES = 512 * 1024
MAX_PLANNING_EVENT_BATCH = MAX_JOURNAL_BATCH_EVENTS - 1
MAX_PLANNING_PAYLOAD_BYTES = 60 * 1024
MAX_PLANNING_RESULT_BYTES = MAX_HOST_RESULT_BYTES - 16 * 1024
EVIDENCE_SLICE_BYTES = 32 * 1024
MAX_EVIDENCE_SLICES_PER_SETTLEMENT = 64
MAX_EVIDENCE_BYTES_PER_SETTLEMENT = 2 * 1024 * 1024
```
- [x] **Step 6: Implement observation and situation records**
Define `EvidenceSliceInput`, `ObservationDraft`, `HypothesisDraft`, `MissingInformationDraft`,
`FacetObservationDraft`, `AccessStateDeltaDraft`, `SecretReferenceDraft`,
`IncompatibilityObservationDraft`, `PrivateValueDraft`, `OutcomeAssessmentDraft`,
`ObjectiveProofDraft`, `ResearchSourceObservationDraft`,
`ObservationBatchDraft`, `ObservedFact`, `ObservedFacet`,
`SituationHypothesis`, `ObjectiveProgress`, `ProofValueReference`, `ProofRejectionRecord`,
`ResearchSourceAssessment`, `AccessState`, `EvidenceInterpretationState`, `SecretReference`,
`AttemptSummary`, `Incompatibility`, and
`SituationProjection`. All private
strings have explicit per-field and
cumulative bounds but no final-flag sanitizer. Every derived state record cites event IDs.
`ObjectiveProofDraft.proof_requirement_id` must resolve against the manifest supplied in its
observation request. Define an `InterpretationSubject` with exact fields
`attachment_event_id: UUID`, `terminal_tool_event_id: UUID | None`, and
`evidence_id: EvidenceId`. The attachment event ID is the unique interpretation identity; the
terminal tool event, when present, must reference that attachment, and `EvidenceId` identifies only
content-addressed slice bytes. `ObservationBatchDraft` and `EvidenceInterpretationState` carry this
subject. Two attachments with identical bytes remain two pending/completed interpretation states
and two independently linked outcome/attempt assessments, although byte reads may be cached.

Use this exact aggregate proof shape rather than putting a flat tuple directly on
`SituationProjection`:

```python
class PrivateValueDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    evidence_id: EvidenceId
    candidate_start: Annotated[int, Field(ge=0)]
    candidate_end: Annotated[int, Field(gt=0)]
    claimed_utf8: Annotated[str, Field(min_length=1, max_length=8192)]

    # candidate_end > candidate_start and covers at most 8192 bytes. This is an untrusted extractor
    # locator/claim, not the persisted value. Conversion requires one supplied EvidenceSliceInput
    # to contain that exact range, verifies its bytes equal claimed_utf8.encode("utf-8"), and
    # derives the persisted candidate-only slice digest locally. No match or multiple inconsistent
    # matches is reference_validation_failed and emits no proof/secret event.
```

```python
class ProofRejectionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    proof_requirement_id: ProofRequirementId
    assessment_generation: Annotated[int, Field(ge=1)]
    rejection_event_id: UUID
    rejected_proof_event_id: UUID
    rejected_value_sha256: Sha256Hex

    # Data-only replay seam. M6C derives it from one validated flag_rejected event and the exact
    # cited objective-proof event; no caller/user supplies the digest.


class ProofProgress(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    proof_requirement_id: ProofRequirementId
    assessment_generation: Annotated[int, Field(ge=1)] = 1
    generation_started_event_id: UUID | None = None
    status: Literal["pending", "supported", "contradicted"]
    supporting_event_ids: Annotated[tuple[UUID, ...], Field(max_length=32)] = ()
    contradicting_event_ids: Annotated[tuple[UUID, ...], Field(max_length=32)] = ()
    value_references: Annotated[tuple[ProofValueReference, ...], Field(max_length=16)] = ()
    historical_assessment_count: Annotated[int, Field(ge=0)] = 0
    historical_assessment_digest: Sha256Hex
    rejected_value_sha256s: Annotated[tuple[Sha256Hex, ...], Field(max_length=32)] = ()
    rejected_value_overflow_count: Annotated[int, Field(ge=0)] = 0
    rejected_value_overflow_digest: Sha256Hex

    # Current-generation IDs are sorted unique. pending has no current support/contradiction;
    # otherwise status is the latest valid assessment event in journal order: supported requires
    # a current support event and contradicted requires a current contradiction. Both sets remain
    # cited when a later current-generation assessment changes status.
    # Every value reference names its objective-proof event and same assessment_generation.
    # Starting a new generation folds prior current event/value refs into the canonical historical
    # count/digest; generation 1 starts at count 0 and SHA-256(canonical JSON `[]`). Journal events
    # remain immutable authority and no historical rejection vanishes.
    # rejected_value_sha256s contains the 32 most recently rejected distinct candidate digests in
    # rejection-event order. Older distinct rejections contribute to overflow count/digest. Empty
    # overflow uses count 0 and SHA-256(canonical JSON `[]`). Situation replay recomputes both
    # partitions from authoritative events, rejects hot duplicates, and verifies hot/overflow are
    # disjoint; the overflow digest is never used as a membership oracle.


class ObjectiveProgress(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    requirements: Annotated[
        tuple[ProofProgress, ...], Field(max_length=MAX_REQUIRED_PROOFS)
    ] = ()

    # Exactly one sorted entry exists for every explicit manifest ProofRequirement.proof_id.
    # Empty manifest => requirements=(); it is never implicit success.
```

`SituationProjection.objective_progress` is exactly one `ObjectiveProgress`. It stores requirement
IDs, statuses, event/value references, and no guessed success condition. Reopen/rejection in M6C
uses the same deterministic proof-generation transition: `retain_rejections` opens a new generation
only for the cited rejected requirement and initializes that generation as contradicted; other
requirements retain their current generation/status. A later grounded
`objective_proof_observed(assessment="supported")` in that new generation may support it and make
automatic closure eligible again only when its locally derived candidate digest has never been
rejected for that requirement in any generation. The same rejected candidate digest can never
support or reclose merely by moving to a new generation; a distinct grounded digest may. Explicit
reopen (`invalidate_all`, and legacy reopen with no
policy) opens a new pending generation for every requirement. Historical support/rejection events
remain immutable and folded into each requirement's history digest. This never adds proof fields to
M6A lifecycle state.
Add 64/65 manifest-bound requirement tests and a 33-plus-requirement settlement/replay fixture;
the projection and `required_proof_ids` must equal the complete manifest set without truncation.
`ProofValueReference` therefore carries exact `proof_event_id`, `proof_requirement_id`,
`assessment_generation`, `assessment`, candidate-only evidence slice, and value SHA-256.
For rejection inventory, deduplicate by value SHA-256 at first rejection. Order unique entries by
their first `flag_rejected` journal event, retain the newest 32 in `rejected_value_sha256s`, and
hash the ordered canonical tuples
`(proof_requirement_id, assessment_generation, rejection_event_id, rejected_proof_event_id,
rejected_value_sha256)` for all older entries into
`rejected_value_overflow_digest`; `rejected_value_overflow_count` is that tuple count. Event replay
must reproduce the exact hot tuple, count, and digest byte-for-byte.
- [x] **Step 7: Implement strategy and frontier records**
Define `RetryPredicate`, `AttemptState`, `ExecutionVariantState`, `StrategyFamilyState`,
`ArchivedStrategyState`, `StrategyArchive`, `StrategyLedger`, `ExecutionVariantDraft`,
`StrategyFamilyDraft`, `StrategyReconciliation`, `FrontierProposalDraft`, `FrontierProposal`,
`FrontierProjection`, and `PlanningGap`. Existing runtime IDs are echoed; new drafts use `None`
until the runtime assigns UUIDs. A proposal carries at most one complete command suggestion so
its one-proposal journal payload remains below 64 KiB; over-limit drafts fail validation and may be
critic-repaired, never truncated. Strategy reconciliation is emitted as a complete same-batch
sequence of one bounded operation/result-snapshot payload per ordinal.
- [x] **Step 8: Implement call/result audit records**
Define `PlanningCallMetadata` with purpose, provider, model, agent ID, prompt ID/version,
response-schema version, input digest, token counts, and elapsed milliseconds. Define
`InterpretationAudit`, `PlanRequestAudit`, `PlannerRepairAudit`, `PlannerRejectionAudit`,
`ResearchPolicyDecision`, `StrategyArchiveTransition`, `StrategyReactivationTransition`,
`PlannerFinding`, `PlannerCriticVerdict`, and `PlanningResult` with closed safe failure codes and
no raw exception/model response field. Define the settlement result as this exact frozen,
status-discriminated contract (all tuples sort deterministically and reject duplicates):

```python
class PlanningResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")
    status: Literal["success", "gap", "failed"]
    engagement_id: UUID
    current_authoritative_journal_revision: JournalRevision
    frontier: FrontierProjection | None = None
    gap: PlanningGap | None = None
    failure_code: Literal[
        "planning_failed", "settlement_unavailable", "result_too_large",
    ] | None = None

    # success requires frontier only; gap requires gap only; failed requires failure_code only.
    # Canonical JSON of this model must be <= MAX_PLANNING_RESULT_BYTES.
```

The public serializer wraps this model in the M6A envelope and rechecks the complete UTF-8 result
against `MAX_HOST_RESULT_BYTES`; it never returns cache/audit/raw response objects. Add JSON
round-trip tests for every discriminator branch and forbid mixed shapes. A failure before exact
lane binding or authoritative journal load (`journal_unavailable|journal_corrupt|engagement_binding_required`)
uses the outer closed tool-error envelope and never constructs `PlanningResult`; add pre-bind and
corrupt-journal serialization cases with no fabricated UUID/revision.

Define the settlement result as this exact frozen,
status-discriminated contract (all tuples sort deterministically and reject duplicates):

```python
SettlementFailureCode = Literal[
    "journal_unavailable",
    "journal_corrupt",
    "evidence_read_failed",
    "extractor_unavailable",
    "invalid_extractor_output",
    "journal_append_failed",
    "concurrent_state_change",
    "terminal_reconciliation_failed",
]


class PendingEvidenceRange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    evidence_id: EvidenceId
    attachment_event_id: UUID
    terminal_tool_event_id: UUID | None = None
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(gt=0)]  # exclusive
    media_type: MediaType
    reason: Literal[
        "budget_exhausted",
        "retryable_interpretation_failure",
        "read_failure",
    ]

    @model_validator(mode="after")
    def _positive_range(self) -> Self:
        if self.end <= self.start:
            raise ValueError("pending_range_must_be_positive")
        return self

    # A range describes all still-pending bytes, not one 32-KiB dispatch slice, so it may be
    # larger than EVIDENCE_SLICE_BYTES.


class _SettlementResultBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    engagement_id: UUID
    reason: SettlementReason
    authoritative_journal_revision: JournalRevision | None
    situation: SituationProjection | None
    required_proof_ids: Annotated[
        tuple[ProofRequirementId, ...], Field(max_length=MAX_REQUIRED_PROOFS)
    ] = ()
    pending_ranges: Annotated[
        tuple[PendingEvidenceRange, ...], Field(max_length=512)
    ] = ()
    pending_total_count: int = Field(
        default=0, ge=0, le=MAX_SETTLEMENT_PENDING_RANGES
    )
    pending_inventory_sha256: Sha256Hex | None = None
    next_pending_subject: PendingSubjectCursor | None = None
    incomplete_reason: Literal["budget_exhausted", "interpretation_incomplete"] | None = None
    all_required_proofs_satisfied: bool
    possible_terminal_evidence: bool

    @model_validator(mode="after")
    def _bind_projection_ranges_and_proofs(self) -> Self:
        if (self.situation is None) != (self.authoritative_journal_revision is None):
            raise ValueError("settlement_projection_revision_pair_required")
        if self.situation is not None and (
            self.authoritative_journal_revision
            != self.situation.authoritative_journal_revision
        ):
            raise ValueError("settlement_projection_revision_mismatch")

        range_keys = tuple(
            (
                str(item.attachment_event_id),
                str(item.terminal_tool_event_id or ""),
                str(item.evidence_id),
                item.start,
                item.end,
            )
            for item in self.pending_ranges
        )
        if range_keys != tuple(sorted(range_keys)) or len(range_keys) != len(set(range_keys)):
            raise ValueError("pending_ranges_not_sorted_unique")
        last_end_by_subject: dict[tuple[str, str, str], int] = {}
        for attachment_id, terminal_id, evidence_id, start, end in range_keys:
            subject = (attachment_id, terminal_id, evidence_id)
            if start < last_end_by_subject.get(subject, 0):
                raise ValueError("pending_ranges_overlap")
            last_end_by_subject[subject] = end

        if self.pending_total_count < len(self.pending_ranges):
            raise ValueError("pending_total_smaller_than_page")
        has_pending = self.pending_total_count > 0
        if has_pending != (self.pending_inventory_sha256 is not None):
            raise ValueError("pending_inventory_digest_policy")
        if (self.pending_total_count > len(self.pending_ranges)) != (
            self.next_pending_subject is not None
        ):
            raise ValueError("pending_cursor_policy")

        if self.required_proof_ids != tuple(sorted(set(self.required_proof_ids))):
            raise ValueError("required_proof_ids_not_sorted_unique")
        if self.situation is None:
            if self.required_proof_ids or self.all_required_proofs_satisfied:
                raise ValueError("proof_state_requires_situation")
            if self.possible_terminal_evidence:
                raise ValueError("terminal_evidence_requires_situation")
            return self

        progress = {
            item.proof_requirement_id: item.status
            for item in self.situation.objective_progress.requirements
        }
        if tuple(sorted(progress)) != self.required_proof_ids:
            raise ValueError("settlement_manifest_proof_mismatch")
        expected = bool(self.required_proof_ids) and all(
            progress[proof_id] == "supported" for proof_id in self.required_proof_ids
        )
        if self.all_required_proofs_satisfied is not expected:
            raise ValueError("settlement_proof_completion_mismatch")
        return self


class SettledSettlementResult(_SettlementResultBase):
    status: Literal["settled"] = "settled"
    authoritative_journal_revision: JournalRevision
    situation: SituationProjection
    pending_ranges: Annotated[tuple[PendingEvidenceRange, ...], Field(max_length=0)] = ()
    pending_total_count: Literal[0] = 0
    pending_inventory_sha256: None = None
    next_pending_subject: None = None
    incomplete_reason: None = None
    failure_code: None = None
    failure_summary: None = None


class NothingPendingSettlementResult(_SettlementResultBase):
    status: Literal["nothing_pending"] = "nothing_pending"
    authoritative_journal_revision: JournalRevision
    situation: SituationProjection
    pending_ranges: Annotated[tuple[PendingEvidenceRange, ...], Field(max_length=0)] = ()
    pending_total_count: Literal[0] = 0
    pending_inventory_sha256: None = None
    next_pending_subject: None = None
    incomplete_reason: None = None
    failure_code: None = None
    failure_summary: None = None


class IncompleteSettlementResult(_SettlementResultBase):
    status: Literal["incomplete"] = "incomplete"
    authoritative_journal_revision: JournalRevision
    situation: SituationProjection
    pending_ranges: Annotated[
        tuple[PendingEvidenceRange, ...], Field(min_length=1, max_length=512)
    ]
    pending_total_count: int = Field(ge=1, le=MAX_SETTLEMENT_PENDING_RANGES)
    pending_inventory_sha256: Sha256Hex
    incomplete_reason: Literal["budget_exhausted", "interpretation_incomplete"]
    failure_code: None = None
    failure_summary: None = None


class FailedSettlementResult(_SettlementResultBase):
    status: Literal["failed"] = "failed"
    incomplete_reason: None = None
    failure_code: SettlementFailureCode
    failure_summary: ShortText

    @model_validator(mode="after")
    def _failure_projection_policy(self) -> Self:
        journal_missing = self.failure_code in {"journal_unavailable", "journal_corrupt"}
        if journal_missing != (self.situation is None):
            raise ValueError("failed_settlement_projection_policy")
        return self

    # Failure never makes a partial extractor response authoritative.


SettlementResult = Annotated[
    SettledSettlementResult
    | NothingPendingSettlementResult
    | IncompleteSettlementResult
    | FailedSettlementResult,
    Field(discriminator="status"),
]
SettlementResultAdapter = TypeAdapter(SettlementResult)
```

Every non-failure variant carries the exact validated `SituationProjection` at the returned
authoritative revision. A post-load failure also carries the last committed projection; only an
unavailable/corrupt journal can return no situation. `possible_terminal_evidence` reports a
candidate observed during this or a prior unsettled batch, but never overrides `status`.
`all_required_proofs_satisfied` is false for an empty manifest. Callers never reconstruct proof
state from the M6A lifecycle projection, and M6C may proceed only for `settled` or
`nothing_pending`.

Also define frozen, `extra="forbid"` conversion envelopes
`ObservationEventConversion`, `PlanningAttemptEventConversion`,
`StrategyReconciliationEventConversion`, and `ResearchEventConversion`. Each contains only the
named planning drafts/audits, a sorted unique map of response-local IDs to preallocated journal
event UUIDs represented as at most 512 typed `LocalEventIdBinding` records, and bounded exact
indexes of valid event/evidence/scope/proof/decision/
proposal/family/variant/knowledge/source refs at the input revision. They contain no service,
repository, callback, path, LLM client, or arbitrary dictionary.
The proof index is a bounded sorted tuple of
`(proof_requirement_id, assessment_generation, rejection_inventory_digest)` records, not IDs
alone. `rejection_inventory_digest` hashes the canonical hot tuple plus overflow count/digest.
For each proof candidate in the same conversion, include one frozen, `extra="forbid"`
`ProofCandidateAdmission` with exact proof ID, generation, locally derived candidate SHA-256,
decision `allowed|previously_rejected`, and `matched_rejection_event_id` required only for a
rejection. `PlanningService` creates these records through `proof_value_was_rejected`; the pure
converter requires an exact candidate/admission match and emits no supported proof when rejected.
- [x] **Step 9: Define a terminal-reconciliation seam without importing M6C**
In `ports.py`, define this exact data-only result and protocol:

```python
class TerminalReconciliationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    action: Literal[
        "unchanged",
        "proof_close_requested",
        "proof_close_cancelled",
        "proof_close_finalized",
        "failed",
    ]
    authoritative_journal_revision: JournalRevision
    lifecycle_status: EngagementStatus
    safe_code: Literal["terminal_reconciliation_failed"] | None = None

    # failed iff safe_code is present; requested requires closing; cancelled requires active;
    # finalized requires closed_unverified/closed_verified. Revision/status describe the exact
    # post-reconciliation M6A snapshot, never a prediction.


class TerminalSettlementPort(Protocol):
    def reconcile(
        self,
        *,
        engagement_id: UUID,
        situation: SituationProjection,
        requirement_ids: tuple[ProofRequirementId, ...],
        authoritative_revision: JournalRevision,
        reason: SettlementReason,
        all_required_proofs_satisfied: bool,
    ) -> TerminalReconciliationResult: ...
```

Planning invokes it only after a successful settlement and after releasing journal locks. The
optional `None` dependency means no reconciliation call; Planning still reloads the exact M6A
snapshot afterward. M6C supplies the real implementation that requests/cancels closing and
finalizes a report. An empty `required_proofs` tuple bypasses the port entirely. The boolean is derived only from
`situation.objective_progress.requirements`, whose proof IDs must exactly equal the supplied
manifest IDs; the port never receives a separately reconstructed proof list. After every real port
return, Planning reloads the M6A snapshot, cross-checks its lifecycle status/revision against the
result, and refreshes `SituationProjection.authoritative_journal_revision`; callers never continue
from the pre-reconciliation snapshot.
- [x] **Step 10: Run planning model tests GREEN**
Run:

```bash
pytest -q tests/planning/test_models.py
```
Expected: PASS.
- [x] **Step 11: Commit Task 5**
```bash
git add -- src/sedna/planning/__init__.py src/sedna/planning/models.py src/sedna/planning/ports.py tests/planning/test_models.py
git commit -m "feat(planning): define adaptive planner contracts"
```

---

### Task 6: Validate and Bind Structured Command Suggestions

**Files:**
- Create: `src/sedna/planning/commands.py`
- Modify: `src/sedna/planning/models.py`
- Modify: `src/sedna/planning/__init__.py`
- Create: `tests/planning/test_commands.py`

**Interfaces:**
- Consumes: `ExecutionExample`, M6A `ScopeReference`, situation `SecretReference`, and planner
  command drafts.
- Produces: `CommandOrigin`, `CommandBinding`, `CommandSuggestionDraft`, `CommandSuggestion`,
  `validate_command_suggestion(...)`, and `render_command_preview(...)`.
- Guarantees: only authorized-scope target placeholders receive concrete targets; source-case
  credential placeholders remain unresolved.
- [x] **Step 1: Write failing source-backed and model-generated command tests**
Test both origins with `curl -i {{target}}`: binding `target` to authorized scope reference
`scope-0123456789abcdef0123456789abcdef` whose value is `192.0.2.44` must render
`curl -i 192.0.2.44` and preserve
`requires_validation=True`. Assert `source_example` requires an exact canonical example match,
while `model_generated` must not claim an example ID.
- [x] **Step 2: Write failing target and credential escape tests**
Reject raw `10.10.10.10`, `10.10.10.0/24`, `http://10.10.10.10/`, `box.htb`, an unresolved target,
an unauthorized scope ref, duplicate binding, and a target binding sourced from a literal. Verify
a `source_case_credential` remains rendered as `{{source_password}}` even when a current secret has
the same value or label.
- [x] **Step 3: Run command tests and witness RED**
Run:

```bash
pytest -q tests/planning/test_commands.py -x
```
Expected: collection fails because command planning contracts do not exist.
- [x] **Step 4: Implement template parsing and structured-target rejection**
Use a full-match placeholder regex `{{([a-z][a-z0-9_]{0,63})}}`, reject unknown or missing
tokens, and scan every non-placeholder token for IPv4, IPv6, CIDR, HTTP(S) URL, and dotted-hostname
shapes. Paths, wordlists, usernames, ports, and credentials that vary at runtime must also use
typed placeholders. Do not attempt to prove arbitrary shell syntax safe.
- [x] **Step 5: Implement exact binding rules and preview rendering**
Define origins `source_example`, `model_generated`, and `host_adapted`; planner drafts permit only
the first two, while journal decision/action records may use `host_adapted`. Resolve target
bindings by `ScopeReference` ID and exact authorization state. Resolve current credential refs only
when the draft explicitly names an engagement `SecretReference`. Never resolve
`source_case_credential`. Render a non-executable preview with placeholder substitution and retain
the structured template and bindings as the authoritative suggestion.
- [x] **Step 6: Run command and model tests GREEN**
Run:

```bash
pytest -q tests/planning/test_commands.py tests/planning/test_models.py
```
Expected: PASS.
- [x] **Step 7: Commit Task 6**
```bash
git add -- src/sedna/planning/commands.py src/sedna/planning/models.py src/sedna/planning/__init__.py tests/planning/test_commands.py
git commit -m "feat(planning): bind command suggestions to scope"
```

---

### Task 7: Add the Four-role Structured Planning LLM Boundary

**Files:**
- Create: `src/sedna/planning/prompts.py`
- Create: `src/sedna/planning/llm.py`
- Modify: `src/sedna/planning/__init__.py`
- Create: `tests/planning/test_llm.py`
- Create: `tests/planning/test_prompt_injection.py`

**Interfaces:**
- Consumes: `HostStructuredLlm`, `StructuredResult`, and Task 5 response models.
- Produces: `PlanningLlmAdapter.complete(...)` with four exact purpose/type contracts and four
  independently versioned prompt constants.
- [x] **Step 1: Write failing exact-contract adapter tests**
For each purpose, assert only its exact request class and response model are accepted. Assert
subclasses, constructed invalid Pydantic instances, NaN, sets, unknown fields, raw completions,
missing `parsed`, malformed host metadata, and provider exceptions produce closed safe failures.
Assert no provider/model override is sent, JSON mode is enabled, and temperature is zero.
- [x] **Step 2: Write failing untrusted-data prompt tests**
Place instruction-override strings in terminal output, HTML, Markdown, canonical artifacts,
`sources.md` entries, prior planner output, command literals, and web excerpts. Assert every value
appears only inside the serialized JSON input item and never inside `instructions` or the appended
static schema text.
- [x] **Step 3: Run LLM tests and witness RED**
Run:

```bash
pytest -q tests/planning/test_llm.py tests/planning/test_prompt_injection.py -x
```
Expected: collection fails because the planning adapter and prompts do not exist.
- [x] **Step 4: Define purpose contracts and safe request envelopes**
Implement:

```python
PlanningLlmPurpose = Literal[
    "sedna.planning.observe",
    "sedna.planning.plan",
    "sedna.planning.critic",
    "sedna.planning.repair",
]

_CALL_CONTRACTS = {
    "sedna.planning.observe": (ObservationRequest, ObservationBatchDraft),
    "sedna.planning.plan": (PlannerRequest, PlannerDraft),
    "sedna.planning.critic": (PlannerCriticRequest, PlannerCriticVerdict),
    "sedna.planning.repair": (PlannerRepairRequest, PlannerDraft),
}
```

Every request inherits a frozen `extra="forbid"`, `revalidate_instances="always"` base and has
explicit item/count/text bounds. Observation requests contain evidence slices with event/evidence
identity, byte range, media type, digest, and private content. Planner requests contain situation,
ledger, selected archive candidates, previous frontier, recent events, retrieval package, scope,
research policy, and bounded structured shared-source hints. Enforce 64 recent events, 64 KiB
cumulative recent-event text, and 512 KiB for the complete canonical JSON request. Build requests
by deterministic priority and preserve omitted historical material through cited aggregate
digests; if the required situation/ledger/reference core alone exceeds 512 KiB, fail with
`planner_input_too_large` rather than truncating a model or reference.
- [x] **Step 5: Implement the adapter**
Mirror the proven JSON-only structural boundary in `semantic/llm.py` without extending its
semantic `_CALL_CONTRACTS`. Use `HostStructuredLlm`, local response revalidation, safe usage/model
attribution, purpose-specific max-token limits, and closed `PlanningLlmError` codes. The adapter
returns no host `audit` object or raw response.
- [x] **Step 6: Define four independent versioned prompts**
Use IDs and version `"1"`:

```python
OBSERVATION_PROMPT_ID = "sedna-observation-extractor"
PLANNER_PROMPT_ID = "sedna-frontier-planner"
PLANNER_CRITIC_PROMPT_ID = "sedna-frontier-critic"
PLANNER_REPAIR_PROMPT_ID = "sedna-frontier-repair"
```

Each prompt independently states the untrusted-data boundary. Observation instructions enforce
facts/hypotheses separation and the seven outcomes. Planner instructions enforce relative-score
semantics, complete reconciliation, retry conditions, creativity, source-as-example behavior, and
execution-error separation. Critic instructions enforce references, applicability, scope,
research policy, loop detection, score explanation, command origin, and silent-loss checks. Repair
instructions permit only critic/source-supported corrections and return a complete replacement.
- [x] **Step 7: Run LLM and injection tests GREEN**
Run:

```bash
pytest -q tests/planning/test_llm.py tests/planning/test_prompt_injection.py
```
Expected: PASS, and no hostile fixture crosses into static instructions.
- [x] **Step 8: Commit Task 7**
```bash
git add -- src/sedna/planning/prompts.py src/sedna/planning/llm.py src/sedna/planning/__init__.py tests/planning/test_llm.py tests/planning/test_prompt_injection.py
git commit -m "feat(planning): add structured planner llm boundary"
```

---

### Task 8: Extend the Closed Engagement Event Vocabulary for Planning

**Files:**
- Modify: `src/sedna/engagement/events.py`
- Modify: `src/sedna/engagement/reducer.py`
- Modify: `src/sedna/engagement/service.py`
- Modify: `src/sedna/engagement/__init__.py`
- Create: `src/sedna/planning/journal_events.py`
- Modify: `src/sedna/planning/__init__.py`
- Create: `tests/engagement/test_events.py`
- Modify: `tests/engagement/test_reducer.py`
- Modify: `tests/engagement/test_service.py`
- Create: `tests/planning/conftest.py`
- Create: `tests/planning/test_journal_events.py`

**Interfaces:**
- Consumes: M6A `JournalEventDraft`, `JournalEvent`, bounded engagement primitives,
  `ProofRequirement.proof_id`, and opaque runtime IDs/references.
- Produces: the complete closed, data-only M6B payload union that later reducers persist and
  replay without importing `sedna.planning` from `sedna.engagement`.
- Produces: package-private `PlanningEventCommitCapability` and `PlanningEventCommitItem`, the only
  authority allowed to append the 19 planner event kinds.
- Guarantees: planning events are authoritative structured facts about what was proposed or
  interpreted; prose-only planner objects are never persisted as an untyped blob.
- [x] **Step 1: Write failing closed-union and round-trip tests**
Parameterize strict JSON round trips for these exact event payloads:

```text
observation_extracted
hypothesis_formed
missing_information_identified
outcome_assessed
objective_proof_observed
interpretation_succeeded
interpretation_failed
plan_requested
frontier_proposed
frontier_criticized
frontier_repaired
frontier_rejected
planning_gap_recorded
strategy_reconciled
strategy_archived
strategy_reactivated
research_query_proposed
research_source_consulted
research_source_assessed
```

Use these exact shared wire primitives. Every tuple is normalized to deterministic order, rejects
duplicates, and enforces its declared bound. Every payload additionally rejects canonical JSON
larger than 64 KiB. Larger logical frontiers/reconciliations/archives are split into a declared
number of same-batch events and never truncated. `PrivateText` deliberately permits engagement
flags; none of these types uses
the searchable/canonical flag sanitizer.

```python
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
StableRef = Annotated[
    str,
    Field(
        min_length=1,
        max_length=512,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}$",
    ),
]
ScopeRefId = Annotated[str, Field(pattern=r"^scope-[0-9a-f]{32}$")]
ProofRequirementId = Annotated[
    str,
    Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$"),
]
FindingCode = Annotated[
    str,
    Field(pattern=r"^[a-z][a-z0-9_]{0,63}$"),
]
PrivateText = Annotated[str, Field(min_length=1, max_length=4096)]
ShortText = Annotated[str, Field(min_length=1, max_length=2048)]
ConditionText = Annotated[str, Field(min_length=1, max_length=512)]
MediaType = Annotated[str, Field(min_length=1, max_length=255)]
Confidence = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
Score = Annotated[int, Field(ge=0, le=100)]


class _EventRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


class _EventPayload(_EventRecord):
    """Data-only journal payload base; the materialized event must fit M6A's hard limit."""


class EvidenceSliceEventRef(_EventRecord):
    evidence_id: EvidenceId
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(gt=0)]
    sha256: Sha256Hex
    media_type: MediaType

    # Validator: 0 < end - start <= EVIDENCE_SLICE_BYTES (32 KiB).


class PlanningCallMetadataEventRecord(_EventRecord):
    purpose: Literal["observe", "plan", "critic", "repair"]
    provider: Annotated[str, Field(min_length=1, max_length=256)]
    model: Annotated[str, Field(min_length=1, max_length=256)]
    agent_id: Annotated[str, Field(min_length=1, max_length=256)]
    prompt_id: StableRef
    prompt_version: StableRef
    response_schema_version: StableRef
    input_digest: Sha256Hex
    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    elapsed_ms: Annotated[int, Field(ge=0)]

    # Safe metadata only: no raw provider response, exception, credential, or host audit object.


class PrivateValueEventRecord(_EventRecord):
    evidence_slice: EvidenceSliceEventRef
    value_sha256: Sha256Hex

    # value_sha256 equals evidence_slice.sha256. The converter, not the LLM, creates this record
    # only after matching the exact candidate-only byte range against a supplied EvidenceSliceInput.
    # It is private engagement data and never enters canonical/FTS/search projections.


class TextFactEventRecord(_EventRecord):
    record_kind: Literal["text_fact"] = "text_fact"
    subject: ConditionText
    value: PrivateText
    polarity: Literal["observed", "not_observed"] = "observed"


class FacetObservationEventRecord(_EventRecord):
    record_kind: Literal["facet"] = "facet"
    dimension: Literal[
        "os_family",
        "os_version",
        "cpu_architecture",
        "execution_environment",
        "service",
        "port",
        "protocol",
        "technology",
        "network_position",
        "security_control",
        "custom",
    ]
    key: ConditionText
    value: ShortText
    relation: Literal["observed", "compatible", "incompatible", "unknown"]


class AccessStateDeltaEventRecord(_EventRecord):
    record_kind: Literal["access_state_delta"] = "access_state_delta"
    scope_reference_id: ScopeRefId
    access_kind: Literal[
        "network_reachability",
        "service_access",
        "authenticated_session",
        "shell",
        "user",
        "administrator",
        "root",
        "custom",
    ]
    transition: Literal["gained", "lost", "confirmed", "denied", "unknown"]
    principal_label: ConditionText | None = None
    service_ref: StableRef | None = None
    privilege_label: ConditionText | None = None


class SecretReferenceEventRecord(_EventRecord):
    record_kind: Literal["secret_reference"] = "secret_reference"
    secret_ref_id: StableRef
    secret_kind: Literal[
        "username",
        "password",
        "token",
        "hash",
        "private_key",
        "cookie",
        "flag_candidate",
        "other",
    ]
    label: ConditionText
    value: PrivateValueEventRecord
    scope_reference_ids: Annotated[tuple[ScopeRefId, ...], Field(max_length=8)] = ()
    service_ref: StableRef | None = None
    username_ref: StableRef | None = None
    origin: Literal["engagement_evidence"] = "engagement_evidence"


class IncompatibilityObservationEventRecord(_EventRecord):
    record_kind: Literal["incompatibility"] = "incompatibility"
    subject_ref: StableRef
    reason: ShortText
    scope_reference_ids: Annotated[tuple[ScopeRefId, ...], Field(max_length=8)] = ()
    event_refs: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=32)]
    knowledge_refs: Annotated[tuple[StableRef, ...], Field(max_length=16)] = ()


ExtractedObservationEventRecord = Annotated[
    TextFactEventRecord
    | FacetObservationEventRecord
    | AccessStateDeltaEventRecord
    | SecretReferenceEventRecord
    | IncompatibilityObservationEventRecord,
    Field(discriminator="record_kind"),
]


OutcomeValue = Literal[
    "progress",
    "partial_progress",
    "no_effect",
    "negative_evidence",
    "incompatible",
    "execution_error",
    "ambiguous",
]
StrategyStatusValue = Literal[
    "available",
    "deferred",
    "blocked",
    "exhausted",
    "completed",
    "archived",
    "superseded",
]
ReconciliationOperationValue = Literal[
    "retain",
    "update",
    "merge",
    "split",
    "supersede",
    "complete",
    "block",
    "archive",
    "reactivate",
]


class RetryPredicateEventRecord(_EventRecord):
    predicate_id: StableRef
    kind: Literal[
        "fact_present",
        "fact_changed",
        "prerequisite_satisfied",
        "evidence_category_present",
        "credential_available",
        "state_revision_after",
    ]
    subject_ref: StableRef
    expected_symbolic_value: StableRef | None = None
    expected_value_digest: Sha256Hex | None = None
    minimum_material_revision: JournalRevision | None = None
    description: ConditionText

    # Validator requires fields by kind: fact_changed uses expected_value_digest;
    # evidence_category_present uses expected_symbolic_value from OutcomeValue;
    # state_revision_after uses minimum_material_revision; credential_available carries only a
    # symbolic SecretReference ID in subject_ref, never a credential value.


class StrategyApplicabilityEventRecord(_EventRecord):
    dimension: Literal[
        "os_family",
        "os_version",
        "cpu_architecture",
        "execution_environment",
        "service",
        "access_state",
        "network_position",
        "custom",
    ]
    relation: Literal["required", "compatible", "incompatible", "unknown"]
    value: ConditionText
    event_refs: Annotated[tuple[UUID, ...], Field(max_length=32)] = ()
    knowledge_refs: Annotated[tuple[StableRef, ...], Field(max_length=16)] = ()

    # Every non-unknown assertion cites at least one event or knowledge ref.


class AttemptOutcomeCountEventRecord(_EventRecord):
    category: OutcomeValue
    count: Annotated[int, Field(ge=0)]


class AttemptAggregateEventRecord(_EventRecord):
    total_count: Annotated[int, Field(ge=0)]
    recent_attempt_ids: Annotated[tuple[UUID, ...], Field(max_length=8)] = ()
    outcome_counts: Annotated[
        tuple[AttemptOutcomeCountEventRecord, ...], Field(max_length=7)
    ] = ()
    first_material_revision: JournalRevision | None = None
    last_material_revision: JournalRevision | None = None
    history_digest: Sha256Hex

    # Validator requires sum(outcome_counts.count) == total_count and empty revisions iff count=0.


class CommandPlaceholderEventRecord(_EventRecord):
    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    kind: Literal[
        "target",
        "port",
        "username",
        "credential_ref",
        "source_case_credential",
        "wordlist",
        "path",
        "value",
    ]
    binding_policy: Literal["authorized_scope", "host_supplied", "never_auto_bind"]
    role: ConditionText


class CommandBindingEventRecord(_EventRecord):
    placeholder_name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    source: Literal[
        "scope_reference",
        "secret_reference",
        "host_supplied",
        "unresolved_source_case",
    ]
    reference_id: StableRef | None = None

    # Validator: scope_reference requires a ScopeRefId; secret_reference requires an existing
    # symbolic SecretReference ID; unresolved_source_case requires reference_id=None.


class CommandSuggestionEventRecord(_EventRecord):
    command_id: UUID
    origin: Literal["source_example", "model_generated", "host_adapted"]
    capability_hint: StableRef
    purpose: ConditionText
    command_template: Annotated[str, Field(min_length=1, max_length=8192)]
    placeholders: Annotated[
        tuple[CommandPlaceholderEventRecord, ...], Field(max_length=32)
    ] = ()
    bindings: Annotated[tuple[CommandBindingEventRecord, ...], Field(max_length=32)] = ()
    rendered_preview: Annotated[str, Field(min_length=1, max_length=8192)]
    source_example_id: StableRef | None = None
    knowledge_refs: Annotated[tuple[StableRef, ...], Field(max_length=16)] = ()
    requires_validation: Literal[True] = True
    validation_note: ConditionText

    # Validators enforce exact template-placeholder coverage, one binding per placeholder,
    # authorized scope for targets, source_example_id iff origin=source_example, and no binding for
    # source_case_credential. Planning drafts cannot emit host_adapted.


class ExecutionVariantEventRecord(_EventRecord):
    record_kind: Literal["execution_variant"] = "execution_variant"
    variant_id: UUID
    family_id: UUID
    stable_key: StableRef
    title: ShortText
    strategic_intent: ShortText
    rationale: ShortText
    score: Score
    confidence: Confidence
    status: StrategyStatusValue
    prerequisites: Annotated[tuple[ConditionText, ...], Field(max_length=16)] = ()
    applicability: Annotated[
        tuple[StrategyApplicabilityEventRecord, ...], Field(max_length=16)
    ] = ()
    retry_predicates: Annotated[tuple[RetryPredicateEventRecord, ...], Field(max_length=16)] = ()
    attempts: AttemptAggregateEventRecord
    evidence_event_ids: Annotated[tuple[UUID, ...], Field(max_length=64)] = ()
    knowledge_refs: Annotated[tuple[StableRef, ...], Field(max_length=32)] = ()
    supersedes_variant_ids: Annotated[tuple[UUID, ...], Field(max_length=16)] = ()
    last_material_revision: JournalRevision


class StrategyFamilyEventRecord(_EventRecord):
    record_kind: Literal["strategy_family"] = "strategy_family"
    family_id: UUID
    stable_key: StableRef
    title: ShortText
    strategic_intent: ShortText
    rationale: ShortText
    score: Score
    confidence: Confidence
    status: StrategyStatusValue
    prerequisites: Annotated[tuple[ConditionText, ...], Field(max_length=16)] = ()
    applicability: Annotated[
        tuple[StrategyApplicabilityEventRecord, ...], Field(max_length=16)
    ] = ()
    retry_predicates: Annotated[tuple[RetryPredicateEventRecord, ...], Field(max_length=16)] = ()
    variant_ids: Annotated[tuple[UUID, ...], Field(max_length=64)] = ()
    evidence_event_ids: Annotated[tuple[UUID, ...], Field(max_length=64)] = ()
    knowledge_refs: Annotated[tuple[StableRef, ...], Field(max_length=32)] = ()
    supersedes_family_ids: Annotated[tuple[UUID, ...], Field(max_length=8)] = ()
    last_material_revision: JournalRevision


class StrategyTombstoneEventRecord(_EventRecord):
    record_kind: Literal["strategy_tombstone"] = "strategy_tombstone"
    entity_kind: Literal["family", "variant"]
    entity_id: UUID
    replacement_ids: Annotated[tuple[UUID, ...], Field(max_length=16)] = ()
    reason: ShortText


StrategyResultSnapshot = Annotated[
    StrategyFamilyEventRecord | ExecutionVariantEventRecord | StrategyTombstoneEventRecord,
    Field(discriminator="record_kind"),
]


class FrontierProposalEventRecord(_EventRecord):
    proposal_id: UUID
    rank: Annotated[int, Field(ge=1, le=8)]
    family_id: UUID | None = None
    variant_id: UUID | None = None
    title: ShortText
    strategic_intent: ShortText
    rationale: ShortText
    score: Score
    confidence: Confidence
    prerequisites: Annotated[tuple[ConditionText, ...], Field(max_length=16)] = ()
    expected_information_gain: ShortText
    expected_evidence: Annotated[tuple[ConditionText, ...], Field(max_length=16)] = ()
    stop_conditions: Annotated[tuple[ConditionText, ...], Field(max_length=16)] = ()
    event_refs: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=32)]
    knowledge_refs: Annotated[tuple[StableRef, ...], Field(max_length=16)] = ()
    scope_reference_ids: Annotated[tuple[ScopeRefId, ...], Field(max_length=8)] = ()
    commands: Annotated[tuple[CommandSuggestionEventRecord, ...], Field(max_length=1)] = ()

    # Validator requires family/variant ancestry, cited score/rationale, unique command IDs, and
    # command origins limited to source_example/model_generated. host_adapted is journalable only
    # on the Hades decision/action record after host validation.


class ArchivedStrategyEventRecord(_EventRecord):
    archive_entry_id: UUID
    snapshot: Annotated[
        StrategyFamilyEventRecord | ExecutionVariantEventRecord,
        Field(discriminator="record_kind"),
    ]
    archive_reason: ShortText
    retry_predicates: Annotated[tuple[RetryPredicateEventRecord, ...], Field(max_length=16)] = ()
    archive_summary: PrivateText
    archived_at_material_revision: JournalRevision
    source_reconciliation_event_id: UUID
    archive_entry_digest: Sha256Hex


class StrategyReconciliationEventOperation(_EventRecord):
    operation_id: UUID
    operation: ReconciliationOperationValue
    family_id: UUID
    variant_id: UUID | None = None
    related_family_ids: Annotated[tuple[UUID, ...], Field(max_length=8)] = ()
    related_variant_ids: Annotated[tuple[UUID, ...], Field(max_length=16)] = ()
    reason: ShortText
    evidence_event_ids: Annotated[tuple[UUID, ...], Field(max_length=64)] = ()
    knowledge_refs: Annotated[tuple[StableRef, ...], Field(max_length=32)] = ()

    # Validator: identity/ancestry fields match the operation. A semantic change requires at least
    # one evidence_event_id or knowledge_ref. The separate resulting snapshot is authoritative for
    # score/status/content; operation prose alone never reconstructs ledger state.
```

Define all 19 payloads exactly as follows; no field may be replaced by `dict[str, Any]`, an
unbounded model dump, raw provider response, or planning-class instance:

```python
class ObservationExtractedEventPayload(_EventPayload):
    kind: Literal["observation_extracted"] = "observation_extracted"
    summary: PrivateText
    observation: ExtractedObservationEventRecord
    confidence: Confidence
    evidence_slices: Annotated[
        tuple[EvidenceSliceEventRef, ...], Field(min_length=1, max_length=64)
    ]
    scope_reference_ids: Annotated[tuple[ScopeRefId, ...], Field(max_length=16)] = ()
    interpretation_input_digest: Sha256Hex

    # Converter emits one event per structured fact/facet/access/secret/incompatibility record.
    # Any nested private evidence slice must occur in evidence_slices; all nested scope refs must
    # occur in scope_reference_ids and in the engagement manifest.


class HypothesisFormedEventPayload(_EventPayload):
    kind: Literal["hypothesis_formed"] = "hypothesis_formed"
    statement: PrivateText
    confidence: Confidence
    supporting_event_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=32)]
    contradicting_event_ids: Annotated[tuple[UUID, ...], Field(max_length=32)] = ()
    scope_reference_ids: Annotated[tuple[ScopeRefId, ...], Field(max_length=16)] = ()
    interpretation_input_digest: Sha256Hex


class MissingInformationIdentifiedEventPayload(_EventPayload):
    kind: Literal["missing_information_identified"] = "missing_information_identified"
    question: ShortText
    reason: PrivateText
    importance: Score
    related_event_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=32)]
    scope_reference_ids: Annotated[tuple[ScopeRefId, ...], Field(max_length=16)] = ()
    interpretation_input_digest: Sha256Hex


class OutcomeAssessedEventPayload(_EventPayload):
    kind: Literal["outcome_assessed"] = "outcome_assessed"
    attachment_event_id: UUID
    terminal_tool_event_id: UUID
    decision_id: UUID | None = None
    tool_call_ids: Annotated[tuple[StableRef, ...], Field(min_length=1, max_length=32)]
    category: OutcomeValue
    summary: PrivateText
    strategic_impact: PrivateText
    evidence_ids: Annotated[tuple[EvidenceId, ...], Field(max_length=64)] = ()
    source_event_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=64)]
    interpretation_input_digest: Sha256Hex

    # terminal_tool_event_id must be a terminal M6A tool event whose
    # evidence_attachment_event_id equals attachment_event_id. The referenced attachment carries
    # one of evidence_ids. Thus identical EvidenceId bytes from two tool calls remain two outcomes.


class ObjectiveProofObservedEventPayload(_EventPayload):
    kind: Literal["objective_proof_observed"] = "objective_proof_observed"
    proof_requirement_id: ProofRequirementId
    assessment_generation: Annotated[int, Field(ge=1)]
    assessment: Literal["supported", "contradicted"]
    candidate_value: PrivateValueEventRecord
    confidence: Confidence
    evidence_ids: Annotated[tuple[EvidenceId, ...], Field(min_length=1, max_length=16)]
    source_event_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=32)]
    interpretation_input_digest: Sha256Hex

    # Validator requires a manifest proof ID, exact equality with the requirement's current
    # assessment_generation at expected revision, and membership of
    # candidate_value.evidence_slice.evidence_id in evidence_ids. A stale generation cannot
    # resurrect a rejected/explicitly reopened proof.


class InterpretationSucceededEventPayload(_EventPayload):
    kind: Literal["interpretation_succeeded"] = "interpretation_succeeded"
    interpretation_id: UUID
    attachment_event_id: UUID
    terminal_tool_event_id: UUID | None = None
    evidence_id: EvidenceId
    covered_slices: Annotated[
        tuple[EvidenceSliceEventRef, ...], Field(max_length=64)
    ]
    emitted_event_ids: Annotated[tuple[UUID, ...], Field(max_length=64)]
    call_metadata: PlanningCallMetadataEventRecord
    call_input_digest: Sha256Hex
    call_output_digest: Sha256Hex

    # interpretation_id identifies one bounded attempt/tranche and is unique within the
    # attachment_event_id subject; one attachment may have multiple attempts across the 2-MiB cap.
    # If terminal_tool_event_id is present, that M6A event must point to the same attachment.
    # covered_slices all use evidence_id. Both tuples may be empty only when the authoritative
    # attachment descriptor has size zero; this terminally interprets an empty text/JSON result
    # without inventing a byte range or semantic event.


class InterpretationFailedEventPayload(_EventPayload):
    kind: Literal["interpretation_failed"] = "interpretation_failed"
    interpretation_id: UUID
    attachment_event_id: UUID
    terminal_tool_event_id: UUID | None = None
    evidence_id: EvidenceId
    attempted_slices: Annotated[
        tuple[EvidenceSliceEventRef, ...], Field(max_length=64)
    ]
    failure_code: Literal[
        "llm_unavailable",
        "invalid_structured_output",
        "reference_validation_failed",
        "concurrent_state_change",
        "unsupported_media",
    ]
    retryable: bool
    safe_summary: ShortText
    call_metadata: PlanningCallMetadataEventRecord | None = None
    call_input_digest: Sha256Hex

    # The same subject/reference invariants as InterpretationSucceeded apply; failure/attempt IDs
    # are tracked under the attachment subject, never globally per content-addressed EvidenceId.
    # unsupported_media is a terminal, non-retryable assessment (`retryable=False`) with no LLM
    # call and requires attempted_slices=(); the reducer validates evidence_id against the exact
    # attachment descriptor, including a zero-byte binary item. Every other failure is retryable,
    # requires at least one real positive slice, and leaves its attempted slices pending.


class PlanRequestedEventPayload(_EventPayload):
    kind: Literal["plan_requested"] = "plan_requested"
    request_id: UUID
    lane_key: Annotated[str, Field(pattern=r"^lane-[0-9a-f]{32}$")]
    situation_digest: Sha256Hex
    material_event_revision: JournalRevision
    input_ledger_digest: Sha256Hex
    canonical_revision: Sha256Hex
    source_registry_digest: Sha256Hex
    max_proposals: Annotated[int, Field(ge=3, le=8)]
    request_digest: Sha256Hex


class FrontierProposedEventPayload(_EventPayload):
    kind: Literal["frontier_proposed"] = "frontier_proposed"
    request_id: UUID
    frontier_id: UUID
    proposal_ordinal: Annotated[int, Field(ge=1, le=8)]
    proposal_count: Annotated[int, Field(ge=1, le=8)]
    proposal: FrontierProposalEventRecord
    situation_digest: Sha256Hex
    input_ledger_digest: Sha256Hex
    knowledge_context_digest: Sha256Hex
    draft_digest: Sha256Hex
    call_metadata: PlanningCallMetadataEventRecord
    planner_call_digest: Sha256Hex

    # Converter emits one payload per proposal in one atomic append batch. Validator requires
    # proposal_ordinal <= proposal_count and proposal.rank == proposal_ordinal.


class FrontierCriticizedEventPayload(_EventPayload):
    kind: Literal["frontier_criticized"] = "frontier_criticized"
    request_id: UUID
    frontier_id: UUID
    critic_pass: Literal[1, 2]
    accepted: bool
    finding_codes: Annotated[tuple[FindingCode, ...], Field(max_length=32)] = ()
    cited_event_ids: Annotated[tuple[UUID, ...], Field(max_length=64)] = ()
    call_metadata: PlanningCallMetadataEventRecord
    call_input_digest: Sha256Hex
    call_output_digest: Sha256Hex

    # Validator: accepted iff finding_codes is empty.


class FrontierRepairedEventPayload(_EventPayload):
    kind: Literal["frontier_repaired"] = "frontier_repaired"
    request_id: UUID
    frontier_id: UUID
    repair_attempt: Literal[1] = 1
    critic_event_id: UUID
    proposal_ordinal: Annotated[int, Field(ge=1, le=8)]
    proposal_count: Annotated[int, Field(ge=1, le=8)]
    proposal: FrontierProposalEventRecord
    repaired_draft_digest: Sha256Hex
    call_metadata: PlanningCallMetadataEventRecord
    call_input_digest: Sha256Hex
    call_output_digest: Sha256Hex

    # Converter emits the complete replacement frontier as one same-batch payload per proposal.


class FrontierRejectedEventPayload(_EventPayload):
    kind: Literal["frontier_rejected"] = "frontier_rejected"
    request_id: UUID
    frontier_id: UUID
    critic_event_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=2)]
    reason_codes: Annotated[tuple[FindingCode, ...], Field(min_length=1, max_length=32)]
    rejected_draft_digest: Sha256Hex


class PlanningGapRecordedEventPayload(_EventPayload):
    kind: Literal["planning_gap_recorded"] = "planning_gap_recorded"
    request_id: UUID | None = None
    code: Literal[
        "planner_input_too_large",
        "journal_payload_too_large",
        "concurrent_state_change",
        "invalid_planner_output",
        "llm_unavailable",
        "critic_rejected",
        "retrieval_unavailable",
        "journal_unavailable",
        "engagement_terminal",
    ]
    summary: ShortText
    retryable: bool
    situation_digest: Sha256Hex
    ledger_digest: Sha256Hex
    related_event_ids: Annotated[tuple[UUID, ...], Field(max_length=32)] = ()


class StrategyReconciledEventPayload(_EventPayload):
    kind: Literal["strategy_reconciled"] = "strategy_reconciled"
    request_id: UUID
    frontier_id: UUID
    reconciliation_id: UUID
    item_ordinal: Annotated[int, Field(ge=1, le=256)]
    item_count: Annotated[int, Field(ge=1, le=256)]
    input_ledger_digest: Sha256Hex
    resulting_ledger_digest: Sha256Hex
    operation: StrategyReconciliationEventOperation
    resulting_snapshot: StrategyResultSnapshot
    reconciliation_digest: Sha256Hex

    # One atomic batch contains every ordinal exactly once. Split/merge operations repeat the same
    # operation_id across their multiple result snapshots. Every resulting hot family/variant and
    # every removed identity tombstone appears exactly once; item_count is never silently capped.


class StrategyArchivedEventPayload(_EventPayload):
    kind: Literal["strategy_archived"] = "strategy_archived"
    request_id: UUID
    archive_batch_id: UUID
    entry_ordinal: Annotated[int, Field(ge=1, le=256)]
    entry_count: Annotated[int, Field(ge=1, le=256)]
    archive_record: ArchivedStrategyEventRecord
    resulting_archive_digest: Sha256Hex

    # Converter emits one compact archive record per entry in the same atomic batch.
    # Its hot-ledger removal must match the companion reconciliation tombstone.


class StrategyReactivatedEventPayload(_EventPayload):
    kind: Literal["strategy_reactivated"] = "strategy_reactivated"
    request_id: UUID
    reactivation_batch_id: UUID
    entry_ordinal: Annotated[int, Field(ge=1, le=256)]
    entry_count: Annotated[int, Field(ge=1, le=256)]
    source_archive_event_id: UUID
    triggering_event_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=32)]
    matched_predicate_ids: Annotated[tuple[StableRef, ...], Field(min_length=1, max_length=16)]
    prior_archive_entry_digest: Sha256Hex
    resulting_archive_digest: Sha256Hex
    restored_snapshot: Annotated[
        StrategyFamilyEventRecord | ExecutionVariantEventRecord,
        Field(discriminator="record_kind"),
    ]

    # Restored snapshot IDs must equal the archived snapshot IDs referenced by source_archive_event.
    # The companion reconciliation snapshot must be byte-equivalent; reducers cross-validate and
    # apply the transaction once rather than double-applying reactivation.


class ResearchQueryProposedEventPayload(_EventPayload):
    kind: Literal["research_query_proposed"] = "research_query_proposed"
    query_id: UUID
    normalized_query: Annotated[str, Field(min_length=1, max_length=2048)]
    query_digest: Sha256Hex
    policy_decision: Literal["allowed", "rejected"]
    policy_version: StableRef
    reason_codes: Annotated[tuple[FindingCode, ...], Field(min_length=1, max_length=16)]
    related_event_ids: Annotated[tuple[UUID, ...], Field(max_length=32)] = ()
    candidate_source_ids: Annotated[tuple[StableRef, ...], Field(max_length=16)] = ()

    # Validator: query_digest is SHA-256 of normalized_query UTF-8 bytes.


class ResearchSourceConsultedEventPayload(_EventPayload):
    kind: Literal["research_source_consulted"] = "research_source_consulted"
    query_id: UUID
    source_id: StableRef
    normalized_locator: Annotated[str, Field(min_length=1, max_length=2048)]
    locator_digest: Sha256Hex
    content_digest: Sha256Hex
    media_type: MediaType
    evidence_ids: Annotated[tuple[EvidenceId, ...], Field(min_length=1, max_length=16)]
    tool_event_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=16)]

    # Validator: locator_digest is SHA-256 of normalized_locator UTF-8 bytes.


class ResearchSourceAssessedEventPayload(_EventPayload):
    kind: Literal["research_source_assessed"] = "research_source_assessed"
    query_id: UUID
    source_id: StableRef
    consulted_event_id: UUID
    assessment: Literal["useful", "contradicted", "stale", "irrelevant", "ambiguous"]
    confidence: Confidence
    summary: PrivateText
    related_event_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=64)]
    assessment_digest: Sha256Hex
    suggested_registry_status: Literal[
        "consulted", "useful", "contradicted", "stale"
    ] | None = None

    # Validator: consulted_event_id occurs in related_event_ids; assessment_digest covers every
    # other semantic field above in canonical JSON.
```

Digest semantics are exact: `interpretation_input_digest` and `call_input_digest` hash canonical
structured request JSON; `call_output_digest` hashes the locally revalidated parsed response;
`PrivateValueEventRecord.value_sha256` hashes the exact referenced candidate-only evidence range;
situation/ledger/knowledge/
registry digests copy the already validated projection/context digest; `canonical_revision` copies
the repository's 64-hex revision guard; `request_digest` hashes every `PlanRequestedEventPayload`
field except `kind` and itself; draft/repaired/rejected digests hash the corresponding complete
normalized draft; `planner_call_digest` hashes safe `PlanningCallMetadata` only;
`reconciliation_digest` hashes the ordered canonical operation/result-snapshot items;
`archive_entry_digest` hashes the compact cold entry, `history_digest` hashes all authoritative
attempt summaries, and `resulting_archive_digest` hashes the complete sorted cold projection after
the batch; `prior_archive_entry_digest` copies the referenced archive record digest;
`query_digest` and `locator_digest` hash normalized UTF-8 text; `content_digest` hashes captured
source bytes; and `assessment_digest` hashes the assessment fields identified above. Tests use
fixed bytes and assert every expected hex value, not merely its shape.

All event references must point backward or to another preallocated event in the same atomic
append batch. Evidence, scope, proof, decision, proposal, family, variant, source, and knowledge
references are validated against the exact snapshot/request used to construct the batch. Digests
are lowercase SHA-256 of the named canonical bytes; they are not arbitrary correlation labels.

Reject unknown event types, unknown fields, invalid/duplicate refs, unbounded text, non-finite
scores, and an `objective_proof_observed` whose `proof_requirement_id` is absent from the manifest.
Assert research payloads retain query ID, policy decision, source ID, bounded normalized locator
plus locator digest, assessment, and cited event IDs without treating page content as instructions.
Round-trip separate observations for Windows/Linux OS, CPU architecture, services/ports, access
gain/loss, engagement credentials, incompatibilities, and exact-slice proof values. Reject a
secret/proof digest mismatch, a
non-candidate-only evidence range, a range not contained byte-for-byte in the supplied evidence,
an unknown scope/proof ID, or any secret origin other than `engagement_evidence`. Add an
adversarial extractor response that claims a plausible flag absent from every supplied evidence
slice; conversion must emit neither `objective_proof_observed` nor a secret event, must leave the
slice pending with `reference_validation_failed`, and must not invoke the terminal close seam.
Also reject a correctly grounded proof carrying an assessment generation older/newer than the
current `ProofProgress`; the converter must never guess or silently rebind generations. Reject a
supported proof whose exact `ProofCandidateAdmission` says `previously_rejected`, including a
match discovered through overflow replay, and reject a missing/mismatched admission or inventory
digest.
Build maximal full command/proposal/family/variant/archive fixtures and materialize each through the
real M6A envelope path. Assert the complete canonical event line, including IDs, actor, correlation,
revision/hash metadata, type, and payload, is at most `MAX_JOURNAL_EVENT_BYTES`; never treat a
payload-only size check as sufficient. Add exact under/over repository-append boundaries and assert
that a payload below `MAX_PLANNING_PAYLOAD_BYTES` can still be rejected if its fully materialized
envelope exceeds the hard limit. Assert multi-item logical records produce complete contiguous
ordinal batches; deleting, duplicating, reordering, or mixing one ordinal fails replay rather than
yielding a partial projection.

Create two M6A tool completions with different attachment/terminal event IDs but byte-identical
evidence and the same `EvidenceId`. Convert both observation batches and assert distinct
interpretation IDs, two success/failure subjects, and two `outcome_assessed` records linked to the
correct terminal tool/attempt contexts. Byte-slice hashing may deduplicate reads, but neither
converter nor reducer may treat the second attachment as already interpreted.

Use this exhaustive conversion/effect table. Independently of its two effect columns,
`EngagementReducer` handles every row identically: validate envelope/reference shape and advance
hash-chain/revision, followed by an explicit lifecycle/lane/tool/closure no-op. It never applies
planning semantics.

| Event payload | Planning source converted by `planning/journal_events.py` | `SituationReducer` effect | `StrategyLedgerReducer` / other effect |
|---|---|---|---|
| `ObservationExtractedEventPayload` | one structured record from `ObservationDraft` plus allocated event ID/call digest | Add exactly one text fact, typed facet, access delta, secret reference, or incompatibility identified from the event ID; retain exact evidence/scope/value refs | No direct ledger mutation |
| `HypothesisFormedEventPayload` | one `HypothesisDraft` after local-ref → event-ID resolution | Add/update one `SituationHypothesis`; keep support and contradiction sets separate | No direct ledger mutation |
| `MissingInformationIdentifiedEventPayload` | one `MissingInformationDraft` | Add one unresolved-information item; later resolution is event-derived | No direct ledger mutation |
| `OutcomeAssessedEventPayload` | one `OutcomeAssessmentDraft` plus bound attachment/terminal-tool/decision events | Retain categorical outcome per attachment context only; access/facet/secret/incompatibility changes require their own structured observation event | Attach the categorical result to the exact attachment, terminal tool event, and attempt IDs; equal EvidenceId bytes do not merge outcomes; score changes require reconciliation |
| `ObjectiveProofObservedEventPayload` | one validated `ObjectiveProofDraft` with current assessment generation and exact private slice | Update only the named current-generation `ProofProgress` to supported/contradicted and retain verifiable private provenance; stale generations fail | No direct ledger mutation; terminal seam reads rebuilt progress |
| `InterpretationSucceededEventPayload` | `InterpretationAudit.success(...)` after all emitted payload IDs are allocated | Mark exactly the listed slices interpreted for the attachment/terminal-tool subject and link emitted semantic events | Settlement bookkeeping keyed by attachment event, never EvidenceId alone |
| `InterpretationFailedEventPayload` | `InterpretationAudit.failure(...)` | Retryable failures retain listed slices as pending; `unsupported_media` records a terminal non-retryable assessment for the attachment while preserving its EvidenceReference/metadata; neither adds a fact | Settlement bookkeeping keyed by attachment event, never EvidenceId alone |
| `PlanRequestedEventPayload` | `PlanRequestAudit` built from the exact settled inputs | No situation mutation | Planning audit only; establishes request/digest identity |
| `FrontierProposedEventPayload` | one ordinal/full `FrontierProposalEventRecord` from validated `PlannerDraft` | No situation mutation | `FrontierReducer` assembles the complete atomic proposal set; no ledger change before acceptance |
| `FrontierCriticizedEventPayload` | one `PlannerCriticVerdict` per critic pass | No situation mutation | Critic audit only |
| `FrontierRepairedEventPayload` | one ordinal/full replacement proposal plus `PlannerRepairAudit` | No situation mutation | `FrontierReducer` assembles a complete replacement set; it still awaits final critic |
| `FrontierRejectedEventPayload` | `PlannerRejectionAudit` after final material rejection | No situation mutation | Preserve prior frontier; mark candidate rejected |
| `PlanningGapRecordedEventPayload` | one `PlanningGap` plus optional request identity | No semantic situation mutation | Publish typed gap/audit; never fabricate a frontier |
| `StrategyReconciledEventPayload` | one ordinal operation plus full result snapshot from accepted `StrategyReconciliation` | No situation mutation | Assemble the full same-batch reconciliation, apply snapshots/tombstones atomically, and verify input/result ledger digests |
| `StrategyArchivedEventPayload` | one ordinal compact `ArchivedStrategyEventRecord` from `StrategyArchiveTransition` | No situation mutation | Assemble the archive batch and move its exact snapshots cold; preserve journal authority |
| `StrategyReactivatedEventPayload` | one ordinal restored snapshot from `StrategyReactivationTransition` | No situation mutation | Restore the exact archived family/variant IDs and full state; never clone identity |
| `ResearchQueryProposedEventPayload` | `ResearchPolicyDecision` for each allowed/rejected query | Research-audit provenance only; no fact/hypothesis | No ledger mutation; rejected query never executes |
| `ResearchSourceConsultedEventPayload` | one validated `ResearchSourceObservationDraft` with host evidence | Retain consulted-source/evidence provenance but add no claim | No ledger mutation |
| `ResearchSourceAssessedEventPayload` | assessment part of `ResearchSourceObservationDraft` after cited observations exist | Retain bounded source assessment; facts still require observation events | May inform a later planner call; never mutates score directly |

- [x] **Step 2: Write failing planning-model conversion and reducer-effect tests**
For every table row, construct the named planning input, preallocate stable event UUIDs, call the
pure converter, and assert the exact payload class, literal `kind`, canonical JSON, digest, bounds,
and reference mapping. Store the 19 cases in a shared parametrized `planning_event_cases` fixture;
Task 9 consumes it to assert every Situation effect/no-op and Task 10 consumes it to assert every
Strategy effect/no-op. In Task 8, assert the M6A lifecycle no-op for all 19 immediately. Add an
architecture test that parses/imports `sedna.engagement.events` while
blocking `sedna.planning`; importing and validating all 19 payloads must still succeed. Assert
`planning/journal_events.py` imports engagement payloads, while no engagement module imports the
converter or a planning model.
- [x] **Step 3: Write failing M6A reducer compatibility tests**
Replay each new event through `EngagementReducer`; assert it advances revision/hash-chain state
without mutating lifecycle, lane binding, closure barrier, or tool-call state. Replay one malformed
payload and assert fail-closed behavior. Assert late planning events cannot manufacture an M6A
`closed_*` lifecycle transition.

Extend M6A's exhaustive append-authority test at the same time: generic
`EngagementJournalService.append_events()` rejects every one of the 19 payloads, while a
repository-issued `PlanningEventCommitCapability` accepts only bounded
`PlanningEventCommitItem(event_id: UUID, payload: PlanningEventPayload,
idempotency_key: str)` values and derives actor/system correlation itself. Reordering, adding a
lifecycle/report/promotion event, forging the capability token, or submitting an unrecognized
future `EventType` fails before repository mutation. Assert the extended owner map and `EventType`
remain set-equal.
- [x] **Step 4: Run event tests and witness RED**
Run:

```bash
pytest -q tests/engagement/test_events.py \
  tests/engagement/test_reducer.py \
  tests/planning/test_journal_events.py -x
```

Expected: parsing fails at the first unknown planning payload.
- [x] **Step 5: Implement data-only payloads in `sedna.engagement`**
Add one frozen, `extra="forbid"`, bounded payload class per event above and extend the discriminated
M6A `EventPayload` union. Payloads carry only primitives, UUIDs/stable IDs, enum strings,
bounded structured records, and event/evidence/knowledge/scope references. Do not import
`sedna.planning.models`, command renderers, retrieval services, or an LLM adapter. Planning models
must explicitly convert to these journal payloads at commit time.

Implement every helper wire record shown above in `engagement/events.py`; do not replace full
proposal, command, retry-predicate, family, variant, tombstone, or archive snapshots with only an
ID/digest. Enforce the 64-KiB canonical payload ceiling before `JournalEventDraft` construction.

`objective_proof_observed` always carries `proof_requirement_id`; interpretation success/failure
always carries the complete evidence-slice ranges covered; plan/critic/repair/rejection events
carry input/output digests and runtime IDs; reconciliation/archive/reactivation events carry exact
family/variant ancestry; research events carry policy and provenance records, never provider
credentials or raw host exceptions.

In `engagement/service.py`, extend the closed owner map with all 19 kinds owned by
`planning_capability`. Define the package-private capability's single exact method:

```python
def commit_planning_events(
    self,
    engagement_id: UUID,
    items: Sequence[PlanningEventCommitItem],
    *,
    operation_id: UUID,
    expected_revision: JournalRevision,
) -> EngagementMutationResult: ...
```

It requires `1 <= len(items) <= MAX_PLANNING_EVENT_BATCH`, unique preallocated IDs/keys, payloads
from the exact 19-member `PlanningEventPayload` union, derives
`SystemCorrelation(source="planning", operation_id=operation_id)` and the fixed actor, then uses
the normal prospective replay/CAS append. The constructor/factory token stays out of
`sedna.engagement.__all__`; `PlanningService` receives an issued instance from owned runtime
composition and never calls generic append for planning facts.
- [x] **Step 6: Implement the exhaustive pure planning-to-payload conversion**
In `planning/journal_events.py`, implement four public pure functions:

```python
def payloads_from_observation_batch(
    conversion: ObservationEventConversion,
) -> tuple[EventPayload, ...]: ...


def payloads_from_planning_attempt(
    conversion: PlanningAttemptEventConversion,
) -> tuple[EventPayload, ...]: ...


def payloads_from_reconciliation(
    conversion: StrategyReconciliationEventConversion,
) -> tuple[EventPayload, ...]: ...


def payloads_from_research_observations(
    conversion: ResearchEventConversion,
) -> tuple[EventPayload, ...]: ...
```

Use frozen input envelopes that contain the exact source models named in the table, preallocated
event UUIDs, current manifest/scope/decision/strategy/source indexes, and call metadata digests.
Build a closed source-model → payload dispatch table covering all 19 literal kinds exactly once.
Resolve response-local IDs to preallocated event UUIDs before constructing payloads, compute every
named digest from canonical JSON/UTF-8 bytes, and deep-revalidate the result through the engagement
payload union. Emit one proposed/repaired event per full proposal, one reconciliation event per
operation/result snapshot, and one archive/reactivation event per compact entry, each with exact
ordinal/count metadata for one atomic append of at most `MAX_PLANNING_EVENT_BATCH` planning events.
Preflight payloads against `MAX_PLANNING_PAYLOAD_BYTES`, then materialize the exact M6A event
envelopes and enforce `MAX_JOURNAL_EVENT_BYTES` before append; an indivisible oversize record
produces `journal_payload_too_large` and no frontier/ledger mutation, while a logically impossible
batch over `MAX_PLANNING_EVENT_BATCH` is rejected before append, never split across commits
or silently omitted. For each private proof/secret claim, locate the declared range inside the
immutable `EvidenceSliceInput` bytes already present in the conversion envelope, require exact
UTF-8 equality, construct a candidate-only `EvidenceSliceEventRef`, and compute its SHA-256
locally. Never serialize `PrivateValueDraft.claimed_utf8`; a missing/mismatched range fails the
whole interpretation atomically with no supported proof. The converter performs no IO, LLM call,
retrieval, score calculation, event
append,
or lifecycle transition. Do not export `_EventPayload`; export only the 19 concrete payload classes
from engagement and the four converters from planning.
- [x] **Step 7: Extend reducer recognition without semantic scoring**
Teach the M6A reducer that these payloads are valid non-lifecycle events. It validates manifest
proof IDs and structural references but does not calculate situation state, scores, archive
selection, research quality, or closure. Those remain M6B projections derived by deterministic
reducers from the typed events.

Extend M6A's closed status policy explicitly. Define
`SETTLEMENT_BOOKKEEPING_EVENT_TYPES` as exactly
`observation_extracted`, `hypothesis_formed`, `missing_information_identified`,
`outcome_assessed`, `objective_proof_observed`, `interpretation_succeeded`, and
`interpretation_failed`; these are legal lifecycle no-ops in active, closing, abandoned, and both
closed states because a mandatory settlement may interpret evidence captured before the terminal
transition. The other 12 planning/research/frontier/ledger event kinds are `ACTIVE_PLANNING` and
fail prospective replay outside active. Neither set can bind a lane, cancel a barrier, start work,
or itself change lifecycle. Add a closing late-contradiction settlement and abandoned-resume
settlement regression, plus a frontier/research append rejected in every non-active status.
- [x] **Step 8: Run event and conversion tests GREEN**
Run:

```bash
pytest -q tests/engagement/test_events.py \
  tests/engagement/test_reducer.py \
  tests/engagement/test_service.py \
  tests/planning/test_journal_events.py
ruff check src/sedna/engagement/events.py src/sedna/engagement/reducer.py \
  src/sedna/engagement/service.py \
  src/sedna/planning/journal_events.py \
  tests/engagement/test_events.py tests/engagement/test_reducer.py \
  tests/engagement/test_service.py \
  tests/planning/conftest.py tests/planning/test_journal_events.py
```

Expected: PASS.
- [x] **Step 9: Commit Task 8**
```bash
git add -- src/sedna/engagement/events.py src/sedna/engagement/reducer.py src/sedna/engagement/service.py src/sedna/engagement/__init__.py src/sedna/planning/journal_events.py src/sedna/planning/__init__.py tests/engagement/test_events.py tests/engagement/test_reducer.py tests/engagement/test_service.py tests/planning/conftest.py tests/planning/test_journal_events.py
git commit -m "feat(engagement): add typed planning journal events"
```

---

### Task 9: Settle Pending Evidence and Rebuild the Current Situation

**Files:**
- Create: `src/sedna/planning/situation.py`
- Create: `src/sedna/planning/service.py`
- Modify: `src/sedna/planning/ports.py`
- Modify: `src/sedna/planning/__init__.py`
- Create: `tests/planning/test_situation.py`
- Create: `tests/planning/test_service.py`

**Interfaces:**
- Consumes: M6A journal facade, `PlanningLlmAdapter`, observation prompt, completed-tool evidence,
  active lane decisions, manifest proof requirements, and the typed events from Task 8.
- Produces: `SituationReducer.rebuild(...)`, `PlanningService.load_situation(engagement_id)`,
  `PlanningService.settle_pending_evidence(self, engagement_id: UUID, *, reason:
  SettlementReason) -> SettlementResult`, terminal-reconciliation callbacks, and the versioned
  SituationProjection-only `state.json` projection. It also produces the M6C-facing data-only
  `ProofRejectionRecord`, `transition_proof_generation(...)`, and
  `proof_value_was_rejected(...)` seams; M6B never imports M6C event classes.
- [x] **Step 1: Write failing deterministic replay tests**
Create authoritative observation/outcome events for the same journal twice and assert
byte-equivalent `SituationProjection`, revision, and digest. Add non-material session checkpoint
events and assert they advance `authoritative_journal_revision` without changing
`material_event_revision` or the situation digest. Add supporting and contradicting events
and assert facts, hypotheses, access, secrets, incompatibilities, unresolved information, and
objective progress remain separate and cite exact event IDs. Assert `engagement-state.json`
continues to decode only as the M6A lifecycle projection and `state.json` only as
`SituationProjection`; cross-loading either file fails closed.
Persist `state.json`, capture canonical projection bytes, delete only that derived file in the
temporary test root, and require event-only rebuild to reproduce identical bytes/digest.
Use structured observations to reconstruct OS family/version, CPU architecture, execution
environment, services/ports/protocols, access gained/lost, exact private engagement secret refs,
incompatibilities, and proof values from exact evidence slices. Assert the rebuilt
private situation exposes exact proof/secret evidence-slice references required by M6C reporting,
which can read their bytes through the bounded evidence API without an LLM re-interpretation;
searchable/canonical projections receive none of those private values. Persisted inline proof or
secret bytes are forbidden. The trusted M6C projector must request exactly the recorded
`(evidence_id, start, end)`, verify returned bytes against `value_sha256`, and symbolize/redact the
value before constructing or serializing `PromotionInput`; a mismatch fails closed.
Replay two distinct attachment/terminal-tool pairs whose evidence bytes and `EvidenceId` are
identical. Assert two `EvidenceInterpretationState` subjects and two categorical
`AttemptSummary`/outcome links survive projection deletion/rebuild; facts may coalesce only by an
explicit deterministic fact identity that retains both source event sets.
Start with two supported requirements, call the pure proof-generation helper with
`policy="retain_rejections"` for one cited requirement and its exact candidate SHA-256, and assert
only that requirement advances generation, becomes contradicted, and retains the rejected digest
while the other remains supported. A new grounded support event with that same digest must fail
with `previously_rejected_proof_value`; a distinct grounded digest in the new generation becomes
supported/closure-eligible. The prior support/rejection remains represented by historical
count/digest and rejection inventory. Apply
`policy="invalidate_all"` and assert both advance to pending. Replay M6A's legacy
`engagement_reopened` (no policy fields) and require the same fail-closed invalidate-all behavior.
An objective-proof event carrying a pre-reopen generation is rejected and cannot reclose.
Generate 35 unique reject/retain cycles for one requirement. Assert the newest 32 digests are hot,
the first three occur only in the exact overflow count/digest, projection deletion plus event replay
is byte-identical, and a support attempt using one of those three forces authoritative-record replay
and fails. A 36th distinct grounded digest may support. Corrupting count, digest, order, cited proof
event, or requirement ID fails closed.
Parameterize over the shared 19-event fixture and assert the Task 8 table exactly: observation,
hypothesis, missing-information, outcome, proof, interpretation coverage/failure, and research
provenance/assessment produce only their declared situation changes; plan/frontier/critic/repair/
rejection/gap/reconciliation/archive/reactivation are situation no-ops. A research assessment never
becomes a fact unless a separate `observation_extracted` event cites its evidence.
- [x] **Step 2: Write failing settlement tests**
Assert settlement:

- returns `nothing_pending` with no LLM call when all evidence is interpreted;
- returns `settled` only after this invocation has authoritatively interpreted all remaining
  evidence and returns no pending range;
- reads every text slice of a large sidecar across bounded settlement invocations without silent
  truncation;
- passes binary evidence metadata without pretending to interpret bytes as text;
- emits one deterministic, non-retryable `interpretation_failed(unsupported_media)` event for a
  binary attachment without calling the LLM, preserves its EvidenceReference for the report, and
  removes that attachment from pending work so manual close/report can complete;
- settles a zero-byte text/JSON attachment with empty `interpretation_succeeded` coverage and a
  zero-byte binary attachment with empty `unsupported_media` coverage; both retain the descriptor,
  invent no synthetic positive range, and allow close/report;
- appends observation/outcome/objective-proof events only after deep validation;
- appends research-source-consulted/assessed events only for structured source metadata actually
  present in host evidence, never from a candidate alone;
- treats a syntax failure as `execution_error`, not negative strategy evidence;
- retains a discovered flag in private evidence and emits `possible_terminal_evidence=True`;
- rejects a proof draft whose `proof_requirement_id` is not present in the manifest;
- rejects a byte-grounded supported proof whose value digest was rejected in any prior generation,
  including a digest present only in folded overflow, without appending proof or invoking close;
- returns a safe failure while leaving evidence pending on extractor/append failure;
- processes exactly 64 32-KiB slices (2 MiB) per invocation, returns `incomplete`, and resumes at
  the first pending byte on the next invocation.
- represents 513+ pending attachment/range subjects with a deterministic 512-record page, true
  `pending_total_count`, ordered inventory digest, and next-subject cursor; repeated settlement
  starts from that cursor and wraps fairly so retryable early subjects cannot starve later ones.

For every row, validate the result through `SettlementResultAdapter` and assert its projection
revision, explicit manifest `required_proof_ids`, derived `all_required_proofs_satisfied`,
`possible_terminal_evidence`, sorted non-overlapping `PendingEvidenceRange` records, and closed
failure code. A corrupt/unavailable journal returns the only failed variant without a situation;
extractor, append, concurrency, evidence-read, and terminal-port failures return the exact last
committed situation. Neither `incomplete` nor `failed` invokes the terminal port.
- [x] **Step 3: Write failing required-proof and terminal-seam tests**
Inject a spy `TerminalSettlementPort`. Assert an empty manifest never invokes it, one supported
proof out of two invokes it with `all_required_proofs_satisfied=False`, and two supported proofs
invoke it with `True` and the exact `SituationProjection`/journal revision. Append later
contradictory evidence and assert the next successful settlement invokes `False`, allowing M6C to
cancel a proof-driven pending close; it must not cancel an explicit manual close solely because
proofs are partial. No M6B path itself appends `closure_requested` or a terminal close event.
Have the spy mutate the journal and return every exact `TerminalReconciliationResult` action.
Assert settlement reloads the post-port M6A snapshot, cross-validates revision/lifecycle status,
and returns a `SituationProjection` rebound to that authoritative revision. A mismatched result or
`action="failed"` returns
`FailedSettlementResult(failure_code="terminal_reconciliation_failed")`; it never silently
continues from the pre-port situation.
- [x] **Step 4: Write failing stale-revision and lock tests**
Use a blocking scripted host and a second writer. Assert the engagement lock is acquirable while
the extractor is blocked, the stale append is rejected, settlement reloads and retries against the
new revision once, and it never duplicates evidence interpretation events.
- [x] **Step 5: Run situation/service tests and witness RED**
Run:

```bash
pytest -q tests/planning/test_situation.py tests/planning/test_service.py -x
```
Expected: collection fails because settlement and reducer do not exist.
- [x] **Step 6: Implement the deterministic reducer**
`SituationReducer` accepts only a strictly validated `EngagementSnapshot` plus ordered typed
journal events. It derives state from event semantics, not model prose outside events. Assign fact,
hypothesis, proof, secret-reference, and attempt identities from event identity; preserve event
references; compute canonical JSON SHA-256 excluding renderer timestamps and unrelated session
events. It resolves proofs only against `snapshot.manifest.required_proofs`, retains contradictory
proof state, requires every objective-proof event's `assessment_generation` to equal the current
requirement generation, and never interprets an empty requirement list as completion.

Implement this exact pure seam in `situation.py` for M6C Task 2:

```python
def transition_proof_generation(
    progress: ObjectiveProgress,
    *,
    policy: Literal["retain_rejections", "invalidate_all"],
    transition_event_id: UUID,
    rejected_requirement_id: ProofRequirementId | None = None,
    rejection_event_id: UUID | None = None,
    rejected_value_sha256: Sha256Hex | None = None,
) -> ObjectiveProgress: ...
```

For `retain_rejections`, require both rejected IDs and `rejected_value_sha256`, resolve exactly one
existing requirement, verify that digest equals the candidate digest on the cited grounded
objective-proof event, fold
its prior current refs into historical count/digest, increment only its generation, set
`generation_started_event_id=transition_event_id`, and initialize it contradicted with
`rejection_event_id`; insert the distinct rejected digest into the newest-32/overflow inventory and
return every other requirement byte-equivalent. For `invalidate_all`, require all three rejected
fields absent, fold/increment every requirement, preserve each rejection inventory unchanged, set
the transition event, and clear current refs/status to pending. The legacy M6A
`engagement_reopened` reducer path calls
`invalidate_all`. M6C later evolves reopen/reject payloads and calls this helper during event replay;
it must derive/pass `rejected_value_sha256` from the cited proof event rather than user input. The
helper itself imports no M6C type and never mutates historical events.
The historical digest is SHA-256 of the ordered canonical tuples
`(assessment_generation, assessment, assessment_event_id, value_sha256_or_null)` for every
pre-current-generation proof assessment seen in journal order; count equals that tuple count. It is
recomputed during event-only replay, not chained from an unchecked projection digest.

Also implement this pure admission seam, consumed by the reducer and M6C replay:

```python
def proof_value_was_rejected(
    progress: ProofProgress,
    *,
    candidate_value_sha256: Sha256Hex,
    authoritative_rejections: Sequence[ProofRejectionRecord],
) -> bool: ...
```

First validate that records all name the same requirement, are in strict rejection-event journal
order, and recompute the exact newest-32 tuple plus overflow count/digest; mismatch with `progress`
is projection corruption. A hot match returns true immediately. When overflow count is nonzero and
the candidate is not hot, scan the validated authoritative records; never infer non-membership from
the folded digest. `SituationReducer` calls this before applying every supported
`objective_proof_observed`: a match fails replay with `previously_rejected_proof_value`, while a
different locally grounded digest may become current support. When loading a cached `state.json`,
settlement may short-circuit only a hot match or an empty overflow; otherwise it must obtain the
full rejection records from the M6A journal and revalidate the projection before conversion/append.
- [x] **Step 7: Implement complete bounded evidence slicing**
Read evidence through `EngagementJournalService.read_evidence_slice`, never direct paths. Use
deterministic 32 KiB byte slices with digest/range metadata. Process at most 64 slices and exactly
2 MiB per settlement invocation; return `incomplete` with pending ranges in the result when more
work remains. Unsupported binary content yields typed metadata and remains linked, but is settled
locally by one `InterpretationFailedEventPayload(failure_code="unsupported_media",
retryable=False, call_metadata=None, attempted_slices=())`; replay
marks that attachment terminally assessed and never presents it as a pending range. Retryable
extractor/read failures stay pending. Do not mark an attachment interpretation subject complete
until every required text slice has a validated assessment or the attachment has this exact
unsupported-media terminal assessment, and never return `settled` while terminal evidence remains
pending. Enumerate pending work from
M6A attachment events, keyed by `attachment_event_id` plus its optional terminal tool event, not by
distinct `EvidenceId`; content-addressed bytes may be read once but every attachment context emits
its own interpretation/outcome linkage.
Build the pending inventory in stable attachment/terminal/evidence/range order, hash the complete
canonical inventory, return at most 512 records, and derive `next_pending_subject` from the first
omitted subject. The next invocation resumes there and wraps once. `pending_total_count` is the true
complete count, not page length; no attachment is silently lost.
The cursor is opaque `pending-<sha256(canonical subject identity)>`: it exposes no attachment,
evidence, or private value and is informational, not mutable scheduling state. On every invocation,
including a fresh runtime with no projection, derive selection entirely from journal history: sort
pending work by `(last_interpretation_attempt_sequence_or_0, canonical_subject_key, start)`, take the
bounded page/tranche, and set `next_pending_subject` to the first omitted key. Thus every committed
retry moves behind never/less-recently attempted subjects and cannot starve them. `state.json` may
cache but never author this order; rebuild must be byte-identical. Add a fresh-runtime test where the
first page fails retryably, restart, and prove later subjects are selected before that page repeats.
- [x] **Step 8: Implement settlement, projection loading, and optimistic append**
Construct the Task 9 `PlanningService` with journal, planning LLM, optional terminal-settlement
port, and clock; Tasks 10–12 extend the same constructor with archive, retrieval, canonical-
revision, and bounded source-registry dependencies when those modules exist. Implement the exact
public signatures:

```python
def load_situation(self, engagement_id: UUID) -> SituationProjection:
    ...


def settle_pending_evidence(
    self,
    engagement_id: UUID,
    *,
    reason: SettlementReason,
) -> SettlementResult:
```

Load snapshot and evidence metadata under M6A operations, release all locks, read slices and call
the extractor, validate every event/evidence/decision reference, then append typed events with
`expected_revision`. On one stale revision, reload and repeat only still-pending evidence. Commit
`state.json` as a `SituationProjection` envelope only after authoritative append succeeds; never
write `engagement-state.json`. `load_situation()` deep-validates its envelope and rebuilds from
events if absent or stale. Every non-failure `SettlementResult` contains that exact projection.

Before converting/appending any supported objective proof, locally materialize its slice digest and
call `proof_value_was_rejected`. Use the projection's newest-32 tuple only as a positive hot match;
if overflow is nonzero and the digest is not hot, derive complete `ProofRejectionRecord` input from
the authoritative journal and verify count/digest before deciding. A prior match produces typed
`reference_validation_failed`, keeps the attachment pending, appends no supported proof, and cannot
reach terminal reconciliation. Never ask the LLM whether two candidate values are equivalent.

After a `settled` or `nothing_pending` result, release all repository locks and invoke the terminal
port only when `manifest.required_proofs` is non-empty. Pass `True` only when every explicit
requirement is supported and none is contradicted; pass `False` for partial or later contradictory
evidence. Never invoke it for `incomplete` or `failed`. Port failure returns
`FailedSettlementResult(failure_code="terminal_reconciliation_failed")` after reloading the exact
latest committed situation/revision, with no rollback or rewrite of valid situation events. On a successful
port result, reload the exact M6A snapshot outside the port/locks, require returned revision/status
to match (or restart once on a newer concurrent revision), and rebuild/rebind the returned
situation to the post-reconciliation authoritative revision before returning settlement.
- [x] **Step 9: Run situation and settlement tests GREEN**
Run:

```bash
pytest -q tests/planning/test_situation.py tests/planning/test_service.py
```
Expected: PASS.
- [x] **Step 10: Commit Task 9**
```bash
git add -- src/sedna/planning/situation.py src/sedna/planning/service.py src/sedna/planning/ports.py src/sedna/planning/__init__.py tests/planning/test_situation.py tests/planning/test_service.py
git commit -m "feat(planning): settle engagement evidence"
```

---

### Task 10: Build the Bounded Strategy Ledger and Archive

**Files:**
- Create: `src/sedna/planning/ledger.py`
- Modify: `src/sedna/planning/service.py`
- Modify: `src/sedna/planning/__init__.py`
- Modify: `src/sedna/engagement/models.py`
- Modify: `src/sedna/engagement/repository.py`
- Modify: `src/sedna/engagement/service.py`
- Modify: `src/sedna/engagement/__init__.py`
- Create: `tests/planning/test_ledger.py`
- Modify: `tests/engagement/test_repository.py`
- Modify: `tests/engagement/test_service.py`

**Interfaces:**
- Consumes: authoritative proposal, decision, tool-call, and outcome events plus current situation.
- Produces: `StrategyLedgerReducer`, `validate_reconciliation(...)`,
  `select_reactivation_candidates(...)`, ledger/archive digests, and rebuildable
  `strategy-ledger.json`/`strategy-archive.jsonl` projections. M6A additionally exposes dedicated
  `load_strategy_archive(...)` and `commit_strategy_archive(...)` operations; no caller supplies a
  path and M6A imports no planning model.
- [x] **Step 1: Write failing descriptor-confined archive writer tests**
Require this M6A service contract:

```python
MAX_STRATEGY_ARCHIVE_RECORDS = 100_000
MAX_STRATEGY_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_STRATEGY_ARCHIVE_PAGE = 256


def load_strategy_archive(
    self,
    engagement_id: UUID,
    *,
    after_entry_id: UUID | None = None,
    limit: int = MAX_STRATEGY_ARCHIVE_PAGE,
) -> StrategyArchivePage | None:
    ...


def commit_strategy_archive(
    self,
    engagement_id: UUID,
    *,
    schema_id: str,
    records: Iterable[StrategyArchiveRecordDraft],
    expected_archive_revision: int | None,
    expected_journal_revision: JournalRevision,
) -> StrategyArchiveCommitResult:
    ...
```

`StrategyArchiveProjectionEnvelope` is header-only `(schema_id, archive_revision,
authoritative_journal_revision, entry_count <= MAX_STRATEGY_ARCHIVE_RECORDS, entries_sha256,
byte_size <= MAX_STRATEGY_ARCHIVE_BYTES)`. `StrategyArchivePage` adds at most 256 deeply validated
records, `next_after_entry_id`, `complete`, and an ordered omitted digest;
`StrategyArchiveCommitResult` contains only the header and fixed-path metadata, never all records.

Assert first create and subsequent CAS replacement, stale archive revision rejection, stale
journal revision rejection, deterministic ordering/digest, deep payload validation, mode `0600`,
fixed filename `strategy-archive.jsonl`, symlink/non-regular-file rejection, crash-safe temp-file
replacement, and fail-closed loading for a corrupt header/entry/digest/count. `schema_id` and
record payload are bounded; an `entry_id` is unique. Commit consumes at most the record cap plus one
sentinel item, streams canonical JSONL through a byte counter/digest to its temp, and rejects
count/byte overflow before replace. Load pre-stats and incrementally validates header/lines, seeking
the sorted UUID cursor without materializing the complete file. The API cannot express `../` or an
alternate filename. Add exact-limit/one-over, oversized-corrupt-file, page-restart, and
infinite-iterator bounded-consumption tests.
- [x] **Step 2: Write failing family/variant/attempt replay tests**
Model an SSH family with common-credential, bounded-wordlist, and credential-reuse variants.
Assert an exhausted wordlist variant may reach score zero while the SSH family remains deferred
with retry condition `credential_available`; a later credential event reactivates the existing
family/variant IDs rather than creating duplicates. Append more than eight attempts to one variant
and more than 256 across hot variants; assert only the newest bounded summaries remain hot while
counts, outcome-category totals, oldest/newest revisions, and an aggregate digest cover every
authoritative attempt event.
Parameterize over the shared 19-event fixture: only `outcome_assessed` attaches a categorical
attempt result, `strategy_reconciled` applies explicit ledger operations, `strategy_archived`
moves exact IDs cold, and `strategy_reactivated` restores those IDs. Assert all other 15 event
kinds are direct ledger no-ops; they may influence only a later LLM-authored reconciliation.
- [x] **Step 3: Write failing event-only ledger/archive reconstruction tests**
Commit families, variants, retry predicates, attempt outcomes, split/merge/tombstone operations,
archive records, and reactivation as multi-event atomic batches. Persist ledger/archive projections,
capture their canonical bytes/digests, delete only those derived files in the temporary test root,
and rebuild from manifest plus journal events. Require byte-identical `StrategyLedger` and
`StrategyArchive` payloads/digests. Reject missing, duplicate, mixed-request, out-of-range, or
non-atomic reconciliation/archive ordinals and any resulting digest mismatch; never fall back to
IDs/digests without the full snapshots.
- [x] **Step 4: Write failing full-reconciliation tests**
Supply every hot entry and selected archive candidate to a planner draft. Reject a response that
omits one, changes its runtime ID, reparents a variant, creates duplicate keys, archives an
available item without reason, or exceeds hot limits without an explicit merge/supersede/archive.
Accept explicit retain, update, merge, split, supersede, complete, block, archive, and reactivate
operations with critic-approved ancestry.
- [x] **Step 5: Write failing archive bound and predicate tests**
Populate more than 32/64 entries, rebuild deterministic hot/archive projections, and verify only
typed predicates choose at most 16 candidates. Test all six predicate kinds and ensure unrelated
new evidence does not reactivate an archived strategy. Assert aggregate summary is deterministic
and at most 16 KiB.
- [x] **Step 6: Run archive/ledger tests and witness RED**
Run:

```bash
pytest -q tests/engagement/test_repository.py \
  tests/engagement/test_service.py \
  tests/planning/test_ledger.py -x
```
Expected: the first new archive-service API assertion fails.
- [x] **Step 7: Implement the dedicated M6A archive projection surface**
Define data-only `StrategyArchiveRecordDraft` and `StrategyArchiveProjectionEnvelope` in
`sedna.engagement`. The first JSONL line is a canonical envelope with schema ID, archive revision,
authoritative journal revision, entry count, and entries digest; following lines are canonical
records bound to that revision. Commit by descriptor-relative fixed-name temp file, fsync, atomic
rename, and directory fsync under an archive lock. Compare both expected archive revision and
current journal revision before publishing. This is a replaceable projection rebuilt from journal
events, not a second append-only authority and not an extension of generic `commit_projection()`.
Before the authoritative planning batch that would change the cold partition, stream-preflight the
prospective archive record set against both hard limits. A batch that cannot retain a rebuildable
projection fails before journal append; recovery repeats the same deterministic preflight.
- [x] **Step 8: Implement immutable identity resolution and replay**
Assign UUIDs only when committing accepted new keys. Existing family/variant IDs must match the
ledger. Attempt IDs derive from the journaled decision plus ordered tool-call IDs. Rebuild current
scores, prior scores, statuses, evidence, prerequisites, retry conditions, relationships, and
attempt outcomes entirely from authoritative events. Apply only ordinal-complete atomic
`StrategyReconciledEventPayload` batches and use their full family/variant/tombstone snapshots as
the resulting state; IDs/digests/reason prose alone are never sufficient. Apply archive and
reactivation batches the same way, cross-validating their companion tombstone/restored snapshot
and applying each transaction once, then verify the declared resulting ledger/archive digest.
- [x] **Step 9: Implement bounded hot/archive partitioning**
Keep available/deferred/selected entries hot, then deterministic recency/status/key order within
the 32/64 caps. Archive older blocked, exhausted, completed, and superseded entries without
deleting their authoritative events. Fail reconciliation when limits cannot be satisfied without
silent loss. Retain exactly eight recent attempts per variant and 256 total hot attempt summaries;
older events contribute to deterministic aggregates. Serialize the complete cold projection only
through the streaming `commit_strategy_archive()` with both archive and journal CAS revisions;
consumers always read bounded pages.
- [x] **Step 10: Implement retry-predicate matching**
Match only explicit situation facts, prerequisites, evidence categories, credential-reference
presence, and revision comparisons. This reducer selects candidates; it does not alter score or
make a semantic judgment.
- [x] **Step 11: Run archive and ledger tests GREEN**
Run:

```bash
pytest -q tests/engagement/test_repository.py \
  tests/engagement/test_service.py \
  tests/planning/test_ledger.py \
  tests/planning/test_situation.py
```
Expected: PASS.
- [x] **Step 12: Commit Task 10**
```bash
git add -- src/sedna/planning/ledger.py src/sedna/planning/service.py src/sedna/planning/__init__.py src/sedna/engagement/models.py src/sedna/engagement/repository.py src/sedna/engagement/service.py src/sedna/engagement/__init__.py tests/planning/test_ledger.py tests/engagement/test_repository.py tests/engagement/test_service.py
git commit -m "feat(planning): retain adaptive strategy ledger"
```

---

### Task 11: Assemble Situation-conditioned Knowledge Safely

**Files:**
- Create: `src/sedna/planning/retrieval.py`
- Modify: `src/sedna/planning/service.py`
- Modify: `src/sedna/planning/__init__.py`
- Modify: `src/sedna/engagement/sources.py`
- Modify: `src/sedna/engagement/__init__.py`
- Create: `tests/planning/test_retrieval.py`
- Modify: `tests/engagement/test_sources.py`

**Interfaces:**
- Consumes: `SituationProjection`, engagement scope, `KnowledgeRetrievalService`, execution-
  example drill-down, and the global `SharedSourceRegistry`.
- Produces: `build_retrieval_queries(...)`, `PlannerKnowledgeContext`, and bounded source-backed
  planner inputs with a canonical revision plus situation-conditioned source/research candidates.
- [x] **Step 1: Write failing state-to-query tests**
Build situations for authorized IPv4, URL, multi-target, unknown OS, known Linux, Windows, and
invalid target states. Assert one conservative query per active authorized target, exact scope
membership, evidence-derived terms/facets/services/access/hypotheses/outcomes, and no invented
architecture or synonyms. Invalid/unauthorized targets stop before index access.
- [x] **Step 2: Write failing lane and example-selection tests**
Assert the planner package preserves references, cases, negative evidence, guidance, rejection
reasons, and lane-local retrieval scores as separate labeled collections. Load execution examples
only for qualifying parent artifacts, never rejected candidates, cap at 16, and retain exact
example/source IDs without inserting command text into query terms. Preserve typed execution-
example coverage gaps beside the affected sources.
- [x] **Step 3: Write failing gap/applicability tests**
Verify Windows-only knowledge is excluded for known Linux, unknown architecture remains unknown,
an Android/ADB corpus miss remains a typed gap with research eligibility, and
`retrieval_unavailable` is not converted to “no knowledge” or a research recommendation. A legacy
strategic hit remains usable, but `legacy_bundle_without_examples` prevents source-command
attribution; a labeled `model_generated` suggestion remains allowed.
- [x] **Step 4: Write failing bounded shared-source tests**
Keep M6A's parameterless full validated `snapshot()`/`list_entries()` contracts unchanged. Add the
separate planner-facing `planner_snapshot()` and `list_planner_hints(...)` methods defined below.
Assert at most 128 managed records/64 KiB in a planner snapshot and at most 16 records/16 KiB in a planner
hint list, with total count, truncation flag, and digest of the complete validated registry. Manual
prose outside managed blocks is preserved on disk but absent from returned records. Reject malformed
managed data rather than passing it through. Given Linux/HTTP evidence, prefer matching topical
records and user suggestions while retaining a typed `truncated` indication; given known Linux,
exclude Windows-only sources; given unknown platform, do not invent one. User suggestions are
priority hints, not an allowlist, so the candidate set may also contain a model-proposed generic
technical source.
- [x] **Step 5: Run source/retrieval tests and witness RED**
Run:

```bash
pytest -q tests/engagement/test_sources.py \
  tests/planning/test_retrieval.py -x
```
Expected: the first new `SharedSourceRegistry.planner_snapshot()`/`list_planner_hints()` assertion fails before
the missing planner retrieval adapter is implemented.
- [x] **Step 6: Implement deterministic query construction**
Implement:

```python
def build_retrieval_queries(
    situation: SituationProjection,
    scope_references: tuple[ScopeReference, ...],
    *,
    max_candidates: int = 32,
    lane_limit: int = 5,
) -> tuple[RetrievalQuery, ...]:
```

Map only evidence-backed state into the existing `CurrentSituation`; never send private secret or
flag values as retrieval terms. Use symbolic credential availability such as
`credential available for ssh`, not the credential value.
- [x] **Step 7: Implement bounded registry snapshots and source selection**
Define these frozen, extra-forbid planner results and methods without changing M6A's full API:

```python
class PlannerSourceSnapshot(BaseModel):
    registry_sha256: Sha256Hex
    total_count: int = Field(ge=0, le=MAX_SOURCE_REGISTRY_ENTRIES)
    entries: tuple[SharedSourceEntry, ...] = Field(max_length=128)
    truncated: bool
    omitted_entries_sha256: Sha256Hex | None = None
    canonical_bytes: int = Field(ge=0, le=64 * 1024)


class PlannerSourceHintPage(BaseModel):
    registry_sha256: Sha256Hex
    total_count: int = Field(ge=0, le=MAX_SOURCE_REGISTRY_ENTRIES)
    entries: tuple[SharedSourceEntry, ...] = Field(max_length=16)
    truncated: bool
    omitted_entries_sha256: Sha256Hex | None = None
    canonical_bytes: int = Field(ge=0, le=16 * 1024)


def planner_snapshot(self) -> PlannerSourceSnapshot: ...

def list_planner_hints(
    self,
    *,
    topic_tokens: tuple[str, ...] = (),
) -> PlannerSourceHintPage: ...
```

Parse only canonical managed blocks into these results; compute the registry digest over every validated managed record
plus a digest of preserved manual bytes without returning those bytes. The registry accepts
bounded topic tokens from planning but imports no `SituationProjection`. In `planning/retrieval.py`,
derive those tokens only from cited platform/architecture/service/protocol facts and assemble
`CandidateResearchSource` records with source ID, normalized locator, topics, origin, status, and
why it applies. Serialize these records as untrusted JSON data, never instructions.
Add `inspect.signature` regressions proving M6A `snapshot()` and `list_entries()` remain
parameterless/full (within their 4,096/1-MiB hard bounds), while the new methods enforce their
smaller canonical-byte/count limits and deterministic omitted digests.
- [x] **Step 8: Implement bounded knowledge assembly**
Call retrieval per query, preserve lane identity, validate canonical revision before and after
the complete retrieval/example drill-down, and build `PlannerKnowledgeContext` with exact
artifact/source/example refs, typed coverage gaps, registry digest, and bounded candidate research
sources. Enforce cumulative text/count bounds and a stable canonical digest. Applicability and
platform constraints on an `ExecutionExample` are independently checked before it can become a
source-backed command candidate.
- [x] **Step 9: Run retrieval adapter tests GREEN**
Run:

```bash
pytest -q tests/engagement/test_sources.py \
  tests/planning/test_retrieval.py \
  tests/knowledge/test_retrieval_service.py
```
Expected: PASS.
- [x] **Step 10: Commit Task 11**
```bash
git add -- src/sedna/planning/retrieval.py src/sedna/planning/service.py src/sedna/planning/__init__.py src/sedna/engagement/sources.py src/sedna/engagement/__init__.py tests/planning/test_retrieval.py tests/engagement/test_sources.py
git commit -m "feat(planning): retrieve situation-conditioned experience"
```

---

### Task 12: Orchestrate Planner, Critic, One Repair, Cache, and Optimistic Commit

**Files:**
- Create: `src/sedna/planning/frontier.py`
- Modify: `src/sedna/planning/service.py`
- Modify: `src/sedna/planning/models.py`
- Modify: `src/sedna/planning/commands.py`
- Modify: `src/sedna/planning/__init__.py`
- Modify: `tests/planning/test_service.py`
- Create: `tests/planning/test_frontier.py`
- Modify: `tests/planning/test_prompt_injection.py`

**Interfaces:**
- Consumes: settled situation, ledger/archive, planner knowledge, shared-source digest, four-role
  LLM adapter, and exact execution lane binding.
- Produces: `PlanningService.plan_next(self, lane: ExecutionLaneKey, *, max_proposals: int = 5)
  -> PlanningResult`, `FrontierReducer.rebuild(...)`, validated frontier events/projection,
  composite cache, and typed planning gaps.
- [x] **Step 1: Write failing accepted and repaired call-path tests**
Script accepted planning and assert purposes are exactly `plan, critic`. Script one material
finding, repaired output, and acceptance; assert exactly `plan, critic, repair, critic`. Script a
second material rejection and assert `planning_gap`, no new frontier projection, safe call
metadata, and preserved previous frontier marked stale rather than newly validated.
For accepted and repaired paths, persist the full proposal-event batch including command
templates/placeholders/bindings, capture canonical `FrontierProjection` bytes, delete only
`frontier.json` in the temporary root, and rebuild from journal events. Require byte-identical
projection/digest and exact command records. Reject an incomplete, duplicate, mixed-frontier, or
non-atomic proposal/repair ordinal set instead of publishing a partial frontier.

Start `plan_next` with every explicit proof supported and a terminal spy that requests/finalizes
proof closure. After settlement, require an exact M6A snapshot reload and a typed
`planning_gap(code="engagement_terminal")` carrying only lifecycle status/revision; assert zero
knowledge retrieval, zero planner/critic calls, and zero planning-event append. Repeat with the
proof-close barrier still `closing` and with an already closed snapshot. A partial-proof settlement
that cancels only a proof-driven close and reloads `active` may proceed normally; an explicit
manual-close barrier remains terminal to this planner call.
- [x] **Step 2: Write failing semantic guardrail tests**
Reject before frontier/ledger persistence; closed rejection/audit events may still be appended:

- invented event, evidence, knowledge, example, strategy, secret, or scope refs;
- out-of-scope target binding;
- score/previous-score disagreement;
- changed score without cited event and explanation;
- score zero without in-scope impossibility/incompatibility status;
- duplicate proposal or command;
- any indivisible proposal/command/snapshot journal record over 64 KiB;
- silent strategy loss;
- invalid archive reconciliation;
- source command presented as model-generated or vice versa;
- unsafe current-machine solution/flag research query.
- [x] **Step 3: Write failing adaptive-path tests**
Use scripted LLM responses to prove a syntax error retains strategic score, rejected common SSH
credentials lower but do not erase SSH, a complete `rockyou` attempt exhausts only that variant,
and new credentials reactivate targeted SSH access. Verify each score change cites exact events
and the planner may add a novel strategy absent from retrieved cases. Assert the planner sees no
more than eight attempts per variant, 256 hot attempts, 64 recent events, or 64 KiB recent-event
text, while aggregate digests preserve the omitted history.
- [x] **Step 4: Write failing composite-cache tests**
Assert identical situation, ledger, canonical revision, source-registry digest, and versions reuse
the frontier with zero LLM calls. Independently change each component and assert replanning. A
session checkpoint advances `authoritative_journal_revision` but leaves
`material_event_revision`, situation digest, and ledger digest unchanged, so it must not invalidate
cache: assert the typed cached result carries the new current authority while the immutable
frontier retains its original publication revision. Do not require equality with the cached
frontier's old authoritative revision. Script an accepted reconciliation that changes the ledger
digest; assert the frontier records old `input_ledger_digest`, new `resulting_ledger_digest`, is
published under the resulting digest, and an immediately identical call hits cache with zero LLM
calls. Assert `max_proposals=3` and `max_proposals=8` use different cache entries and the requested
bound is included in both planner input and cache material.
- [x] **Step 5: Write failing optimistic concurrency/lock tests**
Block planner and critic calls while another lane appends an event. Assert journal and canonical
locks are free, stale output is not committed, one bounded restart occurs, and a second concurrent
change returns `planning_gap(code="concurrent_state_change")` without an infinite retry.
- [x] **Step 6: Run service tests and witness RED**
Run:

```bash
pytest -q tests/planning/test_service.py \
  tests/planning/test_frontier.py \
  tests/planning/test_prompt_injection.py -x
```
Expected: settlement tests pass but planning orchestration assertions fail.
- [x] **Step 7: Implement the composite cache key**
Hash canonical JSON of:

```python
cache_material = {
    "situation_digest": situation.state_digest,
    "material_event_revision": situation.material_event_revision,
    "resulting_ledger_digest": resulting_ledger_digest,
    "canonical_revision": canonical_revision,
    "source_registry_digest": source_registry_digest,
    "max_proposals": max_proposals,
    "observation_prompt_version": OBSERVATION_PROMPT_VERSION,
    "planner_prompt_version": PLANNER_PROMPT_VERSION,
    "critic_prompt_version": PLANNER_CRITIC_PROMPT_VERSION,
    "repair_prompt_version": PLANNER_REPAIR_PROMPT_VERSION,
    "situation_schema_version": SITUATION_SCHEMA_VERSION,
    "ledger_schema_version": STRATEGY_LEDGER_SCHEMA_VERSION,
    "frontier_schema_version": FRONTIER_SCHEMA_VERSION,
    "research_policy_version": RESEARCH_POLICY_VERSION,
    "command_policy_version": COMMAND_POLICY_VERSION,
}
```

The pre-call audit record separately stores `input_ledger_digest=ledger.digest`. After accepted
reconciliation, compute and persist the cache key with the committed resulting ledger digest; if
the reconciliation is a no-op the two digests match. Cache lookup compares current situation
digest/material revision, current ledger digest against `resulting_ledger_digest`, canonical and
source revisions, versions, policy, and `max_proposals`. It deliberately ignores a change to only
`authoritative_journal_revision`; on a hit, retain immutable frontier publication provenance and
set `PlanningResult.current_authoritative_journal_revision` from the freshly loaded snapshot.
- [x] **Step 8: Implement `plan_next` outside locks**
The method resolves the exact lane, calls `settle_pending_evidence(..., reason="plan")`, and uses
the exact `SettlementResult.situation`; it does not load proof state from M6A. Continue only for
`settled` or `nothing_pending`. Convert `incomplete` to a retryable typed planning gap that returns
the exact pending ranges, and propagate `failed` as a safe planning failure; neither path invokes
planner/retrieval or dereferences an absent situation. For either successful settlement status,
reload the authoritative M6A snapshot after terminal reconciliation. If lifecycle status is
`closing`, `closed_unverified`, `closed_verified`, or `abandoned`, return an ephemeral typed
`engagement_terminal` planning gap with exact status/revision and append no plan/gap/ledger/frontier
event; this check occurs before retrieval, cache lookup, or any LLM call. Only an exact `active`
snapshot may continue. It then loads ledger, archive candidates,
at most 64 recent events/64 KiB text, scope, bounded registry snapshot/digest, and knowledge
revisions, all without retaining repository locks across retrieval or an LLM call. Pack and
canonical-serialize the complete request before dispatch; reject anything over 512 KiB. Validate
planner output, run critic and at most one repair/final critic, assign new runtime IDs, validate
command bindings, and build typed authoritative event drafts. Before any frontier/cache/journal
publication, construct the prospective `PlanningResult`, canonicalize it, and require
`<= MAX_PLANNING_RESULT_BYTES`; an oversized accepted draft is fed through the one existing repair
opportunity, then becomes bounded `failed(result_too_large)` with no frontier/cache/proposal event.
The final host serializer separately checks the complete envelope against
`MAX_HOST_RESULT_BYTES`. Add maximal eight-proposal exact-under/one-byte-over tests proving an
unreturnable frontier is never committed or cached.
- [x] **Step 9: Implement event-only frontier replay**
Implement `FrontierReducer` over ordered typed journal events. It assembles all
`frontier_proposed` ordinals for an accepted initial draft or all `frontier_repaired` ordinals for
the accepted repair, verifies common request/frontier/count/digests and exact ordinal coverage,
then deep-validates every full `FrontierProposalEventRecord`, strategy/scope/knowledge ref, and
command record. Accepted publication also joins the ordinal-complete reconciliation with the same
request/frontier ID so `input_ledger_digest` and verified `resulting_ledger_digest` are replayable.
Critic rejection preserves the prior frontier and a planning gap never creates one.
`frontier.json` is only a CAS projection of this replay; deletion or corruption triggers
event-only rebuild with byte-identical canonical payload.
- [x] **Step 10: Implement optimistic commit and bounded restart**
Before append, recheck engagement event revision and canonical revision. Append proposal,
critique, repair/rejection/gap, ledger reconciliation/archive/reactivation, and frontier events
atomically with `expected_revision`; then write rebuildable projections through the dedicated
archive CAS surface. Rebuild/validate the committed ledger, require its digest to equal the
frontier's `resulting_ledger_digest`, and only then publish the frontier/cache under that
post-commit digest; never key a changed reconciliation solely by its pre-call ledger digest.
Permit one full restart. Failure or repeated staleness returns a typed gap and leaves authoritative
evidence unchanged.
- [x] **Step 11: Implement research policy validation**
Allow generic queries about services, versions, errors, protocols, CVEs, and techniques. Reject a
query containing the current display name or aliases together with `walkthrough`, `writeup`,
`solution`, `flag`, `user.txt`, `root.txt`, or equivalent configured terms, and reject a known flag
value. User-suggested sources remain priority hints, not an allowlist. Persist a typed
`research_query_proposed` event for every accepted or rejected candidate with its policy decision;
settlement emits `research_source_consulted` and `research_source_assessed` only from validated
host evidence carrying a normalized locator/source identity and assessment refs. Never infer that
a successful Hades tool return proves the research claim.
- [x] **Step 12: Run service tests GREEN**
Run:

```bash
pytest -q tests/planning/test_service.py \
  tests/planning/test_frontier.py \
  tests/planning/test_commands.py \
  tests/planning/test_ledger.py \
  tests/planning/test_retrieval.py \
  tests/planning/test_prompt_injection.py
```
Expected: PASS.
- [x] **Step 13: Commit Task 12**
```bash
git add -- src/sedna/planning/frontier.py src/sedna/planning/service.py src/sedna/planning/models.py src/sedna/planning/commands.py src/sedna/planning/__init__.py tests/planning/test_frontier.py tests/planning/test_service.py tests/planning/test_prompt_injection.py
git commit -m "feat(planning): orchestrate adaptive frontier"
```

---

### Task 13: Integrate Planning into the Hades Runtime and Plugin Protocol

**Files:**
- Modify: `src/sedna/engagement/hades_adapter.py`
- Modify: `src/sedna/planning/ports.py`
- Modify: `src/sedna/planning/__init__.py`
- Modify: `src/sedna/knowledge/hades_runtime.py`
- Modify: `src/sedna/plugin.py`
- Modify: `plugin.yaml`
- Modify: `docs/llm/sedna-knowledge-tools.md`
- Modify: `README.md`
- Modify: `tests/knowledge/test_hades_runtime.py`
- Modify: `tests/engagement/test_hades_adapter.py`
- Modify: `tests/test_plugin.py`
- Modify: `tests/test_plugin_knowledge.py`
- Create: `tests/test_plugin_planning.py`

**Interfaces:**
- Consumes: M6A registered engagement/decision tools and exact execution-lane kwargs.
- Produces: per-invocation `HadesKnowledgeRuntime.planning`, a lazy
  `PlanningRuntimeFactory`/dynamic `KnowledgeRootResolver`, plugin tool `sedna_plan_next`, completed
  proposal selection/deviation binding, lifecycle settlement wiring, and the Hades protocol
  contract v3.
- [x] **Step 1: Write failing runtime composition tests**
For each settlement or plan invocation, assert one newly opened runtime owns canonical repository,
retrieval index, M6A journal service, and planning service and closes every owned component exactly
once. Construct/register the plugin and M6A adapter with spies and assert neither the dynamic root
resolver nor runtime factory is called and no knowledge directory is created at registration.
Switch the active profile/root between two consecutive settlement calls and between two plan-tool
calls; assert each resolves and mutates only its current root, opens exactly one runtime, and closes
it before returning. The long-lived adapter owns no runtime/service and has no close path. Assert
planner uses the bound host `complete_structured` facade and requires no second provider credential
or daemon. Assert all M6 packages and `plugin.yaml` remain synchronized at product version `0.2.0`;
M6B and M6C do not add another product-version bump.
- [x] **Step 2: Write failing plugin registration and lane tests**
Assert `sedna_plan_next` is declared in `plugin.yaml`, receives the exact bound lane from host
kwargs, rejects missing/ambiguous engagement binding, returns a validated/cached/gap typed result,
and does not expose raw evidence/provider errors. Assert `_PlanNextInput` inherits the rootless
`_ToolInput`, accepts no `knowledge_root`, and obtains the zero-config root only from plugin
context at invocation time. Assert the handler opens one runtime for the resolved root with
`PlanningRuntimeFactory`, performs resolution before opening, delegates once, and exits the
context even on typed failure. Verify a decision recorded in one task cannot select another task's
proposal.
- [x] **Step 3: Write failing non-coercive protocol tests**
Verify an operational action without a proposal remains allowed and journaled as unplanned; the
next plan assesses it. Verify source/model suggestions state `requires_validation`, Hades may
record a `host_adapted` command, and neither Sedna nor a hook executes the suggestion.
- [x] **Step 4: Write failing mandatory-settlement and hook-ownership tests**
Create pending evidence and assert all five M6A/M6B mandatory paths call settlement exactly once
with reasons `plan`, `resume`, `session_finalize`, `close`, and `reopen`. For each M6A adapter path,
block the settlement callback and acquire the engagement repository lock from another thread to
prove the callback runs outside M6A locks. For resume, assert no status/result is serialized until settlement finishes,
then, only for outcome `complete`, reopen and return the reloaded post-settlement snapshot. For
finalize, assert settlement finishes before the final checkpoint event and before logbook rebuild;
any non-complete outcome produces a typed health/finalization result and never renders a falsely
settled logbook.

Attach more than 2 MiB of pending evidence. Assert `PlanningSettlementAdapter` converts
`IncompleteSettlementResult` into M6A's host-neutral
`EngagementSettlementOutcome(status="incomplete")`, carrying the true pending total, next
offset/subject cursor, inventory digest, and `safe_code="evidence_budget_exhausted"` for the 2-MiB cap (otherwise
`interpretation_incomplete`), not a planning model, situation, evidence ID, or private text. Resume
must return a typed incomplete response with no snapshot/lifecycle-status
fields; finalize must not append a clean `session_finalized(reason="finalized")` checkpoint or
render a clean logbook. Repeat for every `FailedSettlementResult`: journal unavailable/corrupt and
`terminal_reconciliation_failed` map to outcome status `unavailable` (the latter with
`settlement_unavailable`); all other closed failures map to `failed`. Both expose only the
mapped safe code and no stale snapshot/status. A later invocation can settle the next bounded
tranche and resume/finalize/close/reopen normally. Close/reopen reload the post-settlement snapshot
and perform their lifecycle CAS only after a complete outcome; non-complete settlement appends no
lifecycle event. M6C later replaces those two host routes with its richer lifecycle service and
reuses the same single settlement rather than calling the port twice. Successful `settled` and
`nothing_pending` map to `EngagementSettlementOutcome(status="complete")`.

Exercise the documented host signatures
`pre_tool_call(tool_name, args, task_id, **kwargs)` and
`post_tool_call(tool_name, args, result, task_id, duration_ms, **kwargs)` with only required fields.
M6A alone owns operational capture/correlation; M6B registers no duplicate operational hooks and
never derives strategic success from `result` or a host return status. The observation extractor
performs interpretation later. `on_session_finalize` is lifecycle orchestration, not an
operational tool executor.
- [x] **Step 5: Run runtime/plugin tests and witness RED**
Run:

```bash
pytest -q tests/knowledge/test_hades_runtime.py \
  tests/engagement/test_service.py \
  tests/engagement/test_hades_adapter.py \
  tests/test_plugin.py \
  tests/test_plugin_knowledge.py \
  tests/test_plugin_planning.py -x
```
Expected: registration/composition failures for the missing planner.
- [x] **Step 6: Implement the one M6A-owned settlement port**
Consume the existing `sedna.engagement.EngagementSettlementPort` and `SettlementReason`; do not
declare a second protocol or enum in planning. The exact reason values are `plan`, `close`,
`verify`, `reject`, `reopen`, `report`, `resume`, and `session_finalize`, and the exact port call
accepts engagement ID plus reason with no per-call `knowledge_root` and returns M6A's exact
host-neutral `EngagementSettlementOutcome`, never `object` or a planning model. Its statuses are
`complete`, `incomplete`, `failed`, and `unavailable`; fields are the true
`pending_range_count >= 0`, `next_pending_offset >= 0 | None`, bounded
`next_pending_subject`, `pending_inventory_sha256`, and a closed `safe_code | None`.
Import M6A's exact `SettlementSafeCode` union: `evidence_budget_exhausted`,
`interpretation_incomplete`, `interpretation_failed`, `journal_unavailable`, `journal_corrupt`, and
`settlement_unavailable`. The M6A validators require: complete = zero with no offset/cursor/digest/code;
incomplete = positive true count plus inventory digest, optional page offset/cursor, and one of the
two incomplete codes; failed = `interpretation_failed` with exact pending metadata when present;
unavailable = zero/no pending metadata plus one of the three unavailable codes. It contains no engagement private data, situation, projection, path,
or planning import.

Define these lazy lifetime ports in `sedna.planning.ports`:

```python
KnowledgeRootResolver = Callable[[], Path]


class PlanningOperations(Protocol):
    def settle_pending_evidence(
        self,
        engagement_id: UUID,
        *,
        reason: SettlementReason,
    ) -> SettlementResult: ...

    def plan_next(
        self,
        lane: ExecutionLaneKey,
        *,
        max_proposals: int = 5,
    ) -> PlanningResult: ...


class OwnedPlanningRuntime(Protocol):
    planning: PlanningOperations


PlanningRuntimeFactory = Callable[
    [Path], AbstractContextManager[OwnedPlanningRuntime]
]


class PlanningSettlementAdapter:
    def __init__(
        self,
        *,
        planning: PlanningOperations,
    ) -> None: ...

    def settle(
        self,
        engagement_id: UUID,
        *,
        reason: SettlementReason,
    ) -> EngagementSettlementOutcome:
        result = self._planning.settle_pending_evidence(
            engagement_id,
            reason=reason,
        )
        if result.status in {"settled", "nothing_pending"}:
            return EngagementSettlementOutcome(
                status="complete",
                pending_range_count=0,
                next_pending_offset=None,
                next_pending_subject=None,
                pending_inventory_sha256=None,
                safe_code=None,
            )
        if result.status == "incomplete":
            return EngagementSettlementOutcome(
                status="incomplete",
                pending_range_count=result.pending_total_count,
                next_pending_offset=min(item.start for item in result.pending_ranges),
                next_pending_subject=result.next_pending_subject,
                pending_inventory_sha256=result.pending_inventory_sha256,
                safe_code=(
                    "evidence_budget_exhausted"
                    if result.incomplete_reason == "budget_exhausted"
                    else "interpretation_incomplete"
                ),
            )
        if result.failure_code in {"journal_unavailable", "journal_corrupt"}:
            return EngagementSettlementOutcome(
                status="unavailable",
                pending_range_count=0,
                next_pending_offset=None,
                next_pending_subject=None,
                pending_inventory_sha256=None,
                safe_code=(
                    "journal_unavailable"
                    if result.failure_code == "journal_unavailable"
                    else "journal_corrupt"
                ),
            )
        if result.failure_code == "terminal_reconciliation_failed":
            return EngagementSettlementOutcome(
                status="unavailable",
                pending_range_count=0,
                next_pending_offset=None,
                next_pending_subject=None,
                pending_inventory_sha256=None,
                safe_code="settlement_unavailable",
            )
        return EngagementSettlementOutcome(
            status="failed",
            pending_range_count=result.pending_total_count,
            next_pending_offset=(
                min(item.start for item in result.pending_ranges)
                if result.pending_ranges
                else None
            ),
            next_pending_subject=result.next_pending_subject,
            pending_inventory_sha256=result.pending_inventory_sha256,
            safe_code="interpretation_failed",
        )


class PlanningSettlementPortFactory:
    def __init__(self, planning_runtime_factory: PlanningRuntimeFactory) -> None: ...

    @contextmanager
    def open(self, resolved_root: Path) -> Iterator[EngagementSettlementPort]:
        with self._planning_runtime_factory(resolved_root) as runtime:
            yield PlanningSettlementAdapter(planning=runtime.planning)
```

The factory stores only the runtime factory and never a resolver/runtime. M6A pins and validates
the active root once per host invocation, then passes that internal path to `open`; the yielded
adapter is bound to exactly that owned runtime and has no resolver. M6A guarantees its Hades
adapter invokes this port for resume, session finalize, close,
and reopen only after its journal context/locks have closed; `PlanningService.plan_next` invokes
the same settlement service with `reason="plan"` outside locks. Refine the adapter ordering so resume settles before serializing
or returning status, while finalize settles before appending a final checkpoint and rebuilding the
logbook. On a non-complete host-neutral outcome, return a typed non-success health result with no
snapshot/lifecycle-status. On `incomplete`, `failed`, or `unavailable`, finalize may record only
the corresponding clearly non-clean checkpoint/logbook state, never `reason="finalized"`. Each
subsequent M6A
read/write uses a newly opened context after the port returns. M6B composition injects the adapter
and adds no duplicate hook or M6A import of planning.
- [x] **Step 7: Add planning to the composition root**
Extend `HadesKnowledgeRuntime` with a public `planning: PlanningService` field. Construct it from
the same bound host LLM, canonical retrieval/revision guard, M6A journal facade, source-registry
snapshot provider, lifecycle/terminal ports, and UTC clock. Preserve close ordering and
idempotence. Provide one composition-owned `PlanningRuntimeFactory` that calls
`HadesKnowledgeRuntime.create(host_llm, resolved_root)`, yields that complete runtime, and closes it
exactly once in `finally`; it must not open a second journal/planning service inside the yielded
runtime. Registration injects `PlanningSettlementPortFactory(planning_runtime_factory)` plus M6A's
dynamic `root_resolver` into the M6A adapter without resolving a root. M6A resolves/pins once and
opens the port factory with that path; the rootless plan-tool handler independently resolves its
own current context root once. No planning tool or settlement protocol method accepts a
caller-supplied root, and no runtime survives an invocation or profile switch. An alternating
resolver regression proves one resume/close/reopen/finalize never crosses stores and the next host
invocation may select the changed profile.
- [x] **Step 8: Register `sedna_plan_next`**
Add strict input:

```python
class _PlanNextInput(_ToolInput):
    max_proposals: int = Field(default=5, ge=3, le=8)
```

The bound handler passes host correlation kwargs into `ExecutionLaneKey` and performs exactly:

```python
root = root_resolver()
with planning_runtime_factory(root) as runtime:
    result = runtime.planning.plan_next(
        lane,
        max_proposals=request.max_proposals,
    )
return serialize_planning_result(result)
```

Serialization happens only after the runtime closes. Add safe tool codes
`engagement_binding_required`, `evidence_budget_exhausted`, `interpretation_incomplete`,
`interpretation_failed`, `settlement_unavailable`, `journal_unavailable`, `planning_failed`, and
`result_too_large`; the last is non-retryable unless a smaller `max_proposals` request is allowed by
policy.
- [x] **Step 9: Complete decision recording compatibility**
Update the M6A `sedna_record_decision` handler schema to accept either exact `proposal_id` or
`custom_strategy` plus rationale, never both. Validate proposal state/cache revision and lane
ownership. Reuse M6A's exact optional `HostAdaptedCommandRecord` field and forward it through the
existing sealed `DecisionRecordedPayload`; Task 13 adds no event type or alternate writer. It
remains private, bounded, `requires_validation`, and is never treated as a Sedna source suggestion.
- [x] **Step 10: Update the host protocol documentation**
Advance `docs/llm/sedna-knowledge-tools.md` to `sedna-knowledge-tools-v3`. Document start/resume,
plan, decision, `/learn` validation, execution, lazy settlement, replan, research, gaps, and
non-coercive deviations. Explicitly state that command examples are suggestions and source cases
are experience, not universal instructions. Document mandatory settlement triggers and that Hades
`/learn`, authorization, approvals, and operational tool execution remain host-owned.
- [x] **Step 11: Run runtime/plugin tests GREEN**
Run:

```bash
pytest -q tests/knowledge/test_hades_runtime.py \
  tests/engagement/test_service.py \
  tests/engagement/test_hades_adapter.py \
  tests/test_plugin.py \
  tests/test_plugin_knowledge.py \
  tests/test_plugin_planning.py
```
Expected: PASS.
- [x] **Step 12: Commit Task 13**
```bash
git add -- src/sedna/engagement/hades_adapter.py src/sedna/planning/ports.py src/sedna/planning/__init__.py src/sedna/knowledge/hades_runtime.py src/sedna/plugin.py plugin.yaml docs/llm/sedna-knowledge-tools.md README.md tests/knowledge/test_hades_runtime.py tests/engagement/test_hades_adapter.py tests/test_plugin.py tests/test_plugin_knowledge.py tests/test_plugin_planning.py
git commit -m "feat(plugin): expose adaptive Sedna planning"
```

---

### Task 14: Prove the Adaptive Machine Path and Controlled Corpus Relearn

**Files:**
- Create: `tests/planning/test_adaptive_engagement.py`
- Create: `tests/planning/simulated_planner.py`
- Create: `tests/planning/fixtures/multi-service-engagement.json`
- Create: `tests/planning/fixtures/adversarial-evidence.json`
- Modify: `docs/llm/sedna-knowledge-tools.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: complete M6A+M6B runtime and scripted host/tool evidence.
- Produces: executable end-to-end acceptance coverage and an exact operator migration/relearn
  procedure for existing corpora.
- [x] **Step 1: Write the end-to-end simulated engagement**
Write `test_adaptive_engagement.py` first so it imports
`from tests.planning.simulated_planner import SimulatedPlanner`, but do not create that module yet.
Its first scenario will drive this sequence without real tools:

```text
authorized HTB-Orion engagement
-> explicit user-flag and root-flag ProofRequirements
-> FTP, SSH, and HTTP evidence
-> information-gathering frontier
-> selected SSH common-credential variant
-> syntax error and corrected execution
-> valid rejection of common credentials
-> complete bounded wordlist failure
-> HTTP/FTP discovery of a credential reference
-> existing SSH credential-reuse variant reactivated
-> source-backed command example suggested
-> Hades host-adapted command recorded
-> user flag evidence settled without terminal completion
-> root flag evidence settled with terminal reconciliation eligible
```

Assert exact strategy IDs survive score/status changes, low-scoring paths remain in ledger/archive,
each score change cites events/knowledge, no unchanged command loops, and planning never invokes an
operational tool. Assert both proof events cite their exact requirement IDs; the first proof alone
does not request terminal success and the second supplies the exact situation to the terminal-port
spy.
- [x] **Step 2: Run the acceptance test and witness a deterministic RED**
Run:

```bash
pytest -q tests/planning/test_adaptive_engagement.py -x
```

Expected: collection fails with `ModuleNotFoundError: tests.planning.simulated_planner`. This RED is
independent of strategy scores, fixture contents, and production behavior.
- [x] **Step 3: Implement the reusable simulated planner harness**
Create `tests/planning/simulated_planner.py` as a thin driver over the real plugin handlers,
engagement service, planning service, scripted `complete_structured` responses, fake clock, and
temporary knowledge root. Give it typed methods for create/resume, append simulated hook evidence,
plan, select/deviate, settle, finalize, inspect events/projections, and inspect terminal-port calls.
It may never shell out, browse, open a socket, emulate SSH semantics, or special-case Orion. Put
all scenario data and expected IDs in the two JSON fixtures; production code receives only normal
typed inputs.
- [x] **Step 4: Add applicability, gap, research, and injection acceptance cases**
Cover Linux rejection of Windows-only cases, unknown architecture, unsupported Android/ADB,
generic technical research, user-suggested source plus an alternative source, rejected
`HTB-Orion walkthrough`/flag queries, hostile terminal/canonical/web instructions, false flag, and
an LLM failure followed by autonomous unplanned Hades action and later recovery. Assert typed
query/source-consulted/source-assessed research events, placeholder-only target commands, unbound
source-case credentials, bounded request sizes, and exact archive reactivation IDs.
- [x] **Step 5: Run the completed acceptance scenario GREEN**
Run:

```bash
pytest -q tests/planning/test_adaptive_engagement.py -x
```

Expected: PASS using the general production reducers and service composition; no fixture-specific
production branch exists.
- [x] **Step 6: Add the progressive corpus relearn procedure**
Document the split contract: the `2.5.0`/prompt-v2 bump makes prior bundles stale for learning, but
an exact valid `2.4.0` bundle remains strategically retrievable before relearn. SQLite-v5 rebuild
indexes its artifacts, zero examples, and a typed capability gap. Operators may call
`sedna_learn_local` progressively on each available original root, then run
`sedna_knowledge_maintenance` with `rebuild` and `audit`; there is no all-corpus outage. If original
bytes are unavailable, keep that source as strategic-only knowledge and never fabricate or
source-attribute a command from old canonical intent.

Include the exact normal plugin request:

```json
{
  "tool": "sedna_learn_local",
  "arguments": {
    "source_path": "/absolute/path/to/the/original/writeup-corpus"
  }
}
```
- [x] **Step 7: Verify progressive migration in an isolated corpus fixture**
Seed two old bundles. Rebuild first and assert both are strategically retrievable, their locators
are empty, and drill-down reports `legacy_bundle_without_examples`. Relearn only the source whose
raw bytes exist; assert it gains verified examples while the other remains strategic-only. Relearn
the available source again and assert `unchanged` with no host calls. Audit passes in this mixed-
version state.
- [ ] **Step 8: Run M6B and full regression verification**
Run:

```bash
pytest -q tests/planning
pytest -q tests/knowledge
pytest -q
ruff check src/sedna tests
ruff format --check src/sedna tests
git diff --check
git status --short
```
Expected: all tests pass, Ruff and formatting pass, diff check is clean, and only intentional M6B
source/test/documentation files are modified.
- [x] **Step 9: Commit Task 14**
```bash
git add -- tests/planning/test_adaptive_engagement.py tests/planning/simulated_planner.py tests/planning/fixtures/multi-service-engagement.json tests/planning/fixtures/adversarial-evidence.json docs/llm/sedna-knowledge-tools.md README.md
git commit -m "test(planning): verify adaptive engagement flow"
```

---

## Final Verification

- [x] Confirm M6A public names and method signatures still match the dependency block before Task
  1 implementation begins.
- [x] Confirm exact legacy bundles remain strategically retrievable before relearn, expose a typed
  source-command coverage gap, recompile exactly once when their source is relearned, and undergo
  no in-place canonical migration.
- [x] Confirm command templates, example lookup, SQLite canonical artifact JSON, and FTS satisfy
  the non-searchable execution-example boundary.
- [x] Confirm each current execution example carries source-cited prerequisites, applicability,
  and platform constraints, and all three participate in canonical identity and critic review.
- [x] Confirm every current target in a suggestion resolves from a typed authorized scope ref and
  every source-case credential remains unbound.
- [x] Confirm raw private evidence may retain flags while retrieval, canonical promotion inputs,
  and command examples exclude final flag values.
- [x] Confirm proof/secret authority contains only locally verified candidate-only evidence slices;
  a hallucinated inline flag emits no supported proof/auto-close, and trusted M6C reads, hashes,
  then symbolizes the exact bytes before promotion.
- [x] Confirm observation, planner, critic, and repair calls use only structured bounded payloads
  and safe call metadata.
- [x] Confirm no lock is held during evidence IO, retrieval, LLM calls, or command rendering.
- [x] Confirm `plan`, `resume`, `session_finalize`, `close`, and `reopen` settle exactly once through
  the one M6A-owned port outside
  journal locks, with no caller-supplied root argument and no duplicate operational hooks; dynamic
  profile changes resolve a fresh root/runtime per invocation and close it exactly once.
- [x] Confirm M6B maps every private settlement variant to M6A's exact six-code host-neutral
  `EngagementSettlementOutcome`, and >2-MiB incomplete resume/finalize exposes no stale success.
- [x] Confirm `engagement-state.json`, SituationProjection-only `state.json`, and CAS-protected
  `strategy-archive.jsonl` remain distinct, descriptor-confined projection surfaces.
- [x] Delete `state.json`, `frontier.json`, `strategy-ledger.json`, and
  `strategy-archive.jsonl` in an isolated test engagement and confirm events alone rebuild
  byte-identical projections, including full proposals/commands/retry predicates/hot/cold state.
- [x] Confirm every repeated frontier/reconciliation/archive/reactivation batch is atomic,
  ordinal-complete, at most 511 planning events with each fully materialized envelope within the
  M6A 64-KiB limit, and fails closed rather
  than truncating a record or splitting a logical transaction across commits.
- [x] Confirm empty/partial proof requirements cannot auto-close, all explicit proofs invoke the
  terminal seam, and later contradictory evidence can cancel only a proof-driven closing state.
- [x] Confirm plan reloads lifecycle after terminal reconciliation and performs zero retrieval,
  LLM, cache publication, or event append when the engagement is closing/closed/abandoned.
- [x] Confirm rejected proof generations retain immutable history, a newly grounded proof can
  support the new generation only with a never-rejected digest, explicit reopen advances all
  requirements to pending, and stale/same-rejected-digest events never reclose. Confirm more than
  32 distinct rejected values rebuild the exact newest-32 tuple plus overflow count/digest, and an
  overflow membership decision always replays authoritative rejection records.
- [x] Confirm two distinct attachment/tool completions with identical `EvidenceId` bytes retain two
  interpretation subjects and two exact outcome/attempt links after event-only replay.
- [x] Confirm the 8/256 attempt, 64-event/64-KiB text, 512-KiB request, and 32-KiB × 64 evidence
  bounds fail or continue explicitly without silent data loss.
- [x] Confirm planner reconciliation cannot silently discard a hot or selected archived strategy.
- [x] Confirm a second critic rejection, stale concurrent state, and unavailable journal each
  produce distinct typed gaps without publishing a false frontier.
- [x] Confirm the cache invalidates independently on situation, ledger, canonical corpus,
  `sources.md`, prompt, schema, research-policy, and command-policy changes.
- [x] Confirm checkpoint-only authoritative revision changes reuse cache with authority rebound only
  in the typed result, and ledger-changing reconciliation caches against its verified resulting
  digest while retaining the input digest for audit.
- [x] Confirm Hades remains free to deviate, `/learn` remains authoritative for exact tool
  operation, and no M6B test or production path invokes an operational tool.
- [x] Confirm bounded shared-source records and research query/consulted/assessed events remain
  structured untrusted data, and product/plugin versions remain synchronized at `0.2.0`.
- [ ] Confirm full suite, Ruff, format, diff, and clean-status checks pass before M6B integration.
