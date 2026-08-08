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
        state_before=CaseState(access="none", environment=("linux",)),
        observations=("HTTP service exposed",),
        hypotheses=(),
        selected_action=CaseAction(intent="inspect_http"),
        evidence=(),
        state_after=CaseState(access="user"),
        negative_evidence=("The default credential was rejected",) if negative else (),
        transfer_conditions=("Validate the service identity",),
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
        subject="HTTP discovery",
        statement="Inspect the HTTP service before choosing a method.",
        action_intent="inspect_http",
        expected_evidence=("Response headers",),
        exceptions=("Do not reuse historical credentials",),
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
        title="Successful HTTP case",
        starting_access="none",
        steps=(positive_step,),
        outcome="HTTP inspected",
        source_quality=SourceQuality.COMPLETE,
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
        title="Failed HTTP case",
        starting_access="none",
        steps=(negative_step,),
        outcome="Credential attempt failed",
        source_quality=SourceQuality.COMPLETE,
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
        trigger_observations=("HTTP service exposed",),
        rationale="Observed HTTP evidence determines the next decision.",
        action_intent="inspect_http",
        expected_evidence=("Response headers",),
        exceptions=("Do not reuse historical credentials",),
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
    assert reference.statement == "Inspect the HTTP service before choosing a method."
    assert reference.action_intent == "inspect_http"
    assert reference.expected_evidence == "Response headers"
    assert reference.exceptions == "Do not reuse historical credentials"
    assert step.observations == "HTTP service exposed"
    assert step.action_intent == "inspect_http"
    assert "trainee:training-password" not in step.fts_text
    assert rule.rationale == "Observed HTTP evidence determines the next decision."


def test_projection_revalidates_constructed_canonical_bundle_before_emitting_rows():
    bundle = _bundle()
    corrupt_reference = bundle.references[0].model_copy(update={"statement": "HTB{unsafe}"})
    corrupt_bundle = bundle.model_copy(update={"references": (corrupt_reference,)})

    with pytest.raises(ValueError, match="final flag"):
        project_semantic_bundle(corrupt_bundle)


def test_projection_rejects_conflicting_duplicate_nested_step_ids():
    bundle = _bundle()
    conflicting_step = _step(identifier="case-positive-step", negative=True)
    corrupt_case = bundle.cases[0].model_copy(update={"steps": (conflicting_step,)})
    corrupt_bundle = bundle.model_copy(update={"cases": (corrupt_case, bundle.cases[1])})

    with pytest.raises(ValueError, match="unique"):
        project_semantic_bundle(corrupt_bundle)
