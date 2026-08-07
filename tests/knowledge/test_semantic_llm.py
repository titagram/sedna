"""Tests for safe structured requests through the host-owned LLM facade."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

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
from sedna.knowledge.semantic import CriticVerdict, SemanticDraftBundle
from sedna.knowledge.semantic.llm import (
    HadesLlmAdapter,
    SafeCriticRequestPayload,
    SafePreparedSourcePayload,
    SafeRepairRequestPayload,
    SemanticLlmError,
    build_safe_source_payload,
)
from sedna.knowledge.semantic.prompts import (
    CRITIC_PROMPT,
    CRITIC_PROMPT_ID,
    CRITIC_PROMPT_VERSION,
    EXTRACTOR_PROMPT,
    EXTRACTOR_PROMPT_ID,
    EXTRACTOR_PROMPT_VERSION,
    REPAIR_PROMPT,
    REPAIR_PROMPT_ID,
    REPAIR_PROMPT_VERSION,
)


class _CompletionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str


@dataclass
class _Usage:
    input_tokens: int = 11
    output_tokens: int = 7


@dataclass
class _HostResult:
    parsed: object
    provider: str = "host-provider"
    model: str = "host-model"
    agent_id: str = "default"
    usage: _Usage = field(default_factory=_Usage)
    audit: dict[str, object] = field(default_factory=lambda: {"plugin_id": "sedna"})


@dataclass
class _MissingParsedHostResult:
    provider: str = "host-provider"
    model: str = "host-model"
    agent_id: str = "default"
    usage: _Usage = field(default_factory=_Usage)
    audit: dict[str, object] = field(default_factory=dict)


class _RecordingHost:
    def __init__(self, result: object | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def complete_structured(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _prepared_source() -> PreparedSource:
    markdown = """# Safe field notes

Observed service behavior before HTB{raw_final_flag} after.

