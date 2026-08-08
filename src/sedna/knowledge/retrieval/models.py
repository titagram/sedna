"""Backend-neutral, strict contracts for deterministic local knowledge retrieval."""

from __future__ import annotations

import ipaddress
import math
import re
from collections.abc import Iterable
from enum import StrEnum
from types import TracebackType
from typing import Annotated, Any, Protocol, TypeAlias, runtime_checkable
from urllib.parse import SplitResult, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sedna.knowledge.parsing.sanitize import sanitize_searchable_text
from sedna.knowledge.schema import (
    ArtifactType,
    CaseStep,
    DecisionRule,
    KnowledgeCase,
    KnowledgeRole,
    ObservedOutcome,
    ReferenceArtifact,
    SemanticKnowledgeBundle,
    SourceRef,
)
from sedna.knowledge.schema.common import SearchableNonEmptyString, SearchableString

Term: TypeAlias = Annotated[SearchableNonEmptyString, Field(max_length=512)]
FacetNamespace: TypeAlias = Annotated[SearchableNonEmptyString, Field(max_length=128)]
FacetKey: TypeAlias = Annotated[SearchableNonEmptyString, Field(max_length=128)]
FacetValue: TypeAlias = Annotated[SearchableNonEmptyString, Field(max_length=2048)]
Reason: TypeAlias = Annotated[SearchableNonEmptyString, Field(max_length=2048)]
ScopeValue: TypeAlias = Annotated[SearchableNonEmptyString, Field(max_length=2048)]

_HOSTNAME = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z",
    re.IGNORECASE,
)
_NUMERIC_DOTTED = re.compile(r"^[0-9.]+$")
_IPV6ISH = re.compile(r"^[0-9a-fA-F:.]+$")
_HOST_PORT = re.compile(r"^[a-z0-9.-]+:\d+$", re.IGNORECASE)
_EXPLICIT_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_MALFORMED_HTTP_PREFIX = re.compile(r"^https?(?::|//)", re.IGNORECASE)
_MAX_SITUATION_ITEMS = 64
_MAX_QUERY_TERMS = 32
_MAX_HIT_REASONS = 32
_MAX_GAP_ITEMS = 32
_MAX_QUERY_TEXT = 8192
_MAX_AUTHORIZATION_TEXT = 16_384
_MAX_CANDIDATE_MATCH_TEXT = 16_384


class TargetKind(StrEnum):
    """Syntactic target identity categories checked before any backend operation."""

    IPV4 = "ipv4"
    IPV6 = "ipv6"
    HOSTNAME = "hostname"
    URL = "url"
    GENERIC = "generic"
    INVALID = "invalid"


class AuthorizationState(StrEnum):
    """Whether the current target has an explicit authorization decision."""

    AUTHORIZED = "authorized"
    UNAUTHORIZED = "unauthorized"
    UNKNOWN = "unknown"


class EpistemicLane(StrEnum):
    """Evidence lanes that must not be globally compared as interchangeable scores."""

    REFERENCE = "reference"
    CASE_STEP = "case_step"
    NEGATIVE_EVIDENCE = "negative_evidence"
    GUIDANCE = "guidance"


class KnowledgeGapCode(StrEnum):
    """The closed vocabulary for explicit retrieval non-answers."""

    INVALID_TARGET = "invalid_target"
    NO_APPLICABLE_KNOWLEDGE = "no_applicable_knowledge"
    MISSING_REQUIRED_CONTEXT = "missing_required_context"
    UNAUTHORIZED_SCOPE = "unauthorized_scope"


def _normalise_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _normalise_unique(values: Any) -> Any:
    if not isinstance(values, (list, tuple)) or not all(isinstance(value, str) for value in values):
        return values
    return tuple(sorted({_normalise_text(value) for value in values}))


def _normalise_outcomes(values: Any) -> Any:
    if not isinstance(values, (list, tuple)):
        return values
    normalised: list[tuple[str, str]] = []
    for value in values:
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 2
            or not all(isinstance(part, str) for part in value)
        ):
            return values
        normalised.append((_normalise_text(value[0]), _normalise_text(value[1])))
    return tuple(sorted(set(normalised)))


