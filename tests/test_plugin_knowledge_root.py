"""Portable zero-configuration knowledge-root behavior at the plugin boundary."""

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
        raise AssertionError("root selection must not invoke the structured LLM")


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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_import(_: str) -> object:
        raise AssertionError("Hades resolver must not be imported for an override")

    monkeypatch.setattr(
        plugin_module,
        "importlib",
        SimpleNamespace(import_module=forbidden_import),
        raising=False,
    )
    context_root = tmp_path / "context"
    context = _Context(configured_root=context_root)

    assert plugin_module._knowledge_root(context, str(tmp_path / "explicit")) == (
        tmp_path / "explicit"
    )
    assert plugin_module._knowledge_root(context, None) == context_root


def test_default_uses_active_hades_home_on_every_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = {"home": tmp_path / "profile-a"}
    module = SimpleNamespace(get_hermes_home=lambda: active["home"])
    monkeypatch.setattr(
        plugin_module,
        "importlib",
        SimpleNamespace(import_module=lambda _: module),
        raising=False,
    )
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


def test_only_absent_hades_module_uses_standalone_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(_: str) -> object:
        raise ModuleNotFoundError("no hermes_constants", name="hermes_constants")

    monkeypatch.setattr(
        plugin_module,
        "importlib",
        SimpleNamespace(import_module=missing),
        raising=False,
    )
    monkeypatch.setattr(
        plugin_module,
        "_platform_hades_home",
        lambda *_: tmp_path / "standalone",
    )

    assert plugin_module._knowledge_root(_Context(), None) == (
        tmp_path / "standalone" / "knowledge" / "sedna"
    )


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("broken home"), ModuleNotFoundError(name="dependency")],
)
def test_installed_but_failing_hades_resolver_never_falls_back(
    failure: BaseException,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_import(_: str) -> object:
        if isinstance(failure, ModuleNotFoundError):
            raise failure
        return SimpleNamespace(get_hermes_home=lambda: (_ for _ in ()).throw(failure))

    monkeypatch.setattr(
        plugin_module,
        "importlib",
        SimpleNamespace(import_module=fail_import),
        raising=False,
    )

    with pytest.raises(plugin_module._ToolBoundaryError) as caught:
        plugin_module._knowledge_root(_Context(), None)

    assert caught.value.code == "knowledge_runtime_unavailable"


def test_hades_resolver_relative_home_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plugin_module.importlib,
        "import_module",
        lambda _: SimpleNamespace(get_hermes_home=lambda: Path("relative-hades-home")),
    )

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


def test_failing_context_override_does_not_fall_through_to_a_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BrokenContext(_Context):
        @property
        def sedna_knowledge_root(self) -> Path:
            raise RuntimeError("broken context configuration")

    def plugin_import(_: str) -> object:
        raise AssertionError("default resolver used")

    monkeypatch.setattr(
        plugin_module,
        "importlib",
        SimpleNamespace(import_module=plugin_import),
        raising=False,
    )

    with pytest.raises(plugin_module._ToolBoundaryError) as caught:
        plugin_module._knowledge_root(_BrokenContext(), None)

    assert caught.value.code == "knowledge_runtime_unavailable"


def test_registration_and_prebackend_rejection_do_not_resolve_or_create_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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

    result = _call(
        context,
        "sedna_retrieve_knowledge",
        {
            "target": "300.456.456.123",
            "authorization": {
                "state": "authorized",
                "exact_targets": ["300.456.456.123"],
                "cidrs": [],
                "hostnames": [],
                "url_origins": [],
                "generic_ids": [],
            },
        },
    )

    assert result["knowledge_gap"]["code"] == "invalid_target"
    assert calls == 0
    assert not (tmp_path / "hades").exists()


def test_first_operation_creates_dynamic_root_and_profiles_remain_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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


def test_default_root_overlap_is_rejected_before_runtime_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hades_home = tmp_path / "hades"
    source_root = hades_home / "knowledge"
    source_root.mkdir(parents=True)
    (source_root / "lesson.md").write_text(
        "# Evidence collection\n\nCompare observations before choosing a hypothesis.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        plugin_module.importlib,
        "import_module",
        lambda _: SimpleNamespace(get_hermes_home=lambda: hades_home),
    )
    context = _Context()
    register(context)

    result = _call(
        context,
        "sedna_learn_local",
        {"source_path": str(source_root)},
    )

    assert result == {"ok": False, "error": "invalid_input"}
    assert not (source_root / "sedna").exists()


def test_host_home_failure_is_returned_as_safe_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_home() -> Path:
        raise RuntimeError("private host profile detail")

    monkeypatch.setattr(
        plugin_module.importlib,
        "import_module",
        lambda _: SimpleNamespace(get_hermes_home=fail_home),
    )
    context = _Context()
    register(context)

    result = _call(context, "sedna_knowledge_maintenance", {"operation": "audit"})

    assert result == {"ok": False, "error": "knowledge_runtime_unavailable"}
    assert "private" not in json.dumps(result)
