"""Deterministic applicability filtering and explainable epistemic-lane ranking."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sedna.knowledge.retrieval.models import (
    EpistemicLane,
    IndexCandidate,
    IndexedArtifact,
    Reason,
    RejectedCandidate,
    RetrievalHit,
    RetrievalQuery,
    ScoreComponents,
    SituationFacet,
)
from sedna.knowledge.schema import (
    ArtifactType,
    CaseStep,
    ContextAssertion,
    ContextRelation,
    DecisionRule,
    Generalizability,
    KnowledgeCase,
    KnowledgeRole,
    ObservedOutcome,
    ReferenceArtifact,
    VerificationStatus,
)

_MAX_RANKING_CANDIDATES = 400
_MAX_EXPLANATION_ITEMS = 32
_MAX_GLOBAL_QUESTIONS = 128
_MAX_OUTPUT_STRING_CHARS = 2048
_MAX_EXPLANATION_VALUE_CHARS = 512
_MAX_OUTPUT_PROVENANCE = 64
_MAX_PROVENANCE_SCAN_ITEMS = 256
_MAX_OUTPUT_SEQUENCE_ITEMS = 256
_MAX_CANONICAL_FACETS = 256
_MAX_CANONICAL_PAYLOAD_BYTES = 262_144
_MIN_KNOWN_FACT_CONFIDENCE = 0.75
_UNKNOWN_VALUES = frozenset({"", "unknown", "unspecified", "not established"})
_HARD_OBSERVED_DIMENSIONS = frozenset(
    {
        ("typed", "cpu_architecture"),
        ("typed", "execution_environment"),
        ("typed", "identity_context"),
        ("typed", "os_family"),
    }
)
_SINGLETON_DIMENSIONS = frozenset(
    {
        ("typed", "cpu_architecture"),
        ("typed", "execution_environment"),
        ("typed", "identity_context"),
        ("typed", "initial_access"),
        ("typed", "network_position"),
        ("typed", "observation_date"),
        ("typed", "os_family"),
        ("typed", "os_version"),
        ("typed", "system_role"),
    }
)

# Scores are meaningful only inside their own epistemic lane.  These values are deliberately
# separate even where two happen to be close: changing one lane must not silently alter another.
LANE_THRESHOLDS: Mapping[EpistemicLane, float] = MappingProxyType(
    {
        EpistemicLane.REFERENCE: 0.40,
        EpistemicLane.CASE_STEP: 0.45,
        EpistemicLane.NEGATIVE_EVIDENCE: 0.35,
        EpistemicLane.GUIDANCE: 0.50,
    }
)

_STATUS_QUALITY = {
    VerificationStatus.EXTRACTED: 0.55,
    VerificationStatus.VERIFIED: 0.90,
    VerificationStatus.CORROBORATED: 1.00,
    VerificationStatus.CONTESTED: 0.35,
    VerificationStatus.DEPRECATED: 0.20,
    VerificationStatus.REJECTED: 0.00,
}
_GENERALIZABILITY_QUALITY = {
    Generalizability.NONE: 0.20,
    Generalizability.LOW: 0.45,
    Generalizability.MEDIUM: 0.70,
    Generalizability.HIGH: 1.00,
}


class RankedCandidates(BaseModel):
    """Lane-separated qualifying hits plus explainable exclusions and context questions."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    references: tuple[RetrievalHit, ...] = ()
    case_steps: tuple[RetrievalHit, ...] = ()
    negative_cases: tuple[RetrievalHit, ...] = ()
    decision_guidance: tuple[RetrievalHit, ...] = ()
    rejected_candidates: tuple[RejectedCandidate, ...] = ()
    missing_context_questions: tuple[Reason, ...] = Field(default=(), max_length=128)

    @model_validator(mode="after")
    def validate_ranked_shape(self) -> RankedCandidates:
        lanes = (
            (EpistemicLane.REFERENCE, self.references),
            (EpistemicLane.CASE_STEP, self.case_steps),
            (EpistemicLane.NEGATIVE_EVIDENCE, self.negative_cases),
            (EpistemicLane.GUIDANCE, self.decision_guidance),
        )
        all_hit_ids: set[str] = set()
        for lane, hits in lanes:
            if any(hit.lane is not lane for hit in hits):
                raise ValueError("ranked hit is in the wrong epistemic lane")
            if hits != _diversified_lane_order(hits):
                raise ValueError(
                    "ranked hits must use stable lane-local independence-group ordering"
                )
            for hit in hits:
                if hit.artifact_id in all_hit_ids:
                    raise ValueError("an artifact can qualify in only one epistemic lane")
                all_hit_ids.add(hit.artifact_id)
        if self.rejected_candidates != tuple(
            sorted(self.rejected_candidates, key=lambda candidate: candidate.artifact_id)
        ):
            raise ValueError("rejected candidates must use stable artifact ordering")
        rejected_ids = {candidate.artifact_id for candidate in self.rejected_candidates}
        if len(rejected_ids) != len(self.rejected_candidates):
            raise ValueError("rejected artifact identities must be unique")
        if all_hit_ids & rejected_ids:
            raise ValueError("an artifact cannot both qualify and be rejected")
        questions = tuple(sorted(set(self.missing_context_questions)))
        object.__setattr__(self, "missing_context_questions", questions)
        return self