def _valid_host(value: str) -> bool:
    if _NUMERIC_DOTTED.fullmatch(value):
        try:
            return ipaddress.ip_address(value).version == 4
        except ValueError:
            return False
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return _HOSTNAME.fullmatch(value) is not None
    return True


def _target_url(value: str) -> tuple[str | None, str | None]:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None, "invalid_url"
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return None, "invalid_url"
    if parsed.username is not None or parsed.password is not None or parsed.hostname is None:
        return None, "invalid_url"
    host = parsed.hostname.casefold()
    if not _valid_host(host):
        return None, "invalid_url"
    rendered_host = f"[{host}]" if ":" in host else host
    netloc = rendered_host if port is None else f"{rendered_host}:{port}"
    normalized = urlunsplit(
        SplitResult(parsed.scheme.casefold(), netloc, parsed.path or "", parsed.query, "")
    )
    if sanitize_searchable_text(normalized, (normalized,)) != normalized:
        return None, "invalid_url"
    return normalized, None


def _looks_structured_target(value: str) -> bool:
    """Catch malformed address/URL syntax before it can become an opaque generic ID."""
    return bool(
        _NUMERIC_DOTTED.fullmatch(value)
        or _IPV6ISH.fullmatch(value)
        or value.startswith("[")
        or _EXPLICIT_URL_SCHEME.match(value) is not None
        or _MALFORMED_HTTP_PREFIX.match(value) is not None
        or _HOST_PORT.fullmatch(value)
        or value.count(":") >= 2
    )


