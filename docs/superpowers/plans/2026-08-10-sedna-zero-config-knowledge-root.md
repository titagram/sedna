# Sedna Zero-Configuration Knowledge Root Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Sedna strategic-knowledge tool securely select
`<active Hades home>/knowledge/sedna` when neither an explicit nor context-owned root is supplied.

**Architecture:** Keep host integration inside `sedna.plugin`: a pure bounded root selector retains
explicit/context precedence, lazily calls Hades' official `get_hermes_home()` resolver, and uses a
small platform-native fallback only when Hades is absent. Pass the resulting `Path` to the existing
descriptor-bound `HadesKnowledgeRuntime`; do not create or cache storage during resolution.

**Tech Stack:** Python 3.11+, `pathlib`, `importlib`, Pydantic v2, pytest, Ruff, Hades
`hermes_constants.get_hermes_home`, existing Sedna runtime/repository/SQLite implementation

## Global Constraints

- Resolution precedence is exactly: request `knowledge_root`, `ctx.sedna_knowledge_root`, active
  Hades home, standalone platform-native fallback.
- The default suffix is exactly `knowledge/sedna` beneath the selected Hades home.
- Only absence of `hermes_constants` may activate the standalone fallback; an installed but
  failing host resolver must fail closed.
- Do not cache the effective root globally or across plugin calls.
- Root selection performs no directory creation and opens no database.
- Malformed-target and unauthorized-scope retrieval must remain pre-backend and must not resolve
  or create the default root.
- Existing explicit and context overrides remain backward compatible.
- Do not migrate, merge, delete, or automatically adopt `~/.hades/knowledge/sedna-machines`.
- Do not add dependencies or a machine-specific plugin-manifest setting.
- Preserve all descriptor confinement, overlap, journal, currentness, and durable-index barriers.

---

## File Map

- `src/sedna/plugin.py`: owns Hades-context lookup, standalone fallback, bounded path selection,
  and the existing handoff to `HadesKnowledgeRuntime`.
- `tests/test_plugin_knowledge_root.py`: focused resolver and plugin lifecycle regressions for
  precedence, portability, profile isolation, side effects, and fail-closed behavior.
- `tests/test_plugin_knowledge.py`: retains existing end-to-end knowledge-tool coverage and gains
  the documentation contract assertion because it already owns the LLM guide/README checks.
- `docs/llm/sedna-knowledge-tools.md`: tells the host LLM that the root is optional and explains
  override precedence without embedding a developer-machine path.
- `README.md`: documents installation-time zero-configuration behavior and pilot/custom-root
  compatibility.

---

### Task 1: Implement and verify portable root resolution

**Files:**
- Modify: `src/sedna/plugin.py:1-10,394-443`
- Create: `tests/test_plugin_knowledge_root.py`

**Interfaces:**
- Consumes: `HadesKnowledgeRuntime.create(host, knowledge_root, external_source_path=...)` and the
  current `_ToolBoundaryError`/`ToolErrorCode` boundary.
- Produces: `_platform_hades_home(platform: str, environment: Mapping[str, str], user_home: Path)
  -> Path`, `_default_knowledge_root() -> Path`, and the revised
  `_knowledge_root(ctx: Any, explicit: str | None) -> Path`.
- Preserves: `_runtime_for_context(...) -> HadesKnowledgeRuntime` and all public tool schemas.

- [ ] **Step 1: Add failing resolver-precedence and portability tests**

Create `tests/test_plugin_knowledge_root.py` with minimal local test doubles and direct tests of
the private plugin boundary:

```python
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import sedna.plugin as plugin_module
from sedna.plugin import register


class _LLM:
    def complete_structured(self, **_: object) -> object:
        raise AssertionError("maintenance and pre-backend calls must not invoke the LLM")


class _Context:
    def __init__(self, *, configured_root: Path | None = None) -> None:
        self.llm = _LLM()
        if configured_root is not None:
            self.sedna_knowledge_root = configured_root
        self.tools: list[dict[str, Any]] = []

    def register_tool(self, **kwargs: Any) -> None:
        self.tools.append(kwargs)


def _call(context: _Context, name: str, payload: object) -> dict[str, Any]:
    tool = next(tool for tool in context.tools if tool["name"] == name)
    result = json.loads(tool["handler"](payload))
    assert type(result) is dict
    return result


def test_explicit_and_context_roots_win_without_importing_hades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_import(_: str) -> object:
        raise AssertionError("Hades resolver must not be imported for an override")

    monkeypatch.setattr(plugin_module.importlib, "import_module", forbidden_import)
    context_root = tmp_path / "context"
    context = _Context(configured_root=context_root)

    assert plugin_module._knowledge_root(context, str(tmp_path / "explicit")) == (
        tmp_path / "explicit"
    )
    assert plugin_module._knowledge_root(context, None) == context_root


def test_default_uses_active_hades_home_on_every_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = {"home": tmp_path / "profile-a"}
    module = SimpleNamespace(get_hermes_home=lambda: active["home"])
    monkeypatch.setattr(plugin_module.importlib, "import_module", lambda _: module)
    context = _Context()

    assert plugin_module._knowledge_root(context, None) == (
        tmp_path / "profile-a" / "knowledge" / "sedna"
    )
    active["home"] = tmp_path / "profile-b"
    assert plugin_module._knowledge_root(context, None) == (
        tmp_path / "profile-b" / "knowledge" / "sedna"
    )


@pytest.mark.parametrize(
    ("platform", "environment", "home", "expected"),
    [
        ("darwin", {}, Path("/Users/tester"), Path("/Users/tester/.hades")),
        ("linux", {}, Path("/home/tester"), Path("/home/tester/.hades")),
        (
            "win32",
            {"LOCALAPPDATA": "C:/Users/tester/AppData/Local"},
            Path("C:/Users/tester"),
            Path("C:/Users/tester/AppData/Local/hades"),
        ),
        (
            "win32",
            {},
            Path("C:/Users/tester"),
            Path("C:/Users/tester/AppData/Local/hades"),
        ),
    ],
)
def test_platform_hades_home_is_portable(
    platform: str,
    environment: dict[str, str],
    home: Path,
    expected: Path,
) -> None:
    assert plugin_module._platform_hades_home(platform, environment, home) == expected
```

Add the fail-closed tests in the same file:

```python
def test_only_absent_hades_module_uses_standalone_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(_: str) -> object:
        raise ModuleNotFoundError("no hermes_constants", name="hermes_constants")

    monkeypatch.setattr(plugin_module.importlib, "import_module", missing)
    monkeypatch.setattr(
        plugin_module,
        "_platform_hades_home",
        lambda *_: tmp_path / "standalone",
    )

    assert plugin_module._knowledge_root(_Context(), None) == (
        tmp_path / "standalone" / "knowledge" / "sedna"
    )


@pytest.mark.parametrize("failure", [RuntimeError("broken home"), ModuleNotFoundError(name="dep")])
def test_installed_but_failing_hades_resolver_never_falls_back(
    failure: BaseException, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_import(_: str) -> object:
        if isinstance(failure, ModuleNotFoundError):
            raise failure
        return SimpleNamespace(get_hermes_home=lambda: (_ for _ in ()).throw(failure))

    monkeypatch.setattr(plugin_module.importlib, "import_module", fail_import)
    with pytest.raises(plugin_module._ToolBoundaryError) as caught:
        plugin_module._knowledge_root(_Context(), None)

    assert caught.value.code == "knowledge_runtime_unavailable"


@pytest.mark.parametrize("configured", ["", "bad\x00root", "x" * 4097, object()])
def test_invalid_context_override_is_a_typed_input_error(configured: object) -> None:
    context = _Context()
    context.sedna_knowledge_root = configured

    with pytest.raises(plugin_module._ToolBoundaryError) as caught:
        plugin_module._knowledge_root(context, None)

    assert caught.value.code == "invalid_input"
```

