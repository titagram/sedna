# Sedna Local Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` to implement this plan task-by-task.

**Goal:** Project verified semantic bundles into a disposable SQLite FTS5/facet index and return
precise, explainable, lane-separated strategic knowledge or a typed knowledge gap for a validated
current situation.

**Architecture:** Canonical JSON bundles remain the source of truth. A backend-neutral retrieval
contract accepts typed situations and queries. `SQLiteRetrievalIndex` stores a rebuildable
relational/FTS5 projection, while deterministic applicability and epistemic scoring run outside the
SQL query. Retrieval never overrides hard incompatibilities. Invalid targets are rejected before
index access; insufficient applicable evidence returns a typed knowledge gap.

**Tech Stack:** Python 3.11–3.13 standard-library `sqlite3`/FTS5 and `ipaddress`, Pydantic 2.13.4,
pytest 8, Ruff 0.15.10. No Elasticsearch, embeddings, vector database, or background service.

## Global Constraints

- Canonical semantic bundles, never SQLite, are the source of truth.
- Index writes consume only strictly validated canonical artifacts and searchable fields.
- Full rebuild is possible after deleting the database.
- References, successful case steps, negative evidence, and decision guidance remain separate
  epistemic lanes; scores from different lanes are never compared globally.
- Known hard incompatibilities exclude a candidate. Unknown required context lowers applicability
  and is reported, never treated as a wildcard.
- Every hit includes exact artifact identity, provenance, score components, matched facets, and
  rejection/qualification reasons.
- Deterministic ordering breaks equal scores by stable artifact ID.
- Invalid target identifiers are rejected before opening/querying SQLite.
- No result above threshold produces an explicit typed knowledge gap.
- Model/vector search remains behind a measured future evaluation gate.

## File Structure

```text
src/sedna/knowledge/retrieval/
├── __init__.py       # public retrieval surface
├── models.py         # situations, queries, lanes, hits, gaps, audits
├── projection.py     # canonical artifact -> normalized rows/search text
├── sqlite.py         # disposable FTS5/facet index
├── ranking.py        # hard compatibility and explainable scoring
├── service.py        # validated lane-aware retrieval API
└── maintenance.py    # repository rebuild/audit orchestration
```

---

### Task 1: Define Backend-neutral Retrieval Contracts

**Files:**
- Create: `src/sedna/knowledge/retrieval/models.py`
- Create: `src/sedna/knowledge/retrieval/__init__.py`
- Create: `tests/knowledge/test_retrieval_models.py`

**Interfaces:**
- Produces: `CurrentSituation`, `ValidatedTarget`, `SituationFacet`, `RetrievalQuery`,
  `EpistemicLane`, `ScoreComponents`, `RetrievalHit`, `RejectedCandidate`, `KnowledgeGap`,
  `RetrievalResult`, `IndexAudit`, and runtime-checkable `RetrievalIndex` protocol.
- Consumes: canonical artifact/source/context enums and models.

- [ ] Write RED schema tests for IPv4/IPv6/hostname/URL validation, including rejection of
  `300.456.456.123`; bounded normalized terms/facets; deterministic unique situation facts;
  lane-exclusive result shapes; closed gap codes; finite score components; exact provenance.
- [ ] Model live situation facets separately from source-backed `ContextAssertion`: live facts have
  namespace/key/value/confidence but do not invent source references.
- [ ] Define retrievable artifacts as reference, case, case step, and decision-rule unions.
- [ ] Make invalid targets representable as a typed result while preserving the guarantee that no
  backend method is called.
- [ ] Run model/schema tests and commit.

Commit: `feat(knowledge): define retrieval contracts`

---

### Task 2: Build Deterministic Canonical Artifact Projections

**Files:**
- Create: `src/sedna/knowledge/retrieval/projection.py`
- Create: `tests/knowledge/test_retrieval_projection.py`

**Interfaces:**
- Produces: `ProjectedArtifact`, `ProjectedFacet`, `ProjectedSource`, and
  `project_semantic_bundle(bundle) -> tuple[ProjectedArtifact, ...]`.
