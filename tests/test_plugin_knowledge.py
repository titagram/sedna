"""End-to-end contract tests for Sedna's Hades knowledge tools."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

import sedna.plugin as plugin_module
from sedna.knowledge.retrieval import KnowledgeGapCode
from sedna.plugin import register
from tests.knowledge.test_semantic_service import (
    RAW_FLAG,
    ROOT_FLAG,
    SOURCE_CASES,
    SOURCE_CREDENTIAL,
    USER_FLAG,
    _load_responses,
    _ScriptedHost,
)


class _FakeContext:
    def __init__(self, *, llm: object, knowledge_root: Path | None = None) -> None:
        self._llm = llm
        if knowledge_root is not None:
            self.sedna_knowledge_root = knowledge_root
        self.tools: list[dict[str, Any]] = []
        self.hooks: dict[str, list] = {}

    @property
    def llm(self) -> object:
        if isinstance(self._llm, BaseException):
            raise self._llm
        return self._llm

    def register_tool(self, **kwargs: Any) -> None:
        self.tools.append(kwargs)

    def register_hook(self, name: str, callback) -> None:
        self.hooks.setdefault(name, []).append(callback)


class _SingleLookupHost:
    def __init__(self, responses: list[object]) -> None:
        self.delegate = _ScriptedHost(responses)
        self.lookups = 0

    @property
    def complete_structured(self) -> object:
        self.lookups += 1
        if self.lookups > 1:
            raise RuntimeError("host descriptor was looked up more than once")
        return self.delegate.complete_structured


def _call_tool(context: _FakeContext, name: str, payload: object) -> dict[str, Any]:
    tool = next(tool for tool in context.tools if tool["name"] == name)
    result = json.loads(tool["handler"](payload))
    assert isinstance(result, dict)
    return result


def _write_case(root: Path, case_name: str = "reference") -> Path:
    case = SOURCE_CASES[case_name]
    target = root / case.relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(case.markdown, encoding="utf-8")
    return target


def _authorized_query(
    *,
    target: str = "192.168.0.1",
    observed_services: list[str] | None = None,
    query_terms: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "target": target,
        "authorization": {
            "state": "authorized",
            "exact_targets": [target],
            "cidrs": [],
            "hostnames": [],
            "url_origins": [],
            "generic_ids": [],
        },
        "observed_terms": [],
        "observed_facts": [],
        "observed_access": [],
        "observed_services": observed_services or [],
        "observed_hypotheses": [],
        "tried_outcomes": [],
        "unresolved_questions": [],
        "query_terms": query_terms or [],
        "query_synonyms": [],
        "query_facets": [],
        "max_candidates": 32,
        "lane_limit": 5,
    }


def test_build_query_projects_typed_code_intelligence_primitives() -> None:
    payload = _authorized_query(query_terms=["web attack surface"])
    payload["observed_primitives"] = [
        {
            "kind": "stored_rendering_path",
            "source": "attacker-controlled transcription",
            "transforms": ["symbol mapping", "server-side persistence"],
            "sink": "administrator HTML renderer",
            "persistence": "database record",
            "trust_boundary": "unprivileged user to administrator",
            "preconditions": ["administrator reviews transcription"],
            "candidate_classes": ["stored xss", "blind xss"],
            "confidence": 0.86,
        }
    ]

    request = plugin_module._RetrieveInput.model_validate_json(json.dumps(payload))
    query = plugin_module._build_query(request)

    assert "stored_rendering_path" in query.terms
    assert "attacker-controlled transcription" in query.terms
    assert "stored xss" in query.synonyms
    assert "blind xss" in query.synonyms
    assert query.situation.facts[-1].namespace == "code_intel"
    assert query.situation.facts[-1].key == "stored_rendering_path"
    assert "sink=administrator html renderer" in query.situation.facts[-1].value
    assert query.situation.facts[-1].confidence == 0.86


def _serialized(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _assert_no_sensitive_source_material(payload: dict[str, Any]) -> None:
    rendered = _serialized(payload)
    for forbidden in (RAW_FLAG, ROOT_FLAG, USER_FLAG, SOURCE_CREDENTIAL, "CaseOnly!23"):
        assert forbidden not in rendered


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for child in value.values() for key in _all_keys(child)}
    if isinstance(value, list):
        return {key for child in value for key in _all_keys(child)}
    return set()


def test_plugin_registers_knowledge_tools_when_context_has_structured_llm(
    tmp_path: Path,
) -> None:
    context = _FakeContext(
        llm=_ScriptedHost([]),
        knowledge_root=tmp_path / "knowledge",
    )

    register(context)

    assert {tool["name"] for tool in context.tools} >= {
        "sedna_learn_local",
        "sedna_retrieve_knowledge",
        "sedna_get_knowledge_artifact",
        "sedna_knowledge_maintenance",
    }
    knowledge_tools = context.tools[2:]
    assert all(
        tool["schema"]["parameters"]["additionalProperties"] is False for tool in knowledge_tools
    )


def test_learn_tool_accepts_one_folder_and_returns_typed_safe_report(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    _write_case(source_root)
    knowledge_root = tmp_path / "knowledge"
    host = _ScriptedHost(_load_responses(SOURCE_CASES["reference"].fixture_name))
    context = _FakeContext(llm=host)
    register(context)

    report = _call_tool(
        context,
        "sedna_learn_local",
        {
            "source_path": str(source_root),
            "knowledge_root": str(knowledge_root),
        },
    )

    assert report["source_path"] == str(source_root.resolve())
    assert report["verified_source_count"] == 1
    assert report["failed"] is False
    assert report["index_report"]["operation"] == "rebuild"
    assert report["index_report"]["succeeded"] is True
    assert [call["purpose"] for call in host.calls] == [
        "sedna.semantic.extract",
        "sedna.semantic.critic",
    ]
    _assert_no_sensitive_source_material(report)


def test_retrieve_invalid_ip_returns_invalid_target_without_opening_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_runtime(*_: object, **__: object) -> object:
        raise AssertionError("invalid targets must not construct the runtime")

    monkeypatch.setattr(plugin_module.HadesKnowledgeRuntime, "create", reject_runtime)
    context = _FakeContext(llm=object())
    register(context)
    payload = _authorized_query(target="300.456.456.123", query_terms=["network"])

    result = _call_tool(context, "sedna_retrieve_knowledge", payload)

    assert result["target"]["kind"] == "invalid"
    assert result["knowledge_gap"]["code"] == "invalid_target"
    assert result["references"] == []


def test_retrieve_android_adb_absence_returns_document_and_research_gap(
    tmp_path: Path,
) -> None:
    host = _ScriptedHost([])
    context = _FakeContext(llm=host, knowledge_root=tmp_path / "knowledge")
    register(context)

    result = _call_tool(
        context,
        "sedna_retrieve_knowledge",
        _authorized_query(
            target="10.123.123.123",
            observed_services=["android debug bridge", "adb"],
            query_terms=["android", "adb", "device analysis"],
        ),
    )

    gap = result["knowledge_gap"]
    assert gap["code"] == "no_applicable_knowledge"
    assert gap["research_eligible"] is True
    assert gap["suggested_document_ingestion"]
    assert "adb" in gap["observed_domain"]
    assert host.calls == []


def test_handlers_never_return_raw_host_or_filesystem_exception_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host_failure = _FakeContext(llm=RuntimeError("provider-secret raw-host-response"))
    register(host_failure)
    host_result = _call_tool(
        host_failure,
        "sedna_knowledge_maintenance",
        {"operation": "audit", "knowledge_root": str(tmp_path / "knowledge")},
    )

    def fail_runtime(*_: object, **__: object) -> object:
        raise OSError("/private/secret/source raw-filesystem-error")

    monkeypatch.setattr(plugin_module.HadesKnowledgeRuntime, "create", fail_runtime)
    filesystem_failure = _FakeContext(llm=_ScriptedHost([]))
    register(filesystem_failure)
    filesystem_result = _call_tool(
        filesystem_failure,
        "sedna_knowledge_maintenance",
        {"operation": "rebuild", "knowledge_root": str(tmp_path / "other")},
    )

    assert host_result == {"ok": False, "error": "structured_llm_unavailable"}
    assert filesystem_result == {"ok": False, "error": "knowledge_runtime_unavailable"}
    rendered = _serialized({"host": host_result, "filesystem": filesystem_result})
    assert "provider-secret" not in rendered
    assert "private/secret" not in rendered
    assert "raw-" not in rendered


def test_plugin_learn_then_retrieve_uses_verified_semantics_from_same_knowledge_root(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    case = SOURCE_CASES["reference"]
    _write_case(source_root)
    host = _ScriptedHost(_load_responses(case.fixture_name))
    context = _FakeContext(llm=host, knowledge_root=tmp_path / "knowledge")
    register(context)

    learned = _call_tool(
        context,
        "sedna_learn_local",
        {"source_path": str(source_root)},
    )
    retrieved = _call_tool(
        context,
        "sedna_retrieve_knowledge",
        _authorized_query(
            observed_services=["http"],
            query_terms=["evidence", "hypothesis", "observation"],
        ),
    )

    assert learned["verified_source_count"] == 1
    assert retrieved["references"]
    hit = retrieved["references"][0]
    artifact = _call_tool(
        context,
        "sedna_get_knowledge_artifact",
        {"artifact_id": hit["artifact_id"]},
    )
    assert artifact["artifact_id"] == hit["artifact_id"]
    assert artifact["source_refs"] == hit["provenance"]
    assert artifact["source_refs"][0]["path"] == case.relative_path
    assert not {"audit", "parsed", "raw_response", "reasoning", "text"} & _all_keys(artifact)
    assert len(host.calls) == 2
    for response in (learned, retrieved, artifact):
        _assert_no_sensitive_source_material(response)


def test_plugin_second_learn_is_unchanged_with_no_new_host_calls(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    _write_case(source_root)
    host = _ScriptedHost(_load_responses(SOURCE_CASES["reference"].fixture_name))
    context = _FakeContext(llm=host, knowledge_root=tmp_path / "knowledge")
    register(context)

    first = _call_tool(context, "sedna_learn_local", {"source_path": str(source_root)})
    calls_after_first = len(host.calls)
    second = _call_tool(context, "sedna_learn_local", {"source_path": str(source_root)})
    audit = _call_tool(context, "sedna_knowledge_maintenance", {"operation": "audit"})

    assert first["verified_source_count"] == 1
    assert second["unchanged_source_count"] == 1
    assert len(host.calls) == calls_after_first == 2
    assert audit["indexed_source_count"] == 1
    assert audit["indexed_artifact_count"] == 1


def test_plugin_mixed_good_bad_folder_reports_every_candidate(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    _write_case(source_root)
    bad = source_root / "Write-ups" / "bad.md"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"\xff\xfe\xfd")
    host = _ScriptedHost(_load_responses(SOURCE_CASES["reference"].fixture_name))
    context = _FakeContext(llm=host, knowledge_root=tmp_path / "knowledge")
    register(context)

    report = _call_tool(context, "sedna_learn_local", {"source_path": str(source_root)})

    assert len(report["outcomes"]) == 2
    assert {outcome["disposition"] for outcome in report["outcomes"]} == {
        "verified",
        "foundation_quarantined",
    }
    assert report["verified_source_count"] == 1
    assert report["foundation_quarantined_source_count"] == 1
    assert len(host.calls) == 2
    _assert_no_sensitive_source_material(report)


def test_plugin_maintenance_audit_and_rebuild_are_safe_and_typed(tmp_path: Path) -> None:
    host = _ScriptedHost([])
    context = _FakeContext(llm=host, knowledge_root=tmp_path / "knowledge")
    register(context)

    audit = _call_tool(context, "sedna_knowledge_maintenance", {"operation": "audit"})
    rebuilt = _call_tool(context, "sedna_knowledge_maintenance", {"operation": "rebuild"})
    invalid = _call_tool(context, "sedna_knowledge_maintenance", {"operation": "vacuum"})

    assert audit["operation"] == "audit" and audit["succeeded"] is True
    assert rebuilt["operation"] == "rebuild" and rebuilt["succeeded"] is True
    assert invalid == {"ok": False, "error": "invalid_input"}
    assert host.calls == []


def test_plugin_rejects_missing_structured_llm_before_source_inventory(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    _write_case(source_root)
    knowledge_root = tmp_path / "knowledge"
    context = _FakeContext(llm=object(), knowledge_root=knowledge_root)
    register(context)

    result = _call_tool(context, "sedna_learn_local", {"source_path": str(source_root)})

    assert result == {"ok": False, "error": "structured_llm_unavailable"}
    assert source_root.exists()
    assert not knowledge_root.exists()


def test_plugin_binds_the_structured_host_callable_once_before_learning(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    _write_case(source_root)
    host = _SingleLookupHost(_load_responses(SOURCE_CASES["reference"].fixture_name))
    context = _FakeContext(llm=host, knowledge_root=tmp_path / "knowledge")
    register(context)

    result = _call_tool(context, "sedna_learn_local", {"source_path": str(source_root)})

    assert result["verified_source_count"] == 1
    assert host.lookups == 1
    assert len(host.delegate.calls) == 2


def test_learn_rejects_overlapping_knowledge_root_before_creating_runtime_state(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    _write_case(source_root)
    knowledge_root = source_root / "knowledge"
    host = _ScriptedHost(_load_responses(SOURCE_CASES["reference"].fixture_name))
    context = _FakeContext(llm=host, knowledge_root=knowledge_root)
    register(context)

    result = _call_tool(context, "sedna_learn_local", {"source_path": str(source_root)})

    assert result == {"ok": False, "error": "invalid_input"}
    assert not knowledge_root.exists()
    assert host.calls == []


def test_learn_blocks_knowledge_ancestor_symlink_swap_before_any_source_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "sources"
    _write_case(source_root)
    external_parent = tmp_path / "external"
    external_parent.mkdir()
    detached_parent = tmp_path / "external-detached"
    knowledge_root = external_parent / "knowledge"
    host = _ScriptedHost(_load_responses(SOURCE_CASES["reference"].fixture_name))
    context = _FakeContext(llm=host, knowledge_root=knowledge_root)
    register(context)
    real_create = plugin_module.HadesKnowledgeRuntime.create

    def swap_then_create(
        host_llm: object,
        requested_root: Path,
        **kwargs: object,
    ) -> object:
        external_parent.rename(detached_parent)
        external_parent.symlink_to(source_root, target_is_directory=True)
        return real_create(host_llm, requested_root, **kwargs)

    monkeypatch.setattr(plugin_module.HadesKnowledgeRuntime, "create", swap_then_create)

    result = _call_tool(context, "sedna_learn_local", {"source_path": str(source_root)})

    assert result == {"ok": False, "error": "knowledge_runtime_unavailable"}
    assert not (source_root / "knowledge").exists()
    assert not (source_root / "knowledge" / "indexes" / "retrieval.sqlite").exists()
    assert host.calls == []


def test_retrieve_unauthorized_scope_returns_existing_gap_without_runtime(tmp_path: Path) -> None:
    context = _FakeContext(llm=object())
    register(context)
    payload = _authorized_query(target="10.10.10.10", query_terms=["ssh"])
    payload["authorization"] = {
        "state": "unauthorized",
        "exact_targets": [],
        "cidrs": [],
        "hostnames": [],
        "url_origins": [],
        "generic_ids": [],
    }

    result = _call_tool(context, "sedna_retrieve_knowledge", payload)

    assert result["knowledge_gap"]["code"] == "unauthorized_scope"
    assert result["references"] == []


def test_retrieve_rejects_opaque_situation_json_instead_of_bypassing_typed_inputs(
    tmp_path: Path,
) -> None:
    context = _FakeContext(llm=_ScriptedHost([]), knowledge_root=tmp_path / "knowledge")
    register(context)

    result = _call_tool(
        context,
        "sedna_retrieve_knowledge",
        {
            "situation": {
                "target": {"value": "10.10.10.10"},
                "authorization": {"state": "authorized"},
            }
        },
    )

    assert result == {"ok": False, "error": "invalid_input"}


def test_handlers_reject_non_json_values_and_scalar_type_coercion(tmp_path: Path) -> None:
    context = _FakeContext(llm=_ScriptedHost([]), knowledge_root=tmp_path / "knowledge")
    register(context)

    non_json = _call_tool(
        context,
        "sedna_learn_local",
        {"source_path": b"/private/non-json-path"},
    )
    coercive = _authorized_query(target="300.456.456.123")
    coercive["max_candidates"] = "32"
    wrong_scalar = _call_tool(context, "sedna_retrieve_knowledge", coercive)

    assert non_json == {"ok": False, "error": "invalid_input"}
    assert wrong_scalar == {"ok": False, "error": "invalid_input"}


LLM_GUIDE = Path(__file__).parents[1] / "docs" / "llm" / "sedna-knowledge-tools.md"
README = Path(__file__).parents[1] / "README.md"
_JSON_EXAMPLE = re.compile(r"```json\n(?P<payload>.*?)\n```", re.DOTALL)
_KNOWLEDGE_TOOL_NAMES = {
    "sedna_learn_local",
    "sedna_retrieve_knowledge",
    "sedna_get_knowledge_artifact",
    "sedna_knowledge_maintenance",
}
_REQUIRED_GUIDE_SECTIONS = (
    "## 1. When to call `sedna_learn_local`",
    "## 2. Supplying authorization and current observations",
    "## 3. Interpreting evidence lanes and applicability",
    "## 4. Exact provenance with `sedna_get_knowledge_artifact`",
    "## 5. Writing a strategic answer",
    "## 6. Knowledge gaps and pre-backend stops",
    "## 7. Idempotence, versions, audit, and rebuild",
    "## 8. Safety and research boundaries",
)


def _guide_examples() -> tuple[dict[str, Any], ...]:
    text = LLM_GUIDE.read_text(encoding="utf-8")
    examples = tuple(json.loads(match.group("payload")) for match in _JSON_EXAMPLE.finditer(text))
    assert examples
    assert all(type(example) is dict for example in examples)
    return examples


def _nested_values(value: object, key: str) -> tuple[object, ...]:
    if isinstance(value, dict):
        direct = (value[key],) if key in value else ()
        return direct + tuple(
            item for child in value.values() for item in _nested_values(child, key)
        )
    if isinstance(value, list):
        return tuple(item for child in value for item in _nested_values(child, key))
    return ()


def test_llm_guide_examples_name_only_registered_tools_and_closed_gap_codes(
    tmp_path: Path,
) -> None:
    examples = _guide_examples()
    tool_names = {value for example in examples for value in _nested_values(example, "tool")}
    gap_codes = {value for example in examples for value in _nested_values(example, "code")}
    context = _FakeContext(
        llm=_ScriptedHost([]),
        knowledge_root=tmp_path / "knowledge",
    )
    register(context)
    registered_names = {tool["name"] for tool in context.tools}

    assert tool_names == _KNOWLEDGE_TOOL_NAMES
    assert tool_names <= registered_names
    assert gap_codes == {code.value for code in KnowledgeGapCode}


def test_llm_guide_examples_are_fictional_json_without_flags_or_secrets() -> None:
    examples = _guide_examples()
    rendered = json.dumps(examples, ensure_ascii=False, sort_keys=True)

    assert "192.0.2." in rendered or "198.51.100." in rendered
    assert re.search(r"(?i)(?:htb|flag)\s*\{", rendered) is None
    assert (
        re.search(
            r"(?i)(?:password|api[_ -]?key|private[_ -]?key)\s*[:=]",
            rendered,
        )
        is None
    )
    assert "provider-secret" not in rendered


def test_llm_guide_is_granular_and_never_makes_case_studies_universal() -> None:
    guide = LLM_GUIDE.read_text(encoding="utf-8")
    lower = guide.casefold()

    assert all(section in guide for section in _REQUIRED_GUIDE_SECTIONS)
    assert "case studies are context-bound examples" in lower
    assert "adapt" in lower
    assert "follow case studies exactly" not in lower
    assert "case studies override current evidence" not in lower
    assert "tool-operation syntax belongs to hades" in lower


def test_readme_describes_the_m4_local_learning_boundary() -> None:
    readme = README.read_text(encoding="utf-8").casefold()

    assert "local file or folder" in readme
    assert "host llm" in readme
    assert "classified and verified automatically" in readme
    assert "idempotent" in readme
    assert "direct remote fetching" in readme
    assert "outside" in readme
