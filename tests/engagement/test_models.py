from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from sedna.engagement import (
    CONTROL_TOOL_NAMES,
    CONTROL_TOOL_POLICY_VERSION,
    ENGAGEMENT_MANIFEST_SCHEMA_VERSION,
    EVENT_ENVELOPE_SCHEMA_VERSION,
    MAX_IN_FLIGHT_CALLS,
    MAX_REQUIRED_PROOFS,
    CaptureLimitation,
    ClosureRequestedPayload,
    ConfinedRelativePath,
    ControlToolInvokedPayload,
    CorrelationKind,
    EngagementManifest,
    EngagementOpenedPayload,
    EngagementSnapshot,
    EngagementState,
    EngagementStatus,
    EvidenceCaptureFailedPayload,
    EvidenceReference,
    ExecutionLaneKey,
    HostAdaptedCommandRecord,
    HostKind,
    JournalEventDraft,
    JournalRevision,
    RecoveryWarningPayload,
    ScopeChangedPayload,
    SessionFinalizedPayload,
    SystemCorrelation,
    ToolCallCompletedPayload,
    ToolCallStartedPayload,
    ToolCorrelation,
    sanitize_host_arguments,
    scope_references,
)
from sedna.knowledge.retrieval import AuthorizationScope, AuthorizationState


def test_manifest_requires_name_objective_utc_and_authorized_scope(manifest) -> None:
    assert manifest.schema_version == ENGAGEMENT_MANIFEST_SCHEMA_VERSION
    assert manifest.display_name == "HTB-Orion"
    assert manifest.created_at.utcoffset().total_seconds() == 0

    with pytest.raises(ValidationError):
        EngagementManifest.model_validate(
            {**manifest.model_dump(mode="json"), "display_name": "   "}
        )
    with pytest.raises(ValidationError):
        EngagementManifest.model_validate(
            {
                **manifest.model_dump(mode="json"),
                "initial_scope": AuthorizationScope(state=AuthorizationState.UNKNOWN),
            }
        )
    with pytest.raises(ValidationError):
        EngagementManifest.model_validate(
            {
                **manifest.model_dump(mode="json"),
                "created_at": datetime(2026, 8, 11, 12, 30).isoformat(),
            }
        )


def test_manifest_normalizes_text_and_bounds_unique_proofs(manifest) -> None:
    normalized = EngagementManifest.model_validate(
        {
            **manifest.model_dump(mode="json"),
            "display_name": "  HTB-Orion\n lab ",
            "initial_objective": "  Obtain\n access  ",
        }
    )
    assert normalized.display_name == "HTB-Orion lab"
    assert normalized.initial_objective == "Obtain access"

    base = manifest.model_dump(mode="json")
    base["required_proofs"] = [
        {"proof_id": f"proof-{index}", "kind": "custom", "description": "proof"}
        for index in range(MAX_REQUIRED_PROOFS)
    ]
    assert len(EngagementManifest.model_validate(base).required_proofs) == MAX_REQUIRED_PROOFS
    base["required_proofs"].append(
        {"proof_id": "proof-over", "kind": "custom", "description": "proof"}
    )
    with pytest.raises(ValidationError):
        EngagementManifest.model_validate(base)


def test_execution_lane_uses_explicit_root_task_fallback() -> None:
    lane = ExecutionLaneKey.from_host(
        host_kind=HostKind.HADES,
        session_id="session-a",
        task_id="",
    )
    assert lane.task_id == "root:session-a"
    assert lane.stable_key.startswith("lane-")


def test_scope_references_are_stable_and_normalized(authorized_scope) -> None:
    first = scope_references(authorized_scope)
    second = scope_references(authorized_scope)

    assert first == second
    assert [(item.kind, item.value) for item in first] == [("exact_target", "192.0.2.44")]
    assert first[0].reference_id.startswith("scope-")


