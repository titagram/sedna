"""Backend-neutral contracts for deterministic local knowledge retrieval.

These models describe a retrieval request and result without making SQLite (or any later
backend) authoritative.  They intentionally keep live observations separate from canonical,
source-backed applicability assertions.
"""

from __future__ import annotations

import ipaddress
import math
import re
from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated, Any, Protocol, runtime_checkable
from urllib.parse import SplitResult, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sedna.knowledge.parsing.sanitize import sanitize_searchable_text
from sedna.knowledge.schema import (
    CaseStep,
    DecisionRule,
    KnowledgeCase,
    ReferenceArtifact,
    SemanticKnowledgeBundle,
    SourceRef,
)
from sedna.knowledge.schema.common import SearchableNonEmptyString, SearchableString

_HOSTNAME = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z",
    re.IGNORECASE,
)
_NUMERIC_DOTTED = re.compile(r"^[0-9.]+$")
_MAX_SITUATION_ITEMS = 64
_MAX_QUERY_TERMS = 32
_MAX_HIT_REASONS = 32
_MAX_GAP_ITEMS = 32


class TargetKind(StrEnum):
    """Syntactic target identity categories understood before backend access."""

    IPV4 = "ipv4"
    IPV6 = "ipv6"
    HOSTNAME = "hostname"
    URL = "url"
    GENERIC = "generic"
    INVALID = "invalid"


class EpistemicLane(StrEnum):
    """Evidence categories that must not be compared as one global ranking."""

    REFERENCE = "reference"
    CASE_STEP = "case_step"
    NEGATIVE_CASE = "negative_case"
    DECISION_GUIDANCE = "decision_guidance"

    # Readable aliases for callers that use the terminology from the design.
    TECHNICAL_REFERENCE = "reference"
    ANALOGOUS_CASE_STEP = "case_step"


class KnowledgeGapCode(StrEnum):
    """The deliberately closed set of retrieval outcomes without qualifying knowledge."""

    INVALID_TARGET = "invalid_target"
    NO_APPLICABLE_KNOWLEDGE = "no_applicable_knowledge"
    MISSING_REQUIRED_CONTEXT = "missing_required_context"
    UNAUTHORIZED_SCOPE = "unauthorized_scope"


def _normalise_text(value: str) -> str:
    """Make current/query text deterministic without bypassing searchable validation."""
    return " ".join(value.split()).casefold()


def _normalise_unique(values: Any) -> Any:
    """Normalise string tuples into a stable unique order, leaving invalid input to Pydantic."""
    if not isinstance(values, (list, tuple)):
        return values
    if not all(isinstance(value, str) for value in values):
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


def _artifact_id(artifact: RetrievableArtifact) -> str:
    if isinstance(artifact, ReferenceArtifact):
        return artifact.artifact_id
    if isinstance(artifact, KnowledgeCase):
        return artifact.case_id
    if isinstance(artifact, CaseStep):
        return artifact.step_id
    return artifact.rule_id


def _target_url(value: str) -> tuple[str | None, str | None]:
    """Return a safe normalized HTTP(S) URL or an explanatory target-validation error."""
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
    normalised = urlunsplit(
        SplitResult(parsed.scheme.casefold(), netloc, parsed.path or "", parsed.query, "")
    )
    if sanitize_searchable_text(normalised, (normalised,)) != normalised:
        return None, "invalid_url"
    return normalised, None


