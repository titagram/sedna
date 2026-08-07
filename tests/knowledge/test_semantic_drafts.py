"""Tests for strict LLM-facing semantic compilation draft contracts."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sedna.knowledge.schema import (
    SemanticCallMetadata,
    SemanticCompilationManifest,
    SemanticKnowledgeBundle,
    SemanticQuarantineRecord,
    SemanticVerificationRecord,
    VerificationFinding,
)
from sedna.knowledge.semantic.drafts import (
    CompilationDisposition,
    CompilationFailureCode,
    CriticVerdict,
    DraftCitation,
    DraftContextAssertion,
    DraftReference,
    SemanticCompilationResult,
    SemanticDraftBundle,
)


def draft_reference(local_id: str = "reference-http") -> DraftReference:
    return DraftReference(
        draft_type="reference",
        local_id=local_id,
        artifact_type="methodology",
        subject="HTTP service inspection",
        statement="Inspect HTTP before choosing an exploit.",
        origin="explicit",
        citations=(DraftCitation(segment_indexes=(0,)),),
    )


def verification(adjudication: str = "verified") -> SemanticVerificationRecord:
    return SemanticVerificationRecord(
        source_id="htb-lame",
        source_sha256="a" * 64,
        critic_call=SemanticCallMetadata(
            purpose="sedna.semantic.critic",
            provider="host",
            model="critic-model",
            agent_id="agent-7",
            input_tokens=100,
            output_tokens=50,
        ),
        adjudication=adjudication,
        recorded_at=datetime(2026, 8, 7, tzinfo=UTC),
    )


def semantic_calls(critic_call: SemanticCallMetadata) -> tuple[SemanticCallMetadata, ...]:
    return (
        SemanticCallMetadata(
            purpose="sedna.semantic.extract",
            provider="host",
            model="extractor-model",
            agent_id="agent-7",
            input_tokens=100,
            output_tokens=50,
        ),
        critic_call,
    )


def bundle() -> SemanticKnowledgeBundle:
    return SemanticKnowledgeBundle(
        schema_version="2.0.0",
        source_id="htb-lame",
        source_sha256="a" * 64,
        compilation_manifest=SemanticCompilationManifest(
            source_id="htb-lame",
            source_sha256="a" * 64,
            foundation_schema_version="1.1.0",
            foundation_parser_id="markdown-it-commonmark",
            foundation_parser_version="1",
            compiler_version="1",
            extractor_prompt_version="extract-v1",
            critic_prompt_version="critic-v1",
            repair_prompt_version="repair-v1",
            extractor_model_id="extractor-model",
            critic_model_id="critic-model",
            disposition="verified",
            repair_count=0,
            started_at=datetime(2026, 8, 7, tzinfo=UTC),
            completed_at=datetime(2026, 8, 7, 0, 1, tzinfo=UTC),
        ),
    )


def quarantine() -> SemanticQuarantineRecord:
    return SemanticQuarantineRecord(
        source_id="htb-lame",
        source_sha256="a" * 64,
        reason_codes=("unsupported_claim",),
        messages=("The source does not support the claim.",),
        segment_indexes=(1,),
        recorded_at=datetime(2026, 8, 7, tzinfo=UTC),
    )


def test_draft_bundle_parses_discriminated_reference_artifacts():
    parsed = SemanticDraftBundle.model_validate(
        {
            "artifacts": (draft_reference().model_dump(mode="json"),),
            "ignored_segment_indexes": (),
        }
    )

    assert isinstance(parsed.artifacts[0], DraftReference)
    assert parsed.model_json_schema()["properties"]["artifacts"]["items"]["discriminator"] == {
        "propertyName": "draft_type",
        "mapping": {
            "case": "#/$defs/DraftCase",
            "guidance": "#/$defs/DraftGuidance",
            "reference": "#/$defs/DraftReference",
        },
    }


def test_draft_artifacts_keep_their_canonical_semantic_type_without_canonical_ids():
    reference = draft_reference()
    guidance = {
        "draft_type": "guidance",
        "local_id": "guidance-http",
        "origin": "explicit",
        "citations": ({"segment_indexes": (1,)},),
        "trigger_observations": ("HTTP service exposed",),
        "rationale": "The service can reveal application behavior.",
        "action_intent": "Inspect the HTTP service.",
    }
    case = {
        "draft_type": "case",
        "local_id": "case-http",
        "origin": "explicit",
        "citations": ({"segment_indexes": (2,)},),
        "knowledge_role": "case_study",
        "title": "HTTP inspection",
        "starting_access": "none",
        "steps": (),
        "outcome": "The service was inspected.",
        "source_quality": "complete",
    }
    parsed = SemanticDraftBundle.model_validate(
        {"artifacts": (reference.model_dump(mode="json"), guidance, case)}
    )

    assert [artifact.artifact_type for artifact in parsed.artifacts] == [
        "methodology",
        "decision_rule",
        "case",
    ]


def test_draft_bundle_rejects_duplicate_local_ids():
    with pytest.raises(ValidationError, match="local IDs"):
        SemanticDraftBundle(
            artifacts=(draft_reference("a"), draft_reference("a")),
            ignored_segment_indexes=(),
        )


def test_draft_citations_require_non_negative_indexes():
    with pytest.raises(ValidationError, match="non-negative"):
        DraftCitation(segment_indexes=(-1,))


def test_draft_context_preserves_unknown_relation_without_accepting_unknown_origin():
    context = DraftContextAssertion(
        value="unknown",
        relation="unknown",
        origin="inferred",
        confidence=0.4,
        citations=(DraftCitation(segment_indexes=(1,)),),
    )

    assert context.relation == "unknown"
    with pytest.raises(ValidationError):
        DraftContextAssertion(
            value="unknown",
            relation="unknown",
            origin="unknown",
            confidence=0.4,
            citations=(DraftCitation(segment_indexes=(1,)),),
        )


def test_draft_local_ids_are_safe_response_scoped_path_segments():
    with pytest.raises(ValidationError):
        draft_reference("../canonical-source")
    with pytest.raises(ValidationError):
        DraftReference(
            **draft_reference().model_dump(),
            source_id="htb-lame",
        )


def test_critic_acceptance_matches_material_finding_severity():
    warning = VerificationFinding(
        code="unsupported_claim",
        severity="warning",
        message="The source does not support the claim.",
        segment_indexes=(0,),
    )
    material = warning.model_copy(update={"severity": "material"})

    assert CriticVerdict(accepted=True, findings=(warning,)).accepted is True
    assert CriticVerdict(accepted=False, findings=(material,)).accepted is False
    with pytest.raises(ValidationError, match="accepted"):
        CriticVerdict(accepted=True, findings=(material,))
    with pytest.raises(ValidationError, match="accepted"):
        CriticVerdict(accepted=False, findings=(warning,))


def test_critic_findings_retain_safe_draft_local_id_references():
    unsafe_local_id = VerificationFinding(
        code="unsupported_claim",
        severity="warning",
        artifact_local_id="../canonical-source",
        message="The source does not support the claim.",
    )

    with pytest.raises(ValidationError, match="safe path segment"):
        CriticVerdict(accepted=True, findings=(unsafe_local_id,))


def test_compiler_range_validation_covers_ignored_and_cited_indexes():
    draft = SemanticDraftBundle(
        artifacts=(draft_reference(),),
        ignored_segment_indexes=(2,),
    )
    finding = VerificationFinding(
        code="unsupported_claim",
        severity="warning",
        message="The source does not support the claim.",
        segment_indexes=(3,),
    )

    draft.validate_against_segment_count(3)
    with pytest.raises(ValueError, match="input segment range"):
        draft.validate_against_segment_count(2)
    with pytest.raises(ValueError, match="input segment range"):
        CriticVerdict(accepted=True, findings=(finding,)).validate_against_segment_count(3)


@pytest.mark.parametrize("disposition", ("verified", "unchanged"))
def test_verified_and_unchanged_results_require_a_bundle_and_verification(disposition: str):
    record = verification("verified")
    result = SemanticCompilationResult(
        disposition=disposition,
        bundle=bundle(),
        verification=record,
        calls=semantic_calls(record.critic_call) if disposition == "verified" else (),
    )

    assert result.disposition == disposition
    with pytest.raises(ValidationError, match="bundle and verification"):
        SemanticCompilationResult(disposition=disposition, verification=verification(disposition))


def test_result_disposition_agrees_with_verified_or_quarantined_audit_adjudication():
    incorrect_verified = verification("quarantined")
    with pytest.raises(ValidationError, match="verification adjudication"):
        SemanticCompilationResult(
            disposition="verified",
            bundle=bundle(),
            verification=incorrect_verified,
            calls=semantic_calls(incorrect_verified.critic_call),
        )
    incorrect_quarantine = verification("verified")
    with pytest.raises(ValidationError, match="verification adjudication"):
        SemanticCompilationResult(
            disposition="quarantined",
            verification=incorrect_quarantine,
            quarantine=quarantine(),
            calls=semantic_calls(incorrect_quarantine.critic_call),
        )


def test_quarantined_and_failed_results_have_exclusive_payload_shapes():
    quarantine_verification = verification("quarantined")
    quarantined = SemanticCompilationResult(
        disposition="quarantined",
        verification=quarantine_verification,
        quarantine=quarantine(),
        calls=semantic_calls(quarantine_verification.critic_call),
    )
    failed = SemanticCompilationResult(
        disposition="failed",
        failure_code="transport_failure",
        failure_message="The host LLM request failed.",
    )

    assert quarantined.bundle is None
    assert failed.verification is None
    with pytest.raises(ValidationError, match="only a safe failure reason"):
        SemanticCompilationResult(
            disposition="failed",
            failure_code="transport_failure",
            failure_message="The host LLM request failed.",
            verification=verification("failed"),
        )


def test_compilation_disposition_has_the_closed_protocol_vocabulary():
    assert CompilationDisposition.__args__ == ("verified", "quarantined", "failed", "unchanged")


def test_failure_codes_are_closed_and_do_not_reuse_critic_finding_codes():
    assert CompilationFailureCode.__args__ == (
        "transport_failure",
        "missing_parsed_response",
        "invalid_structured_response",
        "invalid_input",
        "materialization_failure",
        "internal_failure",
    )
    with pytest.raises(ValidationError, match="failure_code"):
        SemanticCompilationResult(
            disposition="failed",
            failure_code="unsafe_material",
            failure_message="The artifact contains unsafe material.",
        )


@pytest.mark.parametrize(
    ("repair_count", "calls"),
    [
        (1, "two"),
        (0, "four"),
    ],
)
def test_verified_result_rejects_call_path_and_manifest_repair_count_disagreement(
    repair_count: int, calls: str
):
    record = verification("verified")
    bundle_value = bundle().model_copy(
        update={
            "compilation_manifest": bundle().compilation_manifest.model_copy(
                update={"repair_count": repair_count}
            )
        }
    )
    call_metadata = semantic_calls(record.critic_call)
    if calls == "four":
        call_metadata = (
            call_metadata[0],
            SemanticCallMetadata(
                purpose="sedna.semantic.critic",
                provider="host",
                model="initial-critic",
                agent_id="agent-7",
                input_tokens=100,
                output_tokens=50,
            ),
            SemanticCallMetadata(
                purpose="sedna.semantic.repair",
                provider="host",
                model="repair-model",
                agent_id="agent-7",
                input_tokens=100,
                output_tokens=50,
            ),
            record.critic_call,
        )
    with pytest.raises(ValidationError, match="repair_count"):
        SemanticCompilationResult(
            disposition="verified",
            bundle=bundle_value,
            verification=record,
            calls=call_metadata,
        )


def test_verified_result_rejects_manifest_models_that_do_not_bind_to_call_metadata():
    record = verification("verified")
    bundle_value = bundle().model_copy(
        update={
            "compilation_manifest": bundle().compilation_manifest.model_copy(
                update={"extractor_model_id": "wrong-extractor"}
            )
        }
    )

    with pytest.raises(ValidationError, match="model IDs"):
        SemanticCompilationResult(
            disposition="verified",
            bundle=bundle_value,
            verification=record,
            calls=semantic_calls(record.critic_call),
        )
