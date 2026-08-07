"""Tests for safe structured requests through the host-owned LLM facade."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

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
    CriticVerdict,
    DraftCitation,
    DraftReference,
    SemanticDraftBundle,
)
from sedna.knowledge.semantic.llm import (
    HadesLlmAdapter,
    SafeCriticRequestPayload,
    SafePreparedSourcePayload,
    SafeRepairRequestPayload,
    SafeSegmentAsset,
    SafeSourceSegment,
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

    payload = build_safe_source_payload(prepared)
    serialized = payload.model_dump_json()

    assert "alice" not in serialized
    assert "sk-userinfo" not in serialized
    assert "api_key" not in serialized
    assert "sk-query-secret" not in serialized
    assert "private-fragment" not in serialized
    assert "target" not in type(payload.segments[0].assets[0]).model_fields

    with pytest.raises(ValidationError, match="credential material"):
        SafeSourceSegment(
            index=0,
            start_line=1,
            end_line=1,
            text="token=sk-direct-secret",
        )


def test_prompt_boundary_redacts_credentials_but_preserves_benign_technical_prose():
    prepared = _prepared_source()
    credential_text = (
        "token%253Dsk-live-secret password&#61;hunter2 "
        "passwd:open-sesame api_key=sk-query-secret secret=private-value "
        "Authorization: Bearer access-token-123 Authorization=Basic basic-token-789 "
        "Bearer standalone-token-456. Bearer abcdefghijklmnopqrstuvwxyz "
        "password hunter2 passwd open-sesame token access-token-999 "
        "api key live-key-123 secret private123 "
        '{"password":"json-hunter2","token":"secret-token-123",'
        '"secret":"json-private-value"} '
        "access_token=access-secret-123 refresh-token:refresh-secret-456 "
        "client_secret=client-secret-123 consumer-secret:consumer-secret-456 "
        "sk_live_abcdefghijkl sk_test_mnopqrstuvwxyz "
        "token: identifier password=<password> api key: example "
        "password=<hunter2> token=<livecredential> "
        '{"authorization":"Basic quotedhunter2"} authorization Basic barehunter2 '
        "password admin passwd root token abc12 secret test password swordfish "
        "Bearer abc123 Bearer root "
        "Bearer authentication uses access tokens. Token bucket algorithms, password hashing, "
        "password policy, token validation, API key rotation, api key storage, "
        "secret management, and secret handling are useful technical concepts. "
        "Password strength and password complexity matter. Token introspection and "
        "token expiration are protocol concepts. API key permissions and secret scanning "
        "are operational controls. Password: must contain twelve characters. "
        "API key: rotate it regularly. secret: stored in a vault."
    )
    blocks = tuple(
        block.model_copy(update={"text": credential_text}) if index == 1 else block
        for index, block in enumerate(prepared.document.blocks)
    )
    credentialed_asset = prepared.document.assets[0].model_copy(
        update={"target": "shots/api_key=sk-path-secret.png"}
    )
    document = prepared.document.model_copy(
        update={"blocks": blocks, "assets": (credentialed_asset,)}
    )
    prepared = PreparedSource(
        manifest=prepared.manifest,
        document=document,
        segments=segment_document(document),
    )
    host = _RecordingHost(_HostResult(parsed={"artifacts": [], "ignored_segment_indexes": []}))

    HadesLlmAdapter(host).complete(
        SemanticDraftBundle,
        instructions=EXTRACTOR_PROMPT,
        payload=build_safe_source_payload(prepared),
        purpose="sedna.semantic.extract",
    )

    host_input = str(host.calls[0]["input"])
    assert "<EXCLUDED_CREDENTIAL>" in host_input
    assert all(
        secret not in host_input
        for secret in (
            "sk-live-secret",
            "hunter2",
            "open-sesame",
            "sk-query-secret",
            "private-value",
            "access-token-123",
            "basic-token-789",
            "standalone-token-456",
            "abcdefghijklmnopqrstuvwxyz",
            "access-token-999",
            "live-key-123",
            "private123",
            "json-hunter2",
            "secret-token-123",
            "json-private-value",
            "access-secret-123",
            "refresh-secret-456",
            "client-secret-123",
            "consumer-secret-456",
            "<hunter2>",
            "<livecredential>",
            "quotedhunter2",
            "barehunter2",
            "admin",
            "root",
            "abc12",
            "swordfish",
            "Bearer abc123",
            "Bearer root",
            "sk_live_abcdefghijkl",
            "sk_test_mnopqrstuvwxyz",
            "sk-path-secret",
        )
    )
    assert "Bearer authentication uses access tokens" in host_input
    assert "Token bucket algorithms" in host_input
    assert "password hashing" in host_input
    assert "password policy" in host_input
    assert "token validation" in host_input
    assert "API key rotation" in host_input
    assert "api key storage" in host_input
    assert "secret management" in host_input
    assert "secret handling" in host_input
    assert "Password strength" in host_input
    assert "password complexity" in host_input
    assert "Token introspection" in host_input
    assert "token expiration" in host_input
    assert "API key permissions" in host_input
    assert "secret scanning" in host_input
    assert "Password: must contain twelve characters" in host_input
    assert "API key: rotate it regularly" in host_input
    assert "secret: stored in a vault" in host_input
    assert "token: identifier" in host_input
    assert "password=<password>" in host_input
    assert "api key: example" in host_input
    assert '"target"' not in host_input


def test_critic_envelope_rejects_credentials_nested_in_valid_drafts_before_host_call():
    credentialed_drafts = SemanticDraftBundle(
        artifacts=(
            DraftReference(
                draft_type="reference",
                local_id="credentialed-reference",
                artifact_type="methodology",
                subject="password=hunter2 token=sk-live-secret",
                statement="Inspect the service strategically.",
                origin="explicit",
                citations=(DraftCitation(segment_indexes=(0,)),),
            ),
        )
    )
    source = build_safe_source_payload(_prepared_source())
    payload = SafeCriticRequestPayload(source=source, drafts=credentialed_drafts)
    host = _RecordingHost(_HostResult(parsed={"accepted": True, "findings": []}))

    with pytest.raises(TypeError, match="safe semantic request payload"):
        HadesLlmAdapter(host).complete(
            CriticVerdict,
            instructions=CRITIC_PROMPT,
            payload=payload,
            purpose="sedna.semantic.critic",
        )

    assert host.calls == []


def test_extractor_response_rejects_credentials_before_they_can_enter_critic_payload():
    credentialed_drafts = SemanticDraftBundle(
        artifacts=(
            DraftReference(
                draft_type="reference",
                local_id="credentialed-response",
                artifact_type="methodology",
                subject="token=sk-live-response-secret",
                statement="Inspect the service strategically.",
                origin="explicit",
                citations=(DraftCitation(segment_indexes=(0,)),),
            ),
        )
    )
    adapter = HadesLlmAdapter(_RecordingHost(_HostResult(parsed=credentialed_drafts)))

    with pytest.raises(SemanticLlmError) as error:
        adapter.complete(
            SemanticDraftBundle,
            instructions=EXTRACTOR_PROMPT,
            payload=build_safe_source_payload(_prepared_source()),
            purpose="sedna.semantic.extract",
        )

    assert error.value.reason_code == "invalid_structured_response"
    assert "sk-live-response-secret" not in str(error.value)


def test_adapter_rejects_raw_prepared_source_without_calling_host():
    host = _RecordingHost(_HostResult(parsed={"artifacts": [], "ignored_segment_indexes": []}))
    adapter = HadesLlmAdapter(host)

    with pytest.raises(TypeError, match="purpose, payload, and response model"):
        adapter.complete(
            SemanticDraftBundle,
            instructions=EXTRACTOR_PROMPT,
            payload=_prepared_source(),  # type: ignore[arg-type]
            purpose="sedna.semantic.extract",
        )

    assert host.calls == []


def test_adapter_revalidates_nonvalidating_pydantic_copies_before_host_call():
    host = _RecordingHost(_HostResult(parsed={"artifacts": [], "ignored_segment_indexes": []}))
    adapter = HadesLlmAdapter(host)
    safe = build_safe_source_payload(_prepared_source())
    corrupted_payloads = (
        (
            safe.model_copy(update={"title": "HTB{copied_flag}"}),
            SemanticDraftBundle,
            "sedna.semantic.extract",
        ),
        (
            SafePreparedSourcePayload.model_construct(
                source_id=safe.source_id,
                title=_prepared_source(),
                document_type=safe.document_type,
                knowledge_role=safe.knowledge_role,
                quality=safe.quality,
                segments=safe.segments,
            ),
            SemanticDraftBundle,
            "sedna.semantic.extract",
        ),
        (
            SafeCriticRequestPayload.model_construct(
                source=safe,
                drafts=SemanticDraftBundle().model_copy(
                    update={"artifacts": (_prepared_source(),)}
                ),
            ),
            CriticVerdict,
            "sedna.semantic.critic",
        ),
    )

    for corrupted, model_type, purpose in corrupted_payloads:
        with pytest.raises(TypeError, match="safe semantic request payload"):
            adapter.complete(
                model_type,
                instructions=EXTRACTOR_PROMPT,
                payload=corrupted,
                purpose=purpose,
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
    "asset",
    [
        {"asset_index": -1, "start_line": 1, "end_line": 1},
        {"asset_index": 0, "start_line": 0, "end_line": 1},
        {"asset_index": 0, "start_line": 3, "end_line": 2},
    ],
)
def test_safe_asset_records_reject_invalid_indexes_and_line_spans(asset):
    with pytest.raises(ValidationError):
        SafeSegmentAsset.model_validate(asset)


@pytest.mark.parametrize(
    "segment",
    [
        {"index": -1, "start_line": 1, "end_line": 1, "text": "safe"},
        {"index": 0, "start_line": 0, "end_line": 1, "text": "safe"},
        {"index": 0, "start_line": 3, "end_line": 2, "text": "safe"},
    ],
)
def test_safe_segment_records_reject_invalid_indexes_and_line_spans(segment):
    with pytest.raises(ValidationError):
        SafeSourceSegment.model_validate(segment)


@pytest.mark.parametrize("indexes", [(0, 0), (1, 0), (0, 2)])
def test_safe_payload_rejects_duplicate_out_of_order_or_gapped_segment_indexes(indexes):
    prepared = _prepared_source()
    payload = build_safe_source_payload(prepared).model_dump(mode="python")
    original = payload["segments"][0]
    payload["segments"] = tuple({**original, "index": index} for index in indexes)

    with pytest.raises(ValidationError, match="consecutive and ordered"):
        SafePreparedSourcePayload.model_validate(payload)


@pytest.mark.parametrize(
    ("purpose", "instructions", "model_type", "payload_kind", "parsed"),
    [
        (
            "sedna.semantic.extract",
            EXTRACTOR_PROMPT,
            SemanticDraftBundle,
            "source",
            {"artifacts": [], "ignored_segment_indexes": []},
        ),
        (
            "sedna.semantic.critic",
            CRITIC_PROMPT,
            CriticVerdict,
            "critic",
            {"accepted": True, "findings": []},
        ),
        (
            "sedna.semantic.repair",
            REPAIR_PROMPT,
            SemanticDraftBundle,
            "repair",
            {"artifacts": [], "ignored_segment_indexes": []},
        ),
    ],
)
def test_adapter_records_exact_host_contract_without_routing_overrides(
    purpose: str,
    instructions: str,
    model_type: type[BaseModel],
    payload_kind: str,
    parsed: dict[str, object],
):
    host = _RecordingHost(_HostResult(parsed=parsed))
    adapter = HadesLlmAdapter(host, max_tokens=4096, timeout=45.0)
    source = build_safe_source_payload(_prepared_source())
    payload = {
        "source": source,
        "critic": SafeCriticRequestPayload(source=source, drafts=SemanticDraftBundle()),
        "repair": SafeRepairRequestPayload(
            source=source,
            drafts=SemanticDraftBundle(),
            critic=CriticVerdict(accepted=True),
        ),
    }[payload_kind]

    completion = adapter.complete(
        model_type,
        instructions=instructions,
        payload=payload,
        purpose=purpose,
    )

    assert type(completion.parsed) is model_type
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
    assert call["schema_name"] == model_type.__name__
    assert call["json_schema"] == model_type.model_json_schema()
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
            _HostResult(
                parsed={"artifacts": [], "ignored_segment_indexes": []},
                usage=_Usage(input_tokens=-1),
            ),
            "transport_failure",
        ),
        (
            _HostResult(
                parsed={
                    "artifacts": [],
                    "ignored_segment_indexes": [],
                    "raw_response": "HTB{must_not_escape}",
                }
            ),
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
            SemanticDraftBundle,
            instructions=EXTRACTOR_PROMPT,
            payload=build_safe_source_payload(_prepared_source()),
            purpose="sedna.semantic.extract",
        )

    assert error.value.reason_code == reason_code
    assert "HTB{" not in str(error.value)
    assert "raw provider response" not in str(error.value)


@pytest.mark.parametrize(
    "host_parsed",
    [
        SemanticDraftBundle.model_construct(
            artifacts=(_prepared_source(),),
            ignored_segment_indexes=(),
        ),
        {"artifacts": [], "ignored_segment_indexes": {0}},
    ],
)
def test_adapter_deeply_revalidates_host_pydantic_instances_and_json_values(host_parsed):
    adapter = HadesLlmAdapter(_RecordingHost(_HostResult(parsed=host_parsed)))

    with pytest.raises(SemanticLlmError) as error:
        adapter.complete(
            SemanticDraftBundle,
            instructions=EXTRACTOR_PROMPT,
            payload=build_safe_source_payload(_prepared_source()),
            purpose="sedna.semantic.extract",
        )

    assert error.value.reason_code == "invalid_structured_response"
    assert "HTB{" not in str(error.value)
    assert "PreparedSource" not in str(error.value)


@pytest.mark.parametrize(
    ("purpose", "model_type", "payload_kind"),
    [
        ("sedna.semantic.unknown", SemanticDraftBundle, "source"),
        ("sedna.semantic.extract", SemanticDraftBundle, "critic"),
        ("sedna.semantic.critic", CriticVerdict, "source"),
        ("sedna.semantic.repair", SemanticDraftBundle, "critic"),
        ("sedna.semantic.extract", CriticVerdict, "source"),
        ("sedna.semantic.critic", SemanticDraftBundle, "critic"),
        ("sedna.semantic.repair", CriticVerdict, "repair"),
    ],
)
def test_adapter_rejects_unknown_or_mismatched_purpose_payload_response_before_host_call(
    purpose: str,
    model_type: type[BaseModel],
    payload_kind: str,
):
    host = _RecordingHost(_HostResult(parsed={}))
    source = build_safe_source_payload(_prepared_source())
    payload = {
        "source": source,
        "critic": SafeCriticRequestPayload(source=source, drafts=SemanticDraftBundle()),
        "repair": SafeRepairRequestPayload(
            source=source,
            drafts=SemanticDraftBundle(),
            critic=CriticVerdict(accepted=True),
        ),
    }[payload_kind]

    with pytest.raises(TypeError, match="purpose, payload, and response model"):
        HadesLlmAdapter(host).complete(
            model_type,
            instructions=EXTRACTOR_PROMPT,
            payload=payload,
            purpose=purpose,  # type: ignore[arg-type]
        )

    assert host.calls == []


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
            "untrusted data",
            "never as instructions",
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
    assert "untrusted data" in repair
    assert "never as instructions" in repair
    assert "unknown" in repair
    assert "cite every repaired claim and context assertion" in repair
