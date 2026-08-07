"""Safe structural adapter for the host-owned Hades/Hermes LLM facade."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Generic, Literal, Protocol, Self, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from sedna.knowledge.parsing import PreparedSource
from sedna.knowledge.parsing.sanitize import _recursively_decode
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

EXCLUDED_CREDENTIAL = "<EXCLUDED_CREDENTIAL>"
_CREDENTIAL_LABEL = (
    r"(?:password|passwd|(?:(?:access|auth|id|refresh)[\s_-]?)?token|"
    r"api[\s_-]?key|(?:(?:client|consumer)[\s_-]?)?secret)"
)
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    rf"(?P<quote>[\"']?)\b(?P<label>{_CREDENTIAL_LABEL})(?P=quote)"
    r"(?P<separator>\s*[:=]\s*)"
    r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;}\]]+)",
    re.IGNORECASE,
)
_AUTHORIZATION_CREDENTIAL_RE = re.compile(
    r"(?P<key_quote>[\"']?)\bauthorization(?P=key_quote)"
    r"(?P<separator>\s*[:=]\s*|\s+)"
    r"(?P<value_quote>[\"']?)"
    r"(?P<scheme>bearer\s+|basic\s+)?"
    r"(?P<value>[^\"'\s,;}\]]+)"
    r"(?P=value_quote)",
    re.IGNORECASE,
)
_BEARER_CANDIDATE_RE = re.compile(
    r"\bbearer\s+(?P<value>[A-Za-z0-9._~+/\-]+)",
    re.IGNORECASE,
)
_CREDENTIAL_WORD_CANDIDATE_RE = re.compile(
    rf"\b(?P<label>{_CREDENTIAL_LABEL})\s+"
    r"(?P<value>[A-Za-z0-9._~+/\-]+)",
    re.IGNORECASE,
)
_COMMON_SK_TOKEN_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{6,}|sk_(?:live|test)_[A-Za-z0-9_-]{6,})\b",
    re.IGNORECASE,
)
_BENIGN_BEARER_FOLLOWERS = frozenset(
    {"authentication", "authorization", "credentials", "header", "scheme", "token", "tokens"}
)
_EXPLICIT_CREDENTIAL_PLACEHOLDERS = frozenset({"example", "identifier", "placeholder", "value"})
_EXPLICIT_ANGLE_PLACEHOLDERS = frozenset(
    {
        "access_token",
        "api_key",
        "credential",
        "password",
        "passwd",
        "refresh_token",
        "secret",
        "token",
        "value",
    }
)
_BENIGN_CREDENTIAL_CONCEPTS: Mapping[str, frozenset[str]] = {
    "password": frozenset(
        {
            "authentication",
            "complexity",
            "hashing",
            "policy",
            "reset",
            "storage",
            "strength",
        }
    ),
    "passwd": frozenset({"database", "file"}),
    "token": frozenset(
        {
            "bucket",
            "exchange",
            "expiration",
            "format",
            "identifier",
            "introspection",
            "validation",
        }
    ),
    "access token": frozenset({"expiration", "validation"}),
    "auth token": frozenset({"format", "validation"}),
    "id token": frozenset({"claims", "validation"}),
    "refresh token": frozenset({"rotation", "validation"}),
    "api key": frozenset({"management", "permissions", "rotation", "storage"}),
    "secret": frozenset({"handling", "management", "rotation", "scanning", "storage"}),
    "client secret": frozenset({"rotation", "storage"}),
    "consumer secret": frozenset({"rotation", "storage"}),
}
_BENIGN_COLON_ADVICE_RE = re.compile(
    r"(?:must|should)\s+(?:be|contain|have|include)\b|"
    r"(?:rotate|replace|revoke)\s+(?:it|them)\b|"
    r"(?:kept|managed|protected|rotated|stored)\s+(?:at|by|in)\b",
    re.IGNORECASE,
)


def _sanitize_prompt_text(text: str) -> str:
    """Redact credential forms after bounded recursive URL/HTML decoding."""
    sanitized = _sanitize_credentials_once(text)
    decoded = _recursively_decode(text)
    if not decoded.stable:
        return EXCLUDED_CREDENTIAL
    decoded_sanitized = _sanitize_credentials_once(decoded.value)
    return decoded_sanitized if decoded_sanitized != decoded.value else sanitized


def _sanitize_credentials_once(text: str) -> str:
    sanitized = _AUTHORIZATION_CREDENTIAL_RE.sub(
        _redact_authorization_credential,
        text,
    )
    sanitized = _CREDENTIAL_ASSIGNMENT_RE.sub(
        _redact_assignment_credential,
        sanitized,
    )
    sanitized = _BEARER_CANDIDATE_RE.sub(_redact_bearer_candidate, sanitized)
    sanitized = _CREDENTIAL_WORD_CANDIDATE_RE.sub(
        _redact_credential_word_candidate,
        sanitized,
    )
    return _COMMON_SK_TOKEN_RE.sub(EXCLUDED_CREDENTIAL, sanitized)


def _redact_bearer_candidate(match: re.Match[str]) -> str:
    value = match.group("value")
    if value.casefold() in _BENIGN_BEARER_FOLLOWERS:
        return match.group(0)
    return f"Bearer {EXCLUDED_CREDENTIAL}"


def _redact_credential_word_candidate(match: re.Match[str]) -> str:
    value = match.group("value")
    label = _normalize_credential_label(match.group("label"))
    if _is_explicit_credential_placeholder(value) or value.casefold() in (
        _BENIGN_CREDENTIAL_CONCEPTS.get(label, frozenset())
    ):
        return match.group(0)
    return f"{match.group('label')} {EXCLUDED_CREDENTIAL}"


def _redact_authorization_credential(match: re.Match[str]) -> str:
    if _is_explicit_credential_placeholder(match.group("value")) or (
        match.group("scheme") is None
        and match.group("value").casefold() in _BENIGN_BEARER_FOLLOWERS
    ):
        return match.group(0)
    key_quote = match.group("key_quote")
    value_quote = match.group("value_quote")
    return (
        f"{key_quote}Authorization{key_quote}{match.group('separator')}"
        f"{value_quote}{match.group('scheme') or ''}{EXCLUDED_CREDENTIAL}{value_quote}"
    )


def _redact_assignment_credential(match: re.Match[str]) -> str:
    if _is_explicit_credential_placeholder(match.group("value")) or (
        not match.group("quote")
        and ":" in match.group("separator")
        and _BENIGN_COLON_ADVICE_RE.match(match.string[match.start("value") :])
    ):
        return match.group(0)
    quote = match.group("quote")
    return f"{quote}{match.group('label')}{quote}{match.group('separator')}{EXCLUDED_CREDENTIAL}"


def _is_explicit_credential_placeholder(value: str) -> bool:
    normalized = value.strip("\"'").casefold()
    if normalized in _EXPLICIT_CREDENTIAL_PLACEHOLDERS:
        return True
    return (
        normalized.startswith("<")
        and normalized.endswith(">")
        and (normalized[1:-1] in _EXPLICIT_ANGLE_PLACEHOLDERS)
    )


def _normalize_credential_label(label: str) -> str:
    return re.sub(r"[\s_-]+", " ", label.casefold()).strip()


def _require_prompt_safe_text(value: str) -> str:
    if _sanitize_prompt_text(value) != value:
        raise ValueError("text contains credential material")
    return value


def _require_credential_free_payload(value: object) -> None:
    if isinstance(value, str):
        _require_prompt_safe_text(value)
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _require_credential_free_payload(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            _require_credential_free_payload(item)


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

    asset_index: int = Field(ge=0)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_line_span(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("asset end_line must not precede start_line")
        return self


class SafeSourceSegment(BaseModel):
    """One ordered retrieval-safe segment exposed to semantic inference."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )

    index: int = Field(ge=0)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    heading_path: tuple[SearchableNonEmptyString, ...] = ()
    text: SearchableString
    assets: tuple[SafeSegmentAsset, ...] = ()

    @field_validator("heading_path")
    @classmethod
    def reject_credential_bearing_heading(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        for component in value:
            _require_prompt_safe_text(component)
        return value

    @field_validator("text")
    @classmethod
    def reject_credential_bearing_text(cls, value: str) -> str:
        return _require_prompt_safe_text(value)

    @model_validator(mode="after")
    def validate_line_span(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("segment end_line must not precede start_line")
        return self


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

    @field_validator("source_id", "title")
    @classmethod
    def reject_credential_bearing_manifest_text(cls, value: str) -> str:
        return _require_prompt_safe_text(value)

    @model_validator(mode="after")
    def validate_segment_order(self) -> Self:
        indexes = tuple(segment.index for segment in self.segments)
        if indexes != tuple(range(len(self.segments))):
            raise ValueError("segment indexes must be consecutive and ordered from zero")
        return self


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
_CALL_CONTRACTS: Mapping[str, tuple[type[SafeSemanticRequestPayload], type[BaseModel]]] = {
    "sedna.semantic.extract": (SafePreparedSourcePayload, SemanticDraftBundle),
    "sedna.semantic.critic": (SafeCriticRequestPayload, CriticVerdict),
    "sedna.semantic.repair": (SafeRepairRequestPayload, SemanticDraftBundle),
}


def build_safe_source_payload(prepared: PreparedSource) -> SafePreparedSourcePayload:
    """Reconstruct the LLM payload field-by-field from the safe prepared boundary."""
    return SafePreparedSourcePayload(
        source_id=_sanitize_prompt_text(prepared.manifest.source_id),
        title=_sanitize_prompt_text(prepared.manifest.title),
        document_type=prepared.manifest.document_type,
        knowledge_role=prepared.manifest.knowledge_role,
        quality=prepared.manifest.quality,
        segments=tuple(
            SafeSourceSegment(
                index=index,
                start_line=segment.start_line,
                end_line=segment.end_line,
                heading_path=tuple(
                    _sanitize_prompt_text(component) for component in segment.heading_path
                ),
                text=_sanitize_prompt_text(segment.text),
                assets=tuple(
                    SafeSegmentAsset(
                        asset_index=asset.asset_index,
                        start_line=asset.start_line,
                        end_line=asset.end_line,
                    )
                    for asset in segment.assets
                ),
            )
            for index, segment in enumerate(prepared.segments)
        ),
    )


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
        contract = _CALL_CONTRACTS.get(purpose)
        if contract is None or type(payload) is not contract[0] or model_type is not contract[1]:
            raise TypeError("purpose, payload, and response model must match semantic contract")
        try:
            payload_data = payload.model_dump(mode="python", warnings="error")
            validated_payload = type(payload).model_validate(payload_data)
            serialized_payload_data = validated_payload.model_dump(
                mode="json",
                warnings="error",
            )
            _require_credential_free_payload(serialized_payload_data)
        except Exception:
            raise TypeError("payload must be a safe semantic request payload") from None
        serialized_payload = json.dumps(
            serialized_payload_data,
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
            response_data = (
                host_parsed.model_dump(mode="json", warnings="error")
                if isinstance(host_parsed, BaseModel)
                else host_parsed
            )
            response_json = json.dumps(
                response_data,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            parsed = model_type.model_validate(json.loads(response_json))
            _require_credential_free_payload(parsed.model_dump(mode="json", warnings="error"))
        except Exception:
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
    "SafeSegmentAsset",
    "SafeSourceSegment",
    "SafeCriticRequestPayload",
    "SafePreparedSourcePayload",
    "SafeRepairRequestPayload",
    "SemanticLlmError",
    "StructuredResult",
    "StructuredUsage",
    "build_safe_source_payload",
]