- [ ] **Step 2: Run the focused tests and witness RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/test_plugin_knowledge_root.py
```

Expected: collection fails because `sedna.plugin` has no `importlib` or
`_platform_hades_home`, and the current missing-context path returns `knowledge_root_required`.

- [ ] **Step 3: Implement the minimal host-aware selector**

In `src/sedna/plugin.py`, add `importlib`, `os`, `sys`, and `Mapping`, then replace the current
`_knowledge_root` implementation with these bounded helpers:

```python
def _validated_root(value: object, *, error_code: ToolErrorCode) -> Path:
    if type(value) is not str and not isinstance(value, Path):
        raise _ToolBoundaryError(error_code)
    rendered = str(value)
    if not rendered or len(rendered) > _MAX_PATH_LENGTH or "\x00" in rendered:
        raise _ToolBoundaryError(error_code)
    return Path(rendered)


def _platform_hades_home(
    platform: str,
    environment: Mapping[str, str],
    user_home: Path,
) -> Path:
    if platform == "win32":
        local = environment.get("LOCALAPPDATA")
        return (Path(local) if local else user_home / "AppData" / "Local") / "hades"
    return user_home / ".hades"


def _default_knowledge_root() -> Path:
    try:
        module = importlib.import_module("hermes_constants")
    except ModuleNotFoundError as error:
        if error.name != "hermes_constants":
            raise _ToolBoundaryError("knowledge_runtime_unavailable") from error
        home: object = _platform_hades_home(sys.platform, os.environ, Path.home())
    except ImportError as error:
        raise _ToolBoundaryError("knowledge_runtime_unavailable") from error
    else:
        resolver = getattr(module, "get_hermes_home", None)
        if not callable(resolver):
            raise _ToolBoundaryError("knowledge_runtime_unavailable")
        try:
            home = resolver()
        except Exception as error:
            raise _ToolBoundaryError("knowledge_runtime_unavailable") from error
    return _validated_root(home, error_code="knowledge_runtime_unavailable") / "knowledge" / "sedna"


def _knowledge_root(ctx: Any, explicit: str | None) -> Path:
    if explicit is not None:
        return _validated_root(explicit, error_code="invalid_input")
    try:
        configured = ctx.sedna_knowledge_root
    except AttributeError:
        return _default_knowledge_root()
    except Exception as error:
        raise _ToolBoundaryError("knowledge_runtime_unavailable") from error
    return _validated_root(configured, error_code="invalid_input")
```

Do not call `mkdir`, `resolve`, or `HadesKnowledgeRuntime.create` in these helpers. Keep
`_runtime_for_context` as the sole handoff to the secure runtime.

- [ ] **Step 4: Run focused resolver tests and witness GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/test_plugin_knowledge_root.py
```

Expected: all tests pass.

- [ ] **Step 5: Add failing plugin lifecycle and isolation regressions**

Append these tests to `tests/test_plugin_knowledge_root.py`:

