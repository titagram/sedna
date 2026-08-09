# Sedna Autonomous Learning and Hades Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Hades ask Sedna to learn one local document or folder autonomously through the host LLM, make the verified strategic knowledge immediately retrievable through plugin tools, and provide a precise LLM-facing operating guide.

**Architecture:** A `DocumentLearningService` composes the existing deterministic `IngestionPipeline`, `SemanticIngestionService`, and `RetrievalMaintenanceService`. It owns a complete, bounded run report, while canonical repository transitions remain owned by their existing services. The Hades plugin only adapts `ctx.llm` to the already-versioned structured compiler contract and serializes typed, safe results; it neither handles provider keys nor parses raw source content itself.

**Tech Stack:** Python 3.11–3.13, Pydantic 2.13.4, existing Hades structured LLM facade, stdlib pathlib/JSON, existing canonical JSON repository and SQLite FTS5 retrieval index. No new network client, vector store, or provider SDK.

## Global Constraints

- The approved semantic-ingestion design at `docs/superpowers/specs/2026-08-07-sedna-semantic-ingestion-retrieval-design.md` is authoritative.
- A local source may be one regular `.md`/`.pdf` file or one directory; network fetching is not part of this milestone. Hades may save authorized web material locally and pass that local path through the identical pipeline.
- Source content is untrusted data. It crosses the LLM boundary only through `SafePreparedSourcePayload`; no raw source, credential, final flag, raw response, provider secret, or path outside the selected root may be exposed.
- The deterministic foundation owns classification, sanitization, prepared-source currentness, and source dispositions. Semantic compilation owns verified/quarantined semantic state. The learning service must not duplicate either policy.
- Every inventoried candidate gets exactly one run disposition: `verified`, `semantic_quarantined`, `excluded`, `foundation_quarantined`, `unchanged`, or `failed`.
- One malformed document, LLM transport failure, or semantic compiler failure never aborts another document in the same requested folder.
- Idempotence is observable: an unchanged deterministic and semantic version state makes zero host LLM calls and does not duplicate artifacts; version changes use existing controlled reprocessing.
- Rebuild the disposable index only from canonical verified bundles after the folder run. A rebuild failure preserves canonical records and is represented in the report.
- Plugin handlers accept only JSON-object inputs with bounded strings and return JSON made from deep-validated typed models. Raw exception text is never returned.
- Retrieval validates invalid targets before opening SQLite and preserves the existing authorization, lane, gap, and hard-incompatibility semantics.
- Sedna remains strategic. Tool-operation syntax remains in Hades `/learn` skills.

---

### Task 1: Add Typed Autonomous Learning Orchestration

**Files:**
- Create: `src/sedna/knowledge/learning.py`
- Modify: `src/sedna/knowledge/__init__.py`
- Create: `tests/knowledge/test_learning.py`

**Interfaces:**
- Consumes: `discover_sources(path)`, `IngestionPipeline.prepare(candidate)`, `IngestionPipeline.last_outcome`, `SemanticIngestionService.compile_and_store(prepared)`, and `RetrievalMaintenanceService.rebuild()`.
- Produces: `LearningDisposition`, `LearningSourceOutcome`, `LearningRunReport`, and `DocumentLearningService.learn(source_path: Path) -> LearningRunReport`.

- [ ] **Step 1: Write failing learning-service tests**

```python
def test_learn_folder_compiles_verified_sources_then_rebuilds(tmp_path, scripted_semantic_service):
    source_root = _write_source_folder(tmp_path, {"lesson.md": _lesson_markdown()})
    service = _learning_service(source_root, scripted_semantic_service)

    report = service.learn(source_root)

    assert report.source_path == str(source_root.resolve())
    assert [item.disposition for item in report.outcomes] == ["verified"]
    assert report.verified_source_count == 1
    assert report.index_report is not None and report.index_report.succeeded


def test_unchanged_folder_run_makes_no_llm_calls_and_keeps_one_artifact_set(...):
    first = service.learn(source_root)
    calls_after_first = host.call_count
    second = service.learn(source_root)

    assert first.verified_source_count == 1
    assert second.unchanged_source_count == 1
    assert host.call_count == calls_after_first


def test_one_semantic_failure_does_not_abort_other_documents(...):
    report = service.learn(source_root)
    assert {item.disposition for item in report.outcomes} == {"verified", "failed"}
    assert report.index_report is not None and report.index_report.succeeded
```

