"""Tests for deterministic materialization of semantic drafts."""

from __future__ import annotations

import pytest

from sedna.knowledge.parsing import PreparedSource, parse_markdown
from sedna.knowledge.parsing.segment import segment_document
from sedna.knowledge.schema import (
    DocumentManifest,
    DocumentType,
    ExtractionMetadata,
    IngestionStatus,
    KnowledgeRole,
    SourceQuality,
    VerificationStatus,
)
from sedna.knowledge.schema.semantic import SemanticCallMetadata
from sedna.knowledge.semantic import (
    DraftApplicabilityContext,
    DraftCase,
    DraftCaseStep,
    DraftCitation,
    DraftContextAssertion,
    DraftGuidance,
    DraftReference,
    DraftTypedContext,
    SemanticDraftBundle,
)
from sedna.knowledge.semantic.materialize import materialize_bundle


def _prepared_source() -> PreparedSource:
    document = parse_markdown(
        "material-source",
        "raw_src/material.md",
        """# Discovery

The service exposes HTTP on port 80.

# Follow-up

Inspect the HTTP response before selecting an action.
""",
    )
    manifest = DocumentManifest(
        source_id="material-source",
        path="raw_src/material.md",
        sha256="a" * 64,
        title="Materialization notes",
        language="en",
        document_type=DocumentType.MACHINE_WALKTHROUGH,
        knowledge_role=KnowledgeRole.CASE_STUDY,
        quality=SourceQuality.COMPLETE,
        parser_profile="writeup",
        ingestion_status=IngestionStatus.ACCEPTED,
        extraction=ExtractionMetadata(
            schema_version="1",
            parser_id="markdown-it",
            parser_version="1",
            extractor_id="deterministic-foundation",
            extractor_version="1",
        ),
    )
    return PreparedSource(manifest=manifest, document=document, segments=segment_document(document))


def _call_metadata(*, model: str = "host-model") -> SemanticCallMetadata:
    return SemanticCallMetadata(
        purpose="sedna.semantic.extract",
        provider="host-provider",
        model=model,
        agent_id="agent-1",
        input_tokens=10,
        output_tokens=20,
    )


def _reference(
    *, local_id: str = "http-reference", indexes: tuple[int, ...] = (0,)
) -> DraftReference:
    return DraftReference(
        draft_type="reference",
        local_id=local_id,
        artifact_type="methodology",
        subject="HTTP service inspection",
        statement="Inspect the HTTP response before selecting an action.",
        origin="explicit",
        applicable_situations=("service discovered", "web endpoint available"),
        prerequisites=("reachable service", "network access"),
        citations=(DraftCitation(segment_indexes=indexes),),
    )


def _bundle(*artifacts: object, ignored: tuple[int, ...] = ()) -> SemanticDraftBundle:
    return SemanticDraftBundle(artifacts=artifacts, ignored_segment_indexes=ignored)  # type: ignore[arg-type]


def test_materialize_resolves_exact_segment_provenance_and_host_metadata():
    """Would fail if source references used draft IDs or lost exact segment lines."""
    artifact = materialize_bundle(
        _prepared_source(),
        _bundle(_reference(indexes=(1,)), ignored=(0,)),
        _call_metadata(),
        VerificationStatus.VERIFIED,
    )[0]

    source_ref = artifact.source_refs[0]
    assert source_ref.source_id == "material-source"
    assert source_ref.path == "raw_src/material.md"
    assert (source_ref.location.start_line, source_ref.location.end_line) == (5, 7)
    assert source_ref.location.section == "Follow-up"
    assert artifact.extraction.parser_id == "markdown-it"
    assert artifact.extraction.extractor_id == "sedna-semantic-extractor"
    assert artifact.extraction.model_id == "host-model"


def test_materialize_rejects_out_of_range_or_unaccounted_for_segments():
    """Would fail if malformed citations or silently skipped input reached canonical artifacts."""
    prepared = _prepared_source()

    with pytest.raises(ValueError, match="input segment range"):
        materialize_bundle(
            prepared,
            _bundle(_reference(indexes=(2,)), ignored=(0,)),
            _call_metadata(),
            VerificationStatus.VERIFIED,
        )
    with pytest.raises(ValueError, match="cited or explicitly ignored"):
        materialize_bundle(
            prepared,
            _bundle(_reference(indexes=(0,))),
            _call_metadata(),
            VerificationStatus.VERIFIED,
        )


def test_canonical_id_ignores_draft_local_id_model_and_adjudication():
    """Would fail if runtime or draft-local data affected reproducible canonical identity."""
    prepared = _prepared_source()
    first = materialize_bundle(
        prepared,
        _bundle(_reference(local_id="first"), ignored=(1,)),
        _call_metadata(model="model-a"),
        VerificationStatus.EXTRACTED,
    )
    second = materialize_bundle(
        prepared,
        _bundle(_reference(local_id="renamed"), ignored=(1,)),
        _call_metadata(model="model-b"),
        VerificationStatus.VERIFIED,
    )

    assert first[0].artifact_id == second[0].artifact_id
    assert first[0].assessment.verification_status is VerificationStatus.EXTRACTED
    assert second[0].assessment.verification_status is VerificationStatus.VERIFIED


