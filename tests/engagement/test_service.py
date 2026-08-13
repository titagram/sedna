from __future__ import annotations

import inspect
from contextlib import contextmanager
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict

from sedna.engagement import (
    EngagementJournalService,
    EngagementMutationResult,
    ExecutionLaneKey,
    JournalRevision,
    ProofRequirement,
    RevisionConflictError,
    StrategyArchiveRecordDraft,
)
from sedna.engagement.service import (
    EngagementAmbiguousError,
    EngagementNotFoundError,
    create_operational_start_draft,
)


class PlannerState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    revision: JournalRevision
    strategy: str = "planner"


def fixture_planner_state(revision: JournalRevision) -> PlannerState:
    return PlannerState(revision=revision, strategy="enumerate")


@contextmanager
def engagement_service(
    tmp_path, fixed_clock, fixed_uuid_factory
):
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
        lane=ExecutionLaneKey(
            host_kind="hades", session_id="parent", task_id="task-a"
        ),
    )
    second = service.create_engagement(
        display_name="Parent-B",
        objective="Validate foothold",
        scope=service._authorized_scope_fixture,
        lane=ExecutionLaneKey(
            host_kind="hades", session_id="parent", task_id="task-b"
        ),
    )
    return first, second


def start_operational_call_through_sealed_test_capability(
    service, engagement_id, *, lane, call_id, expected_revision
) -> None:
    draft = create_operational_start_draft(lane, call_id=call_id)
    service.append_operational_start(
        engagement_id, draft, expected_revision=expected_revision
    )


def test_create_requires_human_name_and_binds_calling_lane(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    with engagement_service(
        tmp_path, fixed_clock, fixed_uuid_factory
    ) as service:
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
    with engagement_service(
        tmp_path, fixed_clock, fixed_uuid_factory
    ) as service:
        first = create_orion(service, authorized_scope, lane)
        service.unbind_lane(first.engagement_id, lane, reason="session_changed")
        resumed = service.resume_engagement(
            lane=new_lane(session_id="session-2"), scope=authorized_scope
        )
    assert resumed.snapshot.manifest.engagement_id == first.snapshot.manifest.engagement_id


def test_same_target_in_two_open_engagements_returns_readable_candidates(
    tmp_path, authorized_scope, new_lane, fixed_clock, fixed_uuid_factory
) -> None:
    with engagement_service(
        tmp_path, fixed_clock, fixed_uuid_factory
    ) as service:
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
            service.resume_engagement(
                lane=new_lane(session_id="session-c"), scope=authorized_scope
            )
    assert [item.display_name for item in caught.value.candidates] == [
        "Orion-A",
        "Orion-B",
    ]


def test_public_facade_supports_snapshot_events_evidence_and_projection_cas(
    tmp_path, manifest, lane, user_note_draft, fixed_clock, fixed_uuid_factory
) -> None:
    with engagement_service(
        tmp_path, fixed_clock, fixed_uuid_factory
    ) as service:
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
    with engagement_service(
        tmp_path, fixed_clock, fixed_uuid_factory
    ) as service, pytest.raises(RevisionConflictError):
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
    tmp_path, manifest, lane, user_note_draft, tool_started, fixed_clock,
    fixed_uuid_factory,
) -> None:
    with engagement_service(
        tmp_path, fixed_clock, fixed_uuid_factory
    ) as service:
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
    assert all(
        event.sequence <= first.authoritative_revision.sequence
        for event in second.events
    )
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
    with engagement_service(
        tmp_path, fixed_clock, fixed_uuid_factory
    ) as service:
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


def test_m6a_close_stops_at_closing_even_when_barrier_is_ready(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    with engagement_service(
        tmp_path, fixed_clock, fixed_uuid_factory
    ) as service:
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
    with engagement_service(
        tmp_path, fixed_clock, fixed_uuid_factory
    ) as service:
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
    with engagement_service(
        tmp_path, fixed_clock, fixed_uuid_factory
    ) as service:
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
    with engagement_service(
        tmp_path, fixed_clock, fixed_uuid_factory
    ) as service, pytest.raises(EngagementNotFoundError):
        service.resume_engagement(
            lane=new_lane(session_id="missing-session"),
            display_name="does-not-exist",
        )


def test_create_from_manifest_result_shape_is_uniform(
    tmp_path, manifest, lane, fixed_clock, fixed_uuid_factory
) -> None:
    with engagement_service(
        tmp_path, fixed_clock, fixed_uuid_factory
    ) as service:
        created = service.create_from_manifest(manifest, lane=lane)
    assert isinstance(created, EngagementMutationResult)
    assert created.snapshot.engagement_id == manifest.engagement_id
    assert created.created_event_ids