![proof](shots/HTB%7Bencoded_final_flag%7D.png)
"""
    document = parse_markdown("source-safe", "private/raw-notes.md", markdown)
    raw_asset = document.assets[0].model_copy(
        update={"metadata": {"provider_api_key": "sk-must-not-cross"}}
    )
    document = document.model_copy(update={"assets": (raw_asset,)})
    assert "htb{" in " ".join(block.text for block in document.blocks).casefold()
    assert "htb%7b" in document.assets[0].target.casefold()
    assert document.assets[0].metadata["provider_api_key"] == "sk-must-not-cross"

    manifest = DocumentManifest(
        source_id="source-safe",
        path="private/raw-notes.md",
        sha256="a" * 64,
        title="Safe field notes",
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
        warnings=("private warning must not enter the LLM",),
    )
    return PreparedSource(
        manifest=manifest,
        document=document,
        segments=segment_document(document),
    )


def test_safe_payload_reconstructs_only_whitelisted_retrieval_safe_fields(monkeypatch):
    prepared = _prepared_source()

    def forbidden_dump(*args: object, **kwargs: object) -> object:
        raise AssertionError("PreparedSource.model_dump() crossed the LLM boundary")

    monkeypatch.setattr(PreparedSource, "model_dump", forbidden_dump)

    payload = build_safe_source_payload(prepared)
    dumped = payload.model_dump(mode="json")
    serialized = json.dumps(dumped, sort_keys=True).casefold()

    assert set(dumped) == {
        "source_id",
        "title",
        "document_type",
        "knowledge_role",
        "quality",
        "segments",
    }
    assert set(dumped["segments"][0]) == {
        "index",
        "start_line",
        "end_line",
        "heading_path",
        "text",
        "assets",
    }
    assert set(dumped["segments"][0]["assets"][0]) == {
        "asset_index",
        "target",
        "start_line",
        "end_line",
    }
    assert [segment["index"] for segment in dumped["segments"]] == list(
        range(len(prepared.segments))
    )
    assert "<excluded_flag>" in serialized
    assert "htb{" not in serialized
    assert "htb%7b" not in serialized
    assert "private/raw-notes.md" not in serialized
    assert "private warning" not in serialized
    assert "sk-must-not-cross" not in serialized
    assert "sha256" not in serialized
    assert "metadata" not in serialized
    assert "blocks" not in serialized
    assert "relationships" not in serialized


def test_safe_payload_rejects_flag_material_in_whitelisted_manifest_text():
    prepared = _prepared_source()
    unsafe_manifest = prepared.manifest.model_copy(update={"title": "HTB{manifest_flag}"})
    prepared = prepared.model_copy(update={"manifest": unsafe_manifest})

    with pytest.raises(ValidationError, match="final flag material"):
        build_safe_source_payload(prepared)


def test_safe_payload_removes_credentials_from_asset_locator_urls():
    prepared = _prepared_source()
    credentialed_asset = prepared.document.assets[0].model_copy(
        update={
            "target": (
                "https://alice:sk-userinfo@example.test/proof.png"
                "?api_key=sk-query-secret#private-fragment"
            )
        }
    )
    document = prepared.document.model_copy(update={"assets": (credentialed_asset,)})
    prepared = PreparedSource(
        manifest=prepared.manifest,
        document=document,
        segments=segment_document(document),
    )

    serialized = build_safe_source_payload(prepared).model_dump_json()

    assert "alice" not in serialized
    assert "sk-userinfo" not in serialized
    assert "api_key" not in serialized
    assert "sk-query-secret" not in serialized
    assert "private-fragment" not in serialized
    assert "https://example.test/proof.png" in serialized

    unsafe_payload = build_safe_source_payload(prepared).model_dump(mode="python")
    unsafe_payload["segments"][0]["assets"][0]["target"] = (
        "https://alice:secret@example.test/proof.png?api_key=secret"
    )
    with pytest.raises(ValidationError, match="credential-bearing components"):
        SafePreparedSourcePayload.model_validate(unsafe_payload)


def test_adapter_rejects_raw_prepared_source_without_calling_host():
    host = _RecordingHost(_HostResult(parsed={"decision": "accept"}))
    adapter = HadesLlmAdapter(host)

    with pytest.raises(TypeError, match="safe semantic request payload"):
        adapter.complete(
            _CompletionModel,
            instructions=EXTRACTOR_PROMPT,
            payload=_prepared_source(),  # type: ignore[arg-type]
            purpose="sedna.semantic.extract",
        )

    assert host.calls == []


def test_adapter_revalidates_nonvalidating_pydantic_copies_before_host_call():
    host = _RecordingHost(_HostResult(parsed={"decision": "accept"}))
    adapter = HadesLlmAdapter(host)
    safe = build_safe_source_payload(_prepared_source())
    corrupted_payloads = (
        safe.model_copy(update={"title": "HTB{copied_flag}"}),
        SafePreparedSourcePayload.model_construct(
            source_id=safe.source_id,
            title=_prepared_source(),
            document_type=safe.document_type,
            knowledge_role=safe.knowledge_role,
            quality=safe.quality,
            segments=safe.segments,
        ),
        SafeCriticRequestPayload.model_construct(
            source=safe,
            drafts=SemanticDraftBundle().model_copy(update={"artifacts": (_prepared_source(),)}),
        ),
    )

    for corrupted in corrupted_payloads:
        with pytest.raises(TypeError, match="safe semantic request payload"):
            adapter.complete(
                _CompletionModel,
                instructions=EXTRACTOR_PROMPT,
                payload=corrupted,
                purpose="sedna.semantic.extract",
            )

    assert host.calls == []


def test_closed_critic_and_repair_envelopes_accept_only_safe_typed_content():
    source = build_safe_source_payload(_prepared_source())
    drafts = SemanticDraftBundle()
    verdict = CriticVerdict(accepted=True)

    critic = SafeCriticRequestPayload(source=source, drafts=drafts)
    repair = SafeRepairRequestPayload(source=source, drafts=drafts, critic=verdict)

    assert critic.source == source
    assert repair.critic == verdict

    with pytest.raises(ValidationError):
        SafeCriticRequestPayload(
            source=_prepared_source(),  # type: ignore[arg-type]
            drafts=drafts,
        )


@pytest.mark.parametrize(
    ("purpose", "instructions"),
    [
        ("sedna.semantic.extract", EXTRACTOR_PROMPT),
        ("sedna.semantic.critic", CRITIC_PROMPT),
        ("sedna.semantic.repair", REPAIR_PROMPT),
    ],
)
def test_adapter_records_exact_host_contract_without_routing_overrides(
    purpose: str,
    instructions: str,
):
    host = _RecordingHost(_HostResult(parsed={"decision": "accept"}))
    adapter = HadesLlmAdapter(host, max_tokens=4096, timeout=45.0)
    payload = build_safe_source_payload(_prepared_source())

    completion = adapter.complete(
        _CompletionModel,
        instructions=instructions,
        payload=payload,
        purpose=purpose,
    )

    assert completion.parsed == _CompletionModel(decision="accept")
    assert completion.provider == "host-provider"
    assert completion.model == "host-model"
    assert completion.agent_id == "default"
    assert completion.usage.model_dump() == {"input_tokens": 11, "output_tokens": 7}
    assert completion.audit == {"purpose": purpose}
    assert len(host.calls) == 1
    call = host.calls[0]
    assert set(call) == {
        "instructions",
        "input",
        "json_schema",
        "schema_name",
        "temperature",
        "max_tokens",
        "timeout",
        "purpose",
    }
    assert call["instructions"] == instructions
    assert call["purpose"] == purpose
    assert call["temperature"] == 0
    assert call["max_tokens"] == 4096
    assert call["timeout"] == 45.0
    assert call["schema_name"] == "_CompletionModel"
    assert call["json_schema"] == _CompletionModel.model_json_schema()
    assert call["input"] == [
        {
            "type": "text",
            "text": json.dumps(
                payload.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        }
    ]
    assert not {"provider", "model", "agent_id", "profile", "system_prompt"} & set(call)


@pytest.mark.parametrize(
    ("result", "reason_code"),
    [
        (RuntimeError("raw provider response: HTB{must_not_escape}"), "transport_failure"),
        (_HostResult(parsed=None), "missing_parsed_response"),
        (_MissingParsedHostResult(), "missing_parsed_response"),
        (
            _HostResult(parsed={"decision": "accept"}, usage=_Usage(input_tokens=-1)),
            "transport_failure",
        ),
        (
            _HostResult(parsed={"decision": "accept", "raw_response": "HTB{must_not_escape}"}),
            "invalid_structured_response",
        ),
    ],
)
def test_adapter_maps_failure_classes_without_exposing_raw_model_material(
    result: object | Exception,
    reason_code: str,
):
    adapter = HadesLlmAdapter(_RecordingHost(result))

    with pytest.raises(SemanticLlmError) as error:
        adapter.complete(
            _CompletionModel,
            instructions=EXTRACTOR_PROMPT,
            payload=build_safe_source_payload(_prepared_source()),
            purpose="sedna.semantic.extract",
        )

    assert error.value.reason_code == reason_code
    assert "HTB{" not in str(error.value)
    assert "raw provider response" not in str(error.value)


def test_prompt_versions_and_instructions_cover_semantic_safety_contract():
    assert (
        EXTRACTOR_PROMPT_ID,
        EXTRACTOR_PROMPT_VERSION,
        CRITIC_PROMPT_ID,
        CRITIC_PROMPT_VERSION,
        REPAIR_PROMPT_ID,
        REPAIR_PROMPT_VERSION,
    ) == (
        "sedna-semantic-extractor",
        "1",
        "sedna-semantic-critic",
        "1",
        "sedna-semantic-repair",
        "1",
    )

    extractor = " ".join(EXTRACTOR_PROMPT.casefold().split())
    assert all(
        phrase in extractor
        for phrase in (
            "untrusted data",
            "segment indexes",
            "technical reference",
            "historical case",
            "unknown",
            "exact tool tutorials",
        )
    )

    critic = " ".join(CRITIC_PROMPT.casefold().split())
    assert all(
        phrase in critic
        for phrase in (
            "factual fidelity",
            "prerequisites",
            "architecture",
            "generalization",
            "correlation",
            "unsupported confidence",
            "negative evidence",
            "flag-bearing",
            "target-specific details",
        )
    )
    assert all(
        code in critic
        for code in (
            "unsupported_claim",
            "missing_prerequisite",
            "missing_exception",
            "context_omission",
            "overgeneralization",
            "origin_mismatch",
            "unsafe_material",
            "lost_negative_evidence",
            "invalid_provenance",
        )
    )
    assert "the source does not support the claim." in critic
    assert "accepted must be true" in critic
    assert "no material findings" in critic

    repair = " ".join(REPAIR_PROMPT.casefold().split())
    assert "only" in repair
    assert "critic findings" in repair
    assert "source segments" in repair