- [ ] **Step 2: Run the tests to verify RED**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/knowledge/test_learning.py`

Expected: collection fails because `sedna.knowledge.learning` does not exist.

- [ ] **Step 3: Implement bounded report contracts and source selection**

```python
class LearningDisposition(StrEnum):
    VERIFIED = "verified"
    SEMANTIC_QUARANTINED = "semantic_quarantined"
    EXCLUDED = "excluded"
    FOUNDATION_QUARANTINED = "foundation_quarantined"
    UNCHANGED = "unchanged"
    FAILED = "failed"


class DocumentLearningService:
    def learn(self, source_path: Path) -> LearningRunReport:
        root, only_relative_path = _resolve_learning_root(source_path)
        with IngestionPipeline(root, self.knowledge_root) as pipeline:
            for candidate in _select_candidates(discover_sources(root), only_relative_path):
                self._learn_candidate(pipeline, candidate, outcomes)
        index_report = self._maintenance.rebuild()
        return LearningRunReport(...)
```

`_resolve_learning_root()` must resolve exactly one existing directory or a regular non-symlink source file. For a file, use its parent as the deterministic source root and retain only its POSIX-relative candidate. Reject any other suffix, FIFO, device, socket, and missing path before inventory. `LearningRunReport` must be frozen, forbid extras, sort outcomes by source ID, bound outcome/message/domain counts, derive exact counters, and carry only safe compilation/maintenance reason codes—not source text or exception strings.

- [ ] **Step 4: Implement the per-candidate state machine**

```python
def _learn_candidate(self, pipeline, candidate, outcomes) -> None:
    try:
        prepared = pipeline.prepare(candidate)
        if prepared is None:
            outcomes.append(_foundation_outcome(candidate, pipeline.last_outcome))
            return
        semantic = self.semantic_service.compile_and_store(prepared)
        outcomes.append(_semantic_outcome(candidate, semantic))
    except (CandidateIngestionError, OSError, ValueError):
        outcomes.append(_failed_outcome(candidate, "source_processing_failed"))
```

Map deterministic `excluded` and `quarantined` unchanged from `pipeline.last_outcome`; invoke semantic compilation only for a newly prepared source; map a semantic `verified`/`unchanged`/`quarantined`/`failed` result without rewriting it. Call maintenance once after all candidates, including a no-op folder run, so an absent/stale disposable index is repaired. If no candidate was discovered or the final rebuild fails, report it safely rather than manufacture a verified outcome.

- [ ] **Step 5: Add adversarial and incremental tests**

```python
def test_single_file_is_confined_to_parent_and_never_learns_sibling(...): ...
def test_excluded_and_foundation_quarantine_are_reported_without_llm(...): ...
def test_semantic_quarantine_is_persisted_and_reported(...): ...
def test_invalid_root_and_nonregular_input_return_typed_failed_report(...): ...
def test_hidden_model_copy_report_state_is_rejected(...): ...
def test_index_rebuild_failure_does_not_change_semantic_bundle_bytes(...): ...
```

Exercise source disappearance/replacement during inventory, a source path inside the canonical knowledge root, semantic compiler transport failure, two unchanged runs, a compiler-version migration run, and a corrupt canonical repository that makes rebuild fail without losing source accounting.

- [ ] **Step 6: Verify and commit Task 1**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/knowledge/test_learning.py tests/knowledge/test_pipeline.py tests/knowledge/test_semantic_service.py tests/knowledge/test_retrieval_maintenance.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/knowledge
.venv/bin/ruff check src/sedna/knowledge tests/knowledge
.venv/bin/ruff format --check src/sedna/knowledge/learning.py tests/knowledge/test_learning.py
git diff --check
git add src/sedna/knowledge/learning.py src/sedna/knowledge/__init__.py tests/knowledge/test_learning.py
git commit -m "feat(knowledge): learn local documents autonomously"
```

---

### Task 2: Build the Hades Host-LLM Runtime Factory

**Files:**
- Create: `src/sedna/knowledge/hades_runtime.py`
- Modify: `src/sedna/knowledge/__init__.py`
- Create: `tests/knowledge/test_hades_runtime.py`

