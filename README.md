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
Hades tool, expose a user-facing “learn folder” workflow, or provide retrieval or
indexing. Those composition and retrieval surfaces remain later milestones.
PDF contents also remain quarantined until a deterministic PDF parser is added
rather than being partially or silently extracted.

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