def test_proof_requirements_are_explicit_unique_and_may_be_empty(manifest) -> None:
    assert [item.proof_id for item in manifest.required_proofs] == ["user-flag", "root-flag"]

    no_automatic_close = manifest.model_copy(update={"required_proofs": ()})
    assert no_automatic_close.required_proofs == ()

    duplicate = manifest.model_dump(mode="json")
    duplicate["required_proofs"].append(duplicate["required_proofs"][0])
    with pytest.raises(ValidationError, match="proof_id"):
        EngagementManifest.model_validate(duplicate)


@pytest.mark.parametrize(
    "path",
    ["/absolute", "../escape", "a/../b", "C:/x", "C:foo/bar", r"a\\b", "a//b", "a\0b"],
)
def test_confined_relative_path_rejects_unsafe_forms(path: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ConfinedRelativePath).validate_python(path)


def test_evidence_reference_is_content_addressed_and_limitations_are_sorted_unique() -> None:
    digest = sha256(b"payload").hexdigest()
    reference = EvidenceReference(
        evidence_id=f"evidence-sha256-{digest}",
        sha256=digest,
        size=7,
        media_type="application/octet-stream",
        representation="host_bytes",
        relative_path=f"evidence/blob-{digest}.bin",
        capture_limitations=(
            CaptureLimitation.HOST_REPORTED_TRUNCATION,
            CaptureLimitation.PROVIDER_OR_HOST_SECRET_REDACTED,
        ),
    )
    assert reference.evidence_id == f"evidence-sha256-{reference.sha256}"
    assert reference.capture_limitations == tuple(sorted(reference.capture_limitations))
    with pytest.raises(ValidationError):
        EvidenceReference.model_validate(
            {
                **reference.model_dump(mode="json"),
                "capture_limitations": [
                    "host_reported_truncation",
                    "host_reported_truncation",
                ],
            }
        )


def test_event_type_must_match_closed_payload(lane) -> None:
    with pytest.raises(ValidationError):
        JournalEventDraft(
            lane=lane,
            actor="host_agent",
            type="engagement_opened",
            payload=ToolCallStartedPayload(
                call_id="call-1",
                tool_name="terminal",
                correlation=ToolCorrelation.uncertain("missing_stable_identity"),
                safe_arguments={},
            ),
        )


def test_payload_union_is_closed_and_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        EngagementOpenedPayload(scope_references=(), surprise=True)
    with pytest.raises(ValidationError):
        JournalEventDraft.model_validate(
            {"actor": "user", "type": "not_real", "payload": {"kind": "not_real"}}
        )


def test_tool_correlation_prefers_host_tool_call_id(lane) -> None:
    correlation = ToolCorrelation.from_hook(
        lane=lane,
        tool_name="terminal",
        sanitized_arguments=sanitize_host_arguments({"command": "id"}),
        tool_call_id="provider-call-7",
        turn_id="turn-1",
        api_request_id="request-1",
        api_call_count=2,
    )
    assert correlation.kind is CorrelationKind.TOOL_CALL_ID
    assert correlation.deduplication_allowed is True


def test_host_tool_call_id_remains_stable_when_argument_normalization_fails(lane) -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)
    correlation = ToolCorrelation.from_hook(
        lane=lane,
        tool_name="terminal",
        sanitized_arguments=sanitize_host_arguments(cyclic),
        tool_call_id="provider-call-7",
    )
    assert correlation.kind is CorrelationKind.TOOL_CALL_ID
    assert correlation.deduplication_allowed is True


def test_tool_correlation_uses_true_host_tool_ordinal_when_supplied(lane) -> None:
    correlation = ToolCorrelation.from_hook(
        lane=lane,
        tool_name="terminal",
        sanitized_arguments=sanitize_host_arguments({"command": "id"}),
        tool_call_id="",
        turn_id="turn-1",
        api_request_id="request-1",
        api_call_count=2,
        tool_call_ordinal=1,
    )
    assert correlation.kind is CorrelationKind.API_ATTEMPT
    assert correlation.deduplication_allowed is True


def test_hades_attempt_counter_is_not_a_tool_ordinal(lane) -> None:
    correlation = ToolCorrelation.from_hook(
        lane=lane,
        tool_name="terminal",
        sanitized_arguments=sanitize_host_arguments({"command": "id"}),
        tool_call_id="",
        turn_id="turn-1",
        api_request_id="request-1",
        api_call_count=2,
    )
    assert correlation.kind is CorrelationKind.UNCERTAIN
    assert correlation.deduplication_allowed is False


