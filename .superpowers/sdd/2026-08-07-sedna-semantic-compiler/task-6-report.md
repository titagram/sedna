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
