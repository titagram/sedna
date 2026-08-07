"""Tests for immutable, provenance-aware knowledge schema primitives."""

import pytest
from pydantic import ValidationError

from sedna.knowledge.schema import (
    ApplicabilityContext,
    ArtifactType,
    AssetRef,
    CaseAction,
    CaseEvidence,
    CaseHypothesis,
    CaseState,
    CaseStep,
    ContextAssertion,
    ContextRelation,
    DecisionRule,
    DocumentManifest,
    DocumentType,
    EpistemicAssessment,
    ExtractionMetadata,
    Generalizability,
    IngestionStatus,
    KnowledgeCase,
    KnowledgeRole,
    ObservedOutcome,
    Origin,
    ReferenceArtifact,
    ReviewStatus,
    SourceLocation,
    SourceQuality,
    SourceRef,
    TypedContext,
    VerificationStatus,
    verification_from_legacy_review,
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


def observed_context(value: str) -> ContextAssertion:
    return ContextAssertion(
        value=value,
        relation=ContextRelation.OBSERVED,
        origin=Origin.EXPLICIT,
        confidence=0.8,
        source_refs=(walkthrough_ref(),),
    )


def canonical_metadata(
    artifact_type: ArtifactType,
    knowledge_role: KnowledgeRole,
) -> dict[str, object]:
    return {
        "artifact_type": artifact_type,
        "knowledge_role": knowledge_role,
        "origin": Origin.EXPLICIT,
        "review_status": ReviewStatus.DRAFT,
        "generalizability": Generalizability.MEDIUM,
        "source_refs": (walkthrough_ref(),),
        "extraction": foundation_metadata(),
    }


def semantic_metadata(
    artifact_type: ArtifactType,
    knowledge_role: KnowledgeRole,
) -> dict[str, object]:
    return {
        "artifact_type": artifact_type,
        "knowledge_role": knowledge_role,
        "origin": Origin.EXPLICIT,
        "applicability": ApplicabilityContext(),
        "assessment": EpistemicAssessment(
            source_reliability=0.9,
            extraction_confidence=0.8,
            generalizability=Generalizability.MEDIUM,
            context_specificity=0.7,
            verification_status=VerificationStatus.VERIFIED,
            observed_outcome=ObservedOutcome.INFORMATIONAL,
            independence_group="htb-lame",
        ),
        "source_refs": (walkthrough_ref(),),
        "extraction": foundation_metadata(),
    }


def reference_payload() -> dict[str, object]:
    return {
        "artifact_id": "reference-http",
        "subject": "HTTP service inspection",
        "statement": "Inspect HTTP.",
        **canonical_metadata(ArtifactType.METHODOLOGY, KnowledgeRole.REFERENCE),
    }


def case_step_payload(
    knowledge_role: KnowledgeRole = KnowledgeRole.CASE_STUDY,
) -> dict[str, object]:
    return {
        "step_id": "case-http-step-1",
        "ordinal": 1,
        "state_before": CaseState(access="none"),
        "observations": ("HTTP service exposed",),
        "hypotheses": (),
        "selected_action": CaseAction(intent="inspect_http"),
        "evidence": (),
        "state_after": CaseState(access="none"),
        **canonical_metadata(ArtifactType.CASE_STEP, knowledge_role),
    }


def knowledge_case_payload(
    steps: tuple[CaseStep, ...] = (),
    knowledge_role: KnowledgeRole = KnowledgeRole.CASE_STUDY,
) -> dict[str, object]:
    return {
        "case_id": "case-http",
        "title": "HTTP case",
        "starting_access": "none",
        "steps": steps,
        "outcome": "HTTP inspected.",
        "source_quality": SourceQuality.COMPLETE,
        **canonical_metadata(ArtifactType.CASE, knowledge_role),
    }


def decision_rule_payload() -> dict[str, object]:
    return {
        "rule_id": "rule-http",
        "trigger_observations": ("HTTP exposed",),
        "rationale": "Observed services guide investigation.",
        "action_intent": "inspect_http",
        **canonical_metadata(ArtifactType.DECISION_RULE, KnowledgeRole.REFERENCE),
    }


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
        "constraint",
        "evidence_interpretation",
        "exception",
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


def test_legacy_review_statuses_map_to_verification_statuses():
    assert (
        verification_from_legacy_review(ReviewStatus.AUTO_EXTRACTED) is VerificationStatus.EXTRACTED
    )
    assert verification_from_legacy_review(ReviewStatus.DRAFT) is VerificationStatus.EXTRACTED
    assert verification_from_legacy_review(ReviewStatus.APPROVED) is VerificationStatus.VERIFIED
    assert verification_from_legacy_review(ReviewStatus.REJECTED) is VerificationStatus.REJECTED


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
            **{
                **canonical_metadata(
                    ArtifactType.CASE_STEP,
                    KnowledgeRole.CASE_STUDY,
                ),
                "source_refs": (),
            },
        )


