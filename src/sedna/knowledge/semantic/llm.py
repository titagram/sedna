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
_MAX_CREDENTIAL_CLAUSES = 256
_PROVIDER_CREDENTIAL_SUFFIX = (
    r"(?:secret_access_key|access_key_id|application_credentials|session_token|"
    r"client_secret|consumer_secret|refresh_token|access_token|auth_token|id_token|"
    r"private_key|secret_key|access_key|api_key|credentials?|password|passwd|token)"
)
_PROVIDER_CREDENTIAL_LABEL = rf"(?:[A-Za-z][A-Za-z0-9]*_)+{_PROVIDER_CREDENTIAL_SUFFIX}"
_CREDENTIAL_LABEL = (
    rf"(?:{_PROVIDER_CREDENTIAL_LABEL}|authorization|credentials?|password|passwd|"
    r"api[ \t_-]+key|(?:secret|private)[ \t_-]+key|"
    r"(?:access|auth|id|refresh)[ \t_-]+token|token|"
    r"(?:client|consumer)[ \t_-]+secret|secret)"
)
_UNQUOTED_CLAUSE_VALUE = (
    rf"[^.!?,;}}\]\r\n]+?(?=(?:[.!?,;}}\]\r\n]|"
    rf"[ \t]+(?:and|or)[ \t]+[\"']?(?:{_CREDENTIAL_LABEL})"
    r"(?:[\"']?)(?![A-Za-z0-9])|$))"
)
_COMPLETE_CLAUSE_VALUE = rf"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|{_UNQUOTED_CLAUSE_VALUE})"
_SAFE_ASSIGNMENT_PLACEHOLDER = (
    r"(?:example|identifier|placeholder|value|"
    r"<(?:access_token|api_key|credential|password|passwd|refresh_token|secret|token|value)>)"
)
_ASSIGNMENT_VALUE = (
    rf"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|"
    rf"{_SAFE_ASSIGNMENT_PLACEHOLDER}(?=$|[.!?,;}}\]\r\n])|"
    r"(?:(?:bearer|basic)[ \t]+\S+)|\S+)"
)
_ASSIGNMENT_PLACEHOLDER_FORM = (
    rf"(?:\"{_SAFE_ASSIGNMENT_PLACEHOLDER}\"|'"
    rf"{_SAFE_ASSIGNMENT_PLACEHOLDER}'|{_SAFE_ASSIGNMENT_PLACEHOLDER})"
)
_CREDENTIAL_CLAUSE_RE = re.compile(
    rf"(?P<label_quote>[\"']?)\b(?P<label>{_CREDENTIAL_LABEL})"
    r"(?P=label_quote)(?![A-Za-z0-9])"
    rf"(?:(?P<equals>\s*=\s*)(?P<equals_value>{_ASSIGNMENT_VALUE})|"
    rf"(?P<colon>[ \t]*:[ \t]*)(?P<colon_value>{_COMPLETE_CLAUSE_VALUE})|"
    rf"(?P<copula>[ \t]+(?:is|was|equals?)[ \t]+)"
    rf"(?P<copula_value>{_COMPLETE_CLAUSE_VALUE})|"
    rf"(?P<spacing>[ \t]+)(?P<spacing_value>{_COMPLETE_CLAUSE_VALUE}))",
    re.IGNORECASE,
)
_ASSIGNMENT_PLACEHOLDER_TAIL_RE = re.compile(
    rf"[\"']?\b(?:{_CREDENTIAL_LABEL})[\"']?\s*=\s*"
    rf"{_ASSIGNMENT_PLACEHOLDER_FORM}\s+\S",
    re.IGNORECASE,
)
_BEARER_CANDIDATE_RE = re.compile(
    r"\bbearer\s+(?P<value>[A-Za-z0-9._~+/\-]+)",
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
_BENIGN_TECHNICAL_CONTEXTS = frozenset(
    {
        "authentication",
        "authorization",
        "bucket",
        "claims",
        "complexity",
        "credentials",
        "database",
        "endpoint",
        "exchange",
        "expiration",
        "file",
        "format",
        "handling",
        "hashing",
        "header",
        "introspection",
        "management",
        "manager",
        "permissions",
        "policy",
        "reset",
        "revocation",
        "rotation",
        "scanning",
        "scheme",
        "sharing",
        "storage",
        "strength",
        "token",
        "tokens",
        "validation",
    }
)
_BENIGN_TECHNICAL_CONTEXT_RE = re.compile(
    rf"(?:{'|'.join(sorted(_BENIGN_TECHNICAL_CONTEXTS))})"
    r"(?:[ \t]+(?:algorithms?|discovery|matters?|selection))?"
    r"(?:[ \t]+uses?[ \t]+(?:access[ \t]+)?tokens?)?"
    r"(?:[ \t]+(?:is|are)[ \t]+(?:useful[ \t]+)?"
    r"(?:(?:operational|protocol|security|technical)[ \t]+)?"
    r"(?:concepts?|controls?))?",
    re.IGNORECASE,
)
_POLICY_QUANTITY = (
    r"(?:(?:at[ \t]+(?:least|most)|exactly)[ \t]+)?"
    r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r"[ \t]+(?:characters?|digits?|letters?|symbols?|words?)"
)
_POLICY_CADENCE = r"(?:automatically|periodically|regularly|securely)"
_POLICY_STORAGE_LOCATION = (
    r"(?:(?:a|an|the)[ \t]+)?(?:hardware[ \t]+security[ \t]+module|keychain|"
    r"keystore|secret[ \t]+manager|vault)"
)
_BENIGN_POLICY_CONTEXT_RE = re.compile(
    rf"(?:(?:must|should)[ \t]+(?:contain|have|include)[ \t]+{_POLICY_QUANTITY}|"
    rf"(?:must|should)[ \t]+be[ \t]+(?:{_POLICY_QUANTITY}|"
    r"(?:complex|confidential|long|private|random|strong|unique)|"
    rf"(?:rotated|replaced|revoked)[ \t]+{_POLICY_CADENCE}|"
    rf"(?:kept|protected|stored)[ \t]+(?:at|in)[ \t]+{_POLICY_STORAGE_LOCATION})|"
    r"(?:can|may|must|should)[ \t]+remain[ \t]+(?:confidential|private|secret)|"
    rf"(?:rotate|replace|revoke)[ \t]+(?:it|them)[ \t]+{_POLICY_CADENCE}|"
    rf"(?:kept|protected|stored)[ \t]+(?:at|in)[ \t]+{_POLICY_STORAGE_LOCATION}|"
    r"(?:deprecated|disabled|enabled|optional|required|recommended))",
    re.IGNORECASE,
)
_CLAUSE_SEPARATOR_GROUPS = ("equals", "colon", "copula", "spacing")
_CLAUSE_VALUE_GROUPS = (
    "equals_value",
    "colon_value",
    "copula_value",
    "spacing_value",
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
    if _ASSIGNMENT_PLACEHOLDER_TAIL_RE.search(text):
        return EXCLUDED_CREDENTIAL
    sanitized, clause_count = _CREDENTIAL_CLAUSE_RE.subn(
        _redact_credential_clause,
        text,
    )
    sanitized, bearer_count = _BEARER_CANDIDATE_RE.subn(
        _redact_bearer_candidate,
        sanitized,
    )
    sanitized, sk_count = _COMMON_SK_TOKEN_RE.subn(EXCLUDED_CREDENTIAL, sanitized)
    if clause_count + bearer_count + sk_count > _MAX_CREDENTIAL_CLAUSES:
        return EXCLUDED_CREDENTIAL
    return sanitized


def _redact_bearer_candidate(match: re.Match[str]) -> str:
    value = match.group("value")
    if value.casefold() in _BENIGN_BEARER_FOLLOWERS:
        return match.group(0)
    return f"Bearer {EXCLUDED_CREDENTIAL}"


def _redact_credential_clause(match: re.Match[str]) -> str:
    if _is_benign_credential_clause(match):
        return match.group(0)
    label_quote = match.group("label_quote")
    separator = _matched_group(match, _CLAUSE_SEPARATOR_GROUPS)
    return (
        f"{label_quote}{match.group('label')}{label_quote}{separator}"
        f"{_redacted_clause_value(match)}"
    )


def _is_benign_credential_clause(match: re.Match[str]) -> bool:
    value = _matched_group(match, _CLAUSE_VALUE_GROUPS)
    if _is_explicit_credential_placeholder(value):
        return True
    if value.startswith(('"', "'")):
        return False

    if match.group("equals") is not None:
        return False
    if match.group("colon") is not None or match.group("copula") is not None:
        return bool(_BENIGN_POLICY_CONTEXT_RE.fullmatch(value.strip()))
    return bool(
        _BENIGN_TECHNICAL_CONTEXT_RE.fullmatch(value.strip())
        or _BENIGN_POLICY_CONTEXT_RE.fullmatch(value.strip())
    )


def _redacted_clause_value(match: re.Match[str]) -> str:
    value = _matched_group(match, _CLAUSE_VALUE_GROUPS)
    quote = value[0] if value.startswith(('"', "'")) else ""
    unquoted = value[1:-1] if quote else value
    scheme = re.match(r"(?P<scheme>bearer|basic)(?P<spacing>[ \t]+)", unquoted, re.IGNORECASE)
    if _normalize_credential_label(match.group("label")) == "authorization" and scheme:
        replacement = f"{scheme.group('scheme')}{scheme.group('spacing')}{EXCLUDED_CREDENTIAL}"
    else:
        replacement = EXCLUDED_CREDENTIAL
    return f"{quote}{replacement}{quote}"


def _matched_group(match: re.Match[str], names: tuple[str, ...]) -> str:
    for name in names:
        value = match.group(name)
        if value is not None:
            return value
    raise AssertionError("credential clause regex matched without a required group")


def _is_explicit_credential_placeholder(value: str) -> bool:
    normalized = value.strip().strip("\"'").casefold()
    if normalized.rstrip(".!?") == EXCLUDED_CREDENTIAL.casefold():
        return True
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
