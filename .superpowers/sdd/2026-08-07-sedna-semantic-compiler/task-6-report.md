# Task 6 Report: Bounded Semantic Compiler

## Status

Implemented `SemanticCompiler`, which builds one safe source payload and performs exactly one
extractor call, one critic call, and at most one repair plus one post-repair critic call. It
returns verified canonical bundles, explainable quarantines, or closed typed failures without
retaining raw host output.

The result contract now keeps every successful call as immutable safe metadata and distinguishes
compiler/adapter failures from critic finding codes. Source-authored credential-shaped literals
remain case-local examples; this task adds no credential truth classification.

## Files

- Created `src/sedna/knowledge/semantic/compiler.py`
- Modified `src/sedna/knowledge/semantic/__init__.py`
- Modified `src/sedna/knowledge/semantic/drafts.py`
- Created `tests/knowledge/test_semantic_compiler.py`
- Modified `tests/knowledge/test_semantic_drafts.py`

## RED Evidence

Before implementation, the focused compiler suite failed at collection as required:

```text
ImportError: cannot import name 'SemanticCompiler' from 'sedna.knowledge.semantic'
```

The follow-up typed-failure contract test also failed before the new closed failure vocabulary
existed:

```text
ImportError: cannot import name 'CompilationFailureCode'
```

## GREEN Evidence

```text
.venv/bin/python -m pytest -q \
  tests/knowledge/test_semantic_compiler.py \
  tests/knowledge/test_semantic_materialize.py \
  tests/knowledge/test_semantic_llm.py \
  tests/knowledge/test_semantic_drafts.py
80 passed in 0.25s

.venv/bin/python -m pytest -q tests/knowledge
460 passed in 0.83s

.venv/bin/python -m pytest -q
480 passed in 0.86s

.venv/bin/ruff format --check \
  src/sedna/knowledge/semantic/compiler.py \
  src/sedna/knowledge/semantic/drafts.py \
  src/sedna/knowledge/semantic/__init__.py \
  tests/knowledge/test_semantic_compiler.py \
  tests/knowledge/test_semantic_drafts.py
5 files already formatted

.venv/bin/ruff check [same changed Python files]
All checks passed!

git diff --check
passed
```

The repository's pre-existing Ruff deprecation warning for top-level `select` was emitted but did
not cause a lint or formatting failure.

## Self-Review

- Verified exact purpose sequences for accepted, warning, repaired, repeated-material, extractor
  failure, and critic-failure paths.
- Verified repair is bounded to one attempt; material findings after the post-repair critic produce
  a quarantine with no bundle.
- Verified all successful host calls retain provider, model, agent ID, and bounded token counts in
  `SemanticCompilationResult.calls`, including successful extractor metadata when the critic fails.
- Verified adapter transport, missing parsed response, invalid structured response, invalid input,
  and internal failure use a separate closed failure-code vocabulary with exact safe messages.
- Verified canonical materialization validation failure yields an `unsafe_material` quarantine,
  never a bundle or raw response.
- Verified source and critic range accounting before materialization; the compiler never sends raw
  host response text or builds a second safe source payload.

## Concerns

No unresolved implementation concerns. The existing semantic schema stores a critic call in the
verification record; Task 6's result-level immutable call sequence preserves extractor and repair
metadata without changing the canonical bundle/audit schema.

---

## Fix Round 1: Preserve Extractor Identity and Validate Before Criticism

### Changes

- Kept original extractor completion metadata separate from repaired drafts. Repaired artifacts now
  materialize with the original extractor call metadata; the manifest binds extractor model to the
  first call and critic model to the final critic call.
- Added result-contract validation for verified/unchanged source identity, verified audit and
  manifest disposition, bounded repair count, and model-to-call binding.
- Exposed and reused one segment-accounting validator so incomplete extractor output fails before
  any critic call.
- Limited `unsafe_material` quarantine conversion to `ValueError`/`TypeError` from the
  materializer itself. Unexpected materializer exceptions now emit `materialization_failure`; clock,
  verification, manifest, and result construction failures emit `internal_failure`.

### RED Evidence

The new regressions initially failed as follows:

```text
extractor_model_id: repair-model != extractor-model
incomplete extractor output: transport_failure != invalid_structured_response
manifest repair_count mismatch: DID NOT RAISE ValidationError
```

### GREEN Evidence

```text
.venv/bin/python -m pytest -q \
  tests/knowledge/test_semantic_compiler.py \
  tests/knowledge/test_semantic_materialize.py \
  tests/knowledge/test_semantic_llm.py \
  tests/knowledge/test_semantic_drafts.py
89 passed in 0.31s

.venv/bin/python -m pytest -q tests/knowledge
469 passed in 0.88s

.venv/bin/python -m pytest -q
489 passed in 0.90s
```

Ruff format/check on every changed Python file and `git diff --check` passed.

### Self-Review

- Distinct extractor, repair, initial critic, and final critic models are retained in call order;
  canonical artifact extraction and manifest metadata use the original extractor and final critic
  respectively.
- An incomplete draft reaches neither the first critic nor repair.
- Repair and post-repair critic transport failures retain all prior successful call metadata.
- A decreasing aware-UTC clock produces an `internal_failure`, never an unsafe-material quarantine.
