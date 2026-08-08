"""Applicability and lane-ranking behavior for canonical retrieval candidates."""

from __future__ import annotations

import warnings
from collections.abc import Iterable

import pytest

import sedna.knowledge.retrieval.ranking as ranking_module
from sedna.knowledge.retrieval import (
    AuthorizationScope,
    AuthorizationState,
    CurrentSituation,
    IndexCandidate,
    RetrievalQuery,
    RetrievalResult,
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
    observation_date: str | None = None,
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
            observation_date=(
                _assertion(
                    observation_date,
                    source_id=source_id,
                    relation=ContextRelation.OBSERVED,
                )
                if observation_date is not None
                else None
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


def test_contradictory_high_confidence_singleton_facts_cannot_rescue_an_exact_requirement():
    artifact = _reference(
        "windows-requires-resolved-platform",
        applicability=_applicability(
            source_id="source-windows-requires-resolved-platform",
            os_family="windows",
        ),
    )

    ranked = rank_candidates(
        _query(
            _fact("typed", "os_family", "linux"),
            _fact("typed", "os_family", "windows"),
        ),
        (_candidate(artifact, lexical=1.0),),
    )

    assert ranked.rejected_candidates == ()
    hit = ranked.references[0]
    assert hit.matched_facets == ()
    assert hit.score.unknown_condition_penalty > 0
    assert hit.missing_context == (
        "resolve contradictory current typed.os_family values: linux, windows",
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


def test_below_threshold_rejection_preserves_mandatory_provenance_bounding_reason():
    artifact = _reference(
        "weak-many-sources",
        applicability=_applicability(
            source_id="source-weak-many-sources",
            os_family="windows",
        ),
        status=VerificationStatus.DEPRECATED,
    )
    source_refs = tuple(_source_ref(f"weak-source-{offset:02d}") for offset in range(65))
    artifact = ReferenceArtifact.model_validate(
        {**artifact.model_dump(mode="json"), "source_refs": source_refs}
    )

    ranked = rank_candidates(_query(), (_candidate(artifact, lexical=0.0),))

    rejected = ranked.rejected_candidates[0]
    assert any("below reference threshold" in reason for reason in rejected.rejection_reasons)
    assert "provenance bounded: showing 64 of 65 unique source references" in (
        rejected.rejection_reasons
    )


def test_mandatory_bounding_reason_reserves_space_when_hard_rejections_exceed_limit():
    incompatible_facets = tuple(
        ("domain", f"control-{offset:02d}", "present", ContextRelation.INCOMPATIBLE)
        for offset in range(40)
    )
    artifact = _reference(
        "many-rejections-and-sources",
        applicability=_applicability(
            source_id="source-many-rejections-and-sources",
            facets=incompatible_facets,
        ),
    )
    source_refs = tuple(_source_ref(f"bounded-source-{offset:02d}") for offset in range(65))
    artifact = ReferenceArtifact.model_validate(
        {**artifact.model_dump(mode="json"), "source_refs": source_refs}
    )
    facts = tuple(_fact("domain", f"control-{offset:02d}", "present") for offset in range(40))

    ranked = rank_candidates(_query(*facts), (_candidate(artifact, lexical=1.0),))

    reasons = ranked.rejected_candidates[0].rejection_reasons
    assert len(reasons) == 32
    assert "provenance bounded: showing 64 of 65 unique source references" in reasons
    assert sum("explicitly incompatible" in reason for reason in reasons) == 31


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


def test_lane_limit_selection_round_robins_groups_before_repeating_copied_sources():
    copied = tuple(
        _reference(f"copied-{offset}", group="byte-identical-source") for offset in range(5)
    )
    independent_a = _reference("independent-a", group="independent-a")
    independent_b = _reference("independent-b", group="independent-b")

    ranked = rank_candidates(
        _query(),
        (
            *(_candidate(item, lexical=1.0) for item in reversed(copied)),
            _candidate(independent_b, lexical=0.8),
            _candidate(independent_a, lexical=0.9),
        ),
    )

    selected = ranking_module.select_diversified_hits(ranked.references, limit=3)

    assert [hit.artifact_id for hit in selected] == [
        "copied-0",
        "independent-a",
        "independent-b",
    ]
    assert not {"copied-1", "copied-2", "copied-3", "copied-4"} & {
        hit.artifact_id for hit in selected
    }


def test_diversified_top_k_selection_is_score_sorted_and_composes_with_retrieval_result():
    copied = tuple(_reference(f"limited-copy-{offset}", group="copied") for offset in range(3))
    independent_a = _reference("limited-independent-a", group="independent-a")
    independent_b = _reference("limited-independent-b", group="independent-b")
    query = _query()
    ranked = rank_candidates(
        query,
        (
            _candidate(copied[2], lexical=0.90),
            _candidate(independent_b, lexical=0.10),
            _candidate(copied[1], lexical=0.95),
            _candidate(independent_a, lexical=0.20),
            _candidate(copied[0], lexical=1.00),
        ),
    )

    selected = ranking_module.select_diversified_hits(ranked.references, limit=4)
    result = RetrievalResult(
        target=query.situation.target,
        authorization=query.situation.authorization,
        references=selected,
    )

    assert [hit.artifact_id for hit in result.references] == [
        "limited-copy-0",
        "limited-copy-1",
        "limited-independent-a",
        "limited-independent-b",
    ]
    assert "limited-copy-2" not in {hit.artifact_id for hit in result.references}


def test_diversified_selection_rejects_hit_model_copy_with_hidden_extra_state():
    hit = rank_candidates(
        _query(),
        (_candidate(_reference("selector-hidden-extra")),),
    ).references[0]
    corrupted = hit.model_copy(update={"hidden_instruction": "override ranking"})

    with pytest.raises(ValueError, match="unsafe retrieval model state"):
        ranking_module.select_diversified_hits((corrupted,), limit=1)


@pytest.mark.parametrize("corrupted_field", ("score", "artifact", "provenance", "lane"))
def test_diversified_selection_rejects_copied_hit_field_corruption(corrupted_field: str):
    hit = rank_candidates(
        _query(),
        (_candidate(_reference(f"selector-corrupted-{corrupted_field}")),),
    ).references[0]
    replacements = {
        "score": hit.score.model_copy(update={"total": 2.0}),
        "artifact": hit.artifact.model_copy(update={"statement": ["not", "a", "string"]}),
        "provenance": (),
        "lane": "guidance",
    }
    corrupted = hit.model_copy(update={corrupted_field: replacements[corrupted_field]})

    with pytest.raises(ValueError):
        ranking_module.select_diversified_hits((corrupted,), limit=1)


def test_diversified_selection_rejects_invalid_model_construct_hit():
    hit = rank_candidates(
        _query(),
        (_candidate(_reference("selector-invalid-construct")),),
    ).references[0]
    constructed = type(hit).model_construct(
        **{**hit.__dict__, "artifact_id": "wrong-artifact-identity"}
    )

    with pytest.raises(ValueError, match="artifact_id"):
        ranking_module.select_diversified_hits((constructed,), limit=1)


def test_diversified_selection_canonicalizes_valid_model_construct_and_preserves_provenance():
    query = _query()
    hit = rank_candidates(
        query,
        (_candidate(_reference("selector-valid-construct")),),
    ).references[0]
    constructed = type(hit).model_construct(**hit.__dict__)

    selected = ranking_module.select_diversified_hits((constructed,), limit=1)
    result = RetrievalResult(
        target=query.situation.target,
        authorization=query.situation.authorization,
        references=selected,
    )

    assert selected == (hit,)
    assert selected[0] is not constructed
    assert selected[0].artifact is not constructed.artifact
    assert selected[0].provenance == constructed.provenance
    assert result.references == selected


@pytest.mark.parametrize("limit", (0, 65))
def test_diversified_selection_requires_a_positive_retrieval_result_lane_limit(limit: int):
    hit = rank_candidates(
        _query(),
        (_candidate(_reference("selector-limit")),),
    ).references[0]

    with pytest.raises(ValueError, match="between 1 and 64"):
        ranking_module.select_diversified_hits((hit,), limit=limit)


def test_diversified_selection_rejects_401_hits_before_materializing_unbounded_input():
    hit = rank_candidates(
        _query(),
        (_candidate(_reference("selector-401")),),
    ).references[0]

    with pytest.raises(ValueError, match="exceeds 400-hit input budget"):
        ranking_module.select_diversified_hits((hit for _ in range(401)), limit=1)


def test_diversified_selection_does_not_overconsume_a_guarded_5000_hit_generator():
    hit = rank_candidates(
        _query(),
        (_candidate(_reference("selector-guarded-5000")),),
    ).references[0]
    consumed = 0

    def guarded_hits():
        nonlocal consumed
        for offset in range(5000):
            if offset > 400:
                raise AssertionError("selector consumed beyond its max-plus-one boundary")
            consumed += 1
            yield hit

    with pytest.raises(ValueError, match="exceeds 400-hit input budget"):
        ranking_module.select_diversified_hits(guarded_hits(), limit=1)
    assert consumed == 401


def test_diversified_selection_accepts_a_finite_generator_and_rejects_duplicate_hits():
    first = _reference("selector-generator-first", group="selector-generator-first")
    second = _reference("selector-generator-second", group="selector-generator-second")
    ranked = rank_candidates(
        _query(),
        (_candidate(first), _candidate(second)),
    )

    selected = ranking_module.select_diversified_hits(
        (hit for hit in ranked.references),
        limit=2,
    )

    assert selected == ranked.references
    with pytest.raises(ValueError, match="unique artifact identities"):
        ranking_module.select_diversified_hits((selected[0], selected[0]), limit=1)


def test_diversified_selection_never_returns_more_than_64_hits():
    artifacts = tuple(_reference(f"selector-output-bound-{offset:03d}") for offset in range(100))
    query = _query()
    ranked = rank_candidates(
        query,
        tuple(_candidate(artifact) for artifact in artifacts),
    )

    selected = ranking_module.select_diversified_hits(
        (hit for hit in ranked.references),
        limit=64,
    )

    result = RetrievalResult(
        target=query.situation.target,
        authorization=query.situation.authorization,
        references=selected,
    )

    assert len(result.references) == 64


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


def test_hard_rejected_future_candidate_cannot_change_same_lane_freshness_score():
    current = _fact("typed", "os_version", "6.8")
    applicable = _reference(
        "dated-applicable-reference",
        applicability=_applicability(
            source_id="source-dated-applicable-reference",
            os_version="6.8",
        ),
        freshness="2025-01-01",
    )
    unrelated_future = _reference(
        "unrelated-future-reference",
        applicability=_applicability(
            source_id="source-unrelated-future-reference",
            os_version="9.9",
        ),
        freshness="2999-01-01",
    )

    alone = rank_candidates(_query(current), (_candidate(applicable),))
    with_unrelated = rank_candidates(
        _query(current),
        (_candidate(applicable), _candidate(unrelated_future)),
    )

    assert with_unrelated.references[0].score == alone.references[0].score
    assert [item.artifact_id for item in with_unrelated.rejected_candidates] == [
        "unrelated-future-reference"
    ]


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
    as_of = _fact("typed", "observation_date", "2026-01-01")
    recent = _reference(
        "recent",
        applicability=_applicability(source_id="source-recent", os_version="6.8"),
        freshness="2025-01-01",
    )
    stale = _reference(
        "stale",
        applicability=_applicability(source_id="source-stale", os_version="6.8"),
        freshness="2000-01-01",
    )

    ranked = rank_candidates(
        _query(current, as_of),
        (_candidate(stale), _candidate(recent)),
    )
    scores = {hit.artifact_id: hit.score.freshness for hit in ranked.references}

    assert scores["recent"] > scores["stale"]
    assert scores["recent"] == 1.0


def test_case_and_guidance_use_canonical_typed_observation_date_without_peer_clock():
    applicability = _applicability(
        source_id="source-typed-observation-date",
        os_version="6.8",
        observation_date="2025-01-01",
    )
    case = _case_step("dated-case", applicability=applicability)
    guidance = _guidance("dated-guidance", applicability=applicability)

    ranked = rank_candidates(
        _query(_fact("typed", "os_version", "6.8")),
        (_candidate(case), _candidate(guidance)),
    )

    assert ranked.case_steps[0].score.freshness == 0.6
    assert ranked.decision_guidance[0].score.freshness == 0.6


def test_contradictory_query_observation_dates_do_not_become_an_arbitrary_freshness_clock():
    artifact = _reference(
        "dated-reference-with-conflicting-query-clock",
        applicability=_applicability(
            source_id="source-dated-reference-with-conflicting-query-clock",
            os_version="6.8",
            observation_date="2025-01-01",
        ),
    )

    ranked = rank_candidates(
        _query(
            _fact("typed", "os_version", "6.8"),
            _fact("typed", "observation_date", "2026-01-01"),
            _fact("typed", "observation_date", "2999-01-01"),
        ),
        (_candidate(artifact),),
    )

    hit = ranked.references[0]
    assert hit.score.freshness == 0.6
    assert hit.missing_context == (
        "resolve contradictory current typed.observation_date values: 2026-01-01, 2999-01-01",
    )


@pytest.mark.parametrize(
    ("raw_dates", "rendered_dates"),
    (
        (("2026-01-01", "not-a-date"), "2026-01-01, not-a-date"),
        (
            ("2026-01-01", "2026-01-01T00:00:00"),
            "2026-01-01, 2026-01-01t00:00:00",
        ),
    ),
)
def test_distinct_raw_singleton_clock_facts_remain_unresolved_before_date_parsing(
    raw_dates: tuple[str, str],
    rendered_dates: str,
):
    artifact = _reference(
        "dated-reference-with-raw-clock-conflict",
        applicability=_applicability(
            source_id="source-dated-reference-with-raw-clock-conflict",
            os_version="6.8",
            observation_date="2025-01-01",
        ),
    )

    ranked = rank_candidates(
        _query(
            _fact("typed", "os_version", "6.8"),
            *(_fact("typed", "observation_date", value) for value in raw_dates),
        ),
        (_candidate(artifact),),
    )

    hit = ranked.references[0]
    assert hit.score.freshness == 0.6
    assert hit.missing_context == (
        "resolve contradictory current typed.observation_date values: " + rendered_dates,
    )


def test_deprecated_non_versioned_evidence_has_reduced_freshness_and_aligned_explanation():
    deprecated = _reference("deprecated-non-versioned", status=VerificationStatus.DEPRECATED)

    ranked = rank_candidates(
        _query(),
        (_candidate(deprecated, lexical=1.0),),
    )

    hit = ranked.references[0]
    assert hit.score.freshness < 1.0
    assert "deprecated evidence retained with reduced freshness" in hit.qualification_reasons


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


def test_valid_candidate_with_65_sources_returns_bounded_exact_provenance_instead_of_raising():
    artifact = _reference("many-sources")
    source_refs = tuple(_source_ref(f"source-{offset:02d}") for offset in range(65))
    artifact = ReferenceArtifact.model_validate(
        {**artifact.model_dump(mode="json"), "source_refs": source_refs}
    )

    ranked = rank_candidates(_query(), (_candidate(artifact),))

    hit = ranked.references[0]
    assert hit.provenance == source_refs[:64]
    assert hit.artifact.source_refs == source_refs[:64]
    assert "provenance bounded: showing 64 of 65 unique source references" in (
        hit.qualification_reasons
    )


def test_provenance_deduplication_scan_has_an_explicit_deterministic_work_bound():
    artifact = _reference("many-more-sources")
    source_refs = tuple(_source_ref(f"source-{offset:03d}") for offset in range(300))
    artifact = ReferenceArtifact.model_validate(
        {**artifact.model_dump(mode="json"), "source_refs": source_refs}
    )

    ranked = rank_candidates(_query(), (_candidate(artifact),))

    hit = ranked.references[0]
    assert hit.provenance == source_refs[:64]
    assert (
        "provenance bounded: showing 64 of at least 256 unique source references; "
        "44 canonical entries omitted from deduplication scan" in hit.qualification_reasons
    )


def test_duplicate_provenance_is_deduplicated_with_explicit_canonical_entry_count():
    artifact = _reference("duplicate-sources")
    first = _source_ref("source-first")
    second = _source_ref("source-second")
    artifact = ReferenceArtifact.model_validate(
        {
            **artifact.model_dump(mode="json"),
            "source_refs": (first, first, second),
        }
    )

    ranked = rank_candidates(_query(), (_candidate(artifact),))

    hit = ranked.references[0]
    assert hit.provenance == (first, second)
    assert (
        "provenance deduplicated: showing 2 unique source references from 3 canonical entries"
        in (hit.qualification_reasons)
    )


def test_long_required_facet_is_digest_truncated_in_bounded_output_and_questions():
    long_value = "very-long-context-" * 180
    artifact = _reference(
        "long-required-facet",
        applicability=_applicability(
            source_id="source-long-required-facet",
            facets=(("domain", "long-condition", long_value, ContextRelation.REQUIRED),),
        ),
    )

    ranked = rank_candidates(_query(), (_candidate(artifact, lexical=1.0),))

    hit = ranked.references[0]
    question = hit.missing_context[0]
    assert len(question) <= 2048
    assert "[truncated sha256:" in question
    assert len(hit.artifact.applicability.facets[0].assertion.value) <= 2048
    assert "artifact retrieval view compacted to bounded output" in hit.qualification_reasons


def test_canonical_facet_work_budget_rejects_candidate_without_traversal_failure():
    artifact = _reference(
        "too-many-ranking-facets",
        applicability=_applicability(
            source_id="source-too-many-ranking-facets",
            facets=tuple(
                ("domain", f"condition-{offset:03d}", "present", ContextRelation.REQUIRED)
                for offset in range(257)
            ),
        ),
    )

    ranked = rank_candidates(_query(), (_candidate(artifact),))

    assert ranked.references == ()
    rejected = ranked.rejected_candidates[0]
    assert "candidate applicability exceeds 256-facet ranking budget" in (
        rejected.rejection_reasons
    )
    assert "artifact retrieval view sequences bounded to 256 items" in (rejected.rejection_reasons)
    assert len(rejected.artifact.applicability.facets) == 256


def test_canonical_collection_budget_rejects_without_serializing_original_candidate(
    monkeypatch: pytest.MonkeyPatch,
):
    artifact = _reference("oversized-canonical-collection")
    artifact = ReferenceArtifact.model_validate(
        {
            **artifact.model_dump(mode="json"),
            "applicable_situations": tuple(f"situation-{offset}" for offset in range(5000)),
        }
    )
    candidate = _candidate(artifact)
    original_candidate_dump = IndexCandidate.model_dump

    def guarded_candidate_dump(self, *args, **kwargs):
        if self is candidate:
            raise AssertionError("oversized collection candidate was fully serialized")
        return original_candidate_dump(self, *args, **kwargs)

    monkeypatch.setattr(IndexCandidate, "model_dump", guarded_candidate_dump)

    ranked = rank_candidates(_query(), (candidate,))

    rejected = ranked.rejected_candidates[0]
    assert "candidate canonical exceeds 4096-item collection preflight budget" in (
        rejected.rejection_reasons
    )
    assert len(rejected.artifact.applicable_situations) == 256


def test_canonical_field_budget_rejects_without_serializing_original_candidate(
    monkeypatch: pytest.MonkeyPatch,
):
    artifact = _reference("oversized-canonical-fields")
    source_refs = tuple(_source_ref(f"field-source-{offset:04d}") for offset in range(1100))
    artifact = ReferenceArtifact.model_validate(
        {**artifact.model_dump(mode="json"), "source_refs": source_refs}
    )
    candidate = _candidate(artifact)
    original_candidate_dump = IndexCandidate.model_dump

    def guarded_candidate_dump(self, *args, **kwargs):
        if self is candidate:
            raise AssertionError("oversized field candidate was fully serialized")
        return original_candidate_dump(self, *args, **kwargs)

    monkeypatch.setattr(IndexCandidate, "model_dump", guarded_candidate_dump)

    ranked = rank_candidates(_query(), (candidate,))

    rejected = ranked.rejected_candidates[0]
    assert "candidate canonical exceeds 8192-field preflight budget" in (rejected.rejection_reasons)
    assert len(rejected.provenance) == 64


def test_canonical_byte_budget_rejects_before_dumping_or_deep_copying_original_candidate(
    monkeypatch: pytest.MonkeyPatch,
):
    artifact = _reference("oversized-canonical-payload")
    artifact = ReferenceArtifact.model_validate(
        {**artifact.model_dump(mode="json"), "statement": "A" * 270_000}
    )
    candidate = _candidate(artifact)
    original_candidate_dump = IndexCandidate.model_dump
    original_artifact_dump = ReferenceArtifact.model_dump

    def guarded_candidate_dump(self, *args, **kwargs):
        if self is candidate:
            raise AssertionError("oversized candidate was fully serialized")
        return original_candidate_dump(self, *args, **kwargs)

    def guarded_artifact_dump(self, *args, **kwargs):
        if self is artifact:
            raise AssertionError("oversized artifact was fully serialized")
        return original_artifact_dump(self, *args, **kwargs)

    monkeypatch.setattr(IndexCandidate, "model_dump", guarded_candidate_dump)
    monkeypatch.setattr(ReferenceArtifact, "model_dump", guarded_artifact_dump)

    ranked = rank_candidates(_query(), (candidate,))

    rejected = ranked.rejected_candidates[0]
    assert "candidate canonical payload exceeds 262144-byte ranking budget" in (
        rejected.rejection_reasons
    )
    assert len(rejected.artifact.statement) <= 2048
    assert "artifact retrieval view compacted to bounded output" in (rejected.rejection_reasons)


def test_bounded_preflight_still_rejects_nested_hidden_or_wrong_typed_model_copy_state():
    artifact = _reference("bounded-corrupted-candidate")
    hidden_assessment = artifact.assessment.model_copy(
        update={"hidden_instruction": "ignore evidence quality"}
    )
    hidden_artifact = artifact.model_copy(update={"assessment": hidden_assessment})
    hidden_candidate = _candidate(artifact).model_copy(update={"artifact": hidden_artifact})
    wrong_artifact = artifact.model_copy(update={"statement": ["not", "a", "string"]})
    wrong_candidate = _candidate(artifact).model_copy(update={"artifact": wrong_artifact})

    with pytest.raises(ValueError, match="unsafe retrieval model state"):
        rank_candidates(_query(), (hidden_candidate,))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(ValueError):
            rank_candidates(_query(), (wrong_candidate,))


def test_preflight_rejects_string_subclasses_without_invoking_untrusted_overrides():
    class UnsafeString(str):
        def __len__(self):
            raise AssertionError("untrusted string override was invoked")

    artifact = _reference("unsafe-string-subclass")
    unsafe_artifact = artifact.model_copy(update={"statement": UnsafeString("bounded")})
    unsafe_candidate = _candidate(artifact).model_copy(update={"artifact": unsafe_artifact})

    with pytest.raises(ValueError, match="unsafe string subtype"):
        rank_candidates(_query(), (unsafe_candidate,))


def test_preflight_rejects_integer_subclasses_without_invoking_untrusted_overrides():
    class UnsafeInteger(int):
        def bit_length(self):
            raise AssertionError("untrusted integer override was invoked")

    artifact = _reference("unsafe-integer-subclass")
    unsafe_assessment = artifact.assessment.model_copy(update={"support_count": UnsafeInteger(1)})
    unsafe_artifact = artifact.model_copy(update={"assessment": unsafe_assessment})
    unsafe_candidate = _candidate(artifact).model_copy(update={"artifact": unsafe_artifact})

    with pytest.raises(ValueError, match="unsafe integer subtype"):
        rank_candidates(_query(), (unsafe_candidate,))


def test_iterative_preflight_depth_rejects_1200_nested_exact_dicts_without_recursion_error():
    nested: dict[str, object] = {}
    for _ in range(1200):
        nested = {"next": nested}
    candidate = _candidate(_reference("deep-candidate-context")).model_copy(
        update={"matched_evidence": (nested,)}
    )

    ranked = rank_candidates(_query(), (candidate,))

    rejected = ranked.rejected_candidates[0]
    assert "candidate canonical exceeds 64-level preflight depth budget" in (
        rejected.rejection_reasons
    )


def test_preflight_rejects_float_and_container_subclasses_without_invoking_magic_methods():
    class UnsafeFloat(float):
        def __float__(self):
            raise AssertionError("untrusted float override was invoked")

    class UnsafeList(list):
        def __len__(self):
            raise AssertionError("untrusted list override was invoked")

    artifact = _reference("unsafe-float-and-list-subclasses")
    unsafe_assessment = artifact.assessment.model_copy(
        update={"source_reliability": UnsafeFloat(0.9)}
    )
    unsafe_artifact = artifact.model_copy(update={"assessment": unsafe_assessment})
    unsafe_float_candidate = _candidate(artifact).model_copy(update={"artifact": unsafe_artifact})
    unsafe_list_candidate = _candidate(artifact).model_copy(
        update={"matched_evidence": UnsafeList(["bounded"])}
    )

    with pytest.raises(ValueError, match="unsafe float subtype"):
        rank_candidates(_query(), (unsafe_float_candidate,))
    with pytest.raises(ValueError, match="unsafe container subtype"):
        rank_candidates(_query(), (unsafe_list_candidate,))


def test_iterative_preflight_rejects_recursive_exact_dict_deterministically():
    recursive: dict[str, object] = {}
    recursive["self"] = recursive
    candidate = _candidate(_reference("recursive-candidate-context")).model_copy(
        update={"matched_evidence": (recursive,)}
    )

    with pytest.raises(ValueError, match="contains a recursive value"):
        rank_candidates(_query(), (candidate,))