def test_reference_artifact_and_decision_rule_require_source_references():
    with pytest.raises(ValidationError):
        ReferenceArtifact(
            artifact_id="reference-http-inspection",
            statement="Inspect the exposed HTTP service before choosing an exploit.",
            **{
                **canonical_metadata(
                    ArtifactType.METHODOLOGY,
                    KnowledgeRole.REFERENCE,
                ),
                "source_refs": (),
            },
        )
    with pytest.raises(ValidationError):
        DecisionRule(
            rule_id="inspect-http",
            trigger_observations=("HTTP service exposed",),
            rationale="Observed services guide the next investigation.",
            action_intent="inspect_http",
            **{
                **canonical_metadata(
                    ArtifactType.DECISION_RULE,
                    KnowledgeRole.REFERENCE,
                ),
                "source_refs": (),
            },
        )


def test_canonical_artifacts_carry_required_epistemic_and_extraction_metadata():
    reference = ReferenceArtifact.model_validate(reference_payload())
    step = CaseStep.model_validate(case_step_payload())
    case = KnowledgeCase.model_validate(knowledge_case_payload((step,)))
    rule = DecisionRule.model_validate(decision_rule_payload())

    assert reference.extraction.extractor_version == "1"
    assert step.generalizability is Generalizability.MEDIUM
    assert case.origin is Origin.EXPLICIT
    assert rule.knowledge_role is KnowledgeRole.REFERENCE


def test_canonical_artifacts_store_applicability_and_assessment_as_canonical_metadata():
    reference = ReferenceArtifact(
        artifact_id="reference-http",
        subject="HTTP service inspection",
        statement="Inspect HTTP.",
        **semantic_metadata(ArtifactType.METHODOLOGY, KnowledgeRole.REFERENCE),
    )

    assert reference.applicability == ApplicabilityContext()
    assert reference.assessment.verification_status is VerificationStatus.VERIFIED
    assert reference.review_status is ReviewStatus.APPROVED
    assert reference.generalizability is Generalizability.MEDIUM


def test_legacy_review_metadata_maps_only_when_no_explicit_assessment_is_supplied():
    explicit_applicability = ApplicabilityContext(
        typed_context=TypedContext(os_family=observed_context("linux"))
    )
    legacy = ReferenceArtifact(
        artifact_id="reference-http",
        subject="HTTP service inspection",
        statement="Inspect HTTP.",
        applicability=explicit_applicability,
        **canonical_metadata(ArtifactType.METHODOLOGY, KnowledgeRole.REFERENCE),
    )

    assert legacy.assessment.verification_status is VerificationStatus.EXTRACTED
    assert legacy.assessment.generalizability is Generalizability.MEDIUM
    assert legacy.applicability == explicit_applicability
    with pytest.raises(ValidationError, match="review_status"):
        ReferenceArtifact(
            artifact_id="reference-http",
            subject="HTTP service inspection",
            statement="Inspect HTTP.",
            review_status=ReviewStatus.REJECTED,
            **semantic_metadata(ArtifactType.METHODOLOGY, KnowledgeRole.REFERENCE),
        )


def test_every_canonical_artifact_metadata_field_is_required():
    step = CaseStep.model_validate(case_step_payload())
    artifacts = (
        ReferenceArtifact.model_validate(reference_payload()),
        step,
        KnowledgeCase.model_validate(knowledge_case_payload((step,))),
        DecisionRule.model_validate(decision_rule_payload()),
    )

    for artifact in artifacts:
        for field in (
            "artifact_type",
            "knowledge_role",
            "origin",
            "applicability",
            "assessment",
            "source_refs",
            "extraction",
        ):
            payload = artifact.model_dump()
            del payload[field]
            with pytest.raises(ValidationError) as error:
                type(artifact).model_validate(payload)
            assert field in str(error.value)


