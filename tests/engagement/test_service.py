from __future__ import annotations

import inspect
from contextlib import contextmanager
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict

from sedna.engagement import (
    EngagementClosedPayload,
    EngagementJournalService,
    EngagementMutationResult,
    EventType,
    ExecutionLaneKey,
    JournalEventDraft,
    JournalRevision,
    ProofRequirement,
    ReportCommitAbandonedPayload,
    ReportGeneratedPayload,
    ReportRef,
    RevisionConflictError,
    StrategyArchiveRecordDraft,
    SystemCorrelation,
)
from sedna.engagement.service import (
    EVENT_APPEND_OWNER_BY_TYPE,
    EngagementAmbiguousError,
    EngagementNotFoundError,
    PlanningEventCommitItem,
    create_operational_start_draft,
)
from sedna.planning import FrontierProjection, FrontierProposal


class PlannerState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    revision: JournalRevision
    strategy: str = "planner"


def fixture_planner_state(revision: JournalRevision) -> PlannerState:
    return PlannerState(revision=revision, strategy="enumerate")


@contextmanager
def engagement_service(tmp_path, fixed_clock, fixed_uuid_factory):
    with EngagementJournalService.open(
        tmp_path / "knowledge",
        clock=fixed_clock,
        uuid_factory=fixed_uuid_factory,
    ) as service:
        yield service


def create_orion(service, authorized_scope, lane) -> EngagementMutationResult:
    return service.create_engagement(
        display_name="HTB-Orion",
        objective="Obtain the user and root flags",
        scope=authorized_scope,
        lane=lane,
        required_proofs=(
            ProofRequirement(
                proof_id="user-flag",
                kind="flag",
                description="A valid HTB user flag",
            ),
            ProofRequirement(
                proof_id="root-flag",
                kind="flag",
                description="A valid HTB root flag",
            ),
        ),
    )


def create_two_parent_tasks_in_same_session(service):
    first = service.create_engagement(
        display_name="Parent-A",
        objective="Obtain flags",
        scope=service._authorized_scope_fixture,
        lane=ExecutionLaneKey(host_kind="hades", session_id="parent", task_id="task-a"),
    )
    second = service.create_engagement(
        display_name="Parent-B",
        objective="Validate foothold",
        scope=service._authorized_scope_fixture,
        lane=ExecutionLaneKey(host_kind="hades", session_id="parent", task_id="task-b"),
    )
    return first, second


def start_operational_call_through_sealed_test_capability(
    service, engagement_id, *, lane, call_id, expected_revision
) -> None:
    draft = create_operational_start_draft(lane, call_id=call_id)
    service.append_operational_start(engagement_id, draft, expected_revision=expected_revision)


def _repository_report_drafts() -> tuple[JournalEventDraft, ...]:
    report_id = UUID("00000000-0000-4000-8000-000000000901")
    revision = JournalRevision(sequence=2, event_hash="a" * 64)
    correlation = SystemCorrelation(
        source="reporting",
        operation_id=UUID("00000000-0000-4000-8000-000000000902"),
    )
    report = ReportRef(
        report_id=report_id,
        report_revision=1,
        json_relative_path="reports/report-v1.json",
        json_sha256="b" * 64,
        markdown_relative_path="reports/report-v1.md",
        markdown_sha256="c" * 64,
        renderer_version="1",
        journal_revision=revision,
    )
    return (
        JournalEventDraft(
            actor="system",
            type=EventType.REPORT_GENERATED,
            payload=ReportGeneratedPayload(report=report, generation_reason="closure"),
            system_correlation=correlation,
        ),
        JournalEventDraft(
            actor="system",
            type=EventType.ENGAGEMENT_CLOSED,
            payload=EngagementClosedPayload(
                report_id=report_id,
                report_revision=1,
                closure_request_event_id=UUID("00000000-0000-4000-8000-000000000903"),
                terminal_watermark=2,
            ),
            system_correlation=correlation,
        ),
        JournalEventDraft(
            actor="system",
            type=EventType.REPORT_COMMIT_ABANDONED,
            payload=ReportCommitAbandonedPayload(
                intent_id=UUID("00000000-0000-4000-8000-000000000904"),
                report_id=report_id,
                report_revision=1,
                expected_revision=revision,
                json_sha256="b" * 64,
                markdown_sha256="c" * 64,
                orphan_directory="reports/orphans/intent-digest",
                displaced_batch_count=1,
                displaced_batch_digest="d" * 64,
            ),
            system_correlation=SystemCorrelation(
                source="recovery",
                operation_id=UUID("00000000-0000-4000-8000-000000000906"),
            ),
        ),
    )


