# Task 3 Report: Source Inventory and Stable Identity

## Status

Complete.

## Files changed

- `src/sedna/knowledge/inventory.py`: adds immutable source and asset candidates,
  deterministic `.md`/`.pdf` discovery, UUID5 path identity, SHA-256 hashing, and
  recursive local-asset association.
- `tests/knowledge/test_inventory.py`: covers stable ordering, content-independent
  identity, hashing, PDF discovery, nested assets, and `.DS_Store` exclusion.

## Commands and results

- `.venv/bin/python -m pytest -q tests/knowledge/test_inventory.py` — 3 passed.
- `.venv/bin/ruff check src/sedna/knowledge/inventory.py tests/knowledge/test_inventory.py` — passed (Ruff emitted the repository's existing top-level configuration deprecation warning).
- `PYTHONPATH=src .venv/bin/python ... discover_sources(Path('raw_src'))` — 175 Markdown candidates, 3 PDF candidates, 178 total; repeated runs compared equal. This was read-only.
- `.venv/bin/python -m pytest -q` — 32 passed.
- `git diff --check` — passed.

## Commit

- `7a8a410 Add deterministic source inventory`

## Self-review

Reviewed determinism, path normalization, identity/content separation, exclusion of
source documents from their own asset lists, and accidental source-tree writes. No
issues found.

## Concerns

None. The worktree has pre-existing untracked `raw_src` and `.venv` entries; neither
was modified or committed.
