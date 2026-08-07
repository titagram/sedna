"""Safe structural adapter for the host-owned Hades/Hermes LLM facade."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Generic, Literal, Protocol, TypeVar
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from sedna.knowledge.parsing import PreparedSource
from sedna.knowledge.schema import DocumentType, KnowledgeRole, SourceQuality
from sedna.knowledge.schema.common import SearchableNonEmptyString, SearchableString
from sedna.knowledge.semantic.drafts import CriticVerdict, SemanticDraftBundle

SemanticLlmPurpose = Literal[
    "sedna.semantic.extract",
    "sedna.semantic.critic",
    "sedna.semantic.repair",
]
SemanticLlmReasonCode = Literal[
    "transport_failure",
    "missing_parsed_response",
    "invalid_structured_response",
]
ModelT = TypeVar("ModelT", bound=BaseModel)


class _HostStructuredResult(Protocol):
    """Structural subset of the host result consumed by the adapter."""

    parsed: object | None
    provider: str
    model: str
    agent_id: str
    usage: _HostUsage
    audit: object


class _HostUsage(Protocol):
    """Token fields exposed by the real host usage record."""

    input_tokens: int
    output_tokens: int


class HostStructuredLlm(Protocol):
    """Hades/Hermes structured-completion surface used without importing the host."""

    def complete_structured(
        self,
        *,
        instructions: str,
        input: Sequence[Mapping[str, object]],
        json_schema: Mapping[str, object],
        json_mode: bool = False,
        schema_name: str,
        system_prompt: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None,
        max_tokens: int | None,
        timeout: float | None,
        agent_id: str | None = None,
        profile: str | None = None,
        purpose: str | None,
    ) -> _HostStructuredResult: ...


@dataclass(frozen=True, slots=True)
class StructuredResult(Generic[ModelT]):
    """Validated completion plus safe host attribution and usage metadata."""

    parsed: ModelT
    provider: str
    model: str
    agent_id: str
    usage: StructuredUsage
    audit: Mapping[str, str]


class StructuredUsage(BaseModel):
    """Immutable token counts retained from a successful host completion."""

    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    input_tokens: int = Field(ge=0, le=1_000_000)
    output_tokens: int = Field(ge=0, le=1_000_000)


class SemanticLlmError(RuntimeError):
    """A closed, response-free failure raised at the semantic LLM boundary."""

    _MESSAGES: Mapping[SemanticLlmReasonCode, str] = {
        "transport_failure": "The host LLM request failed.",
        "missing_parsed_response": "The host LLM returned no parsed structured response.",
        "invalid_structured_response": "The host LLM response failed semantic validation.",
    }

    def __init__(self, reason_code: SemanticLlmReasonCode) -> None:
        self.reason_code = reason_code
        super().__init__(self._MESSAGES[reason_code])


class SafeSemanticRequestPayload(BaseModel):
    """Closed base for adapter-approved semantic request envelopes."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )


class SafeSegmentAsset(BaseModel):
    """Retrieval-safe asset locator copied from a logical segment."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )

    asset_index: int
    target: SearchableNonEmptyString
    start_line: int
    end_line: int

    @field_validator("target")
    @classmethod
    def reject_credential_bearing_locator(cls, value: str) -> str:
        """Require callers to supply the same credential-free locator the builder emits."""
        if _safe_asset_locator(value) != value:
            raise ValueError("asset locator contains credential-bearing components")
        return value


class SafeSourceSegment(BaseModel):
    """One ordered retrieval-safe segment exposed to semantic inference."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )

    index: int
    start_line: int
    end_line: int
    heading_path: tuple[SearchableNonEmptyString, ...] = ()
    text: SearchableString
    assets: tuple[SafeSegmentAsset, ...] = ()