def test_event_append_owner_map_is_complete_and_authoritative() -> None:
    assert EVENT_APPEND_OWNER_BY_TYPE == {
        "engagement_opened": "repository_create",
        "engagement_resumed": "lifecycle_service",
        "lane_bound": "lifecycle_service",
        "lane_unbound": "lifecycle_service",
        "child_lane_linked": "lifecycle_service",
        "objective_changed": "lifecycle_service",
        "scope_changed": "lifecycle_service",
        "decision_recorded": "lifecycle_service",
        "agent_deviation_recorded": "lifecycle_service",
        "engagement_reopened": "lifecycle_service",
        "engagement_abandoned": "lifecycle_service",
        "session_started": "hook_adapter",
        "session_checkpointed": "hook_adapter",
        "session_finalized": "hook_adapter",
        "tool_call_started": "hook_adapter",
        "tool_call_completed": "hook_adapter",
        "evidence_attached": "hook_adapter",
        "evidence_capture_failed": "hook_adapter",
        "unmatched_tool_completion": "hook_adapter",
        "unplanned_action": "hook_adapter",
        "control_tool_invoked": "hook_adapter",
        "uncertain_correlation": "hook_adapter",
        "tool_call_terminated": "tool_resolution_service",
        "closure_requested": "closure_service",
        "closure_cancelled": "closure_service",
        "source_suggested": "source_registry",
        "recovery_warning": "recovery_repository",
        "user_note": "caller_facade",
        "observation_extracted": "planning_capability",
        "hypothesis_formed": "planning_capability",
        "missing_information_identified": "planning_capability",
        "outcome_assessed": "planning_capability",
        "objective_proof_observed": "planning_capability",
        "interpretation_succeeded": "planning_capability",
        "interpretation_failed": "planning_capability",
        "plan_requested": "planning_capability",
        "frontier_proposed": "planning_capability",
        "frontier_criticized": "planning_capability",
        "frontier_repaired": "planning_capability",
        "frontier_rejected": "planning_capability",
        "planning_gap_recorded": "planning_capability",
        "strategy_reconciled": "planning_capability",
        "strategy_archived": "planning_capability",
        "strategy_reactivated": "planning_capability",
        "research_query_proposed": "planning_capability",
        "research_source_consulted": "planning_capability",
        "research_source_assessed": "planning_capability",
        "report_generated": "report_commit_capability",
        "engagement_closed": "report_commit_capability",
        "report_commit_abandoned": "report_recovery_capability",
    }
    assert set(EVENT_APPEND_OWNER_BY_TYPE) == {item.value for item in EventType}


@pytest.mark.parametrize("draft", _repository_report_drafts(), ids=lambda draft: draft.type)
def test_report_events_reject_same_shape_cross_owner_append_paths(
    tmp_path, manifest, lane, fixed_clock, fixed_uuid_factory, draft
) -> None:
    with engagement_service(tmp_path, fixed_clock, fixed_uuid_factory) as service:
        created = service.create_from_manifest(manifest, lane=lane)
        for append in (
            service.append_events,
            service.append_hook_events,
            lambda engagement_id, drafts, **kwargs: service.append_operational_start(
                engagement_id, drafts[0], **kwargs
            ),
        ):
            with pytest.raises(ValueError, match="cannot append"):
                append(
                    manifest.engagement_id,
                    (draft,),
                    expected_revision=created.snapshot.revision,
                )
        with pytest.raises(ValueError, match="planning event payload"):
            PlanningEventCommitItem(
                event_id=UUID("00000000-0000-4000-8000-000000000905"),
                payload=draft.payload,
                idempotency_key=f"forged:{draft.type}",
            )