**Interfaces:**
- Consumes: a structural host object implementing `complete_structured`, `HadesLlmAdapter`, `SemanticCompiler`, `SemanticIngestionService`, `DocumentLearningService`, `CanonicalKnowledgeRepository`, `SQLiteRetrievalIndex`, `RetrievalMaintenanceService`, and `KnowledgeRetrievalService`.
- Produces: `HadesKnowledgeRuntime.create(host_llm, knowledge_root) -> HadesKnowledgeRuntime` with `.learning`, `.retrieval`, `.maintenance`, and deterministic `.close()`.

- [ ] **Step 1: Write failing runtime tests**

```python
def test_runtime_uses_host_llm_without_provider_configuration(tmp_path):
    host = _RecordingStructuredHost(_accepted_responses())
    runtime = HadesKnowledgeRuntime.create(host, tmp_path / "knowledge")

    report = runtime.learning.learn(_source_root(tmp_path))

    assert report.verified_source_count == 1
    assert [call["purpose"] for call in host.calls] == [
        "sedna.semantic.extract", "sedna.semantic.critic",
    ]


def test_runtime_closes_index_and_repository_idempotently(tmp_path): ...
def test_runtime_rejects_host_without_complete_structured_before_reading_sources(tmp_path): ...
```

- [ ] **Step 2: Run RED**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/knowledge/test_hades_runtime.py`

Expected: collection failure for the absent runtime module.

- [ ] **Step 3: Implement one ownership boundary for the runtime**

```python
@dataclass(slots=True)
class HadesKnowledgeRuntime:
    learning: DocumentLearningService
    retrieval: KnowledgeRetrievalService
    maintenance: RetrievalMaintenanceService
    _index: SQLiteRetrievalIndex

    @classmethod
    def create(cls, host_llm: object, knowledge_root: Path) -> "HadesKnowledgeRuntime":
        adapter = HadesLlmAdapter(_require_structured_host(host_llm))
        repository = CanonicalKnowledgeRepository(knowledge_root)
        compiler = SemanticCompiler(adapter, clock=lambda: datetime.now(UTC))
        semantic = SemanticIngestionService(repository, compiler)
        index = SQLiteRetrievalIndex(knowledge_root / "indexes" / "retrieval.sqlite")
        maintenance = RetrievalMaintenanceService(repository, index)
        return cls(DocumentLearningService(...), KnowledgeRetrievalService(index), maintenance, index)
```

Validate `knowledge_root` as an existing/creatable owned directory, never inside the selected source root (the learning service enforces the pair), and create no provider client or credential configuration. The only acceptable host method is the existing structured facade; raw chat/completion methods must fail closed before inventory. Close the index and repository exactly once even when a later constructor fails.

- [ ] **Step 4: Add lifetime, currentness, and failure tests**

```python
def test_runtime_second_learn_run_reuses_semantics_without_host_calls(...): ...
def test_runtime_compiler_version_change_reinvokes_host_once(...): ...
def test_runtime_host_transport_failure_is_one_failed_source_not_a_raw_exception(...): ...
def test_runtime_index_path_is_disposable_and_rebuildable(...): ...
```

Ensure emitted canonical compilation metadata retains prompt/model/version attribution and that the runtime never turns a semantic failure into a successful indexed artifact.

- [ ] **Step 5: Verify and commit Task 2**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/knowledge/test_hades_runtime.py tests/knowledge/test_learning.py tests/knowledge/test_semantic_llm.py tests/knowledge/test_retrieval_service.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/knowledge
.venv/bin/ruff check src/sedna/knowledge tests/knowledge
.venv/bin/ruff format --check src/sedna/knowledge/hades_runtime.py tests/knowledge/test_hades_runtime.py
git diff --check
git add src/sedna/knowledge/hades_runtime.py src/sedna/knowledge/__init__.py tests/knowledge/test_hades_runtime.py
git commit -m "feat(knowledge): compose Hades learning runtime"
```

---

### Task 3: Expose Safe Hades Plugin Learn, Retrieve, Artifact, and Maintenance Tools

**Files:**
- Modify: `src/sedna/plugin.py`
- Modify: `plugin.yaml`
- Modify: `tests/test_plugin.py`
- Create: `tests/test_plugin_knowledge.py`

**Interfaces:**
- Consumes: `ctx.llm`, `HadesKnowledgeRuntime`, `LearningRunReport`, `RetrievalQuery`, `CurrentSituation`, `AuthorizationScope`, and `KnowledgeRetrievalService`.
- Produces plugin tools `sedna_learn_local`, `sedna_retrieve_knowledge`, `sedna_get_knowledge_artifact`, and `sedna_knowledge_maintenance`.

