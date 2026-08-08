"""Validated, lane-aware orchestration for the local knowledge retrieval index."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, TypeAdapter, ValidationError

from sedna.knowledge.retrieval import ranking
from sedna.knowledge.retrieval.models import (
    AuthorizationState,
    EpistemicLane,
    IndexCandidate,
    IndexedArtifact,
    KnowledgeGap,
    KnowledgeGapCode,
    Reason,
    RetrievalIndex,
    RetrievalQuery,
    RetrievalResult,
)
from sedna.knowledge.schema import CaseStep, DecisionRule, KnowledgeCase, ReferenceArtifact

_ARTIFACT_ID = TypeAdapter(Reason)
_ARTIFACT_TYPES = (ReferenceArtifact, KnowledgeCase, CaseStep, DecisionRule)
_RESULT_REJECTION_LIMIT = 64
_GAP_ITEM_LIMIT = 32
_OBSERVED_DOMAIN_LIMIT = 2048


@dataclass(frozen=True, slots=True)
class KnowledgeRetrievalService:
    """Validate once, query bounded lanes, rank, and return a closed retrieval result."""

    index: RetrievalIndex

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """Return lane-local knowledge or an explicit pre-backend/knowledge gap."""
        query = _strict_query(query)
        prebackend = _prebackend_result(query)
        if prebackend is not None:
            return prebackend

        try:
            candidates = self._search_lanes(query)
            ranked = ranking.rank_candidates(query, candidates)
            references = ranking.select_diversified_hits(
                ranked.references,
                limit=query.lane_limit,
            )
            case_steps = ranking.select_diversified_hits(
                ranked.case_steps,
                limit=query.lane_limit,
            )
            negative_cases = ranking.select_diversified_hits(
                ranked.negative_cases,
                limit=query.lane_limit,
            )
            decision_guidance = ranking.select_diversified_hits(
                ranked.decision_guidance,
                limit=query.lane_limit,
            )
        except Exception:
            return _backend_failure_result(query)

        rejected = ranked.rejected_candidates[:_RESULT_REJECTION_LIMIT]
        if any((references, case_steps, negative_cases, decision_guidance)):
            return RetrievalResult(
                target=query.situation.target,
                authorization=query.situation.authorization,
                references=references,
                case_steps=case_steps,
                negative_cases=negative_cases,
                decision_guidance=decision_guidance,
                rejected_candidates=rejected,
            )
        return RetrievalResult(
            target=query.situation.target,
            authorization=query.situation.authorization,
            rejected_candidates=rejected,
            knowledge_gap=_no_applicable_gap(query, ranked.missing_context_questions),
        )

    def get_artifact(self, artifact_id: str) -> IndexedArtifact | None:
        """Return one exact canonical artifact without exposing backend failure details."""
        canonical_id = _strict_artifact_id(artifact_id)
        try:
            artifact = self.index.get_artifact(canonical_id)
            if artifact is None:
                return None
            if type(artifact) not in _ARTIFACT_TYPES:
                raise ValueError("unsupported indexed artifact type")
            ranking._preflight_or_raise(artifact, label="retrieved canonical artifact")
            canonical = ranking._strict_revalidate(artifact, type(artifact))
            if _artifact_identity(canonical) != canonical_id:
                raise ValueError("artifact lookup identity mismatch")
            return canonical
        except Exception:
            raise RuntimeError("knowledge artifact lookup failed") from None

    def _search_lanes(self, query: RetrievalQuery) -> tuple[IndexCandidate, ...]:
        candidates: list[IndexCandidate] = []
        for lane in EpistemicLane:
            lane_candidates = self.index.search_candidates(
                query,
                lane=lane,
                limit=query.max_candidates,
            )
            if type(lane_candidates) is not tuple:
                raise ValueError("retrieval index must return a candidate tuple")
            if len(lane_candidates) > query.max_candidates:
                raise ValueError("retrieval index exceeded the requested candidate limit")
            if any(not isinstance(candidate, IndexCandidate) for candidate in lane_candidates):
                raise ValueError("retrieval index returned a non-canonical candidate")
            candidates.extend(lane_candidates)
        return tuple(candidates)


def _strict_query(query: RetrievalQuery) -> RetrievalQuery:
    if type(query) is not RetrievalQuery:
        raise ValueError("retrieval requires an exact RetrievalQuery")
    ranking._preflight_or_raise(query, label="retrieval query")
    return ranking._strict_revalidate(query, RetrievalQuery)


def _prebackend_result(query: RetrievalQuery) -> RetrievalResult | None:
    situation = query.situation
    if not situation.target.is_valid:
        return RetrievalResult(
            target=situation.target,
            authorization=situation.authorization,
            knowledge_gap=KnowledgeGap(
                code=KnowledgeGapCode.INVALID_TARGET,
                summary="the supplied target is not syntactically valid",
                observed_domain=_observed_domain(query),
                research_eligible=False,
            ),
        )
    if situation.authorization.state is not AuthorizationState.AUTHORIZED:
        summary = (
            "the supplied target is explicitly outside authorized scope"
            if situation.authorization.state is AuthorizationState.UNAUTHORIZED
            else "authorization for the supplied target has not been established"
        )
        return RetrievalResult(
            target=situation.target,
            authorization=situation.authorization,
            knowledge_gap=KnowledgeGap(
                code=KnowledgeGapCode.UNAUTHORIZED_SCOPE,
                summary=summary,
                observed_domain=_observed_domain(query),
                missing_context=("explicit authorization scope for the supplied target",),
                research_eligible=False,
            ),
        )
    return None


def _no_applicable_gap(
    query: RetrievalQuery,
    ranking_questions: tuple[Reason, ...],
) -> KnowledgeGap:
    missing = tuple(
        sorted(
            {
                *ranking_questions,
                *query.situation.unresolved_questions,
            }
        )
    )[:_GAP_ITEM_LIMIT]
    if not missing:
        missing = ("applicable source-backed evidence for the observed domain",)
    return KnowledgeGap(
        code=KnowledgeGapCode.NO_APPLICABLE_KNOWLEDGE,
        summary="no indexed knowledge qualified for the observed situation",
        observed_domain=_observed_domain(query),
        missing_context=missing,
        suggested_document_ingestion=(
            "ingest source-backed technical documentation matching the observed domain",
            "ingest relevant case studies with explicit applicability context",
        ),
        research_eligible=True,
    )


def _backend_failure_result(query: RetrievalQuery) -> RetrievalResult:
    return RetrievalResult(
        target=query.situation.target,
        authorization=query.situation.authorization,
        knowledge_gap=KnowledgeGap(
            code=KnowledgeGapCode.NO_APPLICABLE_KNOWLEDGE,
            summary="knowledge retrieval is temporarily unavailable",
            observed_domain=_observed_domain(query),
            missing_context=("retrieval index availability",),
            research_eligible=False,
        ),
    )


def _observed_domain(query: RetrievalQuery) -> str:
    values = {
        *query.situation.terms,
        *query.situation.services,
        *query.terms,
        *query.synonyms,
        *(
            f"{facet.namespace}.{facet.key}={facet.value}"
            for facet in (*query.situation.facts, *query.facets)
        ),
    }
    if not values:
        values.add(query.situation.target.kind.value)
    return "; ".join(sorted(values))[:_OBSERVED_DOMAIN_LIMIT]


def _strict_artifact_id(artifact_id: str) -> str:
    if type(artifact_id) is not str or artifact_id != artifact_id.strip():
        raise ValueError("artifact_id must be a bounded canonical identifier")
    try:
        return _ARTIFACT_ID.validate_python(artifact_id)
    except ValidationError as error:
        raise ValueError("artifact_id must be a bounded canonical identifier") from error


def _artifact_identity(artifact: BaseModel) -> str:
    if isinstance(artifact, ReferenceArtifact):
        return artifact.artifact_id
    if isinstance(artifact, KnowledgeCase):
        return artifact.case_id
    if isinstance(artifact, CaseStep):
        return artifact.step_id
    if isinstance(artifact, DecisionRule):
        return artifact.rule_id
    raise ValueError("unsupported indexed artifact type")


__all__ = ["KnowledgeRetrievalService"]
