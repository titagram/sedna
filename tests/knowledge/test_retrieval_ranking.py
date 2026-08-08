"""Applicability and lane-ranking behavior for canonical retrieval candidates."""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from sedna.knowledge.retrieval import (
    AuthorizationScope,
    AuthorizationState,
    CurrentSituation,
    IndexCandidate,
    RetrievalQuery,
    SituationFacet,
    ValidatedTarget,
)
from sedna.knowledge.retrieval.ranking import LANE_THRESHOLDS, rank_candidates
from sedna.knowledge.schema import (
    ApplicabilityContext,
    ArtifactType,
    CaseAction,
    CaseState,
    CaseStep,
    ContextAssertion,
    ContextFacet,
    ContextRelation,
    DecisionRule,
    EpistemicAssessment,
    ExtractionMetadata,
    Generalizability,
    KnowledgeRole,
    ObservedOutcome,
    Origin,
    ReferenceArtifact,
    ServiceContext,
    SourceLocation,
    SourceRef,
    TypedContext,
    VerificationStatus,
)


def _source_ref(source_id: str) -> SourceRef:
    return SourceRef(
        source_id=source_id,
        path=f"raw_src/{source_id}.md",
        location=SourceLocation(start_line=1),
    )


def _assertion(
    value: str,
    *,
    source_id: str,
    relation: ContextRelation = ContextRelation.REQUIRED,
    confidence: float = 1.0,
) -> ContextAssertion:
    return ContextAssertion(
        value=value,
        relation=relation,
        origin=Origin.EXPLICIT,
        confidence=confidence,
        source_refs=(_source_ref(source_id),),
    )


def _applicability(
    *,
    source_id: str,
    os_family: str | None = None,
    os_relation: ContextRelation = ContextRelation.REQUIRED,
    os_version: str | None = None,
    services: tuple[tuple[str, str, ContextRelation], ...] = (),
    facets: tuple[tuple[str, str, str, ContextRelation], ...] = (),
) -> ApplicabilityContext:
    return ApplicabilityContext(
        typed_context=TypedContext(
            os_family=(
                _assertion(os_family, source_id=source_id, relation=os_relation)
                if os_family is not None
                else None
            ),
            os_version=(
                _assertion(os_version, source_id=source_id) if os_version is not None else None
            ),
            services=tuple(
                ServiceContext(
                    service_type=service_type,
                    identity=_assertion(
                        identity,
                        source_id=source_id,
                        relation=relation,
                    ),
                )
                for service_type, identity, relation in services
            ),
        ),
        facets=tuple(
            ContextFacet(
                namespace=namespace,
                key=key,
                assertion=_assertion(
                    value,
                    source_id=source_id,
                    relation=relation,
                ),
            )
            for namespace, key, value, relation in facets
        ),
    )


def _assessment(
    source_id: str,
    *,
    status: VerificationStatus = VerificationStatus.VERIFIED,
    outcome: ObservedOutcome = ObservedOutcome.INFORMATIONAL,
    group: str | None = None,
    freshness: str | None = None,
    support_count: int = 1,
    source_reliability: float = 0.9,
    extraction_confidence: float = 0.9,
    generalizability: Generalizability = Generalizability.HIGH,
) -> EpistemicAssessment:
    return EpistemicAssessment(
        source_reliability=source_reliability,
        extraction_confidence=extraction_confidence,
        generalizability=generalizability,
        context_specificity=0.2,
        verification_status=status,
        support_count=support_count,
        observed_outcome=outcome,
        freshness_observed_at=freshness,
        independence_group=group or source_id,
    )


def _extraction() -> ExtractionMetadata:
    return ExtractionMetadata(
        schema_version="2",
        parser_id="markdown",
        parser_version="1",
        extractor_id="semantic",
        extractor_version="1",
    )


