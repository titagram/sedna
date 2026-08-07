# Sedna Semantic Version Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` to implement this plan task-by-task.

**Goal:** Force controlled foundation re-extraction and semantic recompilation after the safe asset
locator contract changed, so existing versioned canonical state cannot be incorrectly reported as
unchanged.

**Architecture:** Keep canonical formats and repositories unchanged. Advance the deterministic
foundation extractor version because `LogicalSegment.assets[*].target` semantics changed, and
advance the semantic compiler version because the safe locator is now part of the host LLM input.
Existing currentness checks then provide the migration mechanism: old records become stale, the
normal pipeline regenerates them once, and the next identical run is unchanged.

**Tech Stack:** Python 3.11–3.13, Pydantic 2.13.4, pytest 8, Ruff 0.15.10.

## Global Constraints

- `raw_src/` remains immutable; migration is driven only by persisted version evidence.
- No in-place rewriting or compatibility inference for old records.
- Foundation `EXTRACTOR_VERSION` advances from `2` to `3`.
- Semantic `SEMANTIC_COMPILER_VERSION` advances from `1` to `2`.
- An old record is reprocessed exactly once; a second identical pass is `unchanged` and performs
  no host LLM call.
- Model IDs do not force recompilation unless model pinning is explicitly enabled.

---

### Task 1: Version and Verify the Asset-Locator Migration

**Files:**
- Modify: `src/sedna/knowledge/pipeline.py`
- Modify: `src/sedna/knowledge/semantic/compiler.py`
- Create: `tests/knowledge/test_semantic_version_migration.py`

**Interfaces:**
- Produces: foundation extraction version `3` and semantic compiler version `2`.
- Consumes: existing foundation manifest currentness, semantic repository currentness, and
  `SemanticIngestionService`.
- Guarantees: pre-change records are stale and regenerate through normal public APIs.

- [ ] **Step 1: Write failing foundation upgrade regression**

Create a source containing an asset such as
`https://alice:secret@example.test/proof.png?api_key=x#frag`. Seed a valid prior-version manifest
with the current source hash and extractor version `2`. Assert the current pipeline does not return
`unchanged`, returns a `PreparedSource` whose safe asset target is
`https://example.test/proof.png`, persists extractor version `3`, leaves raw bytes unchanged, and
returns `unchanged` on the next pass.

- [ ] **Step 2: Write failing semantic upgrade regression**

Seed a valid verified semantic bundle whose manifest records compiler version `1`. Run the real
`SemanticIngestionService` with a scripted host and assert one extractor/critic sequence recompiles
the source, persists compiler version `2`, and returns `unchanged` with zero additional LLM calls on
the next pass.

- [ ] **Step 3: Witness RED**

Run:

```bash
pytest -q tests/knowledge/test_semantic_version_migration.py
```

Expected: the old-version records are incorrectly treated as current before the version bumps.

- [ ] **Step 4: Advance both versions**

Set `EXTRACTOR_VERSION = "3"` and `SEMANTIC_COMPILER_VERSION = "2"`. Do not change currentness
logic, record formats, prompt versions, or model-pinning policy.

- [ ] **Step 5: Verify migration and regression safety**

Run:

```bash
pytest -q tests/knowledge/test_semantic_version_migration.py
pytest -q tests/knowledge
pytest -q
ruff check src/sedna/knowledge tests/knowledge
git diff --name-only 85aac46 -- '*.py' | xargs ruff format --check
git diff --check
```

Expected: migration tests, the full suite, lint, format, and diff checks pass.

- [ ] **Step 6: Commit**

```bash
git add src/sedna/knowledge/pipeline.py \
  src/sedna/knowledge/semantic/compiler.py \
  tests/knowledge/test_semantic_version_migration.py
git commit -m "fix(knowledge): version semantic locator migration"
```

---

## Final Verification

- [ ] Confirm a seeded v2 foundation manifest regenerates once as v3.
- [ ] Confirm a seeded v1 semantic bundle recompiles once as v2.
- [ ] Confirm second passes are unchanged with zero extra host calls.
- [ ] Confirm raw source bytes are identical before and after migration.
- [ ] Confirm the whole branch is clean under the full suite and M2 security invariants.