def test_materialize_deduplicates_only_identical_normalized_content_and_citations():
    """Would fail if different source evidence was collapsed with otherwise equal content."""
    artifacts = materialize_bundle(
        _prepared_source(),
        _bundle(
            _reference(local_id="one", indexes=(0,)),
            _reference(local_id="same", indexes=(0,)),
            _reference(local_id="different-evidence", indexes=(1,)),
        ),
        _call_metadata(),
        VerificationStatus.VERIFIED,
    )

    assert len(artifacts) == 2
    assert len({artifact.artifact_id for artifact in artifacts}) == 2
    assert tuple(artifact.artifact_id for artifact in artifacts) == tuple(
        sorted(artifact.artifact_id for artifact in artifacts)
    )


def test_materialize_resolves_context_and_preserves_case_step_order():
    """Would fail if context citations were inherited or step chronology was sorted."""
    context = DraftApplicabilityContext(
        typed_context=DraftTypedContext(
            os_family=DraftContextAssertion(
                value="linux",
                relation="observed",
                origin="explicit",
                confidence=1.0,
                citations=(DraftCitation(segment_indexes=(0,)),),
            )
        )
    )
    case = DraftCase(
        draft_type="case",
        local_id="http-case",
        artifact_type="case",
        knowledge_role="case_study",
        title="HTTP inspection",
        starting_access="network access",
        outcome="The service was inspected.",
        source_quality="complete",
        origin="explicit",
        applicability=context,
        citations=(DraftCitation(segment_indexes=(0,)),),
        steps=(
            DraftCaseStep(
                local_id="first-step",
                ordinal=1,
                state_before={"access": "network access"},
                observations=("HTTP was reachable",),
                hypotheses=(),
                selected_action={"intent": "inspect_http"},
                evidence=(),
                state_after={"access": "network access"},
                origin="explicit",
                applicability=context,
                citations=(DraftCitation(segment_indexes=(1,)),),
            ),
            DraftCaseStep(
                local_id="second-step",
                ordinal=2,
                state_before={"access": "network access"},
                observations=("Response headers were available",),
                hypotheses=(),
                selected_action={"intent": "interpret_http_response"},
                evidence=(),
                state_after={"access": "network access"},
                origin="inferred",
                applicability=context,
                citations=(DraftCitation(segment_indexes=(0,)),),
            ),
        ),
    )
    materialized = materialize_bundle(
        _prepared_source(),
        _bundle(case),
        _call_metadata(),
        VerificationStatus.CORROBORATED,
    )[0]

    assert tuple(step.ordinal for step in materialized.steps) == (1, 2)  # type: ignore[union-attr]
    assert (
        materialized.applicability.typed_context.os_family.source_refs[0].location.start_line == 1
    )  # type: ignore[union-attr]
    assert materialized.steps[0].source_refs[0].location.start_line == 5  # type: ignore[union-attr]
    assert materialized.assessment.verification_status is VerificationStatus.CORROBORATED  # type: ignore[union-attr]
    rematerialized = materialize_bundle(
        _prepared_source(),
        _bundle(case),
        _call_metadata(model="other-host-model"),
        VerificationStatus.EXTRACTED,
    )[0]
    assert materialized.case_id == rematerialized.case_id  # type: ignore[union-attr]


def test_materialize_sorts_reference_and_guidance_sets_without_rejecting_case_local_examples():
    """Would fail if set-like fields were unstable or case-local examples were rejected."""
    guidance = DraftGuidance(
        draft_type="guidance",
        local_id="guidance",
        origin="explicit",
        citations=(DraftCitation(segment_indexes=(0,)),),
        trigger_observations=("web endpoint available", "service discovered"),
        rationale="Use the observed service before choosing an action.",
        action_intent="inspect_http",
        prerequisites=("network access", "reachable service"),
    )
    reference = _reference(local_id="credential-example")
    reference = reference.model_copy(
        update={"statement": "Treat password: p@ss:word as a case-local historical example."}
    )

    artifacts = materialize_bundle(
        _prepared_source(),
        _bundle(reference, guidance, ignored=(1,)),
        _call_metadata(),
        VerificationStatus.VERIFIED,
    )

    reference_artifact = next(
        artifact for artifact in artifacts if hasattr(artifact, "artifact_id")
    )
    guidance_artifact = next(artifact for artifact in artifacts if hasattr(artifact, "rule_id"))
    assert reference_artifact.applicable_situations == (
        "service discovered",
        "web endpoint available",
    )
    assert guidance_artifact.trigger_observations == (
        "service discovered",
        "web endpoint available",
    )