@pytest.mark.parametrize(
    ("model", "payload", "field"),
    [
        (
            ReferenceArtifact,
            {**reference_payload(), "artifact_type": ArtifactType.CASE},
            "artifact_type",
        ),
        (
            ReferenceArtifact,
            {
                **reference_payload(),
                "knowledge_role": KnowledgeRole.CASE_STUDY,
            },
            "knowledge_role",
        ),
        (
            CaseStep,
            {**case_step_payload(), "artifact_type": ArtifactType.CASE},
            "artifact_type",
        ),
        (
            KnowledgeCase,
            {
                **knowledge_case_payload(),
                "knowledge_role": KnowledgeRole.REFERENCE,
            },
            "knowledge_role",
        ),
        (
            DecisionRule,
            {**decision_rule_payload(), "artifact_type": ArtifactType.METHODOLOGY},
            "artifact_type",
        ),
    ],
)
def test_canonical_artifact_taxonomies_reject_incompatible_metadata(
    model: type[ReferenceArtifact | CaseStep | KnowledgeCase | DecisionRule],
    payload: dict[str, object],
    field: str,
):
    with pytest.raises(ValidationError) as error:
        model.model_validate(payload)

    assert field in str(error.value)


ENCODED_CANONICAL_LEAK = "HTB%2526%2523123%253Bcanonical_leak%2526%2523125%253B"


def _searchable_field_cases(
    model: type[ReferenceArtifact | CaseStep | KnowledgeCase | DecisionRule],
    payload: dict[str, object],
    scalar_fields: tuple[str, ...],
    sequence_fields: tuple[str, ...],
) -> tuple[
    tuple[
        type[ReferenceArtifact | CaseStep | KnowledgeCase | DecisionRule],
        dict[str, object],
        str,
        bool,
    ],
    ...,
]:
    return tuple(
        (model, payload, field, field in sequence_fields)
        for field in (*scalar_fields, *sequence_fields)
    )


@pytest.mark.parametrize(
    ("model", "base_payload", "field", "is_sequence"),
    [
        *_searchable_field_cases(
            ReferenceArtifact,
            reference_payload(),
            ("statement", "action_intent", "observed_at"),
            (
                "applicable_situations",
                "prerequisites",
                "expected_evidence",
                "success_implications",
                "failure_implications",
                "stop_implications",
                "exceptions",
                "warnings",
                "capability_refs",
            ),
        ),
        *_searchable_field_cases(
            CaseStep,
            case_step_payload(),
            (),
            (
                "observations",
                "negative_evidence",
                "transfer_conditions",
                "case_specific_details",
            ),
        ),
        *_searchable_field_cases(
            KnowledgeCase,
            knowledge_case_payload(),
            ("title", "starting_access", "outcome", "difficulty"),
            ("transferable_properties", "non_transferable_properties"),
        ),
        *_searchable_field_cases(
            DecisionRule,
            decision_rule_payload(),
            ("rationale", "action_intent"),
            (
                "trigger_observations",
                "prerequisites",
                "expected_evidence",
                "success_transitions",
                "failure_transitions",
                "stop_conditions",
                "exceptions",
                "alternative_hypotheses",
                "capability_refs",
            ),
        ),
    ],
)
def test_every_direct_canonical_searchable_field_rejects_encoded_flags(
    model: type[ReferenceArtifact | CaseStep | KnowledgeCase | DecisionRule],
    base_payload: dict[str, object],
    field: str,
    is_sequence: bool,
):
    payload = {
        **base_payload,
        field: (ENCODED_CANONICAL_LEAK,) if is_sequence else ENCODED_CANONICAL_LEAK,
    }

    with pytest.raises(ValidationError, match="final flag"):
        model.model_validate(payload)


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (CaseState, {"access": ENCODED_CANONICAL_LEAK}),
        (CaseState, {"access": "none", "environment": (ENCODED_CANONICAL_LEAK,)}),
        (CaseState, {"access": "none", "privileges": (ENCODED_CANONICAL_LEAK,)}),
        (
            CaseHypothesis,
            {"statement": ENCODED_CANONICAL_LEAK, "origin": Origin.INFERRED},
        ),
        (CaseAction, {"intent": ENCODED_CANONICAL_LEAK}),
        (
            CaseAction,
            {"intent": "inspect_http", "capability_ref": ENCODED_CANONICAL_LEAK},
        ),
        (
            CaseEvidence,
            {"summary": ENCODED_CANONICAL_LEAK, "origin": Origin.EXPLICIT},
        ),
        (
            CaseEvidence,
            {
                "summary": "HTTP exposed",
                "origin": Origin.EXPLICIT,
                "category": ENCODED_CANONICAL_LEAK,
            },
        ),
    ],
)
def test_nested_case_searchable_fields_reject_encoded_flags(
    model: type[CaseState | CaseHypothesis | CaseAction | CaseEvidence],
    payload: dict[str, object],
):
    with pytest.raises(ValidationError, match="final flag"):
        model.model_validate(payload)