@dataclass(frozen=True, slots=True)
class _ApplicabilityFacet:
    namespace: str
    key: str
    value: str
    relation: ContextRelation
    confidence: float

    @property
    def dimension(self) -> tuple[str, str]:
        return (self.namespace, self.key)

    @property
    def rendered_dimension(self) -> str:
        return f"{self.namespace}.{self.key}"


@dataclass(frozen=True, slots=True)
class _ApplicabilityResult:
    hard_rejections: tuple[str, ...]
    matched_facets: tuple[SituationFacet, ...]
    matched_descriptions: tuple[str, ...]
    missing_context: tuple[str, ...]
    required_coverage: float
    context_similarity: float
    unknown_penalty: float


@dataclass(frozen=True, slots=True)
class _SafeArtifactView:
    artifact: IndexedArtifact
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _EvaluatedCandidate:
    candidate: IndexCandidate
    lane: EpistemicLane | None
    safe_artifact: IndexedArtifact
    safe_view_notes: tuple[str, ...]
    applicability: _ApplicabilityResult
    rejection_reasons: tuple[str, ...]


def rank_candidates(
    query: RetrievalQuery,
    candidates: Iterable[IndexCandidate],
) -> RankedCandidates:
    """Filter hard incompatibilities and rank candidates inside independent evidence lanes.

    All Pydantic inputs are checked for hidden ``model_copy`` state, JSON-round-tripped, and
    strictly revalidated before any field influences ranking.  Source-local case details are
    removed from returned retrieval views; canonical repository lookup remains the explicit path
    for inspecting them.
    """

    query = _strict_revalidate(query, RetrievalQuery)
    canonical_candidates = _bounded_candidates(candidates)
    if len({candidate.artifact_id for candidate in canonical_candidates}) != len(
        canonical_candidates
    ):
        raise ValueError("ranking candidates must have unique artifact identities")

    live_facets = _live_facets(query)
    live_values = _live_values(live_facets)
    observed_services = frozenset(_normalize(value) for value in query.situation.services)

    evaluated: list[_EvaluatedCandidate] = []
    all_questions: set[str] = set()
    for candidate in canonical_candidates:
        artifact = candidate.artifact
        lane = _lane_for(artifact)
        budget_reasons = _ranking_budget_reasons(artifact)
        safe_view = _safe_retrieval_artifact(artifact)
        applicability = (
            _empty_applicability()
            if budget_reasons
            else _assess_applicability(
                artifact,
                live_facets,
                live_values,
                observed_services=observed_services,
            )
        )

        rejection_reasons = [*budget_reasons, *applicability.hard_rejections]
        if lane is None:
            rejection_reasons.append("parent case metadata is not a qualifying retrieval hit")
        if artifact.assessment.verification_status is VerificationStatus.REJECTED:
            rejection_reasons.append("canonical verification status is rejected")
        evaluated.append(
            _EvaluatedCandidate(
                candidate=candidate,
                lane=lane,
                safe_artifact=safe_view.artifact,
                safe_view_notes=safe_view.notes,
                applicability=applicability,
                rejection_reasons=tuple(rejection_reasons),
            )
        )

    group_counts = Counter(
        (item.lane, item.candidate.artifact.assessment.independence_group)
        for item in evaluated
        if item.lane is not None and not item.rejection_reasons
    )
    query_as_of = _query_observation_date(query)
    hits: dict[EpistemicLane, list[RetrievalHit]] = defaultdict(list)
    rejected: list[RejectedCandidate] = []
    for item in evaluated:
        candidate = item.candidate
        artifact = candidate.artifact
        lane = item.lane
        safe_artifact = item.safe_artifact
        applicability = item.applicability
        if item.rejection_reasons:
            rejection_reasons = (*item.rejection_reasons, *item.safe_view_notes)
            rejected.append(
                RejectedCandidate(
                    artifact_id=candidate.artifact_id,
                    artifact=safe_artifact,
                    lane=lane,
                    provenance=safe_artifact.source_refs,
                    rejection_reasons=rejection_reasons[:_MAX_EXPLANATION_ITEMS],
                    missing_context=applicability.missing_context[:_MAX_EXPLANATION_ITEMS],
                )
            )
            continue

        assert lane is not None
        all_questions.update(applicability.missing_context)
        score = _score_candidate(
            candidate,
            applicability,
            as_of=query_as_of,
            independence_group_frequency=group_counts[
                (lane, artifact.assessment.independence_group)
            ],
        )
        threshold = LANE_THRESHOLDS[lane]
        if score.total < threshold:
            rejected.append(
                RejectedCandidate(
                    artifact_id=candidate.artifact_id,
                    artifact=safe_artifact,
                    lane=lane,
                    provenance=safe_artifact.source_refs,
                    rejection_reasons=(
                        f"score {score.total:.3f} below {lane.value} threshold {threshold:.2f}",
                    ),
                    missing_context=applicability.missing_context[:_MAX_EXPLANATION_ITEMS],
                )
            )
            continue

        qualification_reasons = [
            f"ranked in {lane.value} lane above threshold {threshold:.2f}",
            f"verification status {artifact.assessment.verification_status.value}",
        ]
        if artifact.assessment.verification_status is VerificationStatus.CONTESTED:
            qualification_reasons.append("contested evidence retained with reduced confidence")
        if (
            artifact.assessment.verification_status is VerificationStatus.DEPRECATED
            and score.freshness < 1.0
        ):
            qualification_reasons.append("deprecated evidence retained with reduced freshness")
        qualification_reasons.extend(item.safe_view_notes)
        qualification_reasons.extend(applicability.matched_descriptions)
        hits[lane].append(
            RetrievalHit(
                artifact_id=candidate.artifact_id,
                artifact=safe_artifact,
                lane=lane,
                provenance=safe_artifact.source_refs,
                score=score,
                matched_facets=applicability.matched_facets[:_MAX_EXPLANATION_ITEMS],
                qualification_reasons=tuple(qualification_reasons[:_MAX_EXPLANATION_ITEMS]),
                missing_context=applicability.missing_context[:_MAX_EXPLANATION_ITEMS],
            )
        )

    for lane, lane_hits in tuple(hits.items()):
        hits[lane] = list(_diversified_lane_order(lane_hits))
    rejected.sort(key=lambda item: item.artifact_id)
    return RankedCandidates(
        references=tuple(hits[EpistemicLane.REFERENCE]),
        case_steps=tuple(hits[EpistemicLane.CASE_STEP]),
        negative_cases=tuple(hits[EpistemicLane.NEGATIVE_EVIDENCE]),
        decision_guidance=tuple(hits[EpistemicLane.GUIDANCE]),
        rejected_candidates=tuple(rejected),
        missing_context_questions=tuple(sorted(all_questions))[:_MAX_GLOBAL_QUESTIONS],
    )