def _reference(
    identifier: str,
    *,
    applicability: ApplicabilityContext | None = None,
    status: VerificationStatus = VerificationStatus.VERIFIED,
    group: str | None = None,
    freshness: str | None = None,
    support_count: int = 1,
    artifact_type: ArtifactType = ArtifactType.METHODOLOGY,
    source_reliability: float = 0.9,
    extraction_confidence: float = 0.9,
    generalizability: Generalizability = Generalizability.HIGH,
) -> ReferenceArtifact:
    source_id = f"source-{identifier}"
    return ReferenceArtifact(
        artifact_type=artifact_type,
        knowledge_role=KnowledgeRole.REFERENCE,
        artifact_id=identifier,
        subject="HTTP discovery",
        statement="Inspect the exposed HTTP service before choosing a strategy.",
        origin=Origin.EXPLICIT,
        applicability=applicability or _applicability(source_id=source_id),
        assessment=_assessment(
            source_id,
            status=status,
            group=group,
            freshness=freshness,
            support_count=support_count,
            source_reliability=source_reliability,
            extraction_confidence=extraction_confidence,
            generalizability=generalizability,
        ),
        source_refs=(_source_ref(source_id),),
        extraction=_extraction(),
    )


def _case_step(
    identifier: str,
    *,
    negative: bool = False,
    group: str | None = None,
    applicability: ApplicabilityContext | None = None,
    freshness: str | None = None,
) -> CaseStep:
    source_id = f"source-{identifier}"
    return CaseStep(
        artifact_type=ArtifactType.CASE_STEP,
        knowledge_role=(KnowledgeRole.NEGATIVE_CASE if negative else KnowledgeRole.CASE_STUDY),
        step_id=identifier,
        ordinal=1,
        state_before=CaseState(access="none"),
        observations=("HTTP service exposed",),
        hypotheses=(),
        selected_action=CaseAction(intent="inspect HTTP response behavior"),
        evidence=(),
        state_after=CaseState(access="none"),
        negative_evidence=("The response did not support the hypothesis",) if negative else (),
        case_specific_details=("Historical credential example: trainee:training-password",),
        origin=Origin.EXPLICIT,
        applicability=applicability or _applicability(source_id=source_id),
        assessment=_assessment(
            source_id,
            outcome=ObservedOutcome.FAILURE if negative else ObservedOutcome.SUCCESS,
            group=group,
            freshness=freshness,
        ),
        source_refs=(_source_ref(source_id),),
        extraction=_extraction(),
    )


def _guidance(
    identifier: str,
    *,
    applicability: ApplicabilityContext | None = None,
    status: VerificationStatus = VerificationStatus.VERIFIED,
) -> DecisionRule:
    source_id = f"source-{identifier}"
    return DecisionRule(
        artifact_type=ArtifactType.DECISION_RULE,
        knowledge_role=KnowledgeRole.REFERENCE,
        rule_id=identifier,
        trigger_observations=("HTTP service exposed",),
        rationale="The response can discriminate between service hypotheses.",
        action_intent="inspect HTTP response behavior",
        origin=Origin.EXPLICIT,
        applicability=applicability or _applicability(source_id=source_id),
        assessment=_assessment(source_id, status=status),
        source_refs=(_source_ref(source_id),),
        extraction=_extraction(),
    )


def _candidate(artifact, *, lexical: float = 0.9) -> IndexCandidate:
    identifier = (
        artifact.artifact_id
        if isinstance(artifact, ReferenceArtifact)
        else artifact.step_id
        if isinstance(artifact, CaseStep)
        else artifact.rule_id
    )
    return IndexCandidate(
        artifact_id=identifier,
        artifact=artifact,
        lexical_relevance=lexical,
        matched_terms=("http",),
        matched_fields=("statement",),
        matched_evidence=("http service",),
    )


def _query(
    *facts: SituationFacet,
    terms: Iterable[str] = ("http",),
    services: Iterable[str] = (),
) -> RetrievalQuery:
    target = ValidatedTarget.parse("192.168.0.1")
    return RetrievalQuery(
        situation=CurrentSituation(
            target=target,
            authorization=AuthorizationScope(
                state=AuthorizationState.AUTHORIZED,
                exact_targets=(target,),
            ),
            facts=facts,
            services=tuple(services),
        ),
        terms=tuple(terms),
    )


def _fact(
    namespace: str,
    key: str,
    value: str,
    *,
    confidence: float = 1.0,
) -> SituationFacet:
    return SituationFacet(
        namespace=namespace,
        key=key,
        value=value,
        confidence=confidence,
    )


