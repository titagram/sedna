"""Safe structural adapter for the host-owned Hades/Hermes LLM facade."""

from __future__ import annotations

import json
import re
import unicodedata
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
_MAX_SPLIT_COMMENT_RECORDS = 16
_LABEL_JOIN = r"(?:[^\S\r\n_-]|[_-])*"
_PROVIDER_CREDENTIAL_SUFFIX = (
    rf"(?:secret{_LABEL_JOIN}access{_LABEL_JOIN}key|"
    rf"access{_LABEL_JOIN}key{_LABEL_JOIN}id|"
    rf"application{_LABEL_JOIN}credentials|session{_LABEL_JOIN}token|"
    rf"client{_LABEL_JOIN}secret|consumer{_LABEL_JOIN}secret|"
    rf"refresh{_LABEL_JOIN}token|access{_LABEL_JOIN}token|"
    rf"auth{_LABEL_JOIN}token|id{_LABEL_JOIN}token|"
    rf"private{_LABEL_JOIN}key|secret{_LABEL_JOIN}key|"
    rf"access{_LABEL_JOIN}key|api{_LABEL_JOIN}key|"
    r"credentials?|password|passwd|token)"
)
_PROVIDER_CREDENTIAL_LABEL = (
    rf"_*(?:[A-Za-z0-9]+_+)*{_PROVIDER_CREDENTIAL_SUFFIX}"
    r"(?:_+[A-Za-z0-9]+)*"
)
_CREDENTIAL_LABEL = (
    rf"(?:{_PROVIDER_CREDENTIAL_LABEL}|authorization|credentials?|password|passwd|"
    rf"api{_LABEL_JOIN}key|(?:access|secret|private){_LABEL_JOIN}key|"
    rf"(?:access|auth|id|refresh){_LABEL_JOIN}token|token|"
    rf"(?:client|consumer){_LABEL_JOIN}secret|secret)"
)
_CREDENTIAL_LABEL_RE = re.compile(
    rf"(?P<label_quote>[\"']?)\b(?P<label>{_CREDENTIAL_LABEL})"
    r"(?P=label_quote)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_IDENTIFIER_LABEL_RE = re.compile(
    r"(?P<label_quote>[\"']?)(?P<label>[A-Za-z_][A-Za-z0-9_-]*)"
    r"(?P=label_quote)(?![A-Za-z0-9_-])"
)
_CREDENTIAL_SYNTAX_RE = re.compile(
    r"(?:(?P<assignment>[^\S\r\n]*[:=][^\S\r\n]*)|"
    r"(?P<copula>[^\S\r\n]+(?:is|was|equals?)[^\S\r\n]+)|"
    r"(?P<spacing>[^\S\r\n]+))",
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
        "managers",
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
    rf"(?P<head>{'|'.join(sorted(_BENIGN_TECHNICAL_CONTEXTS))})\b(?P<tail>.*)",
    re.IGNORECASE,
)
_MODAL_GUIDANCE_RE = re.compile(
    r"(?P<modal>can|may|must|shall|should)[ \t]+(?P<predicate>.+)",
    re.IGNORECASE,
)
_POLICY_STATE_RE = re.compile(
    r"(?:deprecated|disabled|enabled|optional|required|recommended)",
    re.IGNORECASE,
)
_POLICY_QUANTITY_RE = re.compile(
    r"(?:(?:at[ \t]+(?:least|most)|exactly)[ \t]+)?"
    r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r"[ \t]+(?:characters?|digits?|letters?|symbols?|words?)",
    re.IGNORECASE,
)
_SAFE_POLICY_OBJECT_RE = re.compile(
    r"(?:it|them|this|these|(?:a|an|the)[ \t]+(?:access[ \t]+key|api[ \t]+key|"
    r"credential|password|password[ \t]+manager|private[ \t]+key|secret|"
    r"secret[ \t]+manager|token)s?)\b"
    r"(?:[ \t]+(?:automatically|periodically|regularly|securely)|"
    r"[ \t]+(?:after|before|during|when)[ \t]+(?:(?:a|an|the)[ \t]+)?"
    r"(?:incident|maintenance|rotation|support[ \t]+workflow|use)|"
    r"[ \t]+(?:into|on)[ \t]+(?:(?:a|an|the)[ \t]+)?"
    r"(?:documentation|logs?|tickets?)|"
    r"[ \t]+(?:at|in)[ \t]+(?:(?:a|an|the)[ \t]+)?"
    r"(?:hardware[ \t]+security[ \t]+module|keychain|keystore|secret[ \t]+manager|vault))?",
    re.IGNORECASE,
)
_SAFE_STORAGE_LOCATION_RE = re.compile(
    r"(?:(?:a|an|the)[ \t]+)?(?:hardware[ \t]+security[ \t]+module|keychain|"
    r"keystore|secret[ \t]+manager|vault)",
    re.IGNORECASE,
)
_POLICY_ACTION_RE = re.compile(
    r"(?P<verb>avoid|contain|disclose|expose|have|include|paste|protect|replace|"
    r"revoke|rotate|share|store|use|validate)\b[ \t]*(?P<object>.*)",
    re.IGNORECASE,
)
_ENSURE_GUIDANCE_RE = re.compile(
    r"(?:operators?|systems?|teams?|users?)[ \t]+(?P<action>.+)",
    re.IGNORECASE,
)
_NOMINAL_CONTINUATION_RE = re.compile(
    r"(?:algorithms?|discovery|matters?|selection|"
    r"(?:is|are)[ \t]+(?:useful[ \t]+)?"
    r"(?:(?:operational|protocol|security|technical)[ \t]+)?(?:concepts?|controls?)|"
    r"uses?[ \t]+(?:access[ \t]+tokens?|credentials?|password[ \t]+manager)|"
    r"(?:mitigates?|prevents?)[ \t]+(?:(?:offline|online|replay)[ \t]+)?attacks?|"
    r"(?:can|may|must|shall|should)[ \t]+occur[ \t]+(?:after|before|during)[ \t]+"
    r"(?:authentication|authorization|rotation|use|validation)|"
    r"centralizes?[ \t]+(?:access[ \t]+)?(?:control|management)|"
    r"simplif(?:y|ies)[ \t]+(?:authentication|authorization|management|rotation|storage|"
    r"validation)|"
    r"securely[ \t]+stores?(?:[ \t]+(?:access[ \t]+keys?|api[ \t]+keys?|"
    r"credentials?|passwords?|private[ \t]+keys?|secrets?|tokens?))?|"
    r"stores?(?:[ \t]+(?:access[ \t]+keys?|api[ \t]+keys?|credentials?|passwords?|"
    r"private[ \t]+keys?|secrets?|tokens?)(?:[ \t]+securely)?)?|"
    r"resolution(?:[ \t]+(?:across|for|in|of|within)[ \t]+"
    r"(?:regions?|services?|systems?|workflows?))?"
    r"(?:[ \t]+(?:is|are|remains?)[ \t]+"
    r"(?:deterministic|portable|reliable|stable|useful))?)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _CredentialRecordAssessment:
    clauses: int = 0
    unsafe: bool = False


@dataclass(frozen=True, slots=True)
class _CredentialLabelMatch:
    start: int
    end: int
    technical_family: str | None = None


def _sanitize_prompt_text(text: str) -> str:
    """Redact credential forms after bounded recursive URL/HTML decoding."""
    sanitized = _sanitize_credentials_once(text)
    decoded = _recursively_decode(text)
    if not decoded.stable:
        return EXCLUDED_CREDENTIAL
    decoded_sanitized = _sanitize_credentials_once(decoded.value)
    return decoded_sanitized if decoded_sanitized != decoded.value else sanitized


def _sanitize_credentials_once(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    scan_text = "".join(
        char
        for char in normalized
        if unicodedata.category(char) != "Cf" and not unicodedata.category(char).startswith("M")
    )
    records = scan_text.splitlines(keepends=True)
    if not records:
        return text
    if _has_split_credential_assignment(records):
        return EXCLUDED_CREDENTIAL

    clause_count = 0
    for record in records:
        content, line_ending = _split_line_ending(record)
        assessment = _assess_credential_record(
            content,
            terminated=bool(line_ending),
        )
        clause_count += assessment.clauses
        if clause_count > _MAX_CREDENTIAL_CLAUSES:
            return EXCLUDED_CREDENTIAL
        if assessment.unsafe:
            return EXCLUDED_CREDENTIAL

    return text


def _has_split_credential_assignment(records: Sequence[str]) -> bool:
    pending_credential_key = False
    in_block_comment = False
    comment_records = 0
    for record in records:
        content, _ = _split_line_ending(record)
        candidate = content.strip()
        if not candidate:
            continue
        if pending_credential_key:
            if in_block_comment:
                comment_records += 1
                if comment_records > _MAX_SPLIT_COMMENT_RECORDS:
                    return True
                if "*/" not in candidate:
                    continue
                candidate = candidate.split("*/", 1)[1].strip()
                in_block_comment = False
                if not candidate:
                    continue
            while candidate.startswith("/*"):
                comment_records += 1
                if comment_records > _MAX_SPLIT_COMMENT_RECORDS:
                    return True
                if "*/" not in candidate[2:]:
                    in_block_comment = True
                    candidate = ""
                    break
                candidate = candidate.split("*/", 1)[1].strip()
            if not candidate:
                continue
            if candidate.startswith((":", "=")):
                return True
            if _is_comment_only_record(candidate):
                comment_records += 1
                if comment_records > _MAX_SPLIT_COMMENT_RECORDS:
                    return True
                continue
            pending_credential_key = False
            comment_records = 0
        pending_credential_key = any(
            label.end == len(candidate) and not candidate[: label.start].strip(" \t{[,")
            for label in _find_credential_labels(candidate)
        )
    return False


def _is_comment_only_record(record: str) -> bool:
    return record.startswith(("//", "#")) or bool(re.fullmatch(r"/\*.*\*/", record))


def _find_credential_labels(record: str) -> list[_CredentialLabelMatch]:
    candidates = [
        _CredentialLabelMatch(
            start=match.start(),
            end=match.end(),
            technical_family=_technical_identifier_family(match.group("label")),
        )
        for match in _CREDENTIAL_LABEL_RE.finditer(record)
    ]
    candidates.extend(
        _CredentialLabelMatch(
            start=match.start(),
            end=match.end(),
            technical_family=_technical_identifier_family(match.group("label")),
        )
        for match in _IDENTIFIER_LABEL_RE.finditer(record)
        if _is_credential_identifier(match.group("label"))
    )
    selected: list[_CredentialLabelMatch] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (
            item.start,
            -(item.end - item.start),
            -int(item.technical_family is not None),
        ),
    ):
        if any(candidate.start < item.end and item.start < candidate.end for item in selected):
            continue
        selected.append(candidate)
    return selected


def _is_credential_identifier(value: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", value.casefold())
    components = _canonical_identifier_components(value)
    if any(label in compact for label in _COMPOUND_CREDENTIAL_LABELS):
        return True
    if any(component in _SINGLE_CREDENTIAL_LABELS for component in components):
        return True
    if any(compact.endswith(label) for label in _SINGLE_CREDENTIAL_LABELS):
        return True
    qualifier = (
        r"(?:backup|content|data|live|old|payload|plaintext|prod|production|raw|test|val|value)"
    )
    risky_suffix = rf"(?:(?:{qualifier}){{1,3}}\d*|\d+)"
    return any(
        re.search(rf"{label}{risky_suffix}$", compact) for label in _SINGLE_CREDENTIAL_LABELS
    )


def _canonical_identifier_components(value: str) -> tuple[str, ...]:
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    separated = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", separated)
    separated = re.sub(r"(?<=[A-Za-z])(?=[0-9])", "_", separated)
    return tuple(part for part in re.split(r"[^A-Za-z0-9]+", separated.casefold()) if part)


_COMPOUND_CREDENTIAL_LABELS = (
    "secretaccesskey",
    "applicationcredentials",
    "accesskeyid",
    "consumersecret",
    "sessiontoken",
    "clientsecret",
    "accesstoken",
    "privatekey",
    "accesskey",
    "authtoken",
    "secretkey",
    "refreshtoken",
    "idtoken",
    "apikey",
)
_SINGLE_CREDENTIAL_LABELS = (
    "authorization",
    "credentials",
    "credential",
    "password",
    "passwd",
    "secret",
    "token",
)


def _technical_identifier_family(value: str) -> str | None:
    compact = re.sub(r"[^a-z0-9]", "", value.casefold())
    if compact.endswith(("passwordhashalgorithm", "passwordhashalgorithmname")):
        return "hash_algorithm"
    if compact.endswith("secretscanningenabled"):
        return "boolean"
    if compact.endswith(("tokenexpirationseconds", "apikeyrotationdays")):
        return "duration"
    return None


def _is_safe_technical_config_value(value: str, family: str) -> bool:
    candidate = value.strip()
    if " #" in candidate:
        candidate, comment = candidate.split(" #", 1)
        if comment.strip().casefold() not in {
            "default",
            "enabled",
            "hash algorithm",
            "recommended",
            "rotation interval",
            "days between rotations",
            "seconds before expiration",
        }:
            return False
    candidate = candidate.rstrip(".,;}] ")
    if candidate.casefold() == "null":
        return True
    if family == "hash_algorithm":
        return bool(
            re.fullmatch(
                r"[\"']?(?:argon2(?:id)?|bcrypt|pbkdf2|scrypt|sha(?:256|384|512))[\"']?",
                candidate,
                flags=re.IGNORECASE,
            )
        )
    if family == "boolean":
        return candidate.casefold() in {"false", "true"}
    if family == "duration" and re.fullmatch(r"\d+", candidate):
        return int(candidate) <= 31_536_000
    return False


def _assess_credential_record(
    record: str,
    *,
    terminated: bool,
) -> _CredentialRecordAssessment:
    labels = _find_credential_labels(record)
    clauses: list[tuple[_CredentialLabelMatch, re.Match[str]]] = []
    for label in labels:
        syntax = _CREDENTIAL_SYNTAX_RE.match(record, label.end)
        if syntax is not None:
            clauses.append((label, syntax))

    bearer_matches = list(_BEARER_CANDIDATE_RE.finditer(record))
    sk_matches = list(_COMMON_SK_TOKEN_RE.finditer(record))
    count = len(clauses) + len(bearer_matches) + len(sk_matches)
    unsafe = bool(sk_matches) or any(
        match.group("value").casefold() not in _BENIGN_BEARER_FOLLOWERS for match in bearer_matches
    )
    for index, (label, syntax) in enumerate(clauses):
        value_end = clauses[index + 1][0].start if index + 1 < len(clauses) else len(record)
        value = record[syntax.end() : value_end]
        assignment = syntax.group("assignment")
        if assignment is not None:
            if label.technical_family and _is_safe_technical_config_value(
                value, label.technical_family
            ):
                continue
            exact_placeholder = _is_exact_placeholder_record(value)
            colon_guidance = ":" in assignment and _is_guidance_record(value)
            ambiguous_placeholder_continuation = (
                exact_placeholder
                and terminated
                and value_end == len(record)
                and not value.rstrip().endswith((".", "!", "?", ",", ";"))
            )
            if ambiguous_placeholder_continuation or (not exact_placeholder and not colon_guidance):
                unsafe = True
            continue
        if syntax.group("copula") is not None:
            unsafe = unsafe or not _is_guidance_record(value)
            continue
        unsafe = unsafe or not (_is_guidance_record(value) or _is_technical_concept_record(value))

    return _CredentialRecordAssessment(
        clauses=count,
        unsafe=unsafe,
    )


def _is_exact_placeholder_record(value: str) -> bool:
    candidate = value.strip().rstrip(".!?;, ")
    return _is_explicit_credential_placeholder(candidate)


def _is_technical_concept_record(value: str) -> bool:
    candidate = value.strip().lstrip(".,;:!? ")
    if candidate.rstrip(".,;:!? ").casefold() == "securely":
        return True
    match = _BENIGN_TECHNICAL_CONTEXT_RE.fullmatch(candidate)
    if match is None:
        return False
    tail = match.group("tail").strip().strip(".,;:!? ")
    if tail.casefold() in {"and", "or"}:
        return True
    tail = re.sub(r"(?:\s+(?:and|or))$", "", tail, flags=re.IGNORECASE).strip()
    return not tail or bool(_NOMINAL_CONTINUATION_RE.fullmatch(tail))


def _is_guidance_record(value: str) -> bool:
    candidate = value.strip().lstrip(".,;:!? ").rstrip(".,;:!? ")
    if not candidate:
        return False
    modal = _MODAL_GUIDANCE_RE.fullmatch(candidate)
    if modal is not None:
        return _is_safe_policy_predicate(modal.group("predicate"))
    lowered = candidate.casefold()
    for prefix in ("do not ", "don't ", "never ", "always "):
        if lowered.startswith(prefix):
            return _is_safe_policy_predicate(candidate[len(prefix) :])
    if lowered.startswith("ensure "):
        ensured = _ENSURE_GUIDANCE_RE.fullmatch(candidate[len("ensure ") :])
        if ensured is None:
            return False
        action = _POLICY_ACTION_RE.fullmatch(ensured.group("action"))
        return action is not None and _is_safe_policy_action(action)
    return _is_safe_policy_predicate(candidate)


def _is_safe_policy_predicate(predicate: str) -> bool:
    candidate = predicate.strip().rstrip(".,;:!? ")
    lowered = candidate.casefold()
    for prefix in ("not ", "never ", "always "):
        if lowered.startswith(prefix):
            return _is_safe_policy_predicate(candidate[len(prefix) :])
    if lowered.startswith("be "):
        state = candidate[3:].strip()
        if _POLICY_QUANTITY_RE.fullmatch(state):
            return True
        first, _, remainder = state.partition(" ")
        if first.casefold() in {
            "complex",
            "confidential",
            "long",
            "private",
            "random",
            "strong",
            "unique",
        }:
            return not remainder or bool(
                re.fullmatch(
                    r"and[ \t]+(?:complex|confidential|long|private|random|strong|unique)",
                    remainder,
                    flags=re.IGNORECASE,
                )
            )
        if first.casefold() in {"kept", "managed", "protected", "stored"}:
            return _is_safe_storage_predicate(remainder)
        if first.casefold() in {"disclosed", "replaced", "revoked", "rotated", "validated"}:
            return not remainder or bool(
                re.fullmatch(
                    r"(?:automatically|periodically|regularly|securely|"
                    r"(?:after|before|during|when)[ \t]+(?:(?:a|an|the)[ \t]+)?"
                    r"(?:incident|maintenance|rotation|support[ \t]+workflows?|use))",
                    remainder,
                    flags=re.IGNORECASE,
                )
            )
        return False
    if lowered.startswith(("kept ", "managed ", "protected ", "stored ")):
        _, _, remainder = candidate.partition(" ")
        return _is_safe_storage_predicate(remainder)
    if _POLICY_STATE_RE.fullmatch(candidate):
        return True
    action = _POLICY_ACTION_RE.fullmatch(candidate)
    return action is not None and _is_safe_policy_action(action)


def _is_safe_policy_action(action: re.Match[str]) -> bool:
    verb = action.group("verb").casefold()
    object_text = action.group("object").strip()
    if verb in {"contain", "have", "include"}:
        return bool(_POLICY_QUANTITY_RE.fullmatch(object_text))
    if verb == "store":
        safe_object = _SAFE_POLICY_OBJECT_RE.fullmatch(object_text)
        return bool(safe_object) or _is_safe_storage_predicate(object_text)
    return bool(_SAFE_POLICY_OBJECT_RE.fullmatch(object_text))


def _is_safe_storage_predicate(predicate: str) -> bool:
    location = re.sub(r"^(?:at|in)[ \t]+", "", predicate, flags=re.IGNORECASE)
    return bool(_SAFE_STORAGE_LOCATION_RE.fullmatch(location))


def _split_line_ending(record: str) -> tuple[str, str]:
    content = record.rstrip("\r\n")
    return content, record[len(content) :]


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
