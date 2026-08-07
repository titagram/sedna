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
`IngestionPipeline` is the entry point for this flow and returns a typed
`PreparedSource` for accepted documents.

Generated JSON manifests are the canonical processing record for every source,
including accepted, excluded, quarantined, and unchanged outcomes. Quarantine
records explain ambiguous or unsupported input; the raw files themselves remain
the canonical source material and are never rewritten. Prepared structural
documents may retain original source text for provenance review and therefore
must not be indexed directly. Only sanitized logical-segment fields are prepared
for search, and final flags such as `HTB{...}` or contextual user/root flag values
are excluded from those fields.

This foundation stops at `PreparedSource`. Semantic extraction into references,
case studies, case steps, and draft decision rules is a follow-on phase, as are
normalization and retrieval indexing. Sedna will own strategic intent and
evidence transitions; detailed tool operation remains the responsibility of
Hades capabilities. The implementation has no intentional deviations from this
foundation boundary. In particular, PDF contents remain quarantined until a
deterministic PDF parser is added rather than being partially or silently
extracted.

`ingest_markdown` remains available temporarily for callers of the original
SQLite-backed `KnowledgeChunk` workflow. It does not feed the strategic pipeline;
new ingestion integrations should use `IngestionPipeline`.