def test_known_linux_hard_rejects_windows_required_even_with_maximum_lexical_score():
    artifact = _reference(
        "windows-only",
        applicability=_applicability(source_id="source-windows-only", os_family="windows"),
    )

    ranked = rank_candidates(
        _query(_fact("typed", "os_family", "linux")),
        (_candidate(artifact, lexical=1.0),),
    )

    assert ranked.references == ()
    assert [candidate.artifact_id for candidate in ranked.rejected_candidates] == ["windows-only"]
    assert ranked.rejected_candidates[0].rejection_reasons == (
        "required typed.os_family=windows conflicts with observed linux",
    )


def test_global_context_questions_exclude_candidates_already_known_incompatible():
    artifact = _reference(
        "windows-with-other-prerequisite",
        applicability=_applicability(
            source_id="source-windows-with-other-prerequisite",
            os_family="windows",
            facets=(("network", "exposure", "private", ContextRelation.REQUIRED),),
        ),
    )

    ranked = rank_candidates(
        _query(_fact("typed", "os_family", "linux")),
        (_candidate(artifact),),
    )

    assert ranked.missing_context_questions == ()
    assert ranked.rejected_candidates[0].missing_context == (
        "confirm current network.exposure (required value: private)",
    )


def test_low_confidence_platform_conflict_remains_conditional_instead_of_hard_rejected():
    artifact = _reference(
        "windows-conditional-low-confidence",
        applicability=_applicability(
            source_id="source-windows-conditional-low-confidence",
            os_family="windows",
        ),
    )

    ranked = rank_candidates(
        _query(_fact("typed", "os_family", "linux", confidence=0.2)),
        (_candidate(artifact, lexical=1.0),),
    )

    assert ranked.rejected_candidates == ()
    assert ranked.references[0].missing_context == (
        "confirm current typed.os_family (required value: windows)",
    )


def test_matching_explicitly_incompatible_facet_is_always_rejected():
    artifact = _reference(
        "waf-incompatible",
        applicability=_applicability(
            source_id="source-waf-incompatible",
            facets=(("web", "security_control", "waf", ContextRelation.INCOMPATIBLE),),
        ),
    )

    ranked = rank_candidates(
        _query(_fact("web", "security_control", "waf")),
        (_candidate(artifact, lexical=1.0),),
    )

    assert ranked.references == ()
    assert ranked.rejected_candidates[0].rejection_reasons == (
        "observed web.security_control=waf is explicitly incompatible",
    )


def test_unknown_required_os_is_penalized_and_returned_as_a_context_question():
    artifact = _reference(
        "windows-conditional",
        applicability=_applicability(source_id="source-windows-conditional", os_family="windows"),
    )

    unknown = rank_candidates(_query(), (_candidate(artifact),))
    matched = rank_candidates(
        _query(_fact("typed", "os_family", "windows")),
        (_candidate(artifact),),
    )

    assert unknown.references[0].score.unknown_condition_penalty > 0
    assert unknown.references[0].score.total < matched.references[0].score.total
    assert unknown.references[0].missing_context == (
        "confirm current typed.os_family (required value: windows)",
    )
    assert unknown.missing_context_questions == unknown.references[0].missing_context


def test_compatible_service_identity_contributes_a_matched_live_facet():
    artifact = _reference(
        "apache-reference",
        applicability=_applicability(
            source_id="source-apache-reference",
            services=(("http", "apache", ContextRelation.COMPATIBLE),),
        ),
    )
    apache = _fact("typed", "services.http", "apache")

    ranked = rank_candidates(_query(apache), (_candidate(artifact),))

    hit = ranked.references[0]
    assert hit.matched_facets == (apache,)
    assert hit.score.context_similarity > 0.5
    assert "matched typed.services.http=apache" in hit.qualification_reasons


