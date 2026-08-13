"""Bounded situation-conditioned knowledge retrieval for planning."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from hashlib import sha256
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sedna.engagement.models import ScopeReference, Sha256Hex
from sedna.knowledge.retrieval.models import (
    AuthorizationScope,
    AuthorizationState,
    CurrentSituation,
    ExecutionExampleCoverageGap,
    KnowledgeGap,
    KnowledgeGapCode,
    RejectedCandidate,
    RetrievalHit,
    RetrievalQuery,
    SituationFacet,
    ValidatedTarget,
)
from sedna.knowledge.schema.execution import ExecutionExample
from sedna.planning.models import SituationProjection

MAX_PLANNER_KNOWLEDGE_BYTES = 512 * 1024
_PRIVATE_TERM = re.compile(
    r"(?:\b(?:password|passwd|secret|token|api[_ -]?key|credential)\b|"
    r"(?:htb|thm|flag)\s*\{)",
    re.IGNORECASE,
)


class CandidateResearchSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    source_id: Annotated[str, Field(min_length=1, max_length=512)]
    locator: Annotated[str, Field(min_length=1, max_length=4096)]
    topics: tuple[Annotated[str, Field(min_length=1, max_length=256)], ...] = Field(max_length=32)
    origin: Annotated[str, Field(min_length=1, max_length=64)]
    status: Annotated[str, Field(min_length=1, max_length=64)]
    why_applicable: Annotated[str, Field(min_length=1, max_length=2048)]


class PlannerKnowledgeContext(BaseModel):
    """Bounded, data-only planner context preserving retrieval lanes and identities."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    canonical_revision: Sha256Hex
    situation_digest: Sha256Hex
    source_registry_digest: Sha256Hex
    references: tuple[RetrievalHit, ...] = Field(default=(), max_length=64)
    case_steps: tuple[RetrievalHit, ...] = Field(default=(), max_length=64)
    negative_cases: tuple[RetrievalHit, ...] = Field(default=(), max_length=64)
    decision_guidance: tuple[RetrievalHit, ...] = Field(default=(), max_length=64)
    rejected_candidates: tuple[RejectedCandidate, ...] = Field(default=(), max_length=64)
    knowledge_gaps: tuple[KnowledgeGap, ...] = Field(default=(), max_length=32)
    execution_examples: tuple[ExecutionExample, ...] = Field(default=(), max_length=16)
    execution_example_gaps: tuple[ExecutionExampleCoverageGap, ...] = Field(
        default=(), max_length=32
    )
    candidate_research_sources: tuple[CandidateResearchSource, ...] = Field(
        default=(), max_length=16
    )
    retrieval_unavailable: bool = False
    context_digest: Sha256Hex

    @model_validator(mode="after")
    def _bounded_context(self) -> PlannerKnowledgeContext:
        if len(self.model_dump_json().encode("utf-8")) > MAX_PLANNER_KNOWLEDGE_BYTES:
            raise ValueError("planner knowledge context exceeds the cumulative bound")
        return self