def test_incomplete_host_identity_is_typed_uncertain_without_deduplication(lane) -> None:
    correlation = ToolCorrelation.from_hook(
        lane=lane,
        tool_name="terminal",
        sanitized_arguments=sanitize_host_arguments({"command": "id"}),
        tool_call_id="",
        turn_id="",
        api_request_id="",
        api_call_count=None,
    )
    assert correlation.kind is CorrelationKind.UNCERTAIN
    assert correlation.reason == "missing_stable_identity"
    assert correlation.deduplication_allowed is False


def test_tool_call_identity_is_namespaced_by_lane(lane, new_lane) -> None:
    kwargs = {
        "tool_name": "terminal",
        "sanitized_arguments": sanitize_host_arguments({"command": "id"}),
        "tool_call_id": "provider-call-7",
    }
    first = ToolCorrelation.from_hook(lane=lane, **kwargs)
    second = ToolCorrelation.from_hook(lane=new_lane(session_id="other"), **kwargs)
    assert first.stable_key != second.stable_key
    assert first.call_id != second.call_id
    assert "provider-call-7" not in first.model_dump_json()


@pytest.mark.parametrize(
    ("field", "value"),
    [("api_call_count", True), ("tool_call_ordinal", True), ("api_call_count", 1_000_001)],
)
def test_tool_correlation_rejects_invalid_integer_identity_fields(lane, field, value) -> None:
    kwargs = {
        "lane": lane,
        "tool_name": "terminal",
        "sanitized_arguments": sanitize_host_arguments({}),
        "tool_call_id": "",
        "turn_id": "turn",
        "api_request_id": "request",
        "api_call_count": 1,
        "tool_call_ordinal": 0,
    }
    kwargs[field] = value
    with pytest.raises((ValidationError, ValueError)):
        ToolCorrelation.from_hook(**kwargs)


def test_evidence_pair_is_all_or_none(lane) -> None:
    correlation = ToolCorrelation.uncertain("missing_stable_identity")
    with pytest.raises(ValidationError):
        ToolCallCompletedPayload(
            call_id="call-1",
            correlation=correlation,
            technical_status="returned",
            duration_ms=1,
            evidence_id="evidence-sha256-" + "a" * 64,
        )


def test_system_recovery_requires_system_correlation_and_no_lane(lane) -> None:
    payload = RecoveryWarningPayload(
        reason_code="recovered_orphan_evidence",
        evidence_id="evidence-sha256-" + "a" * 64,
    )
    with pytest.raises(ValidationError):
        JournalEventDraft(actor="system", type=payload.kind, payload=payload)

    correlation = SystemCorrelation(
        source="recovery",
        operation_id=UUID("22222222-2222-4222-8222-222222222222"),
    )
    draft = JournalEventDraft(
        actor="system",
        type=payload.kind,
        payload=payload,
        system_correlation=correlation,
    )
    assert draft.lane is None
    with pytest.raises(ValidationError):
        draft.model_copy(update={"lane": lane}, deep=True).__class__.model_validate(
            {**draft.model_dump(), "lane": lane}
        )


def test_session_finalized_settlement_shapes_are_closed() -> None:
    complete = SessionFinalizedPayload(reason="done", settlement_status="complete")
    assert complete.pending_range_count == 0
    with pytest.raises(ValidationError):
        SessionFinalizedPayload(
            reason="not done",
            settlement_status="incomplete",
            pending_range_count=1,
            safe_code="interpretation_incomplete",
        )
    digest = "a" * 64
    incomplete = SessionFinalizedPayload(
        reason="budget reached",
        settlement_status="incomplete",
        pending_range_count=1,
        pending_inventory_sha256=digest,
        safe_code="evidence_budget_exhausted",
    )
    assert incomplete.pending_inventory_sha256 == digest


