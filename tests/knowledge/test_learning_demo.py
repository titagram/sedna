"""Executable M5 demonstrations over the real Hades plugin knowledge surface."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

import sedna.plugin as plugin_module
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


class _DemoContext:
    def __init__(self, *, llm: object, knowledge_root: Path | None = None) -> None:
        self.llm = llm
        if knowledge_root is not None:
            self.sedna_knowledge_root = knowledge_root
        self.tools: list[dict[str, Any]] = []
        self.hooks: dict[str, Any] = {}

    def register_tool(self, **kwargs: Any) -> None:
        self.tools.append(kwargs)

    def register_hook(self, name: str, callback) -> None:
        self.hooks[name] = callback


def _call_tool(context: _DemoContext, name: str, payload: object) -> dict[str, Any]:
    tool = next(tool for tool in context.tools if tool["name"] == name)
    result = json.loads(tool["handler"](payload))
    assert isinstance(result, dict)
    return result


def _write_demo_folder(root: Path) -> _ScriptedHost:
    responses: list[object] = []
    for case_name in ("reference", "repair", "hybrid", "windows"):
        case = SOURCE_CASES[case_name]
        target = root / case.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(case.markdown, encoding="utf-8")
        case_responses = _load_responses(case.fixture_name)
        if case_name == "hybrid":
            case_responses = copy.deepcopy(case_responses)
            negative_case = next(
                artifact
                for artifact in case_responses[0]["artifacts"]
                if artifact["draft_type"] == "case"
            )
            negative_case["knowledge_role"] = "negative_case"
        responses.extend(case_responses)
    return _ScriptedHost(responses)


def _authorized_query(
    *,
    target: str,
    query_terms: list[str],
    observed_services: list[str] | None = None,
    observed_facts: list[dict[str, object]] | None = None,
) -> dict[str, object]:
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
        "observed_facts": observed_facts or [],
        "observed_access": ["network access"],
        "observed_services": observed_services or [],
        "observed_hypotheses": [],
        "tried_outcomes": [],
        "unresolved_questions": ["which operating system is present"],
        "query_terms": query_terms,
        "query_synonyms": [],
        "query_facets": [],
        "max_candidates": 32,
        "lane_limit": 5,
    }


def _learn_demo(context: _DemoContext, source_root: Path) -> dict[str, Any]:
    report = _call_tool(
        context,
        "sedna_learn_local",
        {"source_path": str(source_root)},
    )
    assert report["verified_source_count"] == 4
    return report


def _private_ip_query() -> dict[str, object]:
    return _authorized_query(
        target="192.168.0.1",
        observed_services=["http", "authentication"],
        query_terms=[
            "evidence",
            "hypothesis",
            "authentication",
            "rejection",
            "routing",
            "architecture",
        ],
        observed_facts=[
            {
                "namespace": "typed",
                "key": "cpu_architecture",
                "value": "arm64",
                "confidence": 1.0,
            }
        ],
    )


def _assert_safe_tool_result(payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        RAW_FLAG,
        ROOT_FLAG,
        USER_FLAG,
        SOURCE_CREDENTIAL,
        "CaseOnly!23",
        "raw_response",
        "provider-secret",
    ):
        assert forbidden not in rendered


def test_hypothetical_private_ip_answer_is_source_backed_and_conditional(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    host = _write_demo_folder(source_root)
    context = _DemoContext(llm=host, knowledge_root=tmp_path / "knowledge")
    register(context)
    learned = _learn_demo(context, source_root)

    response = _call_tool(context, "sedna_retrieve_knowledge", _private_ip_query())

    assert response["knowledge_gap"] is None
    assert response["references"]
    assert response["case_steps"]
    assert response["negative_cases"]
    assert response["rejected_candidates"]
    hits = response["references"] + response["case_steps"] + response["negative_cases"]
    assert all(hit["provenance"] for hit in hits)
    assert all(hit["qualification_reasons"] for hit in hits)
    assert any(hit["missing_context"] for hit in response["case_steps"])
    assert any(
        "conflict" in " ".join(candidate["rejection_reasons"])
        for candidate in response["rejected_candidates"]
    )
    _assert_safe_tool_result(learned)
    _assert_safe_tool_result(response)


def test_invalid_ip_demo_never_queries_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_runtime(*_: object, **__: object) -> object:
        raise AssertionError("invalid target reached the knowledge backend")

    monkeypatch.setattr(plugin_module.HadesKnowledgeRuntime, "create", reject_runtime)
    context = _DemoContext(llm=object(), knowledge_root=tmp_path / "knowledge")
    register(context)

    response = _call_tool(
        context,
        "sedna_retrieve_knowledge",
        _authorized_query(target="300.456.456.123", query_terms=["network discovery"]),
    )

    assert response["target"]["kind"] == "invalid"
    assert response["knowledge_gap"]["code"] == "invalid_target"
    assert response["references"] == []
    assert response["rejected_candidates"] == []


def test_android_adb_demo_returns_gap_and_offers_local_docs_or_technical_research(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    host = _write_demo_folder(source_root)
    context = _DemoContext(llm=host, knowledge_root=tmp_path / "knowledge")
    register(context)
    _learn_demo(context, source_root)
    calls_after_learning = len(host.calls)

    response = _call_tool(
        context,
        "sedna_retrieve_knowledge",
        _authorized_query(
            target="10.123.123.123",
            observed_services=["android debug bridge", "adb"],
            query_terms=["android", "adb", "device analysis"],
        ),
    )

    gap = response["knowledge_gap"]
    assert gap["code"] == "no_applicable_knowledge"
    assert gap["research_eligible"] is True
    assert gap["suggested_document_ingestion"]
    assert "adb" in gap["observed_domain"]
    assert len(host.calls) == calls_after_learning
    _assert_safe_tool_result(response)


def test_artifact_drill_down_returns_exact_provenance_for_llm_citation(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    host = _write_demo_folder(source_root)
    context = _DemoContext(llm=host, knowledge_root=tmp_path / "knowledge")
    register(context)
    _learn_demo(context, source_root)
    retrieved = _call_tool(context, "sedna_retrieve_knowledge", _private_ip_query())
    hit = retrieved["references"][0]

    artifact = _call_tool(
        context,
        "sedna_get_knowledge_artifact",
        {"artifact_id": hit["artifact_id"]},
    )

    assert artifact["artifact_id"] == hit["artifact_id"]
    assert artifact["source_refs"] == hit["provenance"]
    assert all(
        {"source_id", "path", "location"} <= set(source_ref)
        and {"start_line", "end_line", "section"} <= set(source_ref["location"])
        for source_ref in artifact["source_refs"]
    )
    assert artifact["source_refs"][0]["path"] in {
        SOURCE_CASES["reference"].relative_path,
        SOURCE_CASES["hybrid"].relative_path,
    }
    _assert_safe_tool_result(artifact)


def test_tool_demo_contains_no_flag_or_raw_source_leak(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    host = _write_demo_folder(source_root)
    context = _DemoContext(llm=host, knowledge_root=tmp_path / "knowledge")
    register(context)

    learned = _learn_demo(context, source_root)
    retrieved = _call_tool(context, "sedna_retrieve_knowledge", _private_ip_query())
    artifact = _call_tool(
        context,
        "sedna_get_knowledge_artifact",
        {"artifact_id": retrieved["references"][0]["artifact_id"]},
    )

    for payload in (learned, retrieved, artifact):
        _assert_safe_tool_result(payload)