- [ ] **Step 1: Write RED plugin registration and handler tests**

```python
def test_plugin_registers_knowledge_tools_when_context_has_structured_llm():
    context = _FakeContext(llm=_ScriptedHost(_accepted_responses()), knowledge_root=_knowledge_root())
    register(context)
    assert {tool["name"] for tool in context.tools} >= {
        "sedna_learn_local", "sedna_retrieve_knowledge",
        "sedna_get_knowledge_artifact", "sedna_knowledge_maintenance",
    }


def test_learn_tool_accepts_one_folder_and_returns_typed_safe_report(...): ...
def test_retrieve_invalid_ip_returns_invalid_target_without_opening_index(...): ...
def test_retrieve_android_adb_absence_returns_document_and_research_gap(...): ...
def test_handlers_never_return_raw_host_or_filesystem_exception_text(...): ...
```

- [ ] **Step 2: Run RED**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/test_plugin.py tests/test_plugin_knowledge.py`

Expected: existing registration lacks all four knowledge tools.

- [ ] **Step 3: Define a narrow, explicit tool JSON surface**

```python
def _learn_local_handler(args: dict[str, Any], *, ctx: Any) -> str:
    runtime = _runtime_for_context(ctx, args)
    report = runtime.learning.learn(Path(_required_string(args, "source_path", 4096)))
    return _json_model(report)


def _retrieve_handler(args: dict[str, Any], *, ctx: Any) -> str:
    query = RetrievalQuery.model_validate(_query_payload(args))
    return _json_model(_runtime_for_context(ctx, args).retrieval.retrieve(query))
```

`sedna_learn_local` takes `source_path` and an optional explicitly configured `knowledge_root`; if omitted it requires a context-owned `sedna_knowledge_root` path outside the source root. Do not default to a directory within the raw source tree. `sedna_retrieve_knowledge` takes a target, explicit authorized scope, observed terms/services/facts, query terms/facets, and bounded lane/candidate limits. It must construct the existing typed query rather than accepting opaque free-form situation JSON. `sedna_get_knowledge_artifact` takes an exact artifact ID. `sedna_knowledge_maintenance` takes `operation: audit|rebuild`.

Handlers must use a per-call runtime context manager, return only `model_dump(mode="json")` of validated results, and map invalid input/runtime construction/tool failures to a stable `{"ok": false, "error": "..."}` vocabulary with no path, model, raw response, or exception disclosure. Existing Nmap tools remain behaviorally unchanged.

- [ ] **Step 4: Add end-to-end plugin tests**

```python
def test_plugin_learn_then_retrieve_uses_verified_semantics_from_same_knowledge_root(...): ...
def test_plugin_second_learn_is_unchanged_with_no_new_host_calls(...): ...
def test_plugin_mixed_good_bad_folder_reports_all_dispositions(...): ...
def test_plugin_maintenance_audit_and_rebuild_are_safe_and_typed(...): ...
def test_plugin_rejects_missing_structured_llm_before_source_inventory(...): ...
```

Assert no final flag/raw credential/provider secret appears in any serialized tool response; assert a direct artifact lookup carries exact canonical provenance but no hidden model state; assert invalid IP calls no SQLite search; assert an unauthorized scope returns the existing `unauthorized_scope` gap.

- [ ] **Step 5: Update manifest and verify/commit Task 3**

```yaml
provides_tools:
  - sedna_nmap_tcp_discovery
  - sedna_nmap_service_scan
  - sedna_learn_local
  - sedna_retrieve_knowledge
  - sedna_get_knowledge_artifact
  - sedna_knowledge_maintenance
```

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/test_plugin.py tests/test_plugin_knowledge.py tests/knowledge/test_hades_runtime.py tests/knowledge/test_learning.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
.venv/bin/ruff check src/sedna tests
.venv/bin/ruff format --check src/sedna/plugin.py tests/test_plugin.py tests/test_plugin_knowledge.py
git diff --check
git add src/sedna/plugin.py plugin.yaml tests/test_plugin.py tests/test_plugin_knowledge.py
git commit -m "feat(plugin): expose autonomous Sedna learning"
```

---

### Task 4: Document the LLM Contract and Prove the Minimum Learned-Knowledge Demo