class ValidatedTarget(BaseModel):
    """A typed target parse, preserving invalid input as a non-backend result."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    value: str = Field(min_length=1, max_length=2048)
    kind: TargetKind | None = None
    normalized: ScopeValue | None = None
    error: str | None = None

    @model_validator(mode="before")
    @classmethod
    def parse_target(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = value.copy()
        raw = payload.get("value")
        if not isinstance(raw, str):
            return payload
        supplied_kind = TargetKind(payload["kind"]) if payload.get("kind") is not None else None
        if not raw or raw != raw.strip():
            payload.update(kind=TargetKind.INVALID, normalized=None, error="invalid_target")
            return payload
        payload["value"] = raw

        inferred_kind, normalized, error = cls._infer(raw)
        if supplied_kind is TargetKind.INVALID:
            payload.update(
                kind=TargetKind.INVALID, normalized=None, error=payload.get("error") or error
            )
            return payload
        if supplied_kind is TargetKind.GENERIC:
            if inferred_kind is not TargetKind.INVALID or _looks_structured_target(raw):
                payload.update(
                    kind=TargetKind.INVALID, normalized=None, error=error or "invalid_target"
                )
                return payload
            normalized = _normalise_text(raw)
            if not normalized or sanitize_searchable_text(normalized, (normalized,)) != normalized:
                payload.update(kind=TargetKind.INVALID, normalized=None, error="invalid_target")
                return payload
            payload.update(kind=TargetKind.GENERIC, normalized=normalized, error=None)
            return payload
        if supplied_kind is not None and supplied_kind is not inferred_kind:
            raise ValueError("target kind does not match its syntactic identifier")
        payload.update(kind=inferred_kind, normalized=normalized, error=error)
        return payload

    @staticmethod
    def _infer(value: str) -> tuple[TargetKind, str | None, str | None]:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            address = None
        if address is not None:
            return (
                (TargetKind.IPV4 if address.version == 4 else TargetKind.IPV6),
                str(address),
                None,
            )
        if _NUMERIC_DOTTED.fullmatch(value):
            return TargetKind.INVALID, None, "invalid_ipv4"
        if _EXPLICIT_URL_SCHEME.match(value) is not None:
            normalized, error = _target_url(value)
            return (
                (TargetKind.URL, normalized, None)
                if error is None
                else (TargetKind.INVALID, None, error)
            )
        if (
            _IPV6ISH.fullmatch(value)
            or value.startswith("[")
            or _MALFORMED_HTTP_PREFIX.match(value) is not None
            or _HOST_PORT.fullmatch(value)
            or value.count(":") >= 2
        ):
            return TargetKind.INVALID, None, "invalid_target"
        if _HOSTNAME.fullmatch(value) is not None:
            return TargetKind.HOSTNAME, value.casefold(), None
        return TargetKind.INVALID, None, "invalid_target"

    @property
    def is_valid(self) -> bool:
        return self.kind is not TargetKind.INVALID

    @classmethod
    def parse(cls, value: str) -> ValidatedTarget:
        return cls(value=value)


class AuthorizationScope(BaseModel):
    """Typed authorization evidence; free-form scope text is deliberately not accepted."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    state: AuthorizationState
    exact_targets: tuple[ValidatedTarget, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)
    cidrs: tuple[ScopeValue, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)
    hostnames: tuple[ScopeValue, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)
    url_origins: tuple[ScopeValue, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)
    generic_ids: tuple[ScopeValue, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)

    @model_validator(mode="before")
    @classmethod
    def normalise_scope(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = value.copy()
        for name in ("cidrs", "hostnames", "url_origins", "generic_ids"):
            if name in payload:
                payload[name] = _normalise_unique(payload[name])
        return payload

    @model_validator(mode="after")
    def validate_scope(self) -> AuthorizationScope:
        targets = tuple(
            sorted(self.exact_targets, key=lambda target: target.normalized or target.value)
        )
        if any(not target.is_valid for target in targets):
            raise ValueError("authorization exact targets must be valid")
        if len({target.normalized for target in targets}) != len(targets):
            raise ValueError("authorization exact targets must be unique")
        networks: list[str] = []
        for value in self.cidrs:
            try:
                networks.append(str(ipaddress.ip_network(value, strict=False)))
            except ValueError as exc:
                raise ValueError("authorization CIDRs must be valid networks") from exc
        hosts: list[str] = []
        for value in self.hostnames:
            if _NUMERIC_DOTTED.fullmatch(value) or _HOSTNAME.fullmatch(value) is None:
                raise ValueError("authorization hostnames must be valid hostnames")
            hosts.append(value)
        origins: list[str] = []
        for value in self.url_origins:
            target = ValidatedTarget.parse(value)
            if target.kind is not TargetKind.URL or target.normalized is None:
                raise ValueError("authorization URL origins must be valid HTTP(S) origins")
            parsed = urlsplit(target.normalized)
            if parsed.path not in {"", "/"} or parsed.query:
                raise ValueError("authorization URL origins cannot contain a path or query")
            origins.append(urlunsplit((parsed.scheme, parsed.netloc, "", "", "")))
        generic_ids: list[str] = []
        for value in self.generic_ids:
            target = ValidatedTarget(value=value, kind=TargetKind.GENERIC)
            if not target.is_valid or target.normalized is None:
                raise ValueError("authorization generic IDs must be explicit generic targets")
            generic_ids.append(target.normalized)
        object.__setattr__(self, "exact_targets", targets)
        object.__setattr__(self, "cidrs", tuple(sorted(set(networks))))
        object.__setattr__(self, "hostnames", tuple(sorted(set(hosts))))
        object.__setattr__(self, "url_origins", tuple(sorted(set(origins))))
        object.__setattr__(self, "generic_ids", tuple(sorted(set(generic_ids))))
        if self.state is AuthorizationState.AUTHORIZED and not any(
            (targets, networks, hosts, origins, generic_ids)
        ):
            raise ValueError("authorized scope requires at least one typed target constraint")
        if _authorization_text_size(self) > _MAX_AUTHORIZATION_TEXT:
            raise ValueError("authorization scope text exceeds the cumulative bound")
        return self

    def authorizes(self, target: ValidatedTarget) -> bool:
        """Return deterministic authorization membership for one valid target."""
        if self.state is not AuthorizationState.AUTHORIZED or not target.is_valid:
            return False
        if target.normalized in {item.normalized for item in self.exact_targets}:
            return True
        address = _target_ip_address(target)
        if address is not None:
            if any(
                item.kind in {TargetKind.IPV4, TargetKind.IPV6} and item.normalized == str(address)
                for item in self.exact_targets
            ):
                return True
            if any(address in ipaddress.ip_network(network) for network in self.cidrs):
                return True
        if target.kind is TargetKind.HOSTNAME and target.normalized in self.hostnames:
            return True
        if target.kind is TargetKind.URL and target.normalized is not None:
            parsed = urlsplit(target.normalized)
            origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
            if origin in self.url_origins or parsed.hostname in self.hostnames:
                return True
        return target.kind is TargetKind.GENERIC and target.normalized in self.generic_ids


class SituationFacet(BaseModel):
    """A live observation. Unlike canonical context, it has no source references."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    namespace: FacetNamespace
    key: FacetKey
    value: FacetValue
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def normalise_facet(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = value.copy()
        for name in ("namespace", "key", "value"):
            if isinstance(payload.get(name), str):
                payload[name] = _normalise_text(payload[name])
        return payload


class CurrentSituation(BaseModel):
    """Bounded, deterministic live context and a typed authorization decision."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    target: ValidatedTarget
    authorization: AuthorizationScope = Field(
        default_factory=lambda: AuthorizationScope(state=AuthorizationState.UNKNOWN)
    )
    terms: tuple[Term, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)
    facts: tuple[SituationFacet, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)
    access: tuple[Term, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)
    services: tuple[Term, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)
    hypotheses: tuple[Term, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)
    tried_outcomes: tuple[tuple[Term, Term], ...] = Field(
        default=(), max_length=_MAX_SITUATION_ITEMS
    )
    unresolved_questions: tuple[Term, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)

    @model_validator(mode="before")
    @classmethod
    def normalise_situation(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = value.copy()
        for name in ("terms", "access", "services", "hypotheses", "unresolved_questions"):
            if name in payload:
                payload[name] = _normalise_unique(payload[name])
        if "tried_outcomes" in payload:
            payload["tried_outcomes"] = _normalise_outcomes(payload["tried_outcomes"])
        return payload

    @model_validator(mode="after")
    def validate_facts_and_authorization(self) -> CurrentSituation:
        facts: dict[tuple[str, str, str], SituationFacet] = {}
        for facet in self.facts:
            identity = (facet.namespace, facet.key, facet.value)
            if identity in facts and facts[identity] != facet:
                raise ValueError(
                    "situation facts with the same namespace, key, and value must be unique"
                )
            facts[identity] = facet
        object.__setattr__(self, "facts", tuple(facts[key] for key in sorted(facts)))
        if (
            self.target.is_valid
            and self.authorization.state is AuthorizationState.AUTHORIZED
            and not self.authorization.authorizes(self.target)
        ):
            raise ValueError("target is not within the authorized scope")
        if _situation_text_size(self) > _MAX_QUERY_TEXT:
            raise ValueError("current situation text exceeds the cumulative bound")
        return self


class RetrievalQuery(BaseModel):
    """A bounded query plan. Invalid/unauthorized situations must stop before a backend call."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    situation: CurrentSituation
    terms: tuple[Term, ...] = Field(default=(), max_length=_MAX_QUERY_TERMS)
    synonyms: tuple[Term, ...] = Field(default=(), max_length=_MAX_QUERY_TERMS)
    facets: tuple[SituationFacet, ...] = Field(default=(), max_length=_MAX_QUERY_TERMS)
    max_candidates: int = Field(default=32, ge=1, le=100)
    lane_limit: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="before")
    @classmethod
    def normalise_query(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = value.copy()
        for name in ("terms", "synonyms"):
            if name in payload:
                payload[name] = _normalise_unique(payload[name])
        return payload

    @model_validator(mode="after")
    def validate_query(self) -> RetrievalQuery:
        facets = {(facet.namespace, facet.key, facet.value): facet for facet in self.facets}
        if len(facets) != len(self.facets):
            raise ValueError("query facets must be unique")
        object.__setattr__(self, "facets", tuple(facets[key] for key in sorted(facets)))
        if (
            sum(map(len, (*self.terms, *self.synonyms))) + _facet_text_size(self.facets)
            > _MAX_QUERY_TEXT
        ):
            raise ValueError("retrieval query text exceeds the cumulative bound")
        return self


class ScoreComponents(BaseModel):
    """Finite, bounded, explainable query-local score dimensions."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    lexical_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    facet_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    context_similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    verification_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    freshness: float = Field(default=0.0, ge=0.0, le=1.0)
    source_diversity: float = Field(default=0.0, ge=0.0, le=1.0)
    unknown_condition_penalty: float = Field(default=0.0, ge=0.0, le=1.0)
    total: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def require_finite_components(self) -> ScoreComponents:
        if any(not math.isfinite(value) for value in self.model_dump().values()):
            raise ValueError("score components must be finite")
        return self


IndexedArtifact = Annotated[
    ReferenceArtifact | KnowledgeCase | CaseStep | DecisionRule,
    Field(discriminator="artifact_type"),
]
HitArtifact = Annotated[
    ReferenceArtifact | CaseStep | DecisionRule,
    Field(discriminator="artifact_type"),
]
RetrievableArtifact = IndexedArtifact


def _artifact_id(artifact: IndexedArtifact) -> str:
    if isinstance(artifact, ReferenceArtifact):
        return artifact.artifact_id
    if isinstance(artifact, KnowledgeCase):
        return artifact.case_id
    if isinstance(artifact, CaseStep):
        return artifact.step_id
    return artifact.rule_id


def _deep_revalidate_artifact(artifact: IndexedArtifact) -> IndexedArtifact:
    """Defend contract boundaries against `model_copy` and constructed-value bypasses."""
    return type(artifact).model_validate(artifact.model_dump(mode="json"))


def _deep_revalidate_provenance(provenance: tuple[SourceRef, ...]) -> tuple[SourceRef, ...]:
    return tuple(SourceRef.model_validate(source.model_dump(mode="json")) for source in provenance)


def _artifact_lane(artifact: IndexedArtifact) -> EpistemicLane | None:
    if isinstance(artifact, KnowledgeCase):
        return None
    if isinstance(artifact, DecisionRule):
        return EpistemicLane.GUIDANCE
    if isinstance(artifact, CaseStep):
        if (
            artifact.knowledge_role is KnowledgeRole.NEGATIVE_CASE
            or artifact.assessment.observed_outcome is ObservedOutcome.FAILURE
        ):
            return EpistemicLane.NEGATIVE_EVIDENCE
        return EpistemicLane.CASE_STEP
    if artifact.artifact_type in {
        ArtifactType.NEGATIVE_EVIDENCE,
        ArtifactType.ANTI_PATTERN,
        ArtifactType.EXCEPTION,
    }:
        return EpistemicLane.NEGATIVE_EVIDENCE
    return EpistemicLane.REFERENCE


class IndexCandidate(BaseModel):
    """Backend output sufficient for ranking without leaking backend scoring semantics."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    artifact_id: Reason
    artifact: IndexedArtifact
    lexical_relevance: float = Field(ge=0.0, le=1.0)
    matched_terms: tuple[Term, ...] = Field(default=(), max_length=_MAX_QUERY_TERMS)
    matched_fields: tuple[FacetKey, ...] = Field(default=(), max_length=_MAX_QUERY_TERMS)
    matched_evidence: tuple[Reason, ...] = Field(default=(), max_length=_MAX_QUERY_TERMS)

    @model_validator(mode="before")
    @classmethod
    def normalise_candidate(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = value.copy()
        for name in ("matched_terms", "matched_fields", "matched_evidence"):
            if name in payload:
                payload[name] = _normalise_unique(payload[name])
        return payload

    @model_validator(mode="after")
    def deeply_validate_candidate(self) -> IndexCandidate:
        if not math.isfinite(self.lexical_relevance):
            raise ValueError("lexical relevance must be finite")
        if _candidate_match_text_size(self) > _MAX_CANDIDATE_MATCH_TEXT:
            raise ValueError("candidate match text exceeds the cumulative bound")
        artifact = _deep_revalidate_artifact(self.artifact)
        if self.artifact_id != _artifact_id(artifact):
            raise ValueError("artifact_id must exactly match the canonical artifact identity")
        object.__setattr__(self, "artifact", artifact)
        return self


SearchCandidate: TypeAlias = IndexCandidate


class RetrievalHit(BaseModel):
    """A qualifying artifact, with deep canonical validation and one compatible lane."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    artifact_id: Reason
    artifact: HitArtifact
    lane: EpistemicLane
    provenance: tuple[SourceRef, ...] = Field(min_length=1, max_length=_MAX_SITUATION_ITEMS)
    score: ScoreComponents
    matched_facets: tuple[SituationFacet, ...] = Field(default=(), max_length=_MAX_HIT_REASONS)
    qualification_reasons: tuple[Reason, ...] = Field(min_length=1, max_length=_MAX_HIT_REASONS)
    missing_context: tuple[Reason, ...] = Field(default=(), max_length=_MAX_HIT_REASONS)

    @model_validator(mode="before")
    @classmethod
    def normalise_explanation(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = value.copy()
        for name in ("qualification_reasons", "missing_context"):
            if name in payload:
                payload[name] = _normalise_unique(payload[name])
        return payload

    @model_validator(mode="after")
    def validate_hit(self) -> RetrievalHit:
        artifact = _deep_revalidate_artifact(self.artifact)
        if isinstance(artifact, KnowledgeCase):
            raise ValueError("parent knowledge cases are indexed metadata, never qualifying hits")
        provenance = _deep_revalidate_provenance(self.provenance)
        if self.artifact_id != _artifact_id(artifact):
            raise ValueError("artifact_id must exactly match the canonical artifact identity")
        if provenance != artifact.source_refs:
            raise ValueError("provenance must exactly match canonical artifact source_refs")
        expected_lane = _artifact_lane(artifact)
        if self.lane is not expected_lane:
            raise ValueError("artifact does not belong to the supplied epistemic lane")
        facets = {(facet.namespace, facet.key, facet.value): facet for facet in self.matched_facets}
        if len(facets) != len(self.matched_facets):
            raise ValueError("matched facets must be unique")
        object.__setattr__(self, "artifact", artifact)
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "matched_facets", tuple(facets[key] for key in sorted(facets)))
        return self


class RejectedCandidate(BaseModel):
    """An excluded candidate with canonical provenance and its true prospective lane."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    artifact_id: Reason
    artifact: IndexedArtifact
    lane: EpistemicLane | None
    provenance: tuple[SourceRef, ...] = Field(min_length=1, max_length=_MAX_SITUATION_ITEMS)
    rejection_reasons: tuple[Reason, ...] = Field(min_length=1, max_length=_MAX_HIT_REASONS)
    missing_context: tuple[Reason, ...] = Field(default=(), max_length=_MAX_HIT_REASONS)

    @model_validator(mode="before")
    @classmethod
    def normalise_explanation(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = value.copy()
        for name in ("rejection_reasons", "missing_context"):
            if name in payload:
                payload[name] = _normalise_unique(payload[name])
        return payload

    @model_validator(mode="after")
    def validate_rejection(self) -> RejectedCandidate:
        artifact = _deep_revalidate_artifact(self.artifact)
        provenance = _deep_revalidate_provenance(self.provenance)
        if self.artifact_id != _artifact_id(artifact):
            raise ValueError("artifact_id must exactly match the canonical artifact identity")
        if provenance != artifact.source_refs:
            raise ValueError("provenance must exactly match canonical artifact source_refs")
        if self.lane is not _artifact_lane(artifact):
            raise ValueError("rejected candidate lane must match the artifact epistemic lane")
        object.__setattr__(self, "artifact", artifact)
        object.__setattr__(self, "provenance", provenance)
        return self


class KnowledgeGap(BaseModel):
    """A typed absence of knowledge or a pre-backend input/scope stop."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    code: KnowledgeGapCode
    summary: Reason
    observed_domain: SearchableString | None = Field(default=None, max_length=2048)
    missing_context: tuple[Reason, ...] = Field(default=(), max_length=_MAX_GAP_ITEMS)
    suggested_document_ingestion: tuple[Reason, ...] = Field(default=(), max_length=_MAX_GAP_ITEMS)
    research_eligible: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalise_gap(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = value.copy()
        for name in ("missing_context", "suggested_document_ingestion"):
            if name in payload:
                payload[name] = _normalise_unique(payload[name])
        for name in ("summary", "observed_domain"):
            if isinstance(payload.get(name), str):
                payload[name] = _normalise_text(payload[name])
        return payload


class RetrievalResult(BaseModel):
    """Exclusive lane results ordered by rank, or exactly one pre-backend/knowledge gap."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    target: ValidatedTarget
    authorization: AuthorizationScope = Field(
        default_factory=lambda: AuthorizationScope(state=AuthorizationState.UNKNOWN)
    )
    references: tuple[RetrievalHit, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)
    case_steps: tuple[RetrievalHit, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)
    negative_cases: tuple[RetrievalHit, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)
    decision_guidance: tuple[RetrievalHit, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)
    rejected_candidates: tuple[RejectedCandidate, ...] = Field(
        default=(), max_length=_MAX_SITUATION_ITEMS
    )
    knowledge_gap: KnowledgeGap | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> RetrievalResult:
        lanes = (
            (EpistemicLane.REFERENCE, self.references),
            (EpistemicLane.CASE_STEP, self.case_steps),
            (EpistemicLane.NEGATIVE_EVIDENCE, self.negative_cases),
            (EpistemicLane.GUIDANCE, self.decision_guidance),
        )
        for lane, hits in lanes:
            if any(hit.lane is not lane for hit in hits):
                raise ValueError(f"{lane.value} result lane contains a different hit lane")
            expected = tuple(sorted(hits, key=lambda hit: (-hit.score.total, hit.artifact_id)))
            if hits != expected:
                raise ValueError(
                    "lane hits must be ordered by descending total score then artifact_id"
                )
        hits = tuple(hit for _, lane_hits in lanes for hit in lane_hits)
        hit_ids = {hit.artifact_id for hit in hits}
        rejected_ids = {candidate.artifact_id for candidate in self.rejected_candidates}
        if len(hit_ids) != len(hits):
            raise ValueError("an artifact may occur in only one qualifying hit lane")
        if len(rejected_ids) != len(self.rejected_candidates):
            raise ValueError("rejected candidates must have unique artifact identities")
        if hit_ids & rejected_ids:
            raise ValueError("an artifact cannot be both a hit and a rejected candidate")
        if self.rejected_candidates != tuple(
            sorted(self.rejected_candidates, key=lambda candidate: candidate.artifact_id)
        ):
            raise ValueError("rejected candidates must be ordered by artifact_id")

        prebackend_stop = (
            not self.target.is_valid
            or self.authorization.state is not AuthorizationState.AUTHORIZED
        )
        if prebackend_stop:
            expected_code = (
                KnowledgeGapCode.INVALID_TARGET
                if not self.target.is_valid
                else KnowledgeGapCode.UNAUTHORIZED_SCOPE
            )
            if hits or self.rejected_candidates:
                raise ValueError("pre-backend results cannot contain candidates")
            if self.knowledge_gap is None or self.knowledge_gap.code is not expected_code:
                raise ValueError("pre-backend results require the matching knowledge gap")
        elif not self.authorization.authorizes(self.target):
            raise ValueError(
                "authorized retrieval result target must be within the authorization scope"
            )
        elif self.knowledge_gap is not None:
            if hits:
                raise ValueError("knowledge gap results cannot also contain qualifying lane hits")
            if self.knowledge_gap.code in {
                KnowledgeGapCode.INVALID_TARGET,
                KnowledgeGapCode.UNAUTHORIZED_SCOPE,
            }:
                raise ValueError("pre-backend knowledge gap does not match an authorized target")
        elif not hits:
            raise ValueError("valid authorized results without lane hits require a knowledge gap")
        return self

    @property
    def is_invalid_target(self) -> bool:
        return not self.target.is_valid


class IndexAudit(BaseModel):
    """Derived index health; corruption/parity problems always require a rebuild."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    artifact_count: int = Field(default=0, ge=0)
    source_count: int = Field(default=0, ge=0)
    facet_count: int = Field(default=0, ge=0)
    fts_count: int = Field(default=0, ge=0)
    orphan_count: int = Field(default=0, ge=0)
    duplicate_id_count: int = Field(default=0, ge=0)
    corruption_count: int = Field(default=0, ge=0)
    issues: tuple[Reason, ...] = Field(default=(), max_length=_MAX_GAP_ITEMS)
    rebuild_required: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalise_issues(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = value.copy()
        if "issues" in payload:
            payload["issues"] = _normalise_unique(payload["issues"])
        return payload

    @model_validator(mode="after")
    def derive_rebuild_requirement(self) -> IndexAudit:
        issues = set(self.issues)
        if self.orphan_count:
            issues.add("orphan_rows")
        if self.duplicate_id_count:
            issues.add("duplicate_artifact_ids")
        if self.fts_count != self.artifact_count:
            issues.add("fts_count_mismatch")
        if self.corruption_count:
            issues.add("canonical_corruption")
        normalized = tuple(sorted(issues))
        object.__setattr__(self, "issues", normalized)
        object.__setattr__(self, "rebuild_required", bool(normalized))
        return self


def _facet_text_size(facets: Iterable[SituationFacet]) -> int:
    return sum(
        len(value) for facet in facets for value in (facet.namespace, facet.key, facet.value)
    )


def _situation_text_size(situation: CurrentSituation) -> int:
    text = (*situation.terms, *situation.access, *situation.services, *situation.hypotheses)
    text += tuple(part for outcome in situation.tried_outcomes for part in outcome)
    text += situation.unresolved_questions
    return (
        sum(map(len, text))
        + _facet_text_size(situation.facts)
        + _authorization_text_size(situation.authorization)
    )


def _authorization_text_size(scope: AuthorizationScope) -> int:
    values = (
        *(target.value for target in scope.exact_targets),
        *scope.cidrs,
        *scope.hostnames,
        *scope.url_origins,
        *scope.generic_ids,
    )
    return sum(map(len, values))


def _candidate_match_text_size(candidate: IndexCandidate) -> int:
    return sum(
        map(
            len,
            (*candidate.matched_terms, *candidate.matched_fields, *candidate.matched_evidence),
        )
    )


def _target_ip_address(
    target: ValidatedTarget,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    if target.normalized is None:
        return None
    if target.kind in {TargetKind.IPV4, TargetKind.IPV6}:
        return ipaddress.ip_address(target.normalized)
    if target.kind is TargetKind.URL:
        host = urlsplit(target.normalized).hostname
        if host is not None:
            try:
                return ipaddress.ip_address(host)
            except ValueError:
                return None
    return None


@runtime_checkable
class RetrievalIndex(Protocol):
    """Backend-neutral protocol for disposable projections of canonical bundles."""

    def upsert_bundle(self, bundle: SemanticKnowledgeBundle) -> None: ...

    def delete_source(self, source_id: str) -> None: ...

    def rebuild(self, bundles: Iterable[SemanticKnowledgeBundle]) -> IndexAudit: ...

    def get_artifact(self, artifact_id: str) -> IndexedArtifact | None: ...

    def search_candidates(
        self,
        query: RetrievalQuery,
        *,
        lane: EpistemicLane,
        limit: int,
    ) -> tuple[IndexCandidate, ...]: ...

    def audit(self) -> IndexAudit: ...

    def close(self) -> None: ...

    def __enter__(self) -> RetrievalIndex: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