def _valid_host(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return _HOSTNAME.fullmatch(value) is not None
    return True


class ValidatedTarget(BaseModel):
    """A pre-backend target parse, including an explicit typed invalid state.

    Generic identifiers are accepted only when their kind is supplied explicitly.  This prevents
    malformed address-like input from silently becoming a hostname or arbitrary text search.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    value: str = Field(min_length=1, max_length=2048)
    kind: TargetKind | None = None
    normalized: str | None = None
    error: str | None = None

    @model_validator(mode="before")
    @classmethod
    def parse_target(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = value.copy()
        raw_value = payload.get("value")
        if not isinstance(raw_value, str):
            return payload
        raw_value = raw_value.strip()
        payload["value"] = raw_value
        supplied_kind = payload.get("kind")
        kind = TargetKind(supplied_kind) if supplied_kind is not None else None

        if kind is TargetKind.GENERIC:
            inferred_kind, _, inferred_error = cls._infer(raw_value)
            if inferred_error == "invalid_ipv4":
                payload.update(kind=TargetKind.INVALID, normalized=None, error=inferred_error)
                return payload
            if inferred_kind is not TargetKind.INVALID:
                raise ValueError("generic target cannot override a recognized target identifier")
            normalized = _normalise_text(raw_value)
            if not normalized or sanitize_searchable_text(normalized, (normalized,)) != normalized:
                raise ValueError("generic target must be safe normalized text")
            payload.update(kind=TargetKind.GENERIC, normalized=normalized, error=None)
            return payload
        if kind is TargetKind.INVALID:
            payload.update(
                kind=TargetKind.INVALID,
                normalized=None,
                error=payload.get("error") or "invalid_target",
            )
            return payload

        inferred_kind, normalized, error = cls._infer(raw_value)
        if kind is not None and kind is not inferred_kind:
            raise ValueError("target kind does not match its syntactic identifier")
        payload.update(kind=inferred_kind, normalized=normalized, error=error)
        return payload

    @staticmethod
    def _infer(value: str) -> tuple[TargetKind, str | None, str | None]:
        if not value or value != value.strip():
            return TargetKind.INVALID, None, "invalid_target"
        try:
            parsed_ip = ipaddress.ip_address(value)
        except ValueError:
            parsed_ip = None
        if parsed_ip is not None:
            kind = TargetKind.IPV4 if parsed_ip.version == 4 else TargetKind.IPV6
            return kind, str(parsed_ip), None
        if _NUMERIC_DOTTED.fullmatch(value):
            return TargetKind.INVALID, None, "invalid_ipv4"
        if value.casefold().startswith(("http://", "https://")):
            normalised, error = _target_url(value)
            if error is None:
                return TargetKind.URL, normalised, None
            return TargetKind.INVALID, None, error
        if _HOSTNAME.fullmatch(value) is not None:
            return TargetKind.HOSTNAME, value.casefold(), None
        return TargetKind.INVALID, None, "invalid_target"

    @property
    def is_valid(self) -> bool:
        """Whether this value can be used to construct a backend query."""
        return self.kind is not TargetKind.INVALID

    @classmethod
    def parse(cls, value: str) -> ValidatedTarget:
        """Construct a target parse from a raw identifier without raising for invalid syntax."""
        return cls(value=value)


class SituationFacet(BaseModel):
    """A live, non-canonical observation; it intentionally has no source references."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    namespace: SearchableNonEmptyString
    key: SearchableNonEmptyString
    value: SearchableNonEmptyString
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
    """Bounded, deterministic live context supplied before query planning."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    target: ValidatedTarget
    terms: tuple[SearchableNonEmptyString, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)
    facts: tuple[SituationFacet, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)
    access: tuple[SearchableNonEmptyString, ...] = Field(
        default=(), max_length=_MAX_SITUATION_ITEMS
    )
    services: tuple[SearchableNonEmptyString, ...] = Field(
        default=(), max_length=_MAX_SITUATION_ITEMS
    )
    hypotheses: tuple[SearchableNonEmptyString, ...] = Field(
        default=(), max_length=_MAX_SITUATION_ITEMS
    )
    tried_outcomes: tuple[tuple[SearchableNonEmptyString, SearchableNonEmptyString], ...] = Field(
        default=(), max_length=_MAX_SITUATION_ITEMS
    )
    unresolved_questions: tuple[SearchableNonEmptyString, ...] = Field(
        default=(), max_length=_MAX_SITUATION_ITEMS
    )
    authorized_scope: tuple[SearchableNonEmptyString, ...] = Field(
        default=(), max_length=_MAX_SITUATION_ITEMS
    )

    @model_validator(mode="before")
    @classmethod
    def normalise_situation(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = value.copy()
        for name in (
            "terms",
            "access",
            "services",
            "hypotheses",
            "unresolved_questions",
            "authorized_scope",
        ):
            if name in payload:
                payload[name] = _normalise_unique(payload[name])
        if "tried_outcomes" in payload:
            payload["tried_outcomes"] = _normalise_outcomes(payload["tried_outcomes"])
        return payload

    @model_validator(mode="after")
    def canonicalise_facts(self) -> CurrentSituation:
        """Deduplicate exact observations and reject conflicting duplicate live facts."""
        facts_by_identity: dict[tuple[str, str, str], SituationFacet] = {}
        for facet in self.facts:
            identity = (facet.namespace, facet.key, facet.value)
            existing = facts_by_identity.get(identity)
            if existing is not None and existing != facet:
                raise ValueError(
                    "situation facts with the same namespace, key, and value must be unique"
                )
            facts_by_identity[identity] = facet
        facts = tuple(facts_by_identity[key] for key in sorted(facts_by_identity))
        object.__setattr__(self, "facts", facts)
        return self


class RetrievalQuery(BaseModel):
    """A bounded query plan that cannot turn an invalid target into backend work."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    situation: CurrentSituation
    terms: tuple[SearchableNonEmptyString, ...] = Field(default=(), max_length=_MAX_QUERY_TERMS)
    synonyms: tuple[SearchableNonEmptyString, ...] = Field(default=(), max_length=_MAX_QUERY_TERMS)
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
    def canonicalise_facets(self) -> RetrievalQuery:
        facet_identities = {
            (facet.namespace, facet.key, facet.value): facet for facet in self.facets
        }
        if len(facet_identities) != len(self.facets):
            raise ValueError("query facets must be unique")
        facets = tuple(facet_identities[key] for key in sorted(facet_identities))
        object.__setattr__(self, "facets", facets)
        return self


class ScoreComponents(BaseModel):
    """Finite, bounded, explainable query-local ranking dimensions."""

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


RetrievableArtifact = Annotated[
    ReferenceArtifact | KnowledgeCase | CaseStep | DecisionRule,
    Field(discriminator="artifact_type"),
]


class RetrievalHit(BaseModel):
    """An applicable artifact with its immutable source-backed provenance and rationale."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    artifact_id: SearchableNonEmptyString
    artifact: RetrievableArtifact
    lane: EpistemicLane
    provenance: tuple[SourceRef, ...] = Field(min_length=1)
    score: ScoreComponents
    matched_facets: tuple[SituationFacet, ...] = Field(default=(), max_length=_MAX_HIT_REASONS)
    qualification_reasons: tuple[SearchableNonEmptyString, ...] = Field(
        min_length=1, max_length=_MAX_HIT_REASONS
    )
    missing_context: tuple[SearchableNonEmptyString, ...] = Field(
        default=(), max_length=_MAX_HIT_REASONS
    )

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
    def require_exact_identity_and_provenance(self) -> RetrievalHit:
        if self.artifact_id != _artifact_id(self.artifact):
            raise ValueError("artifact_id must exactly match the canonical artifact identity")
        if self.provenance != self.artifact.source_refs:
            raise ValueError("provenance must exactly match canonical artifact source_refs")
        facets_by_identity = {
            (facet.namespace, facet.key, facet.value): facet for facet in self.matched_facets
        }
        if len(facets_by_identity) != len(self.matched_facets):
            raise ValueError("matched facets must be unique")
        object.__setattr__(
            self,
            "matched_facets",
            tuple(facets_by_identity[key] for key in sorted(facets_by_identity)),
        )
        return self


class RejectedCandidate(BaseModel):
    """A candidate excluded from a lane, retaining provenance and exact exclusion reasons."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    artifact_id: SearchableNonEmptyString
    artifact: RetrievableArtifact
    lane: EpistemicLane
    provenance: tuple[SourceRef, ...] = Field(min_length=1)
    rejection_reasons: tuple[SearchableNonEmptyString, ...] = Field(
        min_length=1, max_length=_MAX_HIT_REASONS
    )
    missing_context: tuple[SearchableNonEmptyString, ...] = Field(
        default=(), max_length=_MAX_HIT_REASONS
    )

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
    def require_exact_identity_and_provenance(self) -> RejectedCandidate:
        if self.artifact_id != _artifact_id(self.artifact):
            raise ValueError("artifact_id must exactly match the canonical artifact identity")
        if self.provenance != self.artifact.source_refs:
            raise ValueError("provenance must exactly match canonical artifact source_refs")
        return self


class KnowledgeGap(BaseModel):
    """An explicit absence of qualifying knowledge, never improvised advice."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    code: KnowledgeGapCode
    summary: SearchableNonEmptyString
    observed_domain: SearchableString | None = None
    missing_context: tuple[SearchableNonEmptyString, ...] = Field(
        default=(), max_length=_MAX_GAP_ITEMS
    )
    suggested_document_ingestion: tuple[SearchableNonEmptyString, ...] = Field(
        default=(), max_length=_MAX_GAP_ITEMS
    )
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
        if isinstance(payload.get("summary"), str):
            payload["summary"] = _normalise_text(payload["summary"])
        if isinstance(payload.get("observed_domain"), str):
            payload["observed_domain"] = _normalise_text(payload["observed_domain"])
        return payload


class RetrievalResult(BaseModel):
    """Lane-separated retrieval output or one explicit, consistent knowledge gap."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    target: ValidatedTarget
    references: tuple[RetrievalHit, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)
    case_steps: tuple[RetrievalHit, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)
    negative_cases: tuple[RetrievalHit, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)
    decision_guidance: tuple[RetrievalHit, ...] = Field(default=(), max_length=_MAX_SITUATION_ITEMS)
    rejected_candidates: tuple[RejectedCandidate, ...] = Field(
        default=(), max_length=_MAX_SITUATION_ITEMS
    )
    knowledge_gap: KnowledgeGap | None = None

    @model_validator(mode="after")
    def require_exclusive_consistent_shape(self) -> RetrievalResult:
        lane_hits = (
            (EpistemicLane.REFERENCE, self.references),
            (EpistemicLane.CASE_STEP, self.case_steps),
            (EpistemicLane.NEGATIVE_CASE, self.negative_cases),
            (EpistemicLane.DECISION_GUIDANCE, self.decision_guidance),
        )
        for expected_lane, hits in lane_hits:
            if any(hit.lane is not expected_lane for hit in hits):
                raise ValueError(f"{expected_lane.value} result lane contains a different hit lane")

        all_hits = tuple(hit for _, hits in lane_hits for hit in hits)
        identities = tuple(hit.artifact_id for hit in all_hits)
        if len(set(identities)) != len(identities):
            raise ValueError("an artifact may occur in only one retrieval lane")

        for name, hits in (
            ("references", self.references),
            ("case_steps", self.case_steps),
            ("negative_cases", self.negative_cases),
            ("decision_guidance", self.decision_guidance),
        ):
            ordered = tuple(sorted(hits, key=lambda hit: hit.artifact_id))
            object.__setattr__(self, name, ordered)
        rejected = tuple(
            sorted(self.rejected_candidates, key=lambda candidate: candidate.artifact_id)
        )
        if len({candidate.artifact_id for candidate in rejected}) != len(rejected):
            raise ValueError("rejected candidates must have unique artifact identities")
        object.__setattr__(self, "rejected_candidates", rejected)

        if not self.target.is_valid:
            if all_hits or self.rejected_candidates:
                raise ValueError("invalid target results cannot contain backend candidates")
            if (
                self.knowledge_gap is None
                or self.knowledge_gap.code is not KnowledgeGapCode.INVALID_TARGET
            ):
                raise ValueError("invalid target results require an invalid_target knowledge gap")
        elif self.knowledge_gap is not None:
            if all_hits:
                raise ValueError("knowledge gap results cannot also contain qualifying lane hits")
            if self.knowledge_gap.code is KnowledgeGapCode.INVALID_TARGET:
                raise ValueError("invalid_target knowledge gap requires an invalid target")
        elif not all_hits:
            raise ValueError("valid target results without lane hits require a knowledge gap")
        return self

    @property
    def is_invalid_target(self) -> bool:
        """Whether the service must have returned before calling its retrieval backend."""
        return not self.target.is_valid


class IndexAudit(BaseModel):
    """Deterministic index health summary; it never modifies canonical knowledge."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    artifact_count: int = Field(default=0, ge=0)
    source_count: int = Field(default=0, ge=0)
    facet_count: int = Field(default=0, ge=0)
    fts_count: int = Field(default=0, ge=0)
    orphan_count: int = Field(default=0, ge=0)
    duplicate_id_count: int = Field(default=0, ge=0)
    issues: tuple[SearchableNonEmptyString, ...] = Field(default=(), max_length=_MAX_GAP_ITEMS)
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


@runtime_checkable
class RetrievalIndex(Protocol):
    """Backend-neutral protocol for disposable canonical knowledge projections."""

    def upsert_bundle(self, bundle: SemanticKnowledgeBundle) -> None:
        """Atomically project one strictly validated semantic bundle."""

    def delete_source(self, source_id: str) -> None:
        """Delete all derived projection rows for one canonical source identity."""

    def rebuild(self, bundles: Iterable[SemanticKnowledgeBundle]) -> IndexAudit:
        """Replace the disposable projection from canonical bundles."""

    def get_artifact(self, artifact_id: str) -> RetrievableArtifact | None:
        """Return one exact canonical artifact by stable identity."""

    def search_candidates(
        self,
        query: RetrievalQuery,
        *,
        lane: EpistemicLane,
        limit: int,
    ) -> tuple[RetrievableArtifact, ...]:
        """Return deterministic candidate artifacts for exactly one epistemic lane."""

    def audit(self) -> IndexAudit:
        """Report projection consistency, provenance coverage, and rebuild requirements."""

    def close(self) -> None:
        """Release backend resources."""