def assemble_planner_knowledge(
    situation: SituationProjection,
    scope_references: tuple[ScopeReference, ...],
    *,
    retrieval: Any,
    source_registry: Any,
    canonical_revision: Callable[[], str],
) -> PlannerKnowledgeContext:
    """Retrieve all lanes, drill into qualifying hits, and revision-bind the result."""
    revision = _strict_revision(canonical_revision())
    results = tuple(
        retrieval.retrieve(query) for query in build_retrieval_queries(situation, scope_references)
    )
    references = _unique_by_id((hit for result in results for hit in result.references), limit=64)
    case_steps = _unique_by_id((hit for result in results for hit in result.case_steps), limit=64)
    negative_cases = _unique_by_id(
        (hit for result in results for hit in result.negative_cases), limit=64
    )
    guidance = _unique_by_id(
        (hit for result in results for hit in result.decision_guidance), limit=64
    )
    rejected = _unique_by_id(
        (candidate for result in results for candidate in result.rejected_candidates), limit=64
    )
    gaps = tuple(result.knowledge_gap for result in results if result.knowledge_gap is not None)[
        :32
    ]
    examples: list[ExecutionExample] = []
    example_gaps: list[ExecutionExampleCoverageGap] = []
    seen_example_ids: set[str] = set()
    for hit in (*references, *case_steps, *negative_cases, *guidance):
        if len(examples) >= 16:
            break
        drilldown = retrieval.get_execution_examples(hit.artifact_id)
        if drilldown.coverage_gap is not None:
            example_gaps.append(drilldown.coverage_gap)
        for example in drilldown.examples:
            if len(examples) >= 16:
                break
            if example.example_id not in seen_example_ids and _example_applies(example, situation):
                examples.append(example)
                seen_example_ids.add(example.example_id)
    source_page = source_registry.list_planner_hints(topic_tokens=_planner_topic_tokens(situation))
    retrieval_unavailable = any(gap.code is KnowledgeGapCode.RETRIEVAL_UNAVAILABLE for gap in gaps)
    candidates = (
        ()
        if retrieval_unavailable
        else tuple(
            CandidateResearchSource(
                source_id=entry.source_id,
                locator=entry.locator,
                topics=entry.topics,
                origin=entry.origin.value,
                status=entry.status.value,
                why_applicable="candidate source; validate claims against current evidence",
            )
            for entry in source_page.entries
            if _source_applies(entry.topics, situation)
        )
    )
    if _strict_revision(canonical_revision()) != revision:
        raise RuntimeError("canonical knowledge changed during planner retrieval")
    values = {
        "canonical_revision": revision,
        "situation_digest": situation.state_digest,
        "source_registry_digest": source_page.registry_sha256,
        "references": references,
        "case_steps": case_steps,
        "negative_cases": negative_cases,
        "decision_guidance": guidance,
        "rejected_candidates": rejected,
        "knowledge_gaps": gaps,
        "execution_examples": tuple(examples),
        "execution_example_gaps": _bounded_example_gaps(tuple(example_gaps)),
        "candidate_research_sources": candidates,
        "retrieval_unavailable": retrieval_unavailable,
    }
    serializable = {
        key: [item.model_dump(mode="json") for item in value] if isinstance(value, tuple) else value
        for key, value in values.items()
    }
    digest = sha256(
        json.dumps(serializable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return PlannerKnowledgeContext(**values, context_digest=digest)


def _strict_revision(value: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("invalid canonical knowledge revision")
    return value


def _unique_by_id(items: Any, *, limit: int) -> tuple[Any, ...]:
    selected: list[Any] = []
    seen: set[str] = set()
    for item in items:
        identifier = item.artifact_id
        if identifier in seen:
            continue
        selected.append(item)
        seen.add(identifier)
        if len(selected) == limit:
            break
    return tuple(selected)


def _bounded_example_gaps(
    gaps: tuple[ExecutionExampleCoverageGap, ...],
) -> tuple[ExecutionExampleCoverageGap, ...]:
    selected: list[ExecutionExampleCoverageGap] = []
    seen: set[tuple[str, str, str, str]] = set()
    for gap in gaps:
        identity = (
            gap.code.value,
            gap.source_id,
            gap.semantic_schema_version,
            gap.explanation,
        )
        if identity in seen:
            continue
        selected.append(gap)
        seen.add(identity)
        if len(selected) == 32:
            break
    return tuple(selected)


def _planner_topic_tokens(situation: SituationProjection) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                item.value.casefold()
                for item in situation.facets
                if item.key.casefold() in {"os_family", "cpu_architecture", "service", "protocol"}
                and not _PRIVATE_TERM.search(item.value)
            }
        )
    )


def _example_applies(example: ExecutionExample, situation: SituationProjection) -> bool:
    observed = {item.key.casefold(): item.value.casefold() for item in situation.facets}
    typed = example.applicability.typed_context
    for dimension in ("os_family", "os_version", "cpu_architecture", "execution_environment"):
        assertion = getattr(typed, dimension)
        if assertion is not None and not _context_assertion_applies(
            observed.get(dimension), assertion.value, assertion.relation.value
        ):
            return False
    for facet in example.applicability.facets:
        if not _context_assertion_applies(
            observed.get(facet.key.casefold()),
            facet.assertion.value,
            facet.assertion.relation.value,
        ):
            return False
    for constraint in example.platform_constraints:
        current = observed.get(constraint.dimension)
        if current is None and constraint.relation == "required":
            return False
        if current is None:
            continue
        expected = constraint.value.casefold()
        if constraint.relation == "incompatible" and current == expected:
            return False
        if constraint.relation == "required" and current != expected:
            return False
    return True


def _context_assertion_applies(current: str | None, expected: str, relation: str) -> bool:
    normalized = expected.casefold()
    if relation == "required":
        return current is not None and current == normalized
    if relation == "incompatible":
        return current is None or current != normalized
    return True


