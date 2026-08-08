"""Tests for backend-neutral, validated retrieval contracts."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from sedna.knowledge.retrieval import (
    CurrentSituation,
    EpistemicLane,
    IndexAudit,
    KnowledgeGap,
    KnowledgeGapCode,
    RetrievalHit,
    RetrievalIndex,
    RetrievalQuery,
    RetrievalResult,
    ScoreComponents,
    SituationFacet,
    TargetKind,
    ValidatedTarget,
)
from sedna.knowledge.schema import (
    ApplicabilityContext,
    ArtifactType,
    EpistemicAssessment,
    ExtractionMetadata,
    Generalizability,
    KnowledgeRole,
    ObservedOutcome,
    Origin,
    ReferenceArtifact,
    SourceLocation,
    SourceRef,
    VerificationStatus,
)


def source_ref() -> SourceRef:
    return SourceRef(
        source_id="source-http",
        path="raw_src/reference.md",
        location=SourceLocation(start_line=1),
    )


def reference() -> ReferenceArtifact:
    return ReferenceArtifact(
        artifact_type=ArtifactType.METHODOLOGY,
        knowledge_role=KnowledgeRole.REFERENCE,
        artifact_id="reference-http",
        subject="HTTP discovery",
        statement="Inspect an HTTP service before choosing a method.",
        origin=Origin.EXPLICIT,
        applicability=ApplicabilityContext(),
        assessment=EpistemicAssessment(
            source_reliability=0.9,
            extraction_confidence=0.8,
            generalizability=Generalizability.MEDIUM,
            context_specificity=0.4,
            verification_status=VerificationStatus.VERIFIED,
            observed_outcome=ObservedOutcome.INFORMATIONAL,
            independence_group="source-http",
        ),
        source_refs=(source_ref(),),
        extraction=ExtractionMetadata(
            schema_version="2",
            parser_id="markdown",
            parser_version="1",
            extractor_id="semantic",
            extractor_version="1",
        ),
    )


@pytest.mark.parametrize(
    ("value", "kind", "normalized"),
    (
        ("10.10.10.10", TargetKind.IPV4, "10.10.10.10"),
        ("2001:db8::1", TargetKind.IPV6, "2001:db8::1"),
        ("WEB.Example.Test", TargetKind.HOSTNAME, "web.example.test"),
        (
            "https://WEB.Example.Test:8443/docs",
            TargetKind.URL,
            "https://web.example.test:8443/docs",
        ),
    ),
)
def test_validated_target_classifies_network_identifiers(
    value: str,
    kind: TargetKind,
    normalized: str,
):
    target = ValidatedTarget.parse(value)

    assert target.is_valid
    assert target.kind is kind
    assert target.normalized == normalized
    assert target.error is None


def test_invalid_ipv4_is_typed_and_is_not_reclassified_as_hostname():
    target = ValidatedTarget.parse("300.456.456.123")

    assert not target.is_valid
    assert target.kind is TargetKind.INVALID
    assert target.normalized is None
    assert target.error == "invalid_ipv4"


def test_generic_targets_require_an_explicit_kind_instead_of_ambiguous_inference():
    implicit = ValidatedTarget.parse("lab:target")
    explicit = ValidatedTarget(value="lab:target", kind=TargetKind.GENERIC)

    assert implicit.kind is TargetKind.INVALID
    assert explicit.is_valid
    assert explicit.normalized == "lab:target"


def test_live_situation_facets_have_no_invented_source_provenance():
    facet = SituationFacet(namespace="Platform", key="OS_Family", value="Linux", confidence=0.8)

    assert facet.model_dump() == {
        "namespace": "platform",
        "key": "os_family",
        "value": "linux",
        "confidence": 0.8,
    }
    with pytest.raises(ValidationError, match="source_refs"):
        SituationFacet(
            namespace="platform",
            key="os_family",
            value="linux",
            confidence=0.8,
            source_refs=(source_ref(),),
        )


def test_situation_normalizes_bounds_sorts_and_deduplicates_live_facts():
    situation = CurrentSituation(
        target=ValidatedTarget.parse("10.10.10.10"),
        terms=(" HTTP ", "http", "Service Discovery"),
        facts=(
            SituationFacet(namespace="network", key="position", value="internal", confidence=0.7),
            SituationFacet(namespace="platform", key="os_family", value="Linux", confidence=0.9),
            SituationFacet(namespace="platform", key="os_family", value="linux", confidence=0.9),
        ),
        access=(" None ", "none"),
        services=("HTTP:80", "http:80"),
        hypotheses=(" HTTP virtual host ", "http virtual host"),
        tried_outcomes=(
            ("inspect_http", "informational"),
            ("inspect_http", "informational"),
        ),
        unresolved_questions=(" OS family ", "os family"),
        authorized_scope=("10.10.10.0/24", "10.10.10.0/24"),
    )

    assert situation.terms == ("http", "service discovery")
    assert tuple(facet.namespace for facet in situation.facts) == ("network", "platform")
    assert situation.access == ("none",)
    assert situation.services == ("http:80",)
    assert situation.hypotheses == ("http virtual host",)
    assert situation.tried_outcomes == (("inspect_http", "informational"),)
    assert situation.unresolved_questions == ("os family",)
    assert situation.authorized_scope == ("10.10.10.0/24",)
    with pytest.raises(ValidationError, match="unique"):
        CurrentSituation(
            target=ValidatedTarget.parse("10.10.10.10"),
            facts=(
                SituationFacet(namespace="platform", key="os", value="linux", confidence=0.8),
                SituationFacet(namespace="platform", key="os", value="linux", confidence=0.7),
            ),
        )


def test_situation_uses_searchable_validators_for_live_text():
    with pytest.raises(ValidationError, match="final flag"):
        CurrentSituation(
            target=ValidatedTarget.parse("10.10.10.10"),
            terms=("HTB{must_not_be_searchable}",),
        )


def test_retrieval_query_is_bounded_and_carries_only_validated_situation():
    situation = CurrentSituation(target=ValidatedTarget.parse("10.10.10.10"))
    query = RetrievalQuery(
        situation=situation,
        terms=("http", "HTTP"),
        synonyms=("web", "web"),
        max_candidates=12,
        lane_limit=3,
    )

    assert query.terms == ("http",)
    assert query.synonyms == ("web",)
    assert query.max_candidates == 12
    with pytest.raises(ValidationError):
        RetrievalQuery(situation=situation, max_candidates=101)


@pytest.mark.parametrize("value", (math.inf, -math.inf, math.nan, -0.01, 1.01))
def test_score_components_reject_non_finite_and_out_of_range_values(value: float):
    with pytest.raises(ValidationError):
        ScoreComponents(lexical_relevance=value)


def test_retrieval_hit_requires_canonical_identity_and_exact_provenance():
    artifact = reference()
    hit = RetrievalHit(
        artifact_id="reference-http",
        artifact=artifact,
        lane=EpistemicLane.REFERENCE,
        provenance=artifact.source_refs,
        score=ScoreComponents(lexical_relevance=0.8, total=0.8),
        qualification_reasons=("matches normalized HTTP query",),
    )

    assert hit.artifact is artifact
    with pytest.raises(ValidationError, match="artifact_id"):
        RetrievalHit(
            artifact_id="wrong-id",
            artifact=artifact,
            lane=EpistemicLane.REFERENCE,
            provenance=artifact.source_refs,
            score=ScoreComponents(total=0.8),
            qualification_reasons=("matches",),
        )
    with pytest.raises(ValidationError, match="provenance"):
        RetrievalHit(
            artifact_id="reference-http",
            artifact=artifact,
            lane=EpistemicLane.REFERENCE,
            provenance=(),
            score=ScoreComponents(total=0.8),
            qualification_reasons=("matches",),
        )


def test_retrieval_result_separates_lanes_and_requires_consistent_gap_shape():
    artifact = reference()
    hit = RetrievalHit(
        artifact_id="reference-http",
        artifact=artifact,
        lane=EpistemicLane.REFERENCE,
        provenance=artifact.source_refs,
        score=ScoreComponents(total=0.8),
        qualification_reasons=("matches",),
    )
    target = ValidatedTarget.parse("10.10.10.10")
    result = RetrievalResult(target=target, references=(hit,))

    assert result.references == (hit,)
    with pytest.raises(ValidationError, match="case_step"):
        RetrievalResult(target=target, case_steps=(hit,))
    with pytest.raises(ValidationError, match="knowledge gap"):
        RetrievalResult(
            target=target,
            references=(hit,),
            knowledge_gap=KnowledgeGap(
                code=KnowledgeGapCode.NO_APPLICABLE_KNOWLEDGE,
                summary="No applicable knowledge.",
            ),
        )


def test_invalid_target_result_is_a_gap_without_any_backend_candidates():
    target = ValidatedTarget.parse("300.456.456.123")
    result = RetrievalResult(
        target=target,
        knowledge_gap=KnowledgeGap(
            code=KnowledgeGapCode.INVALID_TARGET,
            summary="The supplied target is not syntactically valid.",
        ),
    )

    assert result.is_invalid_target
    with pytest.raises(ValidationError, match="invalid target"):
        RetrievalResult(target=target, references=())


def test_knowledge_gap_codes_are_closed_and_index_protocol_is_runtime_checkable():
    assert {member.value for member in KnowledgeGapCode} == {
        "invalid_target",
        "no_applicable_knowledge",
        "missing_required_context",
        "unauthorized_scope",
    }
    assert isinstance(IndexAudit(), IndexAudit)
    assert isinstance(_IndexDouble(), RetrievalIndex)
    with pytest.raises(ValidationError):
        KnowledgeGap(code="invented", summary="Nope")


class _IndexDouble:
    def upsert_bundle(self, bundle: object) -> None:
        return None

    def delete_source(self, source_id: str) -> None:
        return None

    def rebuild(self, bundles: object) -> IndexAudit:
        return IndexAudit()

    def get_artifact(self, artifact_id: str) -> object | None:
        return None

    def search_candidates(
        self,
        query: RetrievalQuery,
        *,
        lane: EpistemicLane,
        limit: int,
    ) -> tuple[object, ...]:
        return ()

    def audit(self) -> IndexAudit:
        return IndexAudit()

    def close(self) -> None:
        return None