def _diversified_lane_order(hits: Iterable[RetrievalHit]) -> tuple[RetrievalHit, ...]:
    groups: dict[str, list[RetrievalHit]] = defaultdict(list)
    for hit in hits:
        groups[hit.artifact.assessment.independence_group].append(hit)
    for group_hits in groups.values():
        group_hits.sort(key=lambda hit: (-hit.score.total, hit.artifact_id))
    group_order = tuple(
        sorted(
            groups,
            key=lambda group: (
                -groups[group][0].score.total,
                groups[group][0].artifact_id,
                group,
            ),
        )
    )
    ordered: list[RetrievalHit] = []
    offset = 0
    while any(offset < len(groups[group]) for group in group_order):
        ordered.extend(
            groups[group][offset] for group in group_order if offset < len(groups[group])
        )
        offset += 1
    return tuple(ordered)


def _bounded_candidates(candidates: Iterable[IndexCandidate]) -> tuple[IndexCandidate, ...]:
    validated: list[IndexCandidate] = []
    for offset, candidate in enumerate(candidates):
        if offset >= _MAX_RANKING_CANDIDATES:
            raise ValueError("ranking candidate count exceeds the cumulative bound")
        validated.append(_strict_revalidate(candidate, IndexCandidate))
    return tuple(validated)