**Files:**
- Create: `docs/llm/sedna-knowledge-tools.md`
- Modify: `README.md`
- Create: `tests/knowledge/test_learning_demo.py`
- Modify: `tests/test_plugin_knowledge.py`

**Interfaces:**
- Consumes real plugin tool JSON, local source fixtures, scripted structured host responses, and the M3 retrieval service.
- Produces a versioned, granular LLM operating guide and an executable M5 hypothetical demonstration.

- [ ] **Step 1: Write RED end-to-end demo tests**

```python
def test_hypothetical_private_ip_answer_is_source_backed_and_conditional(...):
    _learn_fixture_folder(context)
    response = _call_tool(context, "sedna_retrieve_knowledge", _authorized_ip_query())
    assert response["references"]
    assert response["case_steps"]
    assert response["negative_cases"]
    assert response["rejected_candidates"]


def test_invalid_ip_demo_never_queries_index(...): ...
def test_android_adb_demo_returns_gap_and_offers_local_docs_or_technical_research(...): ...
def test_artifact_drill_down_returns_exact_provenance_for_llm_citation(...): ...
```

- [ ] **Step 2: Run RED**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/knowledge/test_learning_demo.py`

Expected: tests fail until the plugin tool surface and demo fixtures exist.

- [ ] **Step 3: Write the granular LLM guide**

Create `docs/llm/sedna-knowledge-tools.md` with exact sections and JSON examples for:

1. when to call `sedna_learn_local` (local folder/file only; web material must first be saved locally; do not ask for flags or named-machine solutions);
2. how the LLM supplies authorized scope and observed context to `sedna_retrieve_knowledge`;
3. how to interpret each evidence lane, score/rejection, qualification, missing-context question, and gap code;
4. how to use `sedna_get_knowledge_artifact` for exact provenance rather than inventing detail;
5. how to answer with conditional strategic observations, not exact tool syntax or unsupported certainty;
6. how to distinguish `no_applicable_knowledge`, `retrieval_unavailable`, `invalid_target`, and `unauthorized_scope`;
7. the exact idempotence/version behavior and how to run audit/rebuild;
8. source credential example semantics, final-flag prohibition, case-study adaptation, and web-research boundaries.

Every JSON example must use fictional safe values, contain no credential-looking current secret, and be validated by an extraction test that checks tool names, gap codes, and no final flag marker.

- [ ] **Step 4: Update README and add doc/example tests**

```python
def test_llm_guide_examples_name_only_registered_tools_and_closed_gap_codes(): ...
def test_llm_guide_never_claims_case_studies_are_universal_instructions(): ...
def test_tool_demo_contains_no_flag_or_raw_source_leak(): ...
```

README must state that M4 supplies local file/folder learning through the host LLM, that documents are classified and verified automatically, that compilation is idempotent, and that direct remote fetching remains outside Sedna’s local learning tool.

- [ ] **Step 5: Verify and commit Task 4**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/knowledge/test_learning_demo.py tests/test_plugin_knowledge.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/knowledge
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
.venv/bin/ruff check src/sedna tests
.venv/bin/ruff format --check src/sedna tests
git diff --check
git status --short
git add docs/llm/sedna-knowledge-tools.md README.md tests/knowledge/test_learning_demo.py tests/test_plugin_knowledge.py
git commit -m "docs(knowledge): guide LLM autonomous learning"
```

---

## Final Verification

- [ ] `sedna_learn_local` learns one supplied local Markdown file and a folder using the host structured LLM, with no human approval step.
- [ ] A mixed folder reports every source disposition and one bad source does not stop verified sources.
- [ ] A second identical learning run makes no host LLM call and does not duplicate canonical or indexed artifacts.
- [ ] A controlled compiler-version change reprocesses once and then returns unchanged.
- [ ] The index is rebuilt only from verified canonical semantics; index/rebuild failure never mutates canonical data.
- [ ] `sedna_retrieve_knowledge` validates `300.456.456.123` before any backend call, separates lanes, excludes known incompatible knowledge, and returns a truthful Android/ADB gap when absent.
- [ ] Exact artifact lookup gives provenance sufficient for a downstream LLM citation without exposing raw source, flag, provider secret, or hidden state.
- [ ] `docs/llm/sedna-knowledge-tools.md` is current, granular, and its examples are test-checked.
- [ ] Full suite, scoped Ruff, changed-since-`85aac46` format, diff/status checks are clean; no SQLite database is tracked; no Elasticsearch/vector dependency was added.
