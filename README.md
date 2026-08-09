# Sedna

HTB/CTF penetration testing plugin for Hades — tool wrappers, evidence collection, knowledge base.

## Status

Early development. See `pyproject.toml` for dependencies.

## Knowledge ingestion foundation

The deterministic foundation prepares heterogeneous lessons, references, and
walkthroughs without treating them as universal instructions:

```text
raw source -> inventory -> classification -> structural parser
           -> logical segments -> PreparedSource
```

`raw_src/` is immutable input. Inventory records stable source identities and
content hashes, classification chooses a deterministic parser profile and an
ingestion outcome, and the structural parser preserves headings, blocks, line
spans, and provenance. Logical segmentation then creates retrieval-safe units
while keeping related observations, actions, outputs, and conclusions together.
`IngestionPipeline` is the entry point for this flow. Its `prepare()` method
returns a typed `PreparedSource` when an accepted document is newly processed or
reprocessed; an unchanged accepted document returns `None` because its canonical
state already exists.

Generated JSON manifests retain the canonical disposition for every source:
accepted, excluded, or quarantined. A separate `IngestionReport` records each
run's outcomes, including unchanged sources and failures. Quarantine records
explain ambiguous or unsupported input; the raw files themselves remain the
canonical source material and are never rewritten. Prepared structural documents
may retain original source text for provenance review and therefore must not be
indexed directly. Only sanitized logical-segment fields are prepared for search,
and final flags such as `HTB{...}` or contextual user/root flag values are excluded
from those fields.

The deterministic foundation stops at `PreparedSource`. The M2 semantic compiler
can then process one supplied prepared source through a caller-provided Hades LLM
facade:

```text
PreparedSource -> host LLM extractor -> critic -> bounded repair
               -> canonical SemanticKnowledgeBundle
```

`SemanticIngestionService.compile_and_store()` checks canonical currentness
before invoking the model, returns the stored typed bundle and verification as
`unchanged` when all source and compiler versions match, and otherwise compiles
once. Verified bundles and explainable quarantines are persisted atomically;
transport or validation failures remain local to that run. Semantic artifacts
retain exact segment provenance, applicability conditions, and graded epistemic
metadata while keeping strategic intent separate from detailed Hades tool
operation.

M2 accepts a single `PreparedSource`; it does not traverse a folder, register a
Hades tool, or expose a user-facing “learn folder” workflow. M3 adds the local
retrieval layer described below, but not those Hades composition surfaces. PDF
contents also remain quarantined until a deterministic PDF parser is added rather
than being partially or silently extracted.

## Local strategic retrieval (M3)

Verified `SemanticKnowledgeBundle` JSON files under `semantic_bundles/` are the
canonical source of truth. The SQLite FTS5 database under `indexes/` is only a
disposable projection: it can be deleted, is ignored by Git, and must never be
used to reconstruct or overwrite canonical knowledge. Index writes accept only
strictly validated semantic bundles and store normalized artifacts, applicability
facets, parent links, provenance, and searchable strategic fields.

`SQLiteRetrievalIndex` supports source-scoped upsert/delete, exact artifact lookup,
and atomic full rebuild. Its internal schema is versioned and audited for integrity,
canonical projection parity, FTS rows, provenance, ownership, and source identity.
`RetrievalMaintenanceService` rebuilds exclusively from a stable snapshot of the
canonical repository and reports missing, stale, orphaned, or corrupt projection
state. A failed audit asks for a rebuild; it never repairs canonical JSON from the
index.

`KnowledgeRetrievalService` returns four independent epistemic lanes:

- references for source-backed technical or methodological knowledge;
- case steps as analogous experience that must be adapted to the current context;
- negative evidence that explains failed paths without declaring universal dead ends;
- decision guidance that connects observations to strategic action intent.

Scores are explainable and comparable only inside their lane. The current lexical
and facet thresholds are 0.40 for references, 0.45 for case steps, 0.35 for negative
evidence, and 0.50 for guidance. Known OS, architecture, identity, or environment
conflicts are hard exclusions. Missing required context remains conditional, lowers
applicability, and is returned as a question; it is not silently treated as a match.
Copied sources share an independence group so repeated copies cannot monopolize a
bounded result lane.

Every qualifying hit retains its exact canonical identity, provenance, score
components, matched facets, qualifications, and missing context. Invalid target
syntax such as `300.456.456.123` produces `invalid_target` before any index call.
An authorized query with no qualifying evidence produces
`no_applicable_knowledge`, including the observed domain, missing context, suggested
documents, and whether external research is eligible. Backend failure is a distinct
`retrieval_unavailable` gap and never masquerades as corpus absence.

Source-authored credential literals are historical, case-local examples: Sedna does
not decide whether they were “real,” and never promotes them to current-target
credentials. `case_specific_details` remain available in the canonical case for
provenance review but are removed from strategic search fields and ranked case-step
views. Final flags and runtime/provider secrets remain forbidden.

The versioned `retrieval-golden-v1` suite rebuilds a real SQLite index from canonical
M2 bundles and runs the real retrieval service. Its lexical/facet baseline gates are
recall@8 >= 0.90, precision@8 >= 0.70, zero hard-incompatibility violations,
deterministic repeated results, at most 5 seconds per scenario and 30 seconds total,
and an index no larger than 5 MiB for the fixture corpus. The suite also covers a
private IPv4 target, unknown and confirmed OS applicability, all evidence lanes,
invalid input with zero backend access, Android/ADB corpus absence, copied-source
diversity, exact lookup, and delete/rebuild equivalence.

Elasticsearch, embeddings, and vector databases remain deferred. A future backend
must use a new versioned suite and demonstrate at least a 0.05 absolute recall@K
gain without dropping below the lexical precision baseline, introducing any hard
incompatibility violation, or losing deterministic provenance before its operational
cost is considered.

M3 exposes Python retrieval and maintenance APIs only. It does **not** yet provide a
Hades plugin tool, host-LLM orchestration, autonomous web learning, folder traversal,
or a user-facing “learn this folder” command; those are later integration milestones.

The approved architecture for semantic compilation and later retrieval is
documented in the [semantic ingestion and retrieval design](docs/superpowers/specs/2026-08-07-sedna-semantic-ingestion-retrieval-design.md).
It defines the host-LLM extractor and critic, applicability facets, automatic
verification, canonical JSON/JSONL storage, SQLite FTS5 retrieval, the future
document-learning skill, and the evaluation gate for any later vector database.

`ingest_markdown` remains available temporarily for callers of the original
SQLite-backed `KnowledgeChunk` workflow. It does not feed the strategic pipeline;
new ingestion integrations should use `IngestionPipeline`.

Third-party corpora are evaluated as pinned, untrusted reference sources rather
than installed as agent instructions. See the
[Claude-Red integration assessment](docs/architecture/2026-08-06-claude-red-integration-assessment.md)
for the proposed source adapter, Hades boundary, quality gates, and pilot rollout.