def _strict_revalidate(value: Any, model: type[BaseModel]):
    if not isinstance(value, model):
        raise ValueError(f"ranking requires a {model.__name__}")
    _reject_hidden_model_state(value)
    try:
        primitive = json.loads(json.dumps(value.model_dump(mode="json"), allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError("retrieval model is not JSON-primitive safe") from error
    return model.model_validate(primitive)


def _reject_hidden_model_state(value: object, seen: set[int] | None = None) -> None:
    seen = set() if seen is None else seen
    identity = id(value)
    if identity in seen:
        return
    seen.add(identity)
    if isinstance(value, BaseModel):
        fields = type(value).model_fields
        if set(value.__dict__) - set(fields) or value.__pydantic_extra__:
            raise ValueError("unsafe retrieval model state")
        for field_name in fields:
            if field_name in value.__dict__:
                _reject_hidden_model_state(value.__dict__[field_name], seen)
        return
    if isinstance(value, dict):
        for item in value.values():
            _reject_hidden_model_state(item, seen)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _reject_hidden_model_state(item, seen)


def _live_facets(query: RetrievalQuery) -> tuple[SituationFacet, ...]:
    by_identity: dict[tuple[str, str, str], SituationFacet] = {}
    for facet in (*query.situation.facts, *query.facets):
        identity = (facet.namespace, facet.key, facet.value)
        previous = by_identity.get(identity)
        if previous is None or facet.confidence > previous.confidence:
            by_identity[identity] = facet
    return tuple(by_identity[key] for key in sorted(by_identity))


def _live_values(
    live_facets: tuple[SituationFacet, ...],
) -> dict[tuple[str, str], tuple[str, ...]]:
    values: dict[tuple[str, str], set[str]] = defaultdict(set)
    for facet in live_facets:
        if facet.confidence < _MIN_KNOWN_FACT_CONFIDENCE:
            continue
        normalized = _normalize(facet.value)
        if normalized not in _UNKNOWN_VALUES:
            values[(facet.namespace, facet.key)].add(normalized)
    return {dimension: tuple(sorted(items)) for dimension, items in values.items()}


def _ranking_budget_reasons(artifact: IndexedArtifact) -> tuple[str, ...]:
    encoded = json.dumps(
        artifact.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    reasons: list[str] = []
    if len(encoded) > _MAX_CANONICAL_PAYLOAD_BYTES:
        reasons.append(
            "candidate canonical payload exceeds "
            f"{_MAX_CANONICAL_PAYLOAD_BYTES}-byte ranking budget"
        )
    typed = artifact.applicability.typed_context
    typed_scalar_count = sum(
        getattr(typed, key) is not None
        for key in (
            "os_family",
            "os_version",
            "cpu_architecture",
            "execution_environment",
            "system_role",
            "identity_context",
            "initial_access",
            "network_position",
            "observation_date",
        )
    )
    facet_count = (
        typed_scalar_count
        + len(typed.services)
        + len(typed.privileges)
        + len(typed.security_controls)
        + len(artifact.applicability.facets)
    )
    if facet_count > _MAX_CANONICAL_FACETS:
        reasons.append(
            f"candidate applicability exceeds {_MAX_CANONICAL_FACETS}-facet ranking budget"
        )
    return tuple(reasons)


def _empty_applicability() -> _ApplicabilityResult:
    return _ApplicabilityResult(
        hard_rejections=(),
        matched_facets=(),
        matched_descriptions=(),
        missing_context=(),
        required_coverage=0.0,
        context_similarity=0.0,
        unknown_penalty=0.0,
    )


def _assess_applicability(
    artifact: IndexedArtifact,
    live_facets: tuple[SituationFacet, ...],
    live_values: dict[tuple[str, str], tuple[str, ...]],
    *,
    observed_services: frozenset[str],
) -> _ApplicabilityResult:
    canonical_facets = _canonical_facets(artifact)
    live_exact = {
        (facet.namespace, facet.key, _normalize(facet.value)): facet
        for facet in live_facets
        if facet.confidence >= _MIN_KNOWN_FACT_CONFIDENCE
    }
    hard_rejections: set[str] = set()
    matched: dict[tuple[str, str, str], SituationFacet] = {}
    matched_descriptions: set[str] = set()
    missing: set[str] = set()
    required_total = 0.0
    required_matched = 0.0
    similarity_total = 0.0
    similarity_matched = 0.0
    unknown_weight = 0.0

    for facet in canonical_facets:
        weight = max(0.05, facet.confidence)
        value = _normalize(facet.value)
        rendered_dimension = _truncate_text(
            facet.rendered_dimension,
            limit=_MAX_EXPLANATION_VALUE_CHARS,
        )
        rendered_value = _truncate_text(value, limit=_MAX_EXPLANATION_VALUE_CHARS)
        dimension_values = live_values.get(facet.dimension, ())
        exact = live_exact.get((*facet.dimension, value))
        service_type = (
            facet.key.removeprefix("services.")
            if facet.namespace == "typed" and facet.key.startswith("services.")
            else None
        )
        service_type_match = service_type is not None and service_type in observed_services
        source_unknown = value in _UNKNOWN_VALUES or facet.relation is ContextRelation.UNKNOWN
        if source_unknown:
            unknown_weight += weight
            missing.add(f"clarify {rendered_dimension} left unknown by source evidence")
            continue

        if facet.dimension in _SINGLETON_DIMENSIONS and len(dimension_values) > 1:
            rendered_conflict = _truncate_text(
                ", ".join(dimension_values),
                limit=_MAX_EXPLANATION_VALUE_CHARS,
            )
            unknown_weight += weight
            missing.add(
                f"resolve contradictory current {rendered_dimension} values: {rendered_conflict}"
            )
            if facet.relation is ContextRelation.REQUIRED:
                required_total += weight
                similarity_total += weight
            elif facet.relation in {
                ContextRelation.OBSERVED,
                ContextRelation.COMPATIBLE,
            }:
                similarity_total += weight
            continue

        if facet.relation is ContextRelation.INCOMPATIBLE:
            if exact is not None:
                hard_rejections.add(
                    f"observed {rendered_dimension}={rendered_value} is explicitly incompatible"
                )
            elif not dimension_values:
                unknown_weight += weight
                missing.add(
                    f"confirm whether current {rendered_dimension} has incompatible value: "
                    f"{rendered_value}"
                )
            continue

        if facet.relation is ContextRelation.REQUIRED:
            required_total += weight
            similarity_total += weight
            if exact is not None:
                required_matched += weight
                similarity_matched += weight
                matched[(exact.namespace, exact.key, exact.value)] = exact
                matched_descriptions.add(f"matched {rendered_dimension}={rendered_value}")
            elif dimension_values:
                rendered_observed = _truncate_text(
                    ", ".join(dimension_values),
                    limit=_MAX_EXPLANATION_VALUE_CHARS,
                )
                hard_rejections.add(
                    f"required {rendered_dimension}={rendered_value} conflicts with observed "
                    f"{rendered_observed}"
                )
            else:
                unknown_weight += weight
                missing.add(
                    f"confirm current {rendered_dimension} (required value: {rendered_value})"
                )
            continue

        if facet.relation in {ContextRelation.OBSERVED, ContextRelation.COMPATIBLE}:
            if exact is not None:
                similarity_total += weight
                similarity_matched += weight
                matched[(exact.namespace, exact.key, exact.value)] = exact
                matched_descriptions.add(f"matched {rendered_dimension}={rendered_value}")
            elif dimension_values:
                similarity_total += weight
                if (
                    facet.relation is ContextRelation.OBSERVED
                    and facet.dimension in _HARD_OBSERVED_DIMENSIONS
                ):
                    rendered_observed = _truncate_text(
                        ", ".join(dimension_values),
                        limit=_MAX_EXPLANATION_VALUE_CHARS,
                    )
                    hard_rejections.add(
                        f"observed source context {rendered_dimension}={rendered_value} conflicts "
                        f"with current {rendered_observed}"
                    )
            elif service_type_match:
                similarity_total += weight
                similarity_matched += 0.75 * weight
                matched_descriptions.add(f"matched observed service type {service_type}")
            elif (
                facet.relation is ContextRelation.OBSERVED
                and facet.dimension in _HARD_OBSERVED_DIMENSIONS
            ):
                similarity_total += weight
                unknown_weight += weight
                missing.add(
                    f"confirm current {rendered_dimension} to assess observed value: "
                    f"{rendered_value}"
                )

    required_coverage = 1.0 if required_total == 0 else required_matched / required_total
    context_similarity = 0.5 if similarity_total == 0 else similarity_matched / similarity_total
    relevant_weight = max(1.0, required_total + similarity_total + unknown_weight)
    unknown_penalty = min(0.10, 0.10 * unknown_weight / relevant_weight)
    return _ApplicabilityResult(
        hard_rejections=tuple(sorted(hard_rejections)),
        matched_facets=tuple(matched[key] for key in sorted(matched)),
        matched_descriptions=tuple(sorted(matched_descriptions)),
        missing_context=tuple(sorted(missing)),
        required_coverage=_bounded(required_coverage),
        context_similarity=_bounded(context_similarity),
        unknown_penalty=_bounded(unknown_penalty),
    )


def _canonical_facets(artifact: IndexedArtifact) -> tuple[_ApplicabilityFacet, ...]:
    typed = artifact.applicability.typed_context
    facets: list[_ApplicabilityFacet] = []
    for key in (
        "os_family",
        "os_version",
        "cpu_architecture",
        "execution_environment",
        "system_role",
        "identity_context",
        "initial_access",
        "network_position",
        "observation_date",
    ):
        assertion = getattr(typed, key)
        if assertion is not None:
            facets.append(_facet("typed", key, assertion))
    facets.extend(
        _facet("typed", f"services.{service.service_type}", service.identity)
        for service in typed.services
    )
    facets.extend(_facet("typed", "privileges", item) for item in typed.privileges)
    facets.extend(_facet("typed", "security_controls", item) for item in typed.security_controls)
    facets.extend(
        _facet(item.namespace, item.key, item.assertion) for item in artifact.applicability.facets
    )
    return tuple(
        sorted(
            facets,
            key=lambda item: (
                item.namespace,
                item.key,
                item.value,
                item.relation.value,
                item.confidence,
            ),
        )
    )


def _facet(namespace: str, key: str, assertion: ContextAssertion) -> _ApplicabilityFacet:
    return _ApplicabilityFacet(
        namespace=_normalize(namespace),
        key=_normalize(key),
        value=assertion.value,
        relation=assertion.relation,
        confidence=assertion.confidence,
    )


def _score_candidate(
    candidate: IndexCandidate,
    applicability: _ApplicabilityResult,
    *,
    as_of: date | None,
    independence_group_frequency: int,
) -> ScoreComponents:
    artifact = candidate.artifact
    verification = _verification_confidence(artifact)
    freshness = _freshness(artifact, as_of=as_of)
    diversity = _source_diversity(
        artifact,
        independence_group_frequency=independence_group_frequency,
    )
    total = (
        0.30 * candidate.lexical_relevance
        + 0.20 * applicability.required_coverage
        + 0.15 * applicability.context_similarity
        + 0.20 * verification
        + 0.05 * freshness
        + 0.10 * diversity
        - applicability.unknown_penalty
    )
    return ScoreComponents(
        lexical_relevance=_bounded(candidate.lexical_relevance),
        facet_coverage=applicability.required_coverage,
        context_similarity=applicability.context_similarity,
        verification_confidence=verification,
        freshness=freshness,
        source_diversity=diversity,
        unknown_condition_penalty=applicability.unknown_penalty,
        total=_bounded(total),
    )


def _verification_confidence(artifact: IndexedArtifact) -> float:
    assessment = artifact.assessment
    quality = (
        0.30 * _STATUS_QUALITY[assessment.verification_status]
        + 0.25 * assessment.source_reliability
        + 0.25 * assessment.extraction_confidence
        + 0.20 * _GENERALIZABILITY_QUALITY[assessment.generalizability]
    )
    evidence_total = assessment.support_count + assessment.contradiction_count
    if evidence_total:
        quality *= assessment.support_count / evidence_total
    return _bounded(quality)


def _source_diversity(
    artifact: IndexedArtifact,
    *,
    independence_group_frequency: int,
) -> float:
    assessment = artifact.assessment
    support_quality = min(1.0, 0.25 + 0.25 * assessment.support_count)
    evidence_total = assessment.support_count + assessment.contradiction_count
    contradiction_balance = assessment.support_count / evidence_total if evidence_total else 0.25
    group_diversity = 1.0 / math.sqrt(max(1, independence_group_frequency))
    return _bounded(support_quality * contradiction_balance * group_diversity)


def _freshness(artifact: IndexedArtifact, *, as_of: date | None) -> float:
    if artifact.assessment.verification_status is VerificationStatus.DEPRECATED:
        return 0.20
    if not _is_version_sensitive(artifact):
        return 1.0
    observed = _artifact_observed_date(artifact)
    if observed is None:
        return 0.25
    if as_of is None:
        return 0.60
    age_days = (as_of - observed).days
    if age_days < -30:
        return 0.25
    if age_days <= 365:
        return 1.0
    if age_days <= 3 * 365:
        return 0.80
    if age_days <= 5 * 365:
        return 0.60
    if age_days <= 10 * 365:
        return 0.40
    return 0.20


def _query_observation_date(query: RetrievalQuery) -> date | None:
    live_dates = frozenset(
        parsed
        for facet in (*query.situation.facts, *query.facets)
        if facet.namespace == "typed"
        and facet.key == "observation_date"
        and facet.confidence >= _MIN_KNOWN_FACT_CONFIDENCE
        and (parsed := _parse_date(facet.value)) is not None
    )
    return next(iter(live_dates)) if len(live_dates) == 1 else None


def _artifact_observed_date(artifact: IndexedArtifact) -> date | None:
    observed = artifact.assessment.freshness_observed_at
    if observed is None and isinstance(artifact, ReferenceArtifact):
        observed = artifact.observed_at
    if observed is None:
        assertion = artifact.applicability.typed_context.observation_date
        if assertion is not None and assertion.relation not in {
            ContextRelation.INCOMPATIBLE,
            ContextRelation.UNKNOWN,
        }:
            observed = assertion.value
    return _parse_date(observed)


def _is_version_sensitive(artifact: IndexedArtifact) -> bool:
    return any(
        "version" in facet.key or _looks_versioned_service(facet)
        for facet in _canonical_facets(artifact)
    )


def _looks_versioned_service(facet: _ApplicabilityFacet) -> bool:
    return facet.key.startswith("services.") and any(
        character.isdigit() for character in facet.value
    )


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC)
    return parsed.date()


def _lane_for(artifact: IndexedArtifact) -> EpistemicLane | None:
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


def _safe_retrieval_artifact(artifact: IndexedArtifact) -> _SafeArtifactView:
    payload = artifact.model_dump(mode="json")
    notes: set[str] = set()

    def compact(value: Any, *, key: str | None = None, top_level: bool = False) -> Any:
        if isinstance(value, dict):
            return {
                item_key: compact(
                    item_value,
                    key=item_key,
                    top_level=top_level and item_key == "source_refs",
                )
                for item_key, item_value in value.items()
                if item_key != "case_specific_details"
            } | ({"case_specific_details": []} if "case_specific_details" in value else {})
        if isinstance(value, list):
            items = value
            if key == "source_refs":
                scanned_items = items[:_MAX_PROVENANCE_SCAN_ITEMS]
                unique: list[Any] = []
                identities: set[str] = set()
                for item in scanned_items:
                    identity = json.dumps(
                        item,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    if identity not in identities:
                        identities.add(identity)
                        unique.append(item)
                items = unique[:_MAX_OUTPUT_PROVENANCE]
                if top_level:
                    if len(unique) != len(scanned_items):
                        entry_description = (
                            f"{len(scanned_items)} inspected canonical entries"
                            if len(value) > _MAX_PROVENANCE_SCAN_ITEMS
                            else f"{len(value)} canonical entries"
                        )
                        notes.add(
                            "provenance deduplicated: showing "
                            f"{len(items)} unique source references from "
                            f"{entry_description}"
                        )
                    if len(value) > _MAX_PROVENANCE_SCAN_ITEMS:
                        notes.add(
                            "provenance bounded: showing "
                            f"{len(items)} of at least {len(unique)} unique source references; "
                            f"{len(value) - _MAX_PROVENANCE_SCAN_ITEMS} canonical entries "
                            "omitted from deduplication scan"
                        )
                    elif len(unique) > _MAX_OUTPUT_PROVENANCE:
                        notes.add(
                            "provenance bounded: showing "
                            f"{len(items)} of {len(unique)} unique source references"
                        )
                elif (
                    len(unique) != len(scanned_items)
                    or len(unique) > _MAX_OUTPUT_PROVENANCE
                    or len(value) > _MAX_PROVENANCE_SCAN_ITEMS
                ):
                    notes.add("nested provenance bounded in retrieval view")
            elif len(items) > _MAX_OUTPUT_SEQUENCE_ITEMS:
                items = items[:_MAX_OUTPUT_SEQUENCE_ITEMS]
                notes.add(
                    "artifact retrieval view sequences bounded to "
                    f"{_MAX_OUTPUT_SEQUENCE_ITEMS} items"
                )
            return [compact(item) for item in items]
        if isinstance(value, str) and len(value) > _MAX_OUTPUT_STRING_CHARS:
            notes.add("artifact retrieval view compacted to bounded output")
            return _truncate_text(value, limit=_MAX_OUTPUT_STRING_CHARS)
        return value

    safe_payload = compact(payload, top_level=True)
    safe_artifact = type(artifact).model_validate(safe_payload)
    return _SafeArtifactView(artifact=safe_artifact, notes=tuple(sorted(notes)))


def _normalize(value: str) -> str:
    return " ".join(value.split()).casefold()


def _truncate_text(value: str, *, limit: int) -> str:
    if len(value) <= limit:
        return value
    digest = sha256(value.encode("utf-8")).hexdigest()[:16]
    suffix = f" … [truncated sha256:{digest}]"
    return f"{value[: limit - len(suffix)]}{suffix}"


def _bounded(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("ranking score must be finite")
    return round(min(1.0, max(0.0, value)), 6)


__all__ = ["LANE_THRESHOLDS", "RankedCandidates", "rank_candidates"]