def test_observed_service_type_improves_context_without_inventing_a_live_identity_facet():
    artifact = _reference(
        "http-reference",
        applicability=_applicability(
            source_id="source-http-reference",
            services=(("http", "apache", ContextRelation.COMPATIBLE),),
        ),
    )

    without_service = rank_candidates(_query(), (_candidate(artifact),))
    with_service = rank_candidates(
        _query(services=("http",)),
        (_candidate(artifact),),
    )

    assert with_service.references[0].score.context_similarity > (
        without_service.references[0].score.context_similarity
    )
    assert with_service.references[0].matched_facets == ()
    assert "matched observed service type http" in (
        with_service.references[0].qualification_reasons
    )


def test_verified_and_contested_evidence_are_both_preserved_but_not_equally_weighted():
    verified = _reference("verified", status=VerificationStatus.VERIFIED)
    contested = _reference("contested", status=VerificationStatus.CONTESTED)

    ranked = rank_candidates(
        _query(),
        (_candidate(contested, lexical=1.0), _candidate(verified, lexical=1.0)),
    )

    assert [hit.artifact_id for hit in ranked.references] == ["verified", "contested"]
    assert ranked.references[0].score.verification_confidence > (
        ranked.references[1].score.verification_confidence
    )
    assert "contested evidence retained with reduced confidence" in (
        ranked.references[1].qualification_reasons
    )


def test_source_extraction_and_generalizability_dimensions_remain_visible_in_quality_score():
    strong = _reference("strong-quality")
    weak = _reference(
        "weak-quality",
        source_reliability=0.2,
        extraction_confidence=0.3,
        generalizability=Generalizability.LOW,
    )

    ranked = rank_candidates(
        _query(),
        (_candidate(weak, lexical=1.0), _candidate(strong, lexical=1.0)),
    )
    scores = {hit.artifact_id: hit.score.verification_confidence for hit in ranked.references}

    assert scores["strong-quality"] > scores["weak-quality"]


def test_positive_and_negative_case_steps_remain_in_separate_lanes_and_hide_local_details():
    positive = _case_step("positive-step")
    negative = _case_step("negative-step", negative=True)

    ranked = rank_candidates(
        _query(),
        (_candidate(negative), _candidate(positive)),
    )

    assert [hit.artifact_id for hit in ranked.case_steps] == ["positive-step"]
    assert [hit.artifact_id for hit in ranked.negative_cases] == ["negative-step"]
    assert ranked.case_steps[0].artifact.case_specific_details == ()
    assert ranked.negative_cases[0].artifact.case_specific_details == ()


def test_equal_scores_use_stable_artifact_identity_order_regardless_of_input_order():
    first = _reference("a-reference")
    second = _reference("b-reference")

    forward = rank_candidates(_query(), (_candidate(first), _candidate(second)))
    reverse = rank_candidates(_query(), (_candidate(second), _candidate(first)))

    assert [hit.artifact_id for hit in forward.references] == ["a-reference", "b-reference"]
    assert reverse == forward


def test_lane_thresholds_are_applied_per_lane_and_report_below_threshold_candidates():
    reference = _reference(
        "weak-reference",
        applicability=_applicability(source_id="source-weak-reference", os_family="windows"),
        status=VerificationStatus.DEPRECATED,
    )
    guidance = _guidance(
        "weak-guidance",
        applicability=_applicability(source_id="source-weak-guidance", os_family="windows"),
        status=VerificationStatus.DEPRECATED,
    )

    ranked = rank_candidates(
        _query(),
        (_candidate(reference, lexical=0.0), _candidate(guidance, lexical=0.0)),
    )

    assert (
        LANE_THRESHOLDS[ranked.rejected_candidates[0].lane]
        != LANE_THRESHOLDS[ranked.rejected_candidates[1].lane]
    )
    for rejected in ranked.rejected_candidates:
        threshold = LANE_THRESHOLDS[rejected.lane]
        assert any(f"threshold {threshold:.2f}" in reason for reason in rejected.rejection_reasons)


def test_repeated_independence_groups_receive_less_diversity_than_independent_sources():
    copied_a = _reference("copied-a", group="copied-walkthrough")
    copied_b = _reference("copied-b", group="copied-walkthrough")
    independent = _reference("independent", group="independent-source")

    ranked = rank_candidates(
        _query(),
        tuple(_candidate(item) for item in (copied_a, copied_b, independent)),
    )
    scores = {hit.artifact_id: hit.score.source_diversity for hit in ranked.references}

    assert scores["independent"] > scores["copied-a"]
    assert scores["copied-a"] == scores["copied-b"]