def _source_applies(topics: tuple[str, ...], situation: SituationProjection) -> bool:
    observed = {item.value.casefold() for item in situation.facets}
    topic_set = {item.casefold() for item in topics}
    incompatible = {
        "linux": {"windows", "macos", "darwin", "freebsd"},
        "windows": {"linux", "macos", "darwin", "freebsd"},
        "x86_64": {"aarch64", "arm64", "i386", "armv7"},
        "aarch64": {"x86_64", "amd64", "i386", "armv7"},
    }
    return not any(topic_set & incompatible.get(value, set()) for value in observed)


def build_retrieval_queries(
    situation: SituationProjection,
    scope_references: tuple[ScopeReference, ...],
    *,
    max_candidates: int = 32,
    lane_limit: int = 5,
) -> tuple[RetrievalQuery, ...]:
    """Build one conservative query for each valid, explicitly scoped target."""
    terms = _safe_texts((item.text for item in situation.facts), limit=32)
    facets = tuple(
        SituationFacet(
            namespace="observed",
            key=item.key,
            value=item.value,
            confidence=1.0,
        )
        for item in situation.facets
        if not _PRIVATE_TERM.search(item.value)
    )[:32]
    services = tuple(
        sorted(
            {
                word
                for term in terms
                for word in term.split()
                if word.casefold() in {"http", "https", "ssh", "dns", "smb", "ftp", "rdp"}
            }
        )
    )
    access = _safe_texts(
        (
            f"{item.subject}: credential available"
            if " ".join(item.state.split()).casefold() == "credential available"
            else f"{item.subject}: {item.state}"
            for item in situation.access_states
        ),
        limit=64,
        allowed_exact_suffix=": credential available",
    )
    hypotheses = _safe_texts((item.text for item in situation.hypotheses), limit=64)
    outcomes = tuple(
        (item.outcome.value, item.summary)
        for item in situation.attempts
        if not _PRIVATE_TERM.search(item.summary)
    )[:64]
    unresolved = _safe_texts((item.question for item in situation.unresolved_information), limit=64)
    queries: list[RetrievalQuery] = []
    seen: set[str] = set()
    for reference in sorted(scope_references, key=lambda item: item.reference_id):
        target = ValidatedTarget.parse(reference.value)
        if not target.is_valid or target.normalized is None or target.normalized in seen:
            continue
        authorization = _authorization_for(reference, target)
        if not authorization.authorizes(target):
            continue
        seen.add(target.normalized)
        queries.append(
            RetrievalQuery(
                situation=CurrentSituation(
                    target=target,
                    authorization=authorization,
                    terms=terms,
                    facts=facets,
                    access=access,
                    services=services,
                    hypotheses=hypotheses,
                    tried_outcomes=outcomes,
                    unresolved_questions=unresolved,
                ),
                terms=terms,
                facets=facets,
                max_candidates=max_candidates,
                lane_limit=lane_limit,
            )
        )
    return tuple(queries)


def _safe_texts(
    values: Any, *, limit: int, allowed_exact_suffix: str | None = None
) -> tuple[str, ...]:
    """Return deterministic bounded text after removing private-value-shaped records."""
    selected: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(value.split()).casefold()
        safe_symbolic = allowed_exact_suffix is not None and normalized.endswith(
            allowed_exact_suffix
        )
        if _PRIVATE_TERM.search(value) and not safe_symbolic:
            continue
        if not normalized or normalized in seen:
            continue
        selected.append(normalized)
        seen.add(normalized)
        if len(selected) == limit:
            break
    return tuple(sorted(selected))


def _authorization_for(reference: ScopeReference, target: ValidatedTarget) -> AuthorizationScope:
    if reference.kind == "exact_target":
        return AuthorizationScope(state=AuthorizationState.AUTHORIZED, exact_targets=(target,))
    if reference.kind == "hostname":
        return AuthorizationScope(state=AuthorizationState.AUTHORIZED, hostnames=(reference.value,))
    if reference.kind == "url_origin":
        return AuthorizationScope(
            state=AuthorizationState.AUTHORIZED, url_origins=(reference.value,)
        )
    if reference.kind == "generic_id":
        return AuthorizationScope(
            state=AuthorizationState.AUTHORIZED, generic_ids=(reference.value,)
        )
    return AuthorizationScope(state=AuthorizationState.UNKNOWN)
