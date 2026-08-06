"""Tests for immutable, provenance-aware knowledge schema primitives."""

import pytest
from pydantic import ValidationError

from sedna.knowledge.schema import (
    ArtifactType,
    DocumentType,
    ExtractionMetadata,
    Generalizability,
    IngestionStatus,
    KnowledgeRole,
    Origin,
    ReviewStatus,
    SourceLocation,
    SourceQuality,
    SourceRef,
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