def test_create_requires_human_name_and_binds_calling_lane(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    with engagement_service(tmp_path, fixed_clock, fixed_uuid_factory) as service:
        created = create_orion(service, authorized_scope, lane)
        resolved = service.resolve_lane_binding(lane)

    assert created.snapshot.manifest.display_name == "HTB-Orion"
    assert [item.proof_id for item in created.snapshot.manifest.required_proofs] == [
        "user-flag",
        "root-flag",
    ]
    assert created.snapshot.state.status == "active"
    assert resolved.mode == "exact"
    assert resolved.engagement_id == created.snapshot.manifest.engagement_id


def test_resume_by_scope_is_automatic_only_when_one_open_engagement_is_compatible(
    tmp_path, authorized_scope, lane, new_lane, fixed_clock, fixed_uuid_factory
) -> None:
    with engagement_service(tmp_path, fixed_clock, fixed_uuid_factory) as service:
        first = create_orion(service, authorized_scope, lane)
        service.unbind_lane(first.engagement_id, lane, reason="session_changed")
        resumed = service.resume_engagement(
            lane=new_lane(session_id="session-2"), scope=authorized_scope
        )
    assert resumed.snapshot.manifest.engagement_id == first.snapshot.manifest.engagement_id


def test_same_target_in_two_open_engagements_returns_readable_candidates(
    tmp_path, authorized_scope, new_lane, fixed_clock, fixed_uuid_factory
) -> None:
    with engagement_service(tmp_path, fixed_clock, fixed_uuid_factory) as service:
        service.create_engagement(
            display_name="Orion-A",
            objective="Obtain flags",
            scope=authorized_scope,
            lane=new_lane(session_id="session-a"),
        )
        service.create_engagement(
            display_name="Orion-B",
            objective="Validate foothold",
            scope=authorized_scope,
            lane=new_lane(session_id="session-b"),
        )
        with pytest.raises(EngagementAmbiguousError) as caught:
            service.resume_engagement(lane=new_lane(session_id="session-c"), scope=authorized_scope)
    assert [item.display_name for item in caught.value.candidates] == [
        "Orion-A",
        "Orion-B",
    ]


def test_public_facade_supports_snapshot_events_evidence_and_projection_cas(
    tmp_path, manifest, lane, user_note_draft, fixed_clock, fixed_uuid_factory
) -> None:
    with engagement_service(tmp_path, fixed_clock, fixed_uuid_factory) as service:
        created = service.create_from_manifest(manifest, lane=lane)
        appended = service.append_events(
            manifest.engagement_id,
            (user_note_draft("projection CAS fixture"),),
            expected_revision=created.snapshot.revision,
        )
        planner_projection = fixture_planner_state(appended.snapshot.revision)
        state_path = service.commit_projection(
            manifest.engagement_id,
            "state",
            planner_projection,
            expected_revision=appended.snapshot.revision,
        )
        loaded = service.load_projection(
            manifest.engagement_id,
            "state",
            type(planner_projection),
        )
        lifecycle = service.load_projection(
            manifest.engagement_id,
            "engagement-state",
            type(appended.snapshot.state),
        )

    assert state_path.name == "state.json"
    assert loaded == planner_projection
    assert lifecycle == appended.snapshot.state
    with (
        engagement_service(tmp_path, fixed_clock, fixed_uuid_factory) as service,
        pytest.raises(RevisionConflictError),
    ):
        service.commit_projection(
            manifest.engagement_id,
            "state",
            planner_projection,
            expected_revision=created.snapshot.revision,
        )


def test_public_facade_exposes_fixed_strategy_archive(
    tmp_path, manifest, lane, fixed_clock, fixed_uuid_factory
) -> None:
    record = StrategyArchiveRecordDraft(
        entry_id=UUID("00000000-0000-4000-8000-000000000802"), payload={"cold": True}
    )
    with engagement_service(tmp_path, fixed_clock, fixed_uuid_factory) as service:
        opened = service.create_from_manifest(manifest, lane=lane)
        committed = service.commit_strategy_archive(
            manifest.engagement_id,
            schema_id="sedna.strategy-archive.v1",
            records=(record,),
            expected_archive_revision=None,
            expected_journal_revision=opened.snapshot.revision,
        )
        page = service.load_strategy_archive(manifest.engagement_id)

    assert committed.file_name == "strategy-archive.jsonl"
    assert page is not None
    assert page.records == (record,)


def test_facade_pages_events_and_evidence_descriptors(
    tmp_path,
    manifest,
    lane,
    user_note_draft,
    tool_started,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    with engagement_service(tmp_path, fixed_clock, fixed_uuid_factory) as service:
        service.create_from_manifest(manifest, lane=lane)
        service.append_events(
            manifest.engagement_id,
            tuple(user_note_draft(f"note-{index}") for index in range(300)),
        )
        first = service.load_events(manifest.engagement_id, after_sequence=0, limit=256)
        second = service.load_events(
            manifest.engagement_id,
            after_sequence=first.next_after_sequence,
            through_revision=first.authoritative_revision,
            limit=256,
        )
        descriptors = service.list_evidence_descriptors(
            manifest.engagement_id,
            after_sequence=0,
            through_revision=first.authoritative_revision,
            limit=256,
        )

    assert len(first.events) == 256
    assert all(event.sequence <= first.authoritative_revision.sequence for event in second.events)
    assert descriptors.items == ()
    assert first.complete is False
    assert second.complete is True


def test_service_methods_keep_authoritative_keyword_names() -> None:
    required_keywords: dict[str, set[str]] = {
        "append_events": {"expected_revision"},
        "load_events": {"after_sequence", "through_revision", "limit"},
        "list_evidence_descriptors": {"after_sequence", "through_revision", "limit"},
        "commit_projection": {"name", "expected_revision"},
        "commit_strategy_archive": {
            "schema_id",
            "records",
            "expected_archive_revision",
            "expected_journal_revision",
        },
        "load_strategy_archive": {"after_entry_id", "limit"},
        "load_projection": {"name", "model_type"},
        "terminate_tool_call": {"expected_revision", "resolution", "reason", "lane"},
        "request_close": {"expected_revision", "lane", "reason"},
        "record_decision": {"expected_revision", "lane", "strategy", "rationale"},
        "resume_engagement": {"expected_revision", "lane"},
        "read_evidence_slice": {"offset", "limit"},
    }
    for method, keywords in required_keywords.items():
        signature = inspect.signature(getattr(EngagementJournalService, method))
        names = set(signature.parameters)
        missing = keywords - names
        assert not missing, f"{method} must accept {sorted(missing)}"


def test_decision_is_bound_only_to_calling_lane(
    tmp_path, authorized_scope, lane, new_lane, fixed_clock, fixed_uuid_factory
) -> None:
    child = new_lane(session_id=lane.session_id, task_id="child")
    with engagement_service(tmp_path, fixed_clock, fixed_uuid_factory) as service:
        created = create_orion(service, authorized_scope, lane)
        service.bind_lane(created.engagement_id, child, reason="explicit_child")
        service.record_decision(
            created.engagement_id,
            lane=lane,
            strategy="Enumerate exposed services",
            rationale="No target facts exist yet",
        )
        assert service.load_active_decision(created.engagement_id, child) is None
        assert service.load_active_decision(created.engagement_id, lane) is not None


def test_proposal_decision_requires_current_authoritative_frontier(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    current_proposal_id = UUID("00000000-0000-0000-0000-000000000101")
    stale_proposal_id = UUID("00000000-0000-0000-0000-000000000102")
    with engagement_service(tmp_path, fixed_clock, fixed_uuid_factory) as service:
        created = create_orion(service, authorized_scope, lane)
        snapshot = service.load_snapshot(created.engagement_id)
        service.commit_projection(
            created.engagement_id,
            "frontier",
            FrontierProjection(
                frontier_id=UUID("00000000-0000-0000-0000-000000000103"),
                engagement_id=created.engagement_id,
                state_digest="1" * 64,
                input_ledger_digest="2" * 64,
                resulting_ledger_digest="3" * 64,
                proposals=(
                    FrontierProposal(
                        proposal_id=current_proposal_id,
                        family_id=UUID("00000000-0000-0000-0000-000000000104"),
                        variant_id=UUID("00000000-0000-0000-0000-000000000105"),
                        title="Current proposal",
                        score=90,
                        confidence=80,
                        rationale="Current authoritative frontier.",
                    ),
                ),
                constrained_rationale="Only one proposal is currently applicable.",
            ),
            expected_revision=snapshot.revision,
        )

        with pytest.raises(ValueError, match="proposal_not_found"):
            service.record_decision(
                created.engagement_id,
                lane=lane,
                proposal_id=stale_proposal_id,
            )


def test_m6a_close_stops_at_closing_even_when_barrier_is_ready(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    with engagement_service(tmp_path, fixed_clock, fixed_uuid_factory) as service:
        created = create_orion(service, authorized_scope, lane)
        closing = service.request_close(
            created.engagement_id,
            lane=lane,
            reason="objective proof observed",
        )
    assert closing.snapshot.state.status == "closing"
    assert closing.snapshot.state.closure_ready is True
    assert closing.snapshot.state.closure.origin == "manual"


def test_call_without_post_hook_can_be_explicitly_abandoned_before_close(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    with engagement_service(tmp_path, fixed_clock, fixed_uuid_factory) as service:
        created = create_orion(service, authorized_scope, lane)
        start_operational_call_through_sealed_test_capability(
            service,
            created.engagement_id,
            lane=lane,
            call_id="crashed-call",
            expected_revision=created.snapshot.revision,
        )
        waiting = service.request_close(
            created.engagement_id,
            lane=lane,
            reason="manual close after host crash",
        )
        assert waiting.snapshot.state.closure_ready is False
        resolved = service.terminate_tool_call(
            created.engagement_id,
            "crashed-call",
            resolution="abandoned",
            reason="post_tool_call was lost when the host exited",
            lane=lane,
            expected_revision=waiting.snapshot.revision,
        )
    assert resolved.snapshot.state.closure_ready is True


def test_child_inheritance_requires_exact_or_unique_parent_binding(
    tmp_path, authorized_scope, fixed_clock, fixed_uuid_factory
) -> None:
    with engagement_service(tmp_path, fixed_clock, fixed_uuid_factory) as service:
        service._authorized_scope_fixture = authorized_scope
        create_two_parent_tasks_in_same_session(service)
        ambiguous = service.link_child_session(
            parent_session_id="parent",
            parent_task_id=None,
            child_session_id="child",
            child_subagent_id="subagent-1",
        )
    assert ambiguous.mode == "ambiguous"
    assert ambiguous.engagement_id is None


def test_resume_unknown_selector_raises_typed_not_found(
    tmp_path, authorized_scope, lane, new_lane, fixed_clock, fixed_uuid_factory
) -> None:
    with (
        engagement_service(tmp_path, fixed_clock, fixed_uuid_factory) as service,
        pytest.raises(EngagementNotFoundError),
    ):
        service.resume_engagement(
            lane=new_lane(session_id="missing-session"),
            display_name="does-not-exist",
        )


def test_create_from_manifest_result_shape_is_uniform(
    tmp_path, manifest, lane, fixed_clock, fixed_uuid_factory
) -> None:
    with engagement_service(tmp_path, fixed_clock, fixed_uuid_factory) as service:
        created = service.create_from_manifest(manifest, lane=lane)
    assert isinstance(created, EngagementMutationResult)
    assert created.snapshot.engagement_id == manifest.engagement_id
    assert created.created_event_ids