def test_capture_failure_observation_pair_and_reason_are_consistent() -> None:
    sized = EvidenceCaptureFailedPayload(
        call_id="call-1",
        capture_role="result",
        reason_code="item_quota_exceeded",
        observed_size=20,
        observed_sha256="a" * 64,
    )
    assert sized.observed_size == 20
    with pytest.raises(ValidationError):
        EvidenceCaptureFailedPayload(
            call_id="call-1",
            capture_role="result",
            reason_code="unsupported_value",
            observed_size=20,
            observed_sha256="a" * 64,
        )


def test_host_adapted_command_record_is_private_bounded_template() -> None:
    record = HostAdaptedCommandRecord(
        command_template="nmap {target}",
        placeholder_names=("target",),
        adaptation_note="Use the host-provided target binding",
    )
    assert record.origin == "host_adapted"
    assert record.requires_validation is True
    with pytest.raises(ValidationError):
        HostAdaptedCommandRecord(
            command_template="nmap {target}",
            placeholder_names=("target", "target"),
        )


def test_control_tool_payload_uses_authoritative_closed_names() -> None:
    assert frozenset(
        {
            "sedna_manage_engagement",
            "sedna_plan_next",
            "sedna_record_decision",
            "sedna_add_source",
            "sedna_learn_local",
            "sedna_retrieve_knowledge",
            "sedna_get_knowledge_artifact",
            "sedna_knowledge_maintenance",
        }
    ) == CONTROL_TOOL_NAMES
    name = sorted(CONTROL_TOOL_NAMES)[0]
    payload = ControlToolInvokedPayload(
        control_tool=name,
        policy_version=CONTROL_TOOL_POLICY_VERSION,
        correlation=ToolCorrelation.uncertain("missing_stable_identity"),
    )
    assert payload.control_tool == name
    with pytest.raises(ValidationError):
        ControlToolInvokedPayload(
            control_tool="not-a-control-tool",
            policy_version=CONTROL_TOOL_POLICY_VERSION,
            correlation=ToolCorrelation.uncertain("missing_stable_identity"),
        )


def test_scope_changed_payload_deeply_revalidates_scope(authorized_scope) -> None:
    payload = ScopeChangedPayload(
        scope=authorized_scope,
        scope_references=scope_references(authorized_scope),
        authorization_basis="operator supplied scope",
    )
    assert payload.scope.state is AuthorizationState.AUTHORIZED
    with pytest.raises(ValidationError):
        ScopeChangedPayload(
            scope=AuthorizationScope(state=AuthorizationState.UNKNOWN),
            scope_references=(),
            authorization_basis="missing",
        )


def test_closure_in_flight_ids_are_sorted_unique_and_bounded() -> None:
    payload = ClosureRequestedPayload(
        terminal_watermark=1,
        in_flight_call_ids=("call-b", "call-a"),
        reason="close",
    )
    assert payload.in_flight_call_ids == ("call-a", "call-b")
    with pytest.raises(ValidationError):
        ClosureRequestedPayload(
            terminal_watermark=1,
            in_flight_call_ids=tuple(f"call-{index}" for index in range(MAX_IN_FLIGHT_CALLS + 1)),
            reason="close",
        )


def test_snapshot_validates_manifest_event_state_identity(
    manifest, event_chain, opened_draft
) -> None:
    events = event_chain(
        manifest,
        opened_draft(scope_references=scope_references(manifest.initial_scope)),
    )
    revision = JournalRevision(sequence=1, event_hash=events[-1].event_hash)
    state = EngagementState(
        revision=revision,
        status=EngagementStatus.ACTIVE,
        scope_references=scope_references(manifest.initial_scope),
    )
    snapshot = EngagementSnapshot(
        engagement_id=manifest.engagement_id,
        revision=revision,
        manifest=manifest,
        events=events,
        state=state,
    )
    assert snapshot.events[0].schema_version == EVENT_ENVELOPE_SCHEMA_VERSION
    with pytest.raises(ValidationError):
        EngagementSnapshot(
            engagement_id=UUID("33333333-3333-4333-8333-333333333333"),
            revision=revision,
            manifest=manifest,
            events=events,
            state=state,
        )
