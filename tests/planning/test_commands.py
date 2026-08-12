"""Structured command-suggestion contracts bound to authorized engagement scope."""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from sedna import planning
from sedna.engagement import ScopeReference
from sedna.knowledge.schema.common import (
    ExtractionMetadata,
    SourceLocation,
    SourceRef,
)
from sedna.knowledge.schema.execution import ExecutionExample, ExecutionPlaceholder
from sedna.planning.commands import (
    CommandBinding,
    CommandOrigin,
    CommandSuggestionDraft,
    validate_command_suggestion,
)
from sedna.planning.models import SecretReference


def test_command_contracts_are_publicly_exported() -> None:
    assert planning.CommandSuggestionDraft is CommandSuggestionDraft
    assert planning.validate_command_suggestion is validate_command_suggestion


def _source_example() -> ExecutionExample:
    return ExecutionExample(
        example_id="execution-example-probe",
        parent_artifact_id="reference-http",
        command_template="curl -i {{target}}",
        placeholders=(
            ExecutionPlaceholder(
                name="target",
                kind="target",
                binding_policy="authorized_scope",
                role="authorized HTTP target",
            ),
        ),
        capability_hint="http.inspect",
        purpose="Inspect HTTP response metadata.",
        observed_role="Gathered response evidence in the source case.",
        source_refs=(
            SourceRef(
                source_id="source-http",
                path="raw_src/reference.md",
                location=SourceLocation(start_line=1, end_line=1, section="HTTP"),
            ),
        ),
        extraction=ExtractionMetadata(
            schema_version="1",
            parser_id="test-parser",
            parser_version="1",
            extractor_id="test-extractor",
            extractor_version="1",
        ),
    )


def test_credential_ref_requires_an_explicit_current_secret_reference() -> None:
    event_id = UUID("00000000-0000-0000-0000-000000000001")
    secret = SecretReference(
        label="engagement_password",
        evidence_id="evidence-sha256-" + "a" * 64,
        value_sha256="b" * 64,
        event_ids=(event_id,),
    )
    draft = CommandSuggestionDraft(
        origin=CommandOrigin.MODEL_GENERATED,
        command_template="curl -u {{password}} {{target}}",
        placeholder_kinds=("credential_ref", "target"),
        bindings=(
            CommandBinding(
                placeholder_name="password",
                source="secret_reference",
                reference_id="missing-secret",
            ),
            CommandBinding(
                placeholder_name="target",
                source="scope_reference",
                reference_id="scope-0123456789abcdef0123456789abcdef",
            ),
        ),
    )
    with pytest.raises(ValueError, match="command_secret_reference_not_current"):
        validate_command_suggestion(
            draft,
            scope_references=(
                ScopeReference(
                    reference_id="scope-0123456789abcdef0123456789abcdef",
                    kind="exact_target",
                    value="192.0.2.44",
                ),
            ),
            secret_references=(secret,),
            execution_examples=(),
        )


def test_source_case_credential_never_binds_to_engagement_secret() -> None:
    event_id = UUID("00000000-0000-0000-0000-000000000001")
    secret = SecretReference(
        label="source_password",
        evidence_id="evidence-sha256-" + "a" * 64,
        value_sha256="b" * 64,
        event_ids=(event_id,),
    )
    draft = CommandSuggestionDraft(
        origin=CommandOrigin.MODEL_GENERATED,
        command_template="curl -u {{source_password}} {{target}}",
        placeholder_kinds=("source_case_credential", "target"),
        bindings=(
            CommandBinding(
                placeholder_name="source_password",
                source="secret_reference",
                reference_id="source_password",
            ),
            CommandBinding(
                placeholder_name="target",
                source="scope_reference",
                reference_id="scope-0123456789abcdef0123456789abcdef",
            ),
        ),
    )
    with pytest.raises(ValueError, match="command_source_case_credential_binding"):
        validate_command_suggestion(
            draft,
            scope_references=(
                ScopeReference(
                    reference_id="scope-0123456789abcdef0123456789abcdef",
                    kind="exact_target",
                    value="192.0.2.44",
                ),
            ),
            secret_references=(secret,),
            execution_examples=(),
        )