```python
def test_registration_and_prebackend_rejection_do_not_resolve_or_create_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def resolve_home() -> Path:
        nonlocal calls
        calls += 1
        return tmp_path / "hades"

    monkeypatch.setattr(
        plugin_module.importlib,
        "import_module",
        lambda _: SimpleNamespace(get_hermes_home=resolve_home),
    )
    context = _Context()
    register(context)
    assert calls == 0
    assert not (tmp_path / "hades").exists()

    payload = {
        "target": "300.456.456.123",
        "authorization": {
            "state": "authorized",
            "exact_targets": ["300.456.456.123"],
            "cidrs": [],
            "hostnames": [],
            "url_origins": [],
            "generic_ids": [],
        },
    }
    result = _call(context, "sedna_retrieve_knowledge", payload)

    assert result["knowledge_gap"]["code"] == "invalid_target"
    assert calls == 0
    assert not (tmp_path / "hades").exists()


def test_first_operation_creates_dynamic_root_and_profiles_remain_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = {"home": tmp_path / "profile-a"}
    monkeypatch.setattr(
        plugin_module.importlib,
        "import_module",
        lambda _: SimpleNamespace(get_hermes_home=lambda: active["home"]),
    )
    context = _Context()
    register(context)

    first = _call(context, "sedna_knowledge_maintenance", {"operation": "audit"})
    active["home"] = tmp_path / "profile-b"
    second = _call(context, "sedna_knowledge_maintenance", {"operation": "audit"})

    assert first["succeeded"] is True
    assert second["succeeded"] is True
    assert (tmp_path / "profile-a" / "knowledge" / "sedna" / "indexes").is_dir()
    assert (tmp_path / "profile-b" / "knowledge" / "sedna" / "indexes").is_dir()


def test_context_override_still_creates_only_the_selected_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        plugin_module.importlib,
        "import_module",
        lambda _: (_ for _ in ()).throw(AssertionError("default resolver used")),
    )
    selected = tmp_path / "custom"
    context = _Context(configured_root=selected)
    register(context)

    result = _call(context, "sedna_knowledge_maintenance", {"operation": "audit"})

    assert result["succeeded"] is True
    assert (selected / "indexes").is_dir()
```

- [ ] **Step 6: Run the plugin lifecycle tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q \
  tests/test_plugin_knowledge_root.py tests/test_plugin.py tests/test_plugin_knowledge.py
```

Expected: all tests pass. If the new tests expose an ordering defect, preserve this order in the
handler: validate input, construct and reject invalid/unauthorized retrieval queries, then call
`_runtime_for_context`; do not move root resolution earlier.

- [ ] **Step 7: Run formatting and lint for the implementation slice**

Run:

```bash
.venv/bin/ruff format --check src/sedna/plugin.py tests/test_plugin_knowledge_root.py
.venv/bin/ruff check src/sedna/plugin.py tests/test_plugin_knowledge_root.py
git diff --check
```

Expected: all commands exit 0, apart from the repository's already-known Ruff configuration
deprecation warning.

- [ ] **Step 8: Commit the resolver and regressions**

```bash
git add src/sedna/plugin.py tests/test_plugin_knowledge_root.py
git diff --cached --check
git commit -m "feat(plugin): resolve zero-config knowledge root"
```

Expected: the commit contains exactly the two listed files.

---

### Task 2: Document the zero-configuration contract and close the branch gate

**Files:**
- Modify: `tests/test_plugin_knowledge.py:480-end`
- Modify: `docs/llm/sedna-knowledge-tools.md:1-45`
- Modify: `README.md:125-165`

**Interfaces:**
- Consumes: the precedence and default path implemented in Task 1.
- Produces: LLM-facing contract version `sedna-knowledge-tools-v2` and installation/user guidance
  matching executable behavior.
- Preserves: every JSON example must name a registered tool and contain no flag or secret.

- [ ] **Step 1: Add a failing documentation contract test**

Append this test to `tests/test_plugin_knowledge.py`:

```python
def test_zero_config_docs_match_dynamic_root_contract() -> None:
    guide = LLM_GUIDE.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    combined = f"{guide}\n{readme}".casefold()

    assert "contract version: `sedna-knowledge-tools-v2`" in guide.casefold()
    assert "<active hades home>/knowledge/sedna" in combined
    assert "knowledge_root" in combined and "optional" in combined
    assert "ctx.sedna_knowledge_root" in combined
    assert "hermes_home" in combined and "hades_home" in combined
    assert "does not automatically migrate" in combined
    assert "/users/gabriele" not in guide.casefold()
    assert "/users/gabriele" not in readme.casefold()
