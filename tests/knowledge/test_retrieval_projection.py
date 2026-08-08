"""Tests for the canonical-to-retrieval projection boundary."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from sedna.knowledge.retrieval.projection import project_semantic_bundle
from sedna.knowledge.schema import (
    ApplicabilityContext,
    ArtifactType,
    CaseAction,
    CaseEvidence,
    CaseHypothesis,
    CaseState,
    CaseStep,
    ContextAssertion,
    ContextFacet,
    ContextRelation,
    DecisionRule,
    EpistemicAssessment,
    ExtractionMetadata,
    Generalizability,
    KnowledgeCase,
    KnowledgeRole,
    ObservedOutcome,
    Origin,
    ReferenceArtifact,
    SemanticCompilationManifest,
    SemanticKnowledgeBundle,
    SourceLocation,
    SourceQuality,
    SourceRef,
    TypedContext,
    VerificationStatus,
)


def _source_ref(*, source_id: str = "source-retrieval", line: int = 10) -> SourceRef:
    return SourceRef(
        source_id=source_id,
        path="raw_src/reference.md",
        location=SourceLocation(start_line=line, end_line=line + 1, section="Web"),
    )


def _assertion(
    value: str, *, relation: ContextRelation = ContextRelation.REQUIRED
) -> ContextAssertion:
    return ContextAssertion(
        value=value,
        relation=relation,
        origin=Origin.EXPLICIT,
        confidence=0.8,
        source_refs=(_source_ref(line=20),),
    )


def _applicability() -> ApplicabilityContext:
    return ApplicabilityContext(
        typed_context=TypedContext(
            os_family=_assertion("linux"),
            services=(),
            privileges=(_assertion("www-data", relation=ContextRelation.COMPATIBLE),),
            security_controls=(_assertion("waf", relation=ContextRelation.INCOMPATIBLE),),
        ),
        facets=(
            ContextFacet(
                namespace="network",
                key="exposure",
                assertion=_assertion("private"),
            ),
        ),
    )


def _assessment(*, outcome: ObservedOutcome = ObservedOutcome.INFORMATIONAL) -> EpistemicAssessment:
    return EpistemicAssessment(
        source_reliability=0.9,
        extraction_confidence=0.8,
        generalizability=Generalizability.MEDIUM,
        context_specificity=0.4,
        verification_status=VerificationStatus.VERIFIED,
        observed_outcome=outcome,
        freshness_observed_at="2026-08-01",
        independence_group="independent-source-a",
    )


def _extraction() -> ExtractionMetadata:
    return ExtractionMetadata(
        schema_version="2",
        parser_id="markdown",
        parser_version="1",
        extractor_id="semantic",
        extractor_version="1",
    )


def _step(*, identifier: str, negative: bool = False) -> CaseStep:
    role = KnowledgeRole.NEGATIVE_CASE if negative else KnowledgeRole.CASE_STUDY
    return CaseStep(
        artifact_type=ArtifactType.CASE_STEP,
        knowledge_role=role,
        step_id=identifier,
        ordinal=1,
        state_before=CaseState(
            access="before-access-token",
            environment=("before-environment-token",),
            privileges=("before-privilege-token",),
        ),
        observations=("step-observation-token",),
        hypotheses=(CaseHypothesis(statement="step-hypothesis-token", origin=Origin.EXPLICIT),),
        selected_action=CaseAction(
            intent="step-action-token", capability_ref="step-capability-token"
        ),
        expected_information_gain="step-info-gain-token",
        evidence=(
            CaseEvidence(
                summary="step-evidence-token",
                origin=Origin.EXPLICIT,
                category="step-evidence-category-token",
            ),
        ),
        state_after=CaseState(
            access="after-access-token",
            environment=("after-environment-token",),
            privileges=("after-privilege-token",),
        ),
        negative_evidence=("step-negative-evidence-token",) if negative else (),
        transfer_conditions=("step-transfer-condition-token",),
        case_specific_details=("Historical credential example only: trainee:training-password",),
        origin=Origin.EXPLICIT,
        applicability=_applicability(),
        assessment=_assessment(
            outcome=ObservedOutcome.FAILURE if negative else ObservedOutcome.SUCCESS
        ),
        source_refs=(_source_ref(),),
        extraction=_extraction(),
    )


def _bundle() -> SemanticKnowledgeBundle:
    positive_step = _step(identifier="case-positive-step")
    negative_step = _step(identifier="case-negative-step", negative=True)
    reference = ReferenceArtifact(
        artifact_type=ArtifactType.METHODOLOGY,
        knowledge_role=KnowledgeRole.REFERENCE,
        artifact_id="reference-http",
        subject="reference-subject-token",
        statement="reference-statement-token",
        applicable_situations=("reference-situation-token",),
        prerequisites=("reference-prerequisite-token",),
        action_intent="reference-action-token",
        expected_information_gain="reference-info-gain-token",
        expected_evidence=("reference-expected-evidence-token",),
        evidence_interpretation="reference-interpretation-token",
        success_implications=("reference-success-token",),
        failure_implications=("reference-failure-token",),
        stop_implications=("reference-stop-token",),
        exceptions=("reference-exception-token",),
        warnings=("reference-warning-token",),
        capability_refs=("reference-capability-token",),
        origin=Origin.EXPLICIT,
        applicability=_applicability(),
        assessment=_assessment(),
        source_refs=(_source_ref(),),
        extraction=_extraction(),
        observed_at="2026-08-02",
    )
    positive_case = KnowledgeCase(
        artifact_type=ArtifactType.CASE,
        knowledge_role=KnowledgeRole.CASE_STUDY,
        case_id="case-positive",
        title="positive-case-title-token",
        starting_access="positive-case-access-token",
        steps=(positive_step,),
        outcome="positive-case-outcome-token",
        source_quality=SourceQuality.COMPLETE,
        difficulty="positive-case-difficulty-token",
        transferable_properties=("positive-case-transferable-token",),
        non_transferable_properties=("positive-case-nontransferable-token",),
        origin=Origin.EXPLICIT,
        applicability=_applicability(),
        assessment=_assessment(outcome=ObservedOutcome.SUCCESS),
        source_refs=(_source_ref(),),
        extraction=_extraction(),
    )
    negative_case = KnowledgeCase(
        artifact_type=ArtifactType.CASE,
        knowledge_role=KnowledgeRole.NEGATIVE_CASE,
        case_id="case-negative",
        title="negative-case-title-token",
        starting_access="negative-case-access-token",
        steps=(negative_step,),
        outcome="negative-case-outcome-token",
        source_quality=SourceQuality.COMPLETE,
        difficulty="negative-case-difficulty-token",
        transferable_properties=("negative-case-transferable-token",),
        non_transferable_properties=("negative-case-nontransferable-token",),
        origin=Origin.EXPLICIT,
        applicability=_applicability(),
        assessment=_assessment(outcome=ObservedOutcome.FAILURE),
        source_refs=(_source_ref(),),
        extraction=_extraction(),
    )
    rule = DecisionRule(
        artifact_type=ArtifactType.DECISION_RULE,
        knowledge_role=KnowledgeRole.REFERENCE,
        rule_id="rule-http",
        trigger_observations=("rule-trigger-token",),
        rationale="rule-rationale-token",
        action_intent="rule-action-token",
        prerequisites=("rule-prerequisite-token",),
        expected_evidence=("rule-expected-evidence-token",),
        success_transitions=("rule-success-transition-token",),
        failure_transitions=("rule-failure-transition-token",),
        stop_conditions=("rule-stop-condition-token",),
        exceptions=("rule-exception-token",),
        alternative_hypotheses=("rule-alternative-token",),
        capability_refs=("rule-capability-token",),
        origin=Origin.EXPLICIT,
        applicability=_applicability(),
        assessment=_assessment(),
        source_refs=(_source_ref(),),
        contradicting_source_refs=(_source_ref(line=40),),
        extraction=_extraction(),
    )
    identifiers = (
        "case-negative",
        "case-negative-step",
        "case-positive",
        "case-positive-step",
        "reference-http",
        "rule-http",
    )
    manifest = SemanticCompilationManifest(
        source_id="source-retrieval",
        source_sha256="a" * 64,
        foundation_schema_version="1",
        foundation_parser_id="markdown",
        foundation_parser_version="1",
        compiler_version="1",
        extractor_prompt_version="extract",
        critic_prompt_version="critic",
        repair_prompt_version="repair",
        extractor_model_id="model",
        critic_model_id="model",
        disposition="verified",
        repair_count=0,
        emitted_artifact_ids=identifiers,
        started_at=datetime(2026, 8, 8, tzinfo=UTC),
        completed_at=datetime(2026, 8, 8, 0, 1, tzinfo=UTC),
    )
    return SemanticKnowledgeBundle(
        schema_version="2",
        source_id="source-retrieval",
        source_sha256="a" * 64,
        compilation_manifest=manifest,
        references=(reference,),
        cases=(negative_case, positive_case),
        guidance=(rule,),
    )


def test_projects_references_cases_steps_negative_evidence_and_guidance_deterministically():
    bundle = _bundle()

    projection = project_semantic_bundle(bundle)

    assert [artifact.artifact_id for artifact in projection] == [
        "case-negative",
        "case-negative-step",
        "case-positive",
        "case-positive-step",
        "reference-http",
        "rule-http",
    ]
    by_id = {artifact.artifact_id: artifact for artifact in projection}
    assert by_id["case-positive"].artifact_type == ArtifactType.CASE
    assert by_id["case-positive"].knowledge_role == KnowledgeRole.CASE_STUDY
    assert by_id["case-negative-step"].knowledge_role == KnowledgeRole.NEGATIVE_CASE
    assert by_id["case-positive-step"].links[0].relation == "parent_case"
    assert by_id["case-positive-step"].links[0].to_artifact_id == "case-positive"
    assert json.loads(by_id["case-positive"].canonical_json) == _bundle().cases[1].model_dump(
        mode="json"
    )
    assert project_semantic_bundle(bundle) == projection


def test_projection_preserves_typed_context_provenance_and_contradicting_sources():
    artifacts = {artifact.artifact_id: artifact for artifact in project_semantic_bundle(_bundle())}
    reference = artifacts["reference-http"]
    rule = artifacts["rule-http"]

    assert {
        (facet.namespace, facet.key, facet.value, facet.relation) for facet in reference.facets
    } == {
        ("typed", "os_family", "linux", ContextRelation.REQUIRED),
        ("typed", "privileges", "www-data", ContextRelation.COMPATIBLE),
        ("typed", "security_controls", "waf", ContextRelation.INCOMPATIBLE),
        ("network", "exposure", "private", ContextRelation.REQUIRED),
    }
    assert any(source.location == _source_ref(line=20).location for source in reference.sources)
    assert reference.independence_group == "independent-source-a"
    assert any(
        source.relation == "contradicts" and source.location == _source_ref(line=40).location
        for source in rule.sources
    )


def test_projection_builds_bounded_sanitized_fts_columns_without_promoting_case_local_examples():
    artifacts = {artifact.artifact_id: artifact for artifact in project_semantic_bundle(_bundle())}

    reference = artifacts["reference-http"]
    step = artifacts["case-negative-step"]
    rule = artifacts["rule-http"]
    assert "reference-statement-token" in reference.statement
    assert "reference-action-token" in reference.action_intent
    assert reference.expected_evidence == "reference-expected-evidence-token"
    assert "reference-exception-token" in reference.exceptions
    assert "step-observation-token" in step.observations
    assert "step-action-token" in step.action_intent
    assert "trainee:training-password" not in step.fts_text
    assert "rule-rationale-token" in rule.rationale
    assert {
        "reference-subject-token",
        "reference-statement-token",
        "reference-situation-token",
        "reference-prerequisite-token",
        "reference-action-token",
        "reference-info-gain-token",
        "reference-expected-evidence-token",
        "reference-interpretation-token",
        "reference-success-token",
        "reference-failure-token",
        "reference-stop-token",
        "reference-exception-token",
        "reference-warning-token",
        "reference-capability-token",
    } <= set(reference.fts_text.split())
    assert {
        "positive-case-title-token",
        "positive-case-access-token",
        "positive-case-outcome-token",
        "positive-case-difficulty-token",
        "positive-case-transferable-token",
        "positive-case-nontransferable-token",
    } <= set(artifacts["case-positive"].fts_text.split())
    assert {
        "before-access-token",
        "before-environment-token",
        "before-privilege-token",
        "step-observation-token",
        "step-hypothesis-token",
        "step-action-token",
        "step-capability-token",
        "step-info-gain-token",
        "step-evidence-token",
        "step-evidence-category-token",
        "after-access-token",
        "after-environment-token",
        "after-privilege-token",
        "step-negative-evidence-token",
        "step-transfer-condition-token",
    } <= set(step.fts_text.split())
    assert {
        "rule-trigger-token",
        "rule-rationale-token",
        "rule-action-token",
        "rule-prerequisite-token",
        "rule-expected-evidence-token",
        "rule-success-transition-token",
        "rule-failure-transition-token",
        "rule-stop-condition-token",
        "rule-exception-token",
        "rule-alternative-token",
        "rule-capability-token",
    } <= set(rule.fts_text.split())


def test_projection_revalidates_constructed_canonical_bundle_before_emitting_rows():
    bundle = _bundle()
    corrupt_reference = bundle.references[0].model_copy(update={"statement": "HTB{unsafe}"})
    corrupt_bundle = bundle.model_copy(update={"references": (corrupt_reference,)})

    with pytest.raises(ValueError, match="final flag"):
        project_semantic_bundle(corrupt_bundle)


def test_projection_rejects_hidden_nested_model_copy_state_without_leaking_it():
    bundle = _bundle()
    corrupt_location = (
        bundle.references[0]
        .source_refs[0]
        .location.model_copy(update={"raw_response": "HTB{hidden-final-flag}"})
    )
    corrupt_ref = (
        bundle.references[0].source_refs[0].model_copy(update={"location": corrupt_location})
    )
    corrupt_artifact = bundle.references[0].model_copy(update={"source_refs": (corrupt_ref,)})
    corrupt_bundle = bundle.model_copy(update={"references": (corrupt_artifact,)})

    with pytest.raises(ValueError, match="unsafe canonical model state") as error:
        project_semantic_bundle(corrupt_bundle)

    assert "hidden-final-flag" not in str(error.value)


def test_projection_facet_id_is_injective_and_unions_same_assertion_provenance():
    bundle = _bundle()
    same_privilege = _assertion("www-data", relation=ContextRelation.COMPATIBLE).model_copy(
        update={"source_refs": (_source_ref(line=30),)}
    )
    overlapping_facet = ContextFacet(
        namespace="typed",
        key="privileges",
        assertion=_assertion("www-data", relation=ContextRelation.COMPATIBLE),
    )
    context = ApplicabilityContext(
        typed_context=TypedContext(
            privileges=(
                _assertion("www-data", relation=ContextRelation.COMPATIBLE),
                same_privilege,
            )
        ),
        facets=(overlapping_facet,),
    )
    artifact = bundle.references[0].model_copy(update={"applicability": context})
    projected = {
        row.artifact_id: row
        for row in project_semantic_bundle(bundle.model_copy(update={"references": (artifact,)}))
    }["reference-http"]

    typed_privileges = [
        facet
        for facet in projected.facets
        if facet.channel == "typed" and facet.namespace == "typed" and facet.key == "privileges"
    ]
    assert len(typed_privileges) == 1
    assert len({facet.facet_id for facet in projected.facets}) == len(projected.facets)
    extensible_privileges = [
        facet
        for facet in projected.facets
        if (
            facet.channel == "extensible"
            and facet.namespace == "typed"
            and facet.key == "privileges"
        )
    ]
    assert typed_privileges[0].facet_id != extensible_privileges[0].facet_id
    assert {
        source.location.start_line
        for source in projected.sources
        if source.relation == f"facet:{typed_privileges[0].facet_id}"
    } == {20, 30}


def test_projection_rejects_conflicting_duplicate_nested_step_ids():
    bundle = _bundle()
    conflicting_step = _step(identifier="case-positive-step", negative=True)
    corrupt_case = bundle.cases[0].model_copy(update={"steps": (conflicting_step,)})
    corrupt_bundle = bundle.model_copy(update={"cases": (corrupt_case, bundle.cases[1])})

    with pytest.raises(ValueError, match="unique"):
        project_semantic_bundle(corrupt_bundle)