- Consumes: strict `SemanticKnowledgeBundle`.

- [ ] Write RED tests for references, parent cases, individual case steps, negative cases, and
  decision rules. Nested case steps receive their own retrievable ID and a parent-case link.
- [ ] Materialize the logical table fields from the approved design: artifact type/role,
  verification, source/extraction/generalizability/context dimensions, observation time, canonical
  JSON, normalized FTS statement/rationale/observations/action/expected-evidence/exceptions.
- [ ] Flatten typed context and extensible facets with relation/origin/confidence while preserving
  source refs and independence groups.
- [ ] Require deterministic unique IDs/rows and reject any unsafe or internally inconsistent
  canonical bundle through deep primitive revalidation.
- [ ] Run projection/schema tests and commit.

Commit: `feat(knowledge): project canonical retrieval rows`

---

### Task 3: Implement SQLite FTS5 Index, Rebuild, and Audit

**Files:**
- Create: `src/sedna/knowledge/retrieval/sqlite.py`
- Create: `tests/knowledge/test_retrieval_sqlite.py`

**Interfaces:**
- Produces: `SQLiteRetrievalIndex` implementing `RetrievalIndex`.
- Operations: `upsert_bundle`, `delete_source`, `rebuild`, `get_artifact`, `search_candidates`,
  `audit`, and `close`/context-manager lifecycle.

- [ ] Write RED tests for schema creation with FTS5, deterministic artifact/facet/source/link
  rows, transactional source-scoped upsert/delete, exact ID lookup, text search, facet prefilter,
  equal-score ordering, and connection lifecycle.
- [ ] Build normalized tables `artifacts`, `facet_values`, `artifact_links`, `artifact_sources` and
  `artifact_fts`; enforce foreign keys and indexes.
- [ ] Make `upsert_bundle` atomic: delete prior rows for the source and insert the complete new
  projection in one transaction. A failure leaves the prior projection intact.
- [ ] Build complete rebuild into a temporary sibling database, close/check/fsync it, then replace
  the disposable live index. Failure preserves the previous live database.
- [ ] Reject symlink/FIFO database targets and never index non-canonical/search-unsafe values.
- [ ] Audit integrity, orphan rows, canonical JSON validation, source/provenance coverage, FTS row
  parity, duplicate IDs, and deterministic counts.
- [ ] Run SQLite/projection tests and commit.

Commit: `feat(knowledge): add SQLite retrieval index`

---

### Task 4: Add Applicability Filtering and Explainable Lane Ranking

**Files:**
- Create: `src/sedna/knowledge/retrieval/ranking.py`
- Create: `tests/knowledge/test_retrieval_ranking.py`

**Interfaces:**
- Produces: `rank_candidates(query, candidates) -> RankedCandidates` and deterministic lane
  classification.

- [ ] Write RED tests for Windows-required vs known Linux rejection, explicit incompatible facet
  rejection, unknown required OS penalty/question, compatible service matches, verified vs
  contested evidence, negative-case lane separation, and stable tie ordering.
- [ ] Combine normalized FTS relevance, required-facet coverage, contextual similarity,
  verification/source/extraction/generalizability dimensions, version-sensitive freshness, unknown
  penalties, and source-independence diversity into bounded named `ScoreComponents`.
- [ ] Hard incompatibility produces a `RejectedCandidate` with exact reasons and can never be
  rescued by lexical score.
- [ ] Keep lane thresholds independent and return missing prerequisite/context questions.
- [ ] Run ranking/projection tests and commit.

Commit: `feat(knowledge): rank applicable retrieval lanes`

---

### Task 5: Implement Retrieval Service and Typed Knowledge Gaps

**Files:**
- Create: `src/sedna/knowledge/retrieval/service.py`
- Modify: `src/sedna/knowledge/retrieval/__init__.py`
- Modify: `src/sedna/knowledge/__init__.py`
- Create: `tests/knowledge/test_retrieval_service.py`

