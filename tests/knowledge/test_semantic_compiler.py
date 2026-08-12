"""Tests for the bounded semantic extractor, critic, and repair compiler."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote

import pytest

from sedna.knowledge.parsing import PreparedSource, parse_markdown
from sedna.knowledge.parsing.segment import segment_document
from sedna.knowledge.schema import (
    DocumentManifest,
    DocumentType,
    ExtractionMetadata,
    IngestionStatus,
    KnowledgeRole,
    SourceQuality,
)
from sedna.knowledge.semantic import (
    SEMANTIC_COMPILER_VERSION,
    CriticVerdict,
    DraftApplicabilityContext,
    DraftCitation,
    DraftContextAssertion,
    DraftReference,
    DraftTypedContext,
    SemanticCompiler,
    SemanticDraftBundle,
)
from sedna.knowledge.semantic.llm import HadesLlmAdapter


@dataclass
class _Usage:
    input_tokens: int = 11
    output_tokens: int = 7


@dataclass
class _HostResult:
    parsed: object
    provider: str = "test-provider"
    model: str = "test-model"
    agent_id: str = "test-agent"
    usage: _Usage = field(default_factory=_Usage)
    audit: object = None


class _ScriptedHost:
    def __init__(self, results: list[object]) -> None:
        self._results = iter(results)
        self.calls: list[dict[str, Any]] = []

    def complete_structured(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        result = next(self._results)
        if isinstance(result, Exception):
            raise result
        return result


def _prepared_source(*, path: str = "raw_src/compiler.md") -> PreparedSource:
    document = parse_markdown(
        "compiler-source",
        path,
        """# Service

Inspect the HTTP service before selecting an action.

# Context