def test_source_example_requires_exact_canonical_template_match() -> None:
    with pytest.raises(ValueError, match="command_source_example_not_exact"):
        validate_command_suggestion(
            CommandSuggestionDraft(
                origin=CommandOrigin.SOURCE_EXAMPLE,
                source_example_id="execution-example-probe",
                command_template="curl -s {{target}}",
                placeholder_kinds=("target",),
                bindings=(
                    CommandBinding(
                        placeholder_name="target",
                        source="scope_reference",
                        reference_id="scope-0123456789abcdef0123456789abcdef",
                    ),
                ),
            ),
            scope_references=(
                ScopeReference(
                    reference_id="scope-0123456789abcdef0123456789abcdef",
                    kind="exact_target",
                    value="192.0.2.44",
                ),
            ),
            secret_references=(),
            execution_examples=(_source_example(),),
        )


def test_command_draft_rejects_raw_network_target_literals() -> None:
    for template in (
        "curl -i 10.10.10.10",
        "curl -i 10.10.10.0/24",
        "curl -i http://10.10.10.10/",
        "curl -i 2001:db8::1",
        "curl -i box.htb",
    ):
        with pytest.raises(ValidationError, match="command_raw_target_literal"):
            CommandSuggestionDraft(
                origin=CommandOrigin.MODEL_GENERATED,
                command_template=template,
                placeholder_kinds=(),
            )


def test_command_draft_rejects_runtime_port_literal() -> None:
    with pytest.raises(ValidationError, match="command_runtime_value_literal"):
        CommandSuggestionDraft(
            origin=CommandOrigin.MODEL_GENERATED,
            command_template="nc -vz {{target}} 22",
            placeholder_kinds=("target",),
        )


def test_command_draft_rejects_malformed_or_undeclared_placeholder_tokens() -> None:
    with pytest.raises(ValidationError, match="command_placeholder_token_invalid"):
        CommandSuggestionDraft(
            origin=CommandOrigin.MODEL_GENERATED,
            command_template="curl -i {{Target}}",
            placeholder_kinds=(),
        )


def test_target_requires_one_authorized_scope_reference_binding() -> None:
    scope = ScopeReference(
        reference_id="scope-0123456789abcdef0123456789abcdef",
        kind="exact_target",
        value="192.0.2.44",
    )
    unbound = CommandSuggestionDraft(
        origin=CommandOrigin.MODEL_GENERATED,
        command_template="curl -i {{target}}",
        placeholder_kinds=("target",),
    )
    with pytest.raises(ValueError, match="command_target_binding_required"):
        validate_command_suggestion(
            unbound,
            scope_references=(scope,),
            secret_references=(),
            execution_examples=(),
        )

    unauthorized = unbound.model_copy(
        update={
            "bindings": (
                CommandBinding(
                    placeholder_name="target",
                    source="scope_reference",
                    reference_id="scope-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                ),
            ),
        }
    )
    with pytest.raises(ValueError, match="command_scope_reference_not_authorized"):
        validate_command_suggestion(
            unauthorized,
            scope_references=(scope,),
            secret_references=(),
            execution_examples=(),
        )


def test_command_draft_rejects_duplicate_placeholder_bindings() -> None:
    with pytest.raises(ValidationError, match="command_bindings_not_unique"):
        CommandSuggestionDraft(
            origin=CommandOrigin.MODEL_GENERATED,
            command_template="curl -i {{target}}",
            placeholder_kinds=("target",),
            bindings=(
                CommandBinding(
                    placeholder_name="target",
                    source="scope_reference",
                    reference_id="scope-0123456789abcdef0123456789abcdef",
                ),
                CommandBinding(
                    placeholder_name="target",
                    source="scope_reference",
                    reference_id="scope-0123456789abcdef0123456789abcdef",
                ),
            ),
        )


def test_model_generated_target_binding_renders_authorized_scope_preview() -> None:
    scope = ScopeReference(
        reference_id="scope-0123456789abcdef0123456789abcdef",
        kind="exact_target",
        value="192.0.2.44",
    )
    draft = CommandSuggestionDraft(
        origin=CommandOrigin.MODEL_GENERATED,
        command_template="curl -i {{target}}",
        placeholder_kinds=("target",),
        bindings=(
            CommandBinding(
                placeholder_name="target",
                source="scope_reference",
                reference_id=scope.reference_id,
            ),
        ),
    )

    suggestion = validate_command_suggestion(
        draft,
        scope_references=(scope,),
        secret_references=(),
        execution_examples=(),
    )

    assert suggestion.rendered_preview == "curl -i 192.0.2.44"
    assert suggestion.requires_validation is True
    assert suggestion.source_example_id is None
    assert isinstance(suggestion.command_id, UUID)