```

- [ ] **Step 2: Run the documentation test and witness RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q \
  tests/test_plugin_knowledge.py::test_zero_config_docs_match_dynamic_root_contract
```

Expected: FAIL because the guide is still contract v1 and still tells callers that a configured
root is required.

- [ ] **Step 3: Update the LLM guide with exact precedence and a zero-config example**

In `docs/llm/sedna-knowledge-tools.md`:

- change the version line to `Contract version: sedna-knowledge-tools-v2` using the existing
  backtick formatting;
- replace the required-root paragraph with this contract:

```markdown
`knowledge_root` is optional. Sedna selects the first available location in this order: the
request's explicit `knowledge_root`, `ctx.sedna_knowledge_root`, then
`<active Hades home>/knowledge/sedna`. Hades resolves its active home from its current context,
`HERMES_HOME`, the compatibility `HADES_HOME`, and the platform default. Omit `knowledge_root`
for normal zero-configuration use; provide it only for an intentional isolated/custom store.
```

- remove `knowledge_root` from the local-folder JSON example so the documented happy path proves
  zero-configuration use;
- state that custom/pilot roots remain explicit and that this release does not automatically
  migrate or merge them.

- [ ] **Step 4: Update README installation behavior**

Add a compact paragraph under `Autonomous local learning and Hades tools (M4/M5)`:

```markdown
The plugin is zero-configuration for canonical storage. Unless a call supplies
`knowledge_root` or the host supplies `ctx.sedna_knowledge_root`, Sedna resolves
`<active Hades home>/knowledge/sedna` on each operation. The active home honors Hades context,
`HERMES_HOME`, `HADES_HOME`, and platform defaults, so installations and profiles remain
isolated without hardcoded paths. Existing custom or pilot stores are not automatically migrated
or merged.
```

- [ ] **Step 5: Run guide, plugin, and documentation tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q \
  tests/test_plugin.py tests/test_plugin_knowledge.py tests/test_plugin_knowledge_root.py
```

Expected: all tests pass, including JSON-example parsing, registered tool names, closed gap codes,
and the new zero-configuration assertions.

- [ ] **Step 6: Run the complete regression gate**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
.venv/bin/ruff format --check src/sedna tests
.venv/bin/ruff check src/sedna tests
git diff --check
rg -n "/Users/gabriele/.hades/knowledge/sedna-machines" \
  src tests README.md docs/llm plugin.yaml
```

Expected: the full pytest, Ruff, and diff checks pass. The final `rg` returns no executable,
test, README, LLM-guide, or plugin-manifest match; the intentionally historical path remains only
in the approved design specification.

- [ ] **Step 7: Commit documentation and its executable contract**

```bash
git add README.md docs/llm/sedna-knowledge-tools.md tests/test_plugin_knowledge.py
git diff --cached --check
git commit -m "docs(plugin): explain zero-config knowledge storage"
```

Expected: the commit contains exactly the three listed files.

- [ ] **Step 8: Verify final repository state**

Run:

```bash
git status --short --branch
git log -3 --oneline
```

Expected: no tracked or untracked implementation files remain, and the branch contains the
resolver commit followed by the documentation commit. Do not push until explicitly requested.

---

## Self-Review Record

- **Spec coverage:** Task 1 covers precedence, lazy Hades integration, standalone portability,
  fail-closed errors, per-operation resolution, side-effect timing, pre-backend behavior, profile
  isolation, and existing overrides. Task 2 covers user/LLM documentation, the non-migration
  decision, full regressions, and the hardcoded-path audit.
- **Placeholder scan:** the plan contains no deferred implementation markers; every code-producing
  step includes the exact function/test/prose content and an expected command result.
- **Type consistency:** `_platform_hades_home`, `_default_knowledge_root`, `_validated_root`, and
  `_knowledge_root` use the same signatures in tests and implementation. Existing public tool and
  runtime signatures are unchanged.