The observed host uses x86_64 architecture.
""",
    )
    manifest = DocumentManifest(
        source_id="compiler-source",
        path=path,
        sha256="a" * 64,
        title="Compiler notes",
        language="en",
        document_type=DocumentType.MACHINE_WALKTHROUGH,
        knowledge_role=KnowledgeRole.CASE_STUDY,
        quality=SourceQuality.COMPLETE,
        parser_profile="writeup",
        ingestion_status=IngestionStatus.ACCEPTED,
        extraction=ExtractionMetadata(
            schema_version="1",
            parser_id="markdown-it",
            parser_version="1",
            extractor_id="deterministic",
            extractor_version="1",
        ),
    )
    return PreparedSource(manifest=manifest, document=document, segments=segment_document(document))


def _draft(*, architecture: bool = False) -> SemanticDraftBundle:
    applicability = DraftApplicabilityContext()
    if architecture:
        applicability = DraftApplicabilityContext(
            typed_context=DraftTypedContext(
                cpu_architecture=DraftContextAssertion(
                    value="x86_64",
                    relation="observed",
                    origin="explicit",
                    confidence=1.0,
                    citations=(DraftCitation(segment_indexes=(1,)),),
                )
            )
        )
    return SemanticDraftBundle(
        artifacts=(
            DraftReference(
                draft_type="reference",
                local_id="http-inspection",
                artifact_type="methodology",
                subject="HTTP inspection",
                statement="Inspect the HTTP service before selecting an action.",
                origin="explicit",
                citations=(DraftCitation(segment_indexes=(0,)),),
                applicability=applicability,
            ),
        ),
        ignored_segment_indexes=(1,) if not architecture else (),
    )


def _accepted(*findings: object) -> CriticVerdict:
    return CriticVerdict(accepted=True, findings=tuple(findings))  # type: ignore[arg-type]


def _finding(*, code: str = "context_omission", severity: str = "material") -> dict[str, object]:
    messages = {
        "context_omission": "Required applicability context is omitted.",
        "unsupported_claim": "The source does not support the claim.",
    }
    return {
        "code": code,
        "severity": severity,
        "artifact_local_id": "http-inspection",
        "message": messages[code],
        "segment_indexes": [1],
    }


def _compiler(results: list[object]) -> tuple[SemanticCompiler, _ScriptedHost]:
    host = _ScriptedHost(results)
    return (
        SemanticCompiler(
            HadesLlmAdapter(host),
            clock=lambda: datetime(2026, 8, 7, 12, 0, tzinfo=UTC),
        ),
        host,
    )


def _purposes(host: _ScriptedHost) -> list[str]:
    return [call["purpose"] for call in host.calls]


def test_accepted_extraction_materializes_verified_bundle_with_exact_two_calls():
    compiler, host = _compiler(
        [_HostResult(_draft().model_dump(mode="json")), _HostResult({"accepted": True})]
    )

    result = compiler.compile(_prepared_source())

    assert result.disposition == "verified"
    assert result.bundle is not None
    assert result.bundle.compilation_manifest.repair_count == 0
    assert result.bundle.compilation_manifest.foundation_schema_version == "1"
    assert result.bundle.compilation_manifest.foundation_parser_id == "markdown-it"
    assert result.bundle.compilation_manifest.foundation_parser_version == "1"
    assert result.bundle.compilation_manifest.compiler_version == SEMANTIC_COMPILER_VERSION
    assert result.bundle.compilation_manifest.emitted_artifact_ids == (
        result.bundle.references[0].artifact_id,
    )
    assert (
        result.bundle.references[0].assessment.independence_group
        == _prepared_source().manifest.sha256
    )
    assert result.verification is not None
    assert result.verification.findings == ()
    assert [call.purpose for call in result.calls] == [
        "sedna.semantic.extract",
        "sedna.semantic.critic",
    ]
    assert _purposes(host) == ["sedna.semantic.extract", "sedna.semantic.critic"]
    assert result.verification.critic_call.input_tokens == 11
    assert result.verification.recorded_at == datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


def test_warning_only_verdict_is_verified_and_retains_the_finding():
    compiler, host = _compiler(
        [
            _HostResult(_draft().model_dump(mode="json")),
            _HostResult(
                {
                    "accepted": True,
                    "findings": [_finding(code="unsupported_claim", severity="warning")],
                }
            ),
        ]
    )

    result = compiler.compile(_prepared_source())

    assert result.disposition == "verified"
    assert result.verification is not None
    assert result.verification.findings[0].severity == "warning"
    assert _purposes(host) == ["sedna.semantic.extract", "sedna.semantic.critic"]


def test_one_material_finding_triggers_exactly_one_repair_then_accepts():
    compiler, host = _compiler(
        [
            _HostResult(_draft().model_dump(mode="json")),
            _HostResult({"accepted": False, "findings": [_finding()]}),
            _HostResult(_draft(architecture=True).model_dump(mode="json")),
            _HostResult({"accepted": True}),
        ]
    )

    result = compiler.compile(_prepared_source())

    assert result.disposition == "verified"
    assert result.bundle is not None
    assert result.bundle.compilation_manifest.repair_count == 1
    assert [call.purpose for call in result.calls] == [
        "sedna.semantic.extract",
        "sedna.semantic.critic",
        "sedna.semantic.repair",
        "sedna.semantic.critic",
    ]
    assert _purposes(host) == [
        "sedna.semantic.extract",
        "sedna.semantic.critic",
        "sedna.semantic.repair",
        "sedna.semantic.critic",
    ]
    repair_payload = json.loads(host.calls[2]["input"][0]["text"])
    assert repair_payload["critic"]["findings"][0]["code"] == "context_omission"


def test_repaired_bundle_keeps_original_extractor_metadata_and_final_critic_model():
    compiler, _ = _compiler(
        [
            _HostResult(_draft().model_dump(mode="json"), model="extractor-model"),
            _HostResult({"accepted": False, "findings": [_finding()]}, model="initial-critic"),
            _HostResult(_draft(architecture=True).model_dump(mode="json"), model="repair-model"),
            _HostResult({"accepted": True}, model="final-critic"),
        ]
    )

    result = compiler.compile(_prepared_source())

    assert result.disposition == "verified"
    assert result.bundle is not None
    assert result.bundle.compilation_manifest.extractor_model_id == "extractor-model"
    assert result.bundle.compilation_manifest.critic_model_id == "final-critic"
    assert result.bundle.references[0].extraction.model_id == "extractor-model"
    assert [call.model for call in result.calls] == [
        "extractor-model",
        "initial-critic",
        "repair-model",
        "final-critic",
    ]


def test_material_finding_after_repair_is_quarantined_without_artifacts():
    compiler, host = _compiler(
        [
            _HostResult(_draft().model_dump(mode="json")),
            _HostResult({"accepted": False, "findings": [_finding()]}),
            _HostResult(_draft(architecture=True).model_dump(mode="json")),
            _HostResult({"accepted": False, "findings": [_finding()]}),
        ]
    )

    result = compiler.compile(_prepared_source())

    assert result.disposition == "quarantined"
    assert result.bundle is None
    assert result.quarantine is not None
    assert result.quarantine.reason_codes == ("context_omission",)
    assert _purposes(host) == [
        "sedna.semantic.extract",
        "sedna.semantic.critic",
        "sedna.semantic.repair",
        "sedna.semantic.critic",
    ]


def test_architecture_omission_repair_emits_a_cited_context_assertion():
    repaired = _draft(architecture=True)
    compiler, host = _compiler(
        [
            _HostResult(_draft().model_dump(mode="json")),
            _HostResult({"accepted": False, "findings": [_finding()]}),
            _HostResult(repaired.model_dump(mode="json")),
            _HostResult({"accepted": True}),
        ]
    )

    result = compiler.compile(_prepared_source())

    assert result.disposition == "verified"
    assert result.bundle is not None
    assert result.bundle.references[0].applicability.typed_context.cpu_architecture is not None
    assert _purposes(host) == [
        "sedna.semantic.extract",
        "sedna.semantic.critic",
        "sedna.semantic.repair",
        "sedna.semantic.critic",
    ]


def test_incomplete_extractor_segment_accounting_fails_before_critic():
    incomplete = _draft().model_copy(update={"ignored_segment_indexes": ()})
    compiler, host = _compiler([_HostResult(incomplete.model_dump(mode="json"))])

    result = compiler.compile(_prepared_source())

    assert result.disposition == "failed"
    assert result.failure_code == "invalid_structured_response"
    assert [call.purpose for call in result.calls] == ["sedna.semantic.extract"]
    assert _purposes(host) == ["sedna.semantic.extract"]


@pytest.mark.parametrize(
    ("response", "failure_code"),
    [
        ({"not": "a draft bundle"}, "invalid_structured_response"),
        (RuntimeError("secret response"), "transport_failure"),
    ],
)
def test_malformed_or_timeout_extractor_returns_safe_typed_failure(
    response: object, failure_code: str
):
    compiler, host = _compiler([_HostResult(response) if isinstance(response, dict) else response])

    result = compiler.compile(_prepared_source())

    assert result.disposition == "failed"
    assert result.failure_code == failure_code
    assert result.failure_message is not None
    assert result.calls == ()
    assert "secret response" not in result.model_dump_json()
    assert _purposes(host) == ["sedna.semantic.extract"]


def test_critic_failure_retains_successful_extractor_call_metadata():
    compiler, host = _compiler(
        [_HostResult(_draft().model_dump(mode="json")), RuntimeError("critic secret")]
    )

    result = compiler.compile(_prepared_source())

    assert result.disposition == "failed"
    assert result.failure_code == "transport_failure"
    assert [(call.purpose, call.provider, call.input_tokens) for call in result.calls] == [
        ("sedna.semantic.extract", "test-provider", 11)
    ]
    assert "critic secret" not in result.model_dump_json()
    assert _purposes(host) == ["sedna.semantic.extract", "sedna.semantic.critic"]


@pytest.mark.parametrize(
    ("results", "expected_purposes"),
    [
        (
            [
                _HostResult(_draft().model_dump(mode="json")),
                _HostResult({"accepted": False, "findings": [_finding()]}),
                RuntimeError("repair secret"),
            ],
            ["sedna.semantic.extract", "sedna.semantic.critic"],
        ),
        (
            [
                _HostResult(_draft().model_dump(mode="json")),
                _HostResult({"accepted": False, "findings": [_finding()]}),
                _HostResult(_draft(architecture=True).model_dump(mode="json")),
                RuntimeError("post-critic secret"),
            ],
            [
                "sedna.semantic.extract",
                "sedna.semantic.critic",
                "sedna.semantic.repair",
            ],
        ),
    ],
)
def test_repair_stage_failures_retain_all_successful_call_metadata(
    results: list[object], expected_purposes: list[str]
):
    compiler, host = _compiler(results)

    result = compiler.compile(_prepared_source())

    assert result.disposition == "failed"
    assert result.failure_code == "transport_failure"
    assert [call.purpose for call in result.calls] == expected_purposes
    assert _purposes(host) == [
        *expected_purposes,
        "sedna.semantic.repair" if len(expected_purposes) == 2 else "sedna.semantic.critic",
    ]


def test_unsafe_canonical_material_is_quarantined(monkeypatch: pytest.MonkeyPatch):
    import sedna.knowledge.semantic.compiler as compiler_module

    compiler, host = _compiler(
        [_HostResult(_draft().model_dump(mode="json")), _HostResult({"accepted": True})]
    )

    def reject_unsafe(*args: object, **kwargs: object) -> tuple[object, ...]:
        raise ValueError("unsafe canonical material")

    monkeypatch.setattr(compiler_module, "materialize_semantic_content", reject_unsafe)
    result = compiler.compile(_prepared_source())

    assert result.disposition == "quarantined"
    assert result.bundle is None
    assert result.quarantine is not None
    assert result.quarantine.reason_codes == ("unsafe_material",)
    assert result.quarantine.compilation_manifest is not None
    assert result.verification is not None
    assert result.verification.repair_count == 0
    assert result.quarantine.compilation_manifest.started_at == datetime(
        2026, 8, 7, 12, 0, tzinfo=UTC
    )
    assert result.quarantine.compilation_manifest.completed_at == datetime(
        2026, 8, 7, 12, 0, tzinfo=UTC
    )
    assert _purposes(host) == ["sedna.semantic.extract", "sedna.semantic.critic"]


def test_unsafe_canonical_material_after_repair_records_the_four_call_path(
    monkeypatch: pytest.MonkeyPatch,
):
    import sedna.knowledge.semantic.compiler as compiler_module

    compiler, host = _compiler(
        [
            _HostResult(_draft().model_dump(mode="json")),
            _HostResult({"accepted": False, "findings": [_finding()]}),
            _HostResult(_draft(architecture=True).model_dump(mode="json")),
            _HostResult({"accepted": True}),
        ]
    )

    def reject_unsafe(*args: object, **kwargs: object) -> tuple[object, ...]:
        raise ValueError("unsafe canonical material")

    monkeypatch.setattr(compiler_module, "materialize_semantic_content", reject_unsafe)
    result = compiler.compile(_prepared_source())

    assert result.disposition == "quarantined"
    assert result.quarantine is not None
    assert result.quarantine.compilation_manifest is not None
    assert result.quarantine.compilation_manifest.repair_count == 1
    assert result.verification is not None
    assert result.verification.repair_count == 1
    assert _purposes(host) == [
        "sedna.semantic.extract",
        "sedna.semantic.critic",
        "sedna.semantic.repair",
        "sedna.semantic.critic",
    ]


@pytest.mark.parametrize(
    "unsafe_path",
    (
        "raw_src/HTB{path_final}.md",
        "raw_src/HTB%2526%2523123%253Bpath_final%2526%2523125%253B.md",
        "raw_src/Root flag abcdef0123456789abcdef0123456789.md",
        "raw_src/User flag 0123456789abcdef0123456789abcdef.md",
    ),
)
def test_final_flag_foundation_path_compiles_to_safe_unsafe_material_quarantine(
    unsafe_path: str,
):
    """Would fail if an unsafe raw filename became canonical or bypassed quarantine."""
    compiler, host = _compiler(
        [_HostResult(_draft().model_dump(mode="json")), _HostResult({"accepted": True})]
    )

    result = compiler.compile(_prepared_source(path=unsafe_path))

    assert result.disposition == "quarantined"
    assert result.bundle is None
    assert result.quarantine is not None
    assert result.quarantine.reason_codes == ("unsafe_material",)
    decoded = result.model_dump_json()
    for _ in range(8):
        next_decoded = unquote(html.unescape(decoded))
        if next_decoded == decoded:
            break
        decoded = next_decoded
    assert "htb{" not in decoded.casefold()
    assert "abcdef0123456789abcdef0123456789" not in decoded.casefold()
    assert "0123456789abcdef0123456789abcdef" not in decoded.casefold()
    assert _purposes(host) == ["sedna.semantic.extract", "sedna.semantic.critic"]


def test_unexpected_materializer_failure_is_a_typed_failure(monkeypatch: pytest.MonkeyPatch):
    import sedna.knowledge.semantic.compiler as compiler_module

    compiler, host = _compiler(
        [_HostResult(_draft().model_dump(mode="json")), _HostResult({"accepted": True})]
    )

    def fail_materialization(*args: object, **kwargs: object) -> tuple[object, ...]:
        raise RuntimeError("materializer implementation failure")

    monkeypatch.setattr(compiler_module, "materialize_semantic_content", fail_materialization)
    result = compiler.compile(_prepared_source())

    assert result.disposition == "failed"
    assert result.failure_code == "materialization_failure"
    assert result.quarantine is None
    assert _purposes(host) == ["sedna.semantic.extract", "sedna.semantic.critic"]


def test_decreasing_clock_produces_internal_failure_not_unsafe_quarantine():
    instants = iter(
        [
            datetime(2026, 8, 7, 12, 0, tzinfo=UTC),
            datetime(2026, 8, 7, 11, 0, tzinfo=UTC),
            datetime(2026, 8, 7, 11, 0, tzinfo=UTC),
            datetime(2026, 8, 7, 11, 0, tzinfo=UTC),
            datetime(2026, 8, 7, 11, 0, tzinfo=UTC),
        ]
    )
    host = _ScriptedHost(
        [_HostResult(_draft().model_dump(mode="json")), _HostResult({"accepted": True})]
    )
    compiler = SemanticCompiler(HadesLlmAdapter(host), clock=lambda: next(instants))

    result = compiler.compile(_prepared_source())

    assert result.disposition == "failed"
    assert result.failure_code == "internal_failure"
    assert result.quarantine is None
    assert [call.purpose for call in result.calls] == [
        "sedna.semantic.extract",
        "sedna.semantic.critic",
    ]
