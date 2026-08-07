# Task 7 Report: Atomic Semantic Repository

## Status

Implemented atomic, source-locked persistence for verified and quarantined semantic compilation
results, strict descriptor-relative loaders, crash recovery, and semantic currentness checks.
Failed and unchanged results do not mutate canonical semantic state.

The parent authorized a cross-task schema amendment required by currentness: semantic compilation
manifests now persist the foundation schema/parser identity and the semantic compiler version.
`SEMANTIC_COMPILER_VERSION` is defined once in the compiler and exported by the semantic package.

## Files

- Modified `src/sedna/knowledge/repository.py`.
- Modified `src/sedna/knowledge/schema/semantic.py`.
- Modified `src/sedna/knowledge/semantic/compiler.py` and its package exports.
- Added `tests/knowledge/test_semantic_repository.py`.
- Updated semantic schema, compiler, and draft tests for the persisted version contract.

## RED Evidence

The schema/compiler amendment first failed at collection with the expected missing export:

```text
ImportError: cannot import name 'SEMANTIC_COMPILER_VERSION' from 'sedna.knowledge.semantic'
```

After the minimal schema/compiler change, the focused schema/compiler run passed 40 tests.

The initial repository RED run was:

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q \
  tests/knowledge/test_semantic_repository.py tests/knowledge/test_repository.py
27 failed, 38 passed in 0.45s
```

All 27 failures were the expected absent Task 7 repository methods. A later fault-injection RED
case proved that a journal deletion/fsync failure initially left the new semantic bytes visible;
the transaction boundary was extended so that this failure now restores the prior bytes before
re-raising the original exception.

## GREEN and Verification Evidence

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q \
  tests/knowledge/test_semantic_repository.py \
  tests/knowledge/test_repository.py \
  tests/knowledge/test_semantic_compiler.py
82 passed in 0.79s

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/knowledge
499 passed in 1.19s

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
519 passed in 1.22s

.venv/bin/ruff check [8 changed Python files]
All checks passed!

.venv/bin/ruff format --check [8 changed Python files]
8 files already formatted

git diff --check
passed
```

Ruff emitted only the repository's pre-existing deprecation warning for top-level lint settings.

## Self-Review

- Semantic records use only allowlisted directories opened relative to the retained repository
  descriptor. Reads use `O_NONBLOCK | O_NOFOLLOW`, require regular files, validate with Pydantic,
  and reject unsafe source IDs, symlinks, FIFOs, corrupt JSON, orphan records, mixed disposition,
  and source/hash identity disagreement.
- Verified transitions write verification then bundle and delete quarantine. Quarantined
  transitions write verification then quarantine and delete bundle. Failed and unchanged results
  return before lock, journal, or record creation.
- Transitions share the existing per-source POSIX lock across repository instances. All three raw
  semantic byte snapshots are journaled before mutation and restored byte-for-byte on every
  failure, including journal deletion/fsync failure, while preserving the original exception.
- Every semantic directory is fsynced before the semantic journal is removed. Startup scans both
  foundation and semantic journal suffixes, locks each source once, and recovers each transaction
  type without changing the foundation journal format or path discipline.
- Currentness requires a verified, non-quarantined state and exact source/hash, foundation
  schema/parser identity, semantic schema, all prompt versions, and compiler version. Stored model
  identity is ignored by default and compared only under an explicit model-pinned policy.
- Tests cover deterministic bytes, mutually exclusive dispositions, safe IDs, symlink/FIFO
  rejection, identity mismatch, corrupt records, byte-exact rollback, journal recovery,
  cross-instance serialization, stale version dimensions, optional model pinning, and idempotent
  repeated writes.

## Concerns

None. The semantic manifest amendment is intentionally strict: older records missing the newly
required persisted version evidence fail validation rather than being inferred from artifacts.
