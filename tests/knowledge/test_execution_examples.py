"""Strict canonical and draft execution-example contracts for M6B planning."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sedna.knowledge.schema.common import (
    ExtractionMetadata,
    SourceLocation,
    SourceRef,
)
from sedna.knowledge.schema.context import ApplicabilityContext
from sedna.knowledge.schema.execution import (
    ExecutionCondition,
    ExecutionExample,
    ExecutionPlaceholder,
    ExecutionPlatformConstraint,
    PlaceholderBindingPolicy,
)
from sedna.knowledge.semantic.drafts import (
    DraftApplicabilityContext,
    DraftCitation,
    DraftExecutionExample,
    DraftExecutionPlaceholder,
    SemanticDraftBundle,
)


def source_ref() -> SourceRef:
    return SourceRef(
        source_id="source-http",
        path="docs/http.md",
        location=SourceLocation(page=1),
    )


def extraction_metadata() -> ExtractionMetadata:
    return ExtractionMetadata(
        schema_version="1",
        parser_id="markdown-parser",
        parser_version="1",
        extractor_id="semantic-extractor",
        extractor_version="1",
        model_id="fixture",
        model_version="1",
    )


def example_payload(**overrides) -> dict:
    payload = {
        "schema_version": "1",
        "example_id": "execution-example-http-probe",
        "parent_artifact_id": "case_step-http-enumeration",
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
        "observed_role": "This invocation gathered response evidence in the source case.",
        "prerequisites": (
            {
                "statement": "An authorized HTTP target is available.",
                "source_refs": (source_ref(),),
            },
        ),
        "applicability": ApplicabilityContext(),
        "platform_constraints": (
            {
                "dimension": "execution_environment",
                "relation": "compatible",
                "value": "network-reachable HTTP service",
                "source_refs": (source_ref(),),
            },
        ),
        "source_refs": (source_ref(),),
        "extraction": extraction_metadata(),
        "requires_validation": True,
    }
    payload.update(overrides)
    return payload


# -- canonical execution examples ------------------------------------------


def test_execution_example_requires_typed_template_placeholders() -> None:
    example = ExecutionExample.model_validate(example_payload())

    assert example.command_template == "curl -i {{target}}"
    assert example.placeholders[0].binding_policy == "authorized_scope"
    assert example.requires_validation is True


def test_source_case_credential_can_never_auto_bind() -> None:
    with pytest.raises(ValidationError, match="source-case credentials"):
        ExecutionPlaceholder(
            name="source_password",
            kind="source_case_credential",
            binding_policy="authorized_scope",
            role="password observed only in the source case",
        )
    ok = ExecutionPlaceholder(
        name="source_password",
        kind="source_case_credential",
        binding_policy="never_auto_bind",
        role="password observed only in the source case",
    )
    assert ok.binding_policy == PlaceholderBindingPolicy.NEVER_AUTO_BIND


def test_target_placeholder_requires_authorized_scope() -> None:
    with pytest.raises(ValidationError, match="authorized_scope"):
        ExecutionPlaceholder(
            name="target",
            kind="target",
            binding_policy="host_supplied",
            role="authorized HTTP target",
        )


def test_execution_example_is_frozen_and_rejects_missing_validation() -> None:
    example = ExecutionExample.model_validate(example_payload())
    with pytest.raises(ValidationError):
        example.placeholders = ()
    with pytest.raises(ValidationError, match="requires_validation"):
        ExecutionExample.model_validate(example_payload(requires_validation=False))


def test_placeholders_are_sorted_and_template_coverage_is_exact() -> None:
    example = ExecutionExample.model_validate(
        example_payload(
            command_template="curl {{host}} {{path}}",
            placeholders=(
                {
                    "name": "path",
                    "kind": "path",
                    "binding_policy": "host_supplied",
                    "role": "URL path",
                },
                {
                    "name": "host",
                    "kind": "target",
                    "binding_policy": "authorized_scope",
                    "role": "authorized host",
                },
            ),
        )
    )
    assert [placeholder.name for placeholder in example.placeholders] == [
        "host",
        "path",
    ]
    with pytest.raises(ValidationError, match="placeholder"):
        ExecutionExample.model_validate(example_payload(command_template="curl {{undeclared}}"))
    with pytest.raises(ValidationError, match="placeholder"):
        ExecutionExample.model_validate(
            example_payload(
                command_template="curl {{target}}",
                placeholders=(
                    {
                        "name": "target",
                        "kind": "target",
                        "binding_policy": "authorized_scope",
                        "role": "authorized HTTP target",
                    },
                    {
                        "name": "unused",
                        "kind": "value",
                        "binding_policy": "host_supplied",
                        "role": "never referenced",
                    },
                ),
            )
        )


def test_prerequisite_and_platform_constraints_require_source_refs() -> None:
    with pytest.raises(ValidationError, match="source"):
        ExecutionExample.model_validate(
            example_payload(prerequisites=({"statement": "No source citation here."},))
        )
    with pytest.raises(ValidationError, match="source"):
        ExecutionExample.model_validate(
            example_payload(
                platform_constraints=(
                    {
                        "dimension": "os_family",
                        "relation": "required",
                        "value": "linux",
                    },
                )
            )
        )


def test_execution_condition_and_platform_constraint_shape() -> None:
    condition = ExecutionCondition(
        statement="An authorized HTTP target is available.",
        source_refs=(source_ref(),),
    )
    constraint = ExecutionPlatformConstraint(
        dimension="cpu_architecture",
        relation="compatible",
        value="x86_64",
        source_refs=(source_ref(),),
    )
    assert condition.source_refs[0].source_id == "source-http"
    assert constraint.dimension == "cpu_architecture"


def test_asserted_platform_hidden_only_in_prose_is_rejected() -> None:
    with pytest.raises(ValidationError, match="platform constraint"):
        ExecutionExample.model_validate(
            example_payload(
                purpose="Run this on linux x86_64 hosts.",
                platform_constraints=(),
            )
        )
    # an explicit matching constraint satisfies the requirement
    ok = ExecutionExample.model_validate(
        example_payload(
            purpose="Run this on linux hosts.",
            platform_constraints=(
                {
                    "dimension": "os_family",
                    "relation": "required",
                    "value": "linux",
                    "source_refs": (source_ref(),),
                },
            ),
        )
    )
    assert ok.platform_constraints[0].value == "linux"


def test_final_flag_material_is_rejected_in_command_template() -> None:
    with pytest.raises(ValidationError, match="flag"):
        ExecutionExample.model_validate(
            example_payload(command_template="cat {{path}} && echo HTB{fake}")
        )


# -- draft execution examples ----------------------------------------------


def _draft_example(local_id: str, parent_local_id: str, **overrides) -> dict:
    payload = {
        "local_id": local_id,
        "parent_local_id": parent_local_id,
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
        "applicability": DraftApplicabilityContext(),
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
        "citations": (DraftCitation(segment_indexes=(2,)),),
        "requires_validation": True,
    }
    payload.update(overrides)
    return payload


def test_draft_parent_may_reference_reference_or_case_step() -> None:
    bundle = SemanticDraftBundle(
        artifacts=(
            {
                "draft_type": "reference",
                "local_id": "reference-http",
                "origin": "explicit",
                "artifact_type": "concept",
                "subject": "HTTP",
                "statement": "HTTP services expose metadata.",
                "citations": (DraftCitation(segment_indexes=(0,)),),
            },
            {
                "draft_type": "case",
                "local_id": "case-orion",
                "origin": "explicit",
                "knowledge_role": "case_study",
                "title": "Orion",
                "starting_access": "none",
                "steps": (
                    {
                        "artifact_type": "case_step",
                        "local_id": "case_step-http-enumeration",
                        "ordinal": 1,
                        "state_before": {"access": "unauthenticated"},
                        "observations": ("HTTP open",),
                        "hypotheses": (),
                        "selected_action": {"intent": "enumerate exposed services"},
                        "evidence": (),
                        "state_after": {"access": "enumeration_done"},
                        "origin": "explicit",
                        "citations": (DraftCitation(segment_indexes=(1,)),),
                    },
                ),
                "outcome": "rooted",
                "source_quality": "complete",
                "citations": (DraftCitation(segment_indexes=(1,)),),
            },
        ),
        execution_examples=(
            DraftExecutionExample.model_validate(
                _draft_example(
                    "example-a",
                    "reference-http",
                )
            ),
            DraftExecutionExample.model_validate(
                _draft_example(
                    "example-b",
                    "case_step-http-enumeration",
                )
            ),
        ),
    )
    assert len(bundle.execution_examples) == 2


@pytest.mark.parametrize(
    "parent",
    ["case-orion", "missing-local-id", "example-a"],
)
def test_draft_parent_rejects_case_guidance_missing_or_example(
    parent: str,
) -> None:
    with pytest.raises(ValidationError, match="parent"):
        SemanticDraftBundle(
            artifacts=(
                {
                    "draft_type": "reference",
                    "local_id": "reference-http",
                    "origin": "explicit",
                    "artifact_type": "concept",
                    "subject": "HTTP",
                    "statement": "HTTP services expose metadata.",
                    "citations": (DraftCitation(segment_indexes=(0,)),),
                },
            ),
            execution_examples=(
                DraftExecutionExample.model_validate(_draft_example("example-a", parent)),
            ),
        )


def test_draft_local_ids_are_unique_across_artifacts_and_examples() -> None:
    with pytest.raises(ValidationError, match="unique"):
        SemanticDraftBundle(
            artifacts=(
                {
                    "draft_type": "reference",
                    "local_id": "shared-id",
                    "origin": "explicit",
                    "artifact_type": "concept",
                    "subject": "HTTP",
                    "statement": "HTTP services expose metadata.",
                    "citations": (DraftCitation(segment_indexes=(0,)),),
                },
            ),
            execution_examples=(
                DraftExecutionExample.model_validate(_draft_example("shared-id", "reference-http")),
            ),
        )


def test_draft_execution_example_is_frozen_and_requires_validation() -> None:
    example = DraftExecutionExample.model_validate(_draft_example("example-a", "reference-http"))
    with pytest.raises(ValidationError):
        example.placeholders = ()
    with pytest.raises(ValidationError, match="requires_validation"):
        DraftExecutionExample.model_validate(
            _draft_example(
                "example-a",
                "reference-http",
                requires_validation=False,
            )
        )


def test_draft_execution_placeholder_credential_policy() -> None:
    with pytest.raises(ValidationError, match="source-case credentials"):
        DraftExecutionPlaceholder(
            name="source_password",
            kind="source_case_credential",
            binding_policy="authorized_scope",
            role="password observed only in the source case",
        )