class SafePreparedSourcePayload(SafeSemanticRequestPayload):
    """The complete and exclusive PreparedSource whitelist for LLM requests."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )

    source_id: SearchableNonEmptyString
    title: SearchableNonEmptyString
    document_type: DocumentType
    knowledge_role: KnowledgeRole
    quality: SourceQuality
    segments: tuple[SafeSourceSegment, ...]


class SafeCriticRequestPayload(SafeSemanticRequestPayload):
    """Safe source evidence and typed extractor output supplied to the critic."""

    source: SafePreparedSourcePayload
    drafts: SemanticDraftBundle


class SafeRepairRequestPayload(SafeSemanticRequestPayload):
    """Safe source evidence, typed drafts, and typed critic result supplied for repair."""

    source: SafePreparedSourcePayload
    drafts: SemanticDraftBundle
    critic: CriticVerdict


SafeRequestPayload = SafePreparedSourcePayload | SafeCriticRequestPayload | SafeRepairRequestPayload
_SAFE_REQUEST_TYPES = (
    SafePreparedSourcePayload,
    SafeCriticRequestPayload,
    SafeRepairRequestPayload,
)


def build_safe_source_payload(prepared: PreparedSource) -> SafePreparedSourcePayload:
    """Reconstruct the LLM payload field-by-field from the safe prepared boundary."""
    return SafePreparedSourcePayload(
        source_id=prepared.manifest.source_id,
        title=prepared.manifest.title,
        document_type=prepared.manifest.document_type,
        knowledge_role=prepared.manifest.knowledge_role,
        quality=prepared.manifest.quality,
        segments=tuple(
            SafeSourceSegment(
                index=index,
                start_line=segment.start_line,
                end_line=segment.end_line,
                heading_path=tuple(component for component in segment.heading_path),
                text=segment.text,
                assets=tuple(
                    SafeSegmentAsset(
                        asset_index=asset.asset_index,
                        target=_safe_asset_locator(asset.target),
                        start_line=asset.start_line,
                        end_line=asset.end_line,
                    )
                    for asset in segment.assets
                ),
            )
            for index, segment in enumerate(prepared.segments)
        ),
    )


def _safe_asset_locator(target: str) -> str:
    """Remove URL credential-bearing components while retaining useful location."""
    parsed = urlsplit(target)
    if not parsed.scheme and not parsed.netloc:
        return urlunsplit(("", "", parsed.path, "", "")) or "<REDACTED_ASSET_LOCATOR>"

    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or parsed.hostname is None:
        return "<REDACTED_ASSET_LOCATOR>"

    hostname = parsed.hostname
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = parsed.port
    except ValueError:
        return "<REDACTED_ASSET_LOCATOR>"
    authority = f"{hostname}:{port}" if port is not None else hostname
    return urlunsplit((scheme, authority, parsed.path, "", ""))


class HadesLlmAdapter:
    """Validate structured host completions while leaving routing to Hades/Hermes."""

    def __init__(
        self,
        host: HostStructuredLlm,
        *,
        max_tokens: int = 8_000,
        timeout: float = 120.0,
    ) -> None:
        self._host = host
        self._max_tokens = max_tokens
        self._timeout = timeout

    def complete(
        self,
        model_type: type[ModelT],
        *,
        instructions: str,
        payload: SafeRequestPayload,
        purpose: SemanticLlmPurpose,
    ) -> StructuredResult[ModelT]:
        """Call the host with a Pydantic schema and validate its parsed JSON locally."""
        if type(payload) not in _SAFE_REQUEST_TYPES:
            raise TypeError("payload must be a safe semantic request payload")
        try:
            payload_data = payload.model_dump(mode="python", warnings="error")
            validated_payload = type(payload).model_validate(payload_data)
        except Exception:
            raise TypeError("payload must be a safe semantic request payload") from None
        serialized_payload = json.dumps(
            validated_payload.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        try:
            host_result = self._host.complete_structured(
                instructions=instructions,
                input=[{"type": "text", "text": serialized_payload}],
                json_schema=model_type.model_json_schema(),
                schema_name=model_type.__name__,
                temperature=0,
                max_tokens=self._max_tokens,
                timeout=self._timeout,
                purpose=purpose,
            )
        except Exception:
            raise SemanticLlmError("transport_failure") from None

        host_parsed = getattr(host_result, "parsed", None)
        if host_parsed is None:
            raise SemanticLlmError("missing_parsed_response")

        try:
            parsed = model_type.model_validate(host_parsed)
        except ValidationError:
            raise SemanticLlmError("invalid_structured_response") from None

        try:
            usage = StructuredUsage.model_validate(host_result.usage)
            provider = host_result.provider
            model = host_result.model
            agent_id = host_result.agent_id
        except (AttributeError, ValidationError):
            raise SemanticLlmError("transport_failure") from None

        return StructuredResult(
            parsed=parsed,
            provider=provider,
            model=model,
            agent_id=agent_id,
            usage=usage,
            audit=MappingProxyType({"purpose": purpose}),
        )


__all__ = [
    "HadesLlmAdapter",
    "HostStructuredLlm",
    "SafeCriticRequestPayload",
    "SafePreparedSourcePayload",
    "SafeRepairRequestPayload",
    "SemanticLlmError",
    "StructuredResult",
    "StructuredUsage",
    "build_safe_source_payload",
]