def test_source_diversity_is_lane_local_and_cannot_couple_unlike_evidence_scores():
    reference = _reference("lane-local-reference", group="shared-source")
    negative = _case_step("lane-local-negative", negative=True, group="shared-source")

    reference_only = rank_candidates(_query(), (_candidate(reference),))
    mixed_lanes = rank_candidates(
        _query(),
        (_candidate(reference), _candidate(negative)),
    )

    assert mixed_lanes.references[0].score == reference_only.references[0].score


def test_freshness_reference_date_is_lane_local_and_cannot_couple_unlike_evidence_scores():
    reference = _reference(
        "lane-local-dated-reference",
        applicability=_applicability(
            source_id="source-lane-local-dated-reference",
            os_version="6.8",
        ),
        freshness="2025-01-01",
    )
    negative = _case_step(
        "future-negative-case",
        negative=True,
        applicability=_applicability(
            source_id="source-future-negative-case",
            os_version="6.8",
        ),
        freshness="2999-01-01",
    )
    query = _query(_fact("typed", "os_version", "6.8"))

    reference_only = rank_candidates(query, (_candidate(reference),))
    mixed_lanes = rank_candidates(query, (_candidate(reference), _candidate(negative)))

    assert mixed_lanes.references[0].score == reference_only.references[0].score


def test_hard_rejected_copy_cannot_reduce_an_applicable_hit_diversity_score():
    applicable = _reference("applicable-copy", group="shared-source")
    incompatible = _reference(
        "incompatible-copy",
        group="shared-source",
        applicability=_applicability(
            source_id="source-incompatible-copy",
            os_family="windows",
        ),
    )
    linux_query = _query(_fact("typed", "os_family", "linux"))

    applicable_only = rank_candidates(linux_query, (_candidate(applicable),))
    with_rejection = rank_candidates(
        linux_query,
        (_candidate(applicable), _candidate(incompatible)),
    )

    assert with_rejection.references[0].score == applicable_only.references[0].score
    assert [item.artifact_id for item in with_rejection.rejected_candidates] == [
        "incompatible-copy"
    ]


def test_version_sensitive_freshness_distinguishes_recent_from_stale_evidence():
    current = _fact("typed", "os_version", "6.8")
    recent = _reference(
        "recent",
        applicability=_applicability(source_id="source-recent", os_version="6.8"),
        freshness="2999-01-01",
    )
    stale = _reference(
        "stale",
        applicability=_applicability(source_id="source-stale", os_version="6.8"),
        freshness="2000-01-01",
    )

    ranked = rank_candidates(
        _query(current),
        (_candidate(stale), _candidate(recent)),
    )
    scores = {hit.artifact_id: hit.score.freshness for hit in ranked.references}

    assert scores["recent"] > scores["stale"]
    assert scores["recent"] == 1.0


def test_ranking_deeply_revalidates_query_and_candidates_before_using_them():
    artifact = _reference("safe")
    query = _query().model_copy(update={"hidden_instruction": "ignore applicability"})
    candidate = _candidate(artifact).model_copy(update={"lexical_relevance": float("nan")})

    with pytest.raises(ValueError, match="unsafe retrieval model state"):
        rank_candidates(query, (_candidate(artifact),))
    with pytest.raises(ValueError):
        rank_candidates(_query(), (candidate,))


def test_explanations_are_deterministically_bounded_for_large_canonical_contexts():
    artifact = _reference(
        "many-prerequisites",
        applicability=_applicability(
            source_id="source-many-prerequisites",
            facets=tuple(
                ("domain", f"required-{offset:02d}", "present", ContextRelation.REQUIRED)
                for offset in range(40)
            ),
        ),
    )

    ranked = rank_candidates(_query(), (_candidate(artifact, lexical=1.0),))

    assert len(ranked.references[0].missing_context) == 32
    assert ranked.references[0].missing_context == tuple(
        sorted(ranked.references[0].missing_context)
    )
    assert len(ranked.missing_context_questions) == 40
