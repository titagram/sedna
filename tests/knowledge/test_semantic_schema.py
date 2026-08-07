"""Tests for immutable applicability and epistemic schema contracts."""

import pytest
from pydantic import ValidationError

from sedna.knowledge.schema import (
    ApplicabilityContext,
    ContextAssertion,
    ContextFacet,
    ContextRelation,
    EpistemicAssessment,
    Generalizability,
    ObservedOutcome,
    Origin,
    ServiceContext,
    SourceLocation,
    SourceRef,
    TypedContext,
    VerificationStatus,
)


def walkthrough_ref() -> SourceRef:
    return SourceRef(
        source_id="htb-lame",
        path="raw_src/Write-ups/Machines/Lame/walkthrough.md",
        location=SourceLocation(start_line=10, end_line=18),
    )


def observed(value: str) -> ContextAssertion:
    return ContextAssertion(
        value=value,
        relation=ContextRelation.OBSERVED,
        origin=Origin.EXPLICIT,
        confidence=0.8,
        source_refs=(walkthrough_ref(),),
    )


def test_context_assertion_requires_provenance_and_bounded_confidence():
    assertion = ContextAssertion(
        value="windows",
        relation=ContextRelation.OBSERVED,
        origin=Origin.EXPLICIT,
        confidence=1.0,
        source_refs=(walkthrough_ref(),),
    )

    assert assertion.value == "windows"
    with pytest.raises(ValidationError):
        ContextAssertion(
            value="windows",
            relation=ContextRelation.OBSERVED,
            origin=Origin.INFERRED,
            confidence=1.01,
            source_refs=(walkthrough_ref(),),
        )
    with pytest.raises(ValidationError):
        ContextAssertion(
            value="windows",
            relation=ContextRelation.OBSERVED,
            origin=Origin.EXPLICIT,
            confidence=0.8,
            source_refs=(),
        )


def test_unknown_is_not_a_compatibility_wildcard():
    unknown = ContextAssertion(
        value="unknown",
        relation=ContextRelation.UNKNOWN,
        origin=Origin.INFERRED,
        confidence=0.4,
        source_refs=(walkthrough_ref(),),
    )

    assert unknown.relation is ContextRelation.UNKNOWN
    assert unknown.relation is not ContextRelation.COMPATIBLE


def test_semantic_enums_match_the_design_vocabulary():
    assert {member.value for member in VerificationStatus} == {
        "extracted",
        "verified",
        "corroborated",
        "contested",
        "deprecated",
        "rejected",
    }
    assert {member.value for member in ContextRelation} == {
        "observed",
        "required",
        "compatible",
        "incompatible",
        "unknown",
    }
    assert {member.value for member in ObservedOutcome} == {
        "success",
        "failure",
        "mixed",
        "informational",
        "not_applicable",
    }


def test_applicability_context_rejects_duplicate_service_identities_and_facets():
    service = ServiceContext(service_type="web", identity=observed("http:80"))
    facet = ContextFacet(namespace="network", key="exposure", assertion=observed("public"))

    with pytest.raises(ValidationError):
        ApplicabilityContext(typed_context=TypedContext(services=(service, service)))
    with pytest.raises(ValidationError):
        ApplicabilityContext(
            typed_context=TypedContext(),
            facets=(facet, facet),
        )


def test_context_and_epistemic_models_are_immutable_and_strict():
    assertion = observed("linux")
    typed_context = TypedContext(os_family=assertion)
    assessment = EpistemicAssessment(
        source_reliability=0.9,
        extraction_confidence=0.8,
        generalizability=Generalizability.MEDIUM,
        context_specificity=0.7,
        verification_status=VerificationStatus.CORROBORATED,
        observed_outcome=ObservedOutcome.SUCCESS,
        independence_group="htb-lame",
    )

    assert typed_context.os_family == assertion
    assert assessment.support_count == 1
    with pytest.raises(ValidationError):
        typed_context.os_family = observed("windows")
    with pytest.raises(ValidationError):
        EpistemicAssessment(
            source_reliability=-0.1,
            extraction_confidence=0.8,
            generalizability=Generalizability.MEDIUM,
            context_specificity=0.7,
            verification_status=VerificationStatus.CORROBORATED,
            observed_outcome=ObservedOutcome.SUCCESS,
            independence_group="htb-lame",
        )