def test_canonical_models_accept_the_explicit_exclusion_sentinel():
    reference = ReferenceArtifact(
        artifact_id="reference-excluded-result",
        subject="Excluded result",
        statement="The result is <EXCLUDED_FLAG>.",
        **canonical_metadata(ArtifactType.METHODOLOGY, KnowledgeRole.REFERENCE),
    )

    assert reference.statement == "The result is <EXCLUDED_FLAG>."


@pytest.mark.parametrize(
    "statement",
    [
        "Root flag: abcdef0123456789abcdef0123456789",
        "User flag: %61bcdef0123456789abcdef0123456789",
        "Root%20flag%3A%20abcdef0123456789abcdef0123456789",
    ],
)
def test_canonical_searchable_fields_reject_contextual_bare_hex_flags(
    statement: str,
):
    with pytest.raises(ValidationError, match="final flag"):
        ReferenceArtifact.model_validate({**reference_payload(), "statement": statement})


def test_nested_case_searchable_fields_apply_their_own_flag_context():
    with pytest.raises(ValidationError, match="final flag"):
        CaseState(access="User flag: abcdef0123456789abcdef0123456789")


@pytest.mark.parametrize(
    "statement",
    [
        "HTB&#123canonical_secret&#125",
        "HTB&#x7bsecret&#x7d",
        "Root&#32flag: abcdef0123456789abcdef0123456789",
        "Root&nbspflag: abcdef0123456789abcdef0123456789",
    ],
)
def test_canonical_fields_reject_semicolonless_html_entity_bypasses(statement: str):
    with pytest.raises(ValidationError, match="final flag"):
        ReferenceArtifact.model_validate({**reference_payload(), "statement": statement})


@pytest.mark.parametrize(
    "statement",
    [
        "MD5: abcdef0123456789abcdef0123456789",
        "MD5%3A%20abcdef0123456789abcdef0123456789",
        "R&D notes MD5: abcdef0123456789abcdef0123456789",
    ],
)
def test_canonical_searchable_fields_preserve_unrelated_md5_hashes(statement: str):
    reference = ReferenceArtifact.model_validate({**reference_payload(), "statement": statement})

    assert reference.statement == statement


@pytest.mark.parametrize(
    "unstable_value",
    [
        f"HTB%{'25' * 20}7Bcanonical_deep_leak%{'25' * 20}7D",
        "note:%2525252525252541;" * 2_500,
        f"HTB%{'25' * 20_000}7Bcanonical_deep_leak%{'25' * 20_000}7D",
    ],
)
def test_canonical_searchable_fields_reject_decode_budget_exhaustion(
    unstable_value: str,
):
    with pytest.raises(ValidationError, match="final flag"):
        ReferenceArtifact.model_validate({**reference_payload(), "statement": unstable_value})


def test_knowledge_case_rejects_steps_from_a_different_epistemic_lane():
    negative_step = CaseStep(
        step_id="mixed-lane-step",
        ordinal=1,
        state_before=CaseState(access="none"),
        observations=("The attempted path produced no evidence.",),
        hypotheses=(),
        selected_action=CaseAction(intent="stop_unproductive_path"),
        evidence=(),
        state_after=CaseState(access="none"),
        **canonical_metadata(ArtifactType.CASE_STEP, KnowledgeRole.NEGATIVE_CASE),
    )

    with pytest.raises(ValidationError, match="knowledge_role"):
        KnowledgeCase(
            case_id="mixed-lane-case",
            title="Mixed lane",
            starting_access="none",
            steps=(negative_step,),
            outcome="The path was stopped.",
            source_quality=SourceQuality.COMPLETE,
            **canonical_metadata(ArtifactType.CASE, KnowledgeRole.CASE_STUDY),
        )


def test_knowledge_case_rejects_duplicate_step_ordinals():
    step = CaseStep(
        step_id="duplicate-step",
        ordinal=1,
        state_before=CaseState(access="none"),
        observations=("HTTP service exposed",),
        hypotheses=(),
        selected_action=CaseAction(intent="inspect_http"),
        evidence=(),
        state_after=CaseState(access="none"),
        **canonical_metadata(ArtifactType.CASE_STEP, KnowledgeRole.CASE_STUDY),
    )

    with pytest.raises(ValidationError):
        KnowledgeCase(
            case_id="htb-lame",
            title="Lame",
            starting_access="none",
            steps=(step, step),
            outcome="Initial HTTP investigation completed.",
            source_quality=SourceQuality.COMPLETE,
            **canonical_metadata(ArtifactType.CASE, KnowledgeRole.CASE_STUDY),
        )
