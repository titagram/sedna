"""Tests for immutable, provenance-aware knowledge schema primitives."""

import pytest
from pydantic import ValidationError

from sedna.knowledge.schema import (
    ArtifactType,
    AssetRef,
    CaseAction,
    CaseState,
    CaseStep,
    DecisionRule,
    DocumentManifest,
    DocumentType,
    ExtractionMetadata,
    Generalizability,
    IngestionStatus,
    KnowledgeCase,
    KnowledgeRole,
    Origin,
    ReferenceArtifact,
    ReviewStatus,
    SourceLocation,
    SourceQuality,
    SourceRef,
)


def foundation_metadata() -> ExtractionMetadata:
    return ExtractionMetadata(
        schema_version="1.0.0",
        parser_id="markdown-it-commonmark",
        parser_version="1",
        extractor_id="deterministic-foundation",
        extractor_version="1",
    )


def walkthrough_ref() -> SourceRef:
    return SourceRef(
        source_id="htb-lame",
        path="raw_src/Write-ups/Machines/Lame/walkthrough.md",
        location=SourceLocation(start_line=10, end_line=18),
    )


def test_source_ref_requires_a_precise_location():
    ref = SourceRef(
        source_id="htb-lame",
        path="raw_src/Write-ups/Machines/Lame/walkthrough.md",
        location=SourceLocation(start_line=10, end_line=18),
    )

    assert ref.location.start_line == 10


def test_source_location_rejects_reversed_lines():
    with pytest.raises(ValidationError):
        SourceLocation(start_line=18, end_line=10)


def test_extraction_metadata_records_reproducibility_versions():
    metadata = ExtractionMetadata(
        schema_version="1.0.0",
        parser_id="markdown-it-commonmark",
        parser_version="1",
        extractor_id="deterministic-foundation",
        extractor_version="1",
    )

    assert metadata.schema_version == "1.0.0"
    assert DocumentType.MACHINE_WALKTHROUGH.value == "machine_walkthrough"


def test_enums_match_the_design_vocabulary():
    assert {member.value for member in DocumentType} == {
        "lesson",
        "machine_walkthrough",
        "challenge_walkthrough",
        "cheatsheet_reference",
        "external_stub",
        "excluded",
    }
    assert {member.value for member in KnowledgeRole} == {
        "reference",
        "case_study",
        "negative_case",
    }
    assert {member.value for member in ArtifactType} == {
        "concept",
        "methodology",
        "decision_rule",
        "case",
        "case_step",
        "negative_evidence",
        "anti_pattern",
    }
    assert {member.value for member in Origin} == {"explicit", "inferred", "derived"}
    assert {member.value for member in ReviewStatus} == {
        "auto_extracted",
        "draft",
        "approved",
        "rejected",
    }
    assert {member.value for member in Generalizability} == {"none", "low", "medium", "high"}
    assert {member.value for member in SourceQuality} == {
        "complete",
        "partial",
        "minimal",
        "unusable",
    }
    assert {member.value for member in IngestionStatus} == {
        "accepted",
        "excluded",
        "quarantined",
    }


def test_schema_models_are_immutable_and_forbid_unknown_fields():
    location = SourceLocation(section="Recon")

    with pytest.raises(ValidationError):
        SourceRef(
            source_id="htb-lame",
            path="raw_src/Write-ups/Machines/Lame/walkthrough.md",
            location=location,
            unexpected=True,
        )
    with pytest.raises(ValidationError):
        location.section = "Enumeration"


def test_manifest_tracks_hash_profile_and_emitted_artifacts():
    manifest = DocumentManifest(
        source_id="htb-lame",
        path="raw_src/Write-ups/Machines/Lame/walkthrough.md",
        sha256="a" * 64,
        title="Lame",
        language="en",
        document_type=DocumentType.MACHINE_WALKTHROUGH,
        knowledge_role=KnowledgeRole.CASE_STUDY,
        quality=SourceQuality.COMPLETE,
        parser_profile="github_walkthrough",
        ingestion_status=IngestionStatus.ACCEPTED,
        extraction=foundation_metadata(),
        assets=(AssetRef(path="raw_src/Write-ups/Machines/Lame/diagram.png", sha256="b" * 64),),
        emitted_artifact_ids=("case-htb-lame",),
    )

    assert manifest.sha256 == "a" * 64
    assert manifest.assets[0].sha256 == "b" * 64
    assert manifest.emitted_artifact_ids == ("case-htb-lame",)


def test_manifest_rejects_noncanonical_sha256_values():
    with pytest.raises(ValidationError):
        AssetRef(path="raw_src/asset.png", sha256="A" * 64)


def test_case_step_requires_at_least_one_source_reference():
    with pytest.raises(ValidationError):
        CaseStep(
            ordinal=1,
            state_before=CaseState(access="none"),
            observations=("HTTP service exposed",),
            hypotheses=(),
            selected_action=CaseAction(intent="inspect_http"),
            evidence=(),
            state_after=CaseState(access="none"),
            source_refs=(),
        )


def test_reference_artifact_and_decision_rule_require_source_references():
    with pytest.raises(ValidationError):
        ReferenceArtifact(
            artifact_id="reference-http-inspection",
            statement="Inspect the exposed HTTP service before choosing an exploit.",
            artifact_type=ArtifactType.METHODOLOGY,
            source_refs=(),
        )
    with pytest.raises(ValidationError):
        DecisionRule(
            rule_id="inspect-http",
            trigger_observations=("HTTP service exposed",),
            rationale="Observed services guide the next investigation.",
            action_intent="inspect_http",
            source_refs=(),
        )


def test_knowledge_case_rejects_duplicate_step_ordinals():
    step = CaseStep(
        ordinal=1,
        state_before=CaseState(access="none"),
        observations=("HTTP service exposed",),
        hypotheses=(),
        selected_action=CaseAction(intent="inspect_http"),
        evidence=(),
        state_after=CaseState(access="none"),
        source_refs=(walkthrough_ref(),),
    )

    with pytest.raises(ValidationError):
        KnowledgeCase(
            case_id="htb-lame",
            title="Lame",
            starting_access="none",
            steps=(step, step),
            outcome="Initial HTTP investigation completed.",
            source_quality=SourceQuality.COMPLETE,
            review_status=ReviewStatus.DRAFT,
        )