**Interfaces:**
- Produces: `KnowledgeRetrievalService.retrieve(query) -> RetrievalResult` and
  `get_artifact(artifact_id)`.

- [ ] Write RED tests proving invalid target rejection before any index call, bounded candidate
  fetch, separate lane limits, score explanations, rejected incompatibilities, exact provenance,
  and deterministic output.
- [ ] Return gap `no_applicable_knowledge` when nothing crosses lane thresholds; include observed
  domain, missing context, suggested document ingestion, and research eligibility without
  inventing operational advice.
- [ ] Return an Android/ADB gap against a corpus with no qualifying artifacts.
- [ ] Preserve negative evidence beside positive analogies rather than filtering it out.
- [ ] Run service/ranking/index tests and commit.

Commit: `feat(knowledge): expose lane-aware retrieval`

---

### Task 6: Rebuild and Audit from the Canonical Repository

**Files:**
- Modify: `src/sedna/knowledge/repository.py`
- Create: `src/sedna/knowledge/retrieval/maintenance.py`
- Create: `tests/knowledge/test_retrieval_maintenance.py`

**Interfaces:**
- Produces: strict `iter_semantic_bundles()` repository enumeration and
  `RetrievalMaintenanceService.rebuild()` / `audit()`.

- [ ] Enumerate verified semantic bundles descriptor-relatively with safe IDs, sorted order,
  `O_NOFOLLOW`/nonblocking strict loads, and no path fallback. Ignore quarantines and reject corrupt
  canonical records.
- [ ] Rebuild a fresh SQLite projection from every verified bundle and return typed counts/timing.
- [ ] Verify deleting the index then rebuilding yields byte-independent but result-equivalent
  retrieval and audit output.
- [ ] Detect stale/missing source projections and provide an actionable rebuild-required audit,
  while never mutating canonical records.
- [ ] Run maintenance/repository/recovery tests and commit.

Commit: `feat(knowledge): rebuild retrieval projection`

---

### Task 7: Add Golden Retrieval Evaluation and Document M3

**Files:**
- Create: `tests/knowledge/fixtures/retrieval/golden.yaml`
- Create: `tests/knowledge/test_retrieval_golden.py`
- Modify: `README.md`

**Interfaces:**
- Produces: versioned golden scenarios and `RetrievalEvaluationReport` with recall@K, precision@K,
  incompatibility violations, deterministic reproducibility, latency, and index size.

- [ ] Cover valid private IP information gathering, unknown OS conditional candidates, confirmed
  Linux exclusion of Windows-only knowledge, reference+analogous case+negative evidence lanes,
  invalid IP, Android/ADB knowledge gap, copied-source diversity, ID lookup, and rebuild equivalence.
- [ ] Use canonical M2 bundles as fixture inputs, never pre-baked retrieval responses.
- [ ] Establish explicit lexical/facet baseline thresholds and record that vector retrieval remains
  deferred unless a future versioned suite demonstrates material recall gain.
- [ ] Update README with canonical-vs-derived storage, index lifecycle, retrieval lanes, gap
  semantics, and the explicit M3 boundary: no Hades learn-folder/plugin tool yet.
- [ ] Run all knowledge/full tests, scoped Ruff, changed-since-`85aac46` format, diff/status checks.
- [ ] Commit and request whole-branch review.

Commit: `feat(knowledge): complete local retrieval milestone`

---

## Final Verification

- [ ] Full suite, scoped Ruff, changed-file format, diff/status clean.
- [ ] SQLite file is ignored/untracked and completely rebuildable.
- [ ] Every hit has canonical provenance and explainable bounded scores.
- [ ] Known incompatible contexts never appear as applicable hits.
- [ ] Invalid target never queries the backend.
- [ ] Android/ADB absence returns a typed knowledge gap.
- [ ] Deleting/rebuilding the index preserves golden retrieval results.
- [ ] No vector/embedding/Elasticsearch dependency was introduced.
