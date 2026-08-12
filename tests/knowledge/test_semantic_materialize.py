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
    DraftExecutionExample,
    DraftGuidance,
    DraftReference,
    DraftTypedContext,
    SemanticDraftBundle,
)
from sedna.knowledge.semantic.materialize import (
    MaterializedSemanticContent,
    materialize_bundle,
    materialize_semantic_content,
)


def _prepared_source(
    *,
    path: str = "raw_src/material.md",
    source_id: str = "material-source",
    sha256: str = "a" * 64,
) -> PreparedSource:
    document = parse_markdown(
        source_id,
        path,
        """# Discovery

The service exposes HTTP on port 80.

# Follow-up

Inspect the HTTP response before selecting an action.
""",
    )
    manifest = DocumentManifest(
        source_id=source_id,
        path=path,
        sha256=sha256,
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


def _bundle(
    *artifacts: object, ignored: tuple[int, ...] = (), execution: object = ()
) -> SemanticDraftBundle:
    if isinstance(execution, DraftExecutionExample):
        execution = (execution,)
    return SemanticDraftBundle(
        artifacts=artifacts,  # type: ignore[arg-type]
        ignored_segment_indexes=ignored,
        execution_examples=execution,  # type: ignore[arg-type]
    )


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


def test_byte_identical_sources_share_content_derived_independence_group():
    first = materialize_bundle(
        _prepared_source(path="raw_src/copy-a.md", source_id="copy-a", sha256="b" * 64),
        _bundle(_reference(), ignored=(1,)),
        _call_metadata(),
        VerificationStatus.VERIFIED,
    )[0]
    second = materialize_bundle(
        _prepared_source(path="raw_src/copy-b.md", source_id="copy-b", sha256="b" * 64),
        _bundle(_reference(), ignored=(1,)),
        _call_metadata(),
        VerificationStatus.VERIFIED,
    )[0]

    assert first.source_refs[0].source_id == "copy-a"
    assert second.source_refs[0].source_id == "copy-b"
    assert first.assessment.independence_group == second.assessment.independence_group == "b" * 64


@pytest.mark.parametrize(
    "unsafe_path",
    (
        "raw_src/HTB{path_final}.md",
        "raw_src/HTB%2526%2523123%253Bpath_final%2526%2523125%253B.md",
        "raw_src/Root flag abcdef0123456789abcdef0123456789.md",
        "raw_src/User flag 0123456789abcdef0123456789abcdef.md",
    ),
)
def test_materialize_rejects_final_flag_material_from_foundation_paths(unsafe_path: str):
    """Would fail if a valid raw provenance path leaked into a canonical SourceRef."""
    prepared = _prepared_source(path=unsafe_path)

    with pytest.raises(ValueError, match="final flag"):
        materialize_bundle(
            prepared,
            _bundle(_reference(), ignored=(1,)),
            _call_metadata(),
            VerificationStatus.VERIFIED,
        )


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


def test_reference_observation_time_is_retained_but_excluded_from_its_canonical_id():
    """Would fail if a source observation timestamp changed canonical identity."""
    prepared = _prepared_source()
    first_reference = _reference(local_id="first").model_copy(update={"observed_at": "2024-01-01"})
    second_reference = _reference(local_id="second").model_copy(
        update={"observed_at": "2025-02-02"}
    )

    first = materialize_bundle(
        prepared,
        _bundle(first_reference, ignored=(1,)),
        _call_metadata(),
        VerificationStatus.VERIFIED,
    )[0]
    second = materialize_bundle(
        prepared,
        _bundle(second_reference, ignored=(1,)),
        _call_metadata(),
        VerificationStatus.VERIFIED,
    )[0]

    assert first.artifact_id == second.artifact_id
    assert first.observed_at == "2024-01-01"
    assert second.observed_at == "2025-02-02"


def test_duplicate_timestamp_variants_collapse_to_the_earliest_known_observation():
    """Would fail if timestamp-free duplicate identity still emitted duplicate artifacts."""
    older = _reference(local_id="older").model_copy(update={"observed_at": "2024-01-01"})
    newer = _reference(local_id="newer").model_copy(update={"observed_at": "2025-02-02"})
    unknown = _reference(local_id="unknown").model_copy(update={"observed_at": None})

    forward = materialize_bundle(
        _prepared_source(),
        _bundle(newer, unknown, older, ignored=(1,)),
        _call_metadata(),
        VerificationStatus.VERIFIED,
    )
    reverse = materialize_bundle(
        _prepared_source(),
        _bundle(older, unknown, newer, ignored=(1,)),
        _call_metadata(),
        VerificationStatus.VERIFIED,
    )

    assert len(forward) == len(reverse) == 1
    assert forward[0].artifact_id == reverse[0].artifact_id
    assert forward[0].observed_at == reverse[0].observed_at == "2024-01-01"


def test_duplicate_references_without_observation_times_retain_none():
    """Would fail if a duplicate merge invented an observation timestamp."""
    artifacts = materialize_bundle(
        _prepared_source(),
        _bundle(_reference(local_id="one"), _reference(local_id="two"), ignored=(1,)),
        _call_metadata(),
        VerificationStatus.VERIFIED,
    )

    assert len(artifacts) == 1
    assert artifacts[0].observed_at is None


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


def _draft_example(
    local_id: str = "example-1", parent: str = "http-reference", **overrides
) -> DraftExecutionExample:
    payload = {
        "local_id": local_id,
        "parent_local_id": parent,
        "command_template": "curl -i {{target}}",
        "placeholders": (
            {
                "name": "target",
                "kind": "target",
                "binding_policy": "authorized_scope",
                "role": "authorized HTTP target",
            },
        ),
        "capability_hint": "http.inspect",
        "purpose": "Inspect HTTP response metadata.",
        "observed_role": "Gathered response evidence in the source case.",
        "prerequisites": (
            {
                "statement": "An authorized HTTP target is available.",
                "citations": (DraftCitation(segment_indexes=(0,)),),
            },
        ),
        "platform_constraints": (
            {
                "dimension": "execution_environment",
                "relation": "compatible",
                "value": "network-reachable HTTP service",
                "citations": (DraftCitation(segment_indexes=(1,)),),
            },
        ),
        "citations": (DraftCitation(segment_indexes=(1,)),),
    }
    payload.update(overrides)
    return DraftExecutionExample.model_validate(payload)


def _content_with_example() -> MaterializedSemanticContent:
    return materialize_semantic_content(
        _prepared_source(),
        _bundle(_reference(), execution=DraftExecutionExample.model_validate(_draft_example())),
        _call_metadata(),
        VerificationStatus.VERIFIED,
    )


def test_execution_example_ids_are_deterministic_and_content_scoped():
    first = _content_with_example()
    second = _content_with_example()
    assert first.execution_examples == second.execution_examples
    assert first.execution_examples[0].example_id.startswith("execution-example-")


def test_execution_example_parent_is_canonical_and_draft_ids_never_cross():
    content = _content_with_example()
    reference = content.artifacts[0]
    assert content.execution_examples[0].parent_artifact_id == reference.artifact_id
    assert reference.artifact_id != "http-reference"


def test_changing_example_content_changes_the_id():
    base = _content_with_example().execution_examples[0]
    changed = materialize_semantic_content(
        _prepared_source(),
        _bundle(
            _reference(),
            execution=DraftExecutionExample.model_validate(
                _draft_example(observed_role="A different observed role.")
            ),
        ),
        _call_metadata(),
        VerificationStatus.VERIFIED,
    ).execution_examples[0]
    assert changed.example_id != base.example_id


def test_draft_final_flag_in_command_is_rejected_at_materialization():
    with pytest.raises(Exception, match="flag"):
        materialize_semantic_content(
            _prepared_source(),
            _bundle(
                _reference(),
                execution=DraftExecutionExample.model_validate(
                    _draft_example(command_template="cat {{path}} && echo HTB{fake}")
                ),
            ),
            _call_metadata(),
            VerificationStatus.VERIFIED,
        )
