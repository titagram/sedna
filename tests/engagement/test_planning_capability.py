from __future__ import annotations

from contextlib import contextmanager
from uuid import UUID

import pytest

from sedna.engagement import (
    EngagementJournalService,
    EventType,
    JournalEventDraft,
    PlanningGapRecordedEventPayload,
    SystemCorrelation,
)
from sedna.engagement.service import PlanningEventCommitItem


@contextmanager
def engagement_service(tmp_path, fixed_clock, fixed_uuid_factory):
    with EngagementJournalService.open(
        tmp_path / "knowledge", clock=fixed_clock, uuid_factory=fixed_uuid_factory
    ) as service:
        yield service


def gap() -> PlanningGapRecordedEventPayload:
    return PlanningGapRecordedEventPayload(
        code="critic_rejected",
        summary="no acceptable frontier",
        retryable=True,
        situation_digest="a" * 64,
        ledger_digest="b" * 64,
    )


def test_generic_facade_rejects_planning_payload(
    tmp_path, fixed_clock, fixed_uuid_factory, manifest, lane
) -> None:
    with engagement_service(tmp_path, fixed_clock, fixed_uuid_factory) as service:
        created = service.create_from_manifest(manifest, lane=lane)
        draft = JournalEventDraft(
            event_id=UUID("00000000-0000-0000-0000-000000000010"),
            actor="system",
            type=EventType.PLANNING_GAP_RECORDED,
            payload=gap(),
            system_correlation=SystemCorrelation(
                source="planning",
                operation_id=UUID("00000000-0000-0000-0000-000000000020"),
            ),
        )
        with pytest.raises(ValueError, match="generic facade cannot append"):
            service.append_events(
                manifest.engagement_id,
                (draft,),
                expected_revision=created.snapshot.revision,
            )


def test_issued_capability_derives_sealed_planning_envelope(
    tmp_path, fixed_clock, fixed_uuid_factory, manifest, lane
) -> None:
    event_id = UUID("00000000-0000-0000-0000-000000000010")
    operation_id = UUID("00000000-0000-0000-0000-000000000020")
    with engagement_service(tmp_path, fixed_clock, fixed_uuid_factory) as service:
        created = service.create_from_manifest(manifest, lane=lane)
        capability = service._issue_planning_event_commit_capability()
        result = capability.commit_planning_events(
            manifest.engagement_id,
            (PlanningEventCommitItem(event_id=event_id, payload=gap(), idempotency_key="gap:1"),),
            operation_id=operation_id,
            expected_revision=created.snapshot.revision,
        )

    event = result.snapshot.events[-1]
    assert result.created_event_ids == (event_id,)
    assert event.actor == "system"
    assert event.lane is None
    assert event.system_correlation == SystemCorrelation(
        source="planning", operation_id=operation_id
    )


def test_capability_constructor_rejects_forged_token(
    tmp_path, fixed_clock, fixed_uuid_factory
) -> None:
    from sedna.engagement.service import PlanningEventCommitCapability

    with (
        engagement_service(tmp_path, fixed_clock, fixed_uuid_factory) as service,
        pytest.raises(ValueError, match="invalid planning capability token"),
    ):
        PlanningEventCommitCapability(service, object())


def test_capability_rejects_duplicate_ids_before_append(
    tmp_path, fixed_clock, fixed_uuid_factory, manifest, lane
) -> None:
    event_id = UUID("00000000-0000-0000-0000-000000000010")
    item = PlanningEventCommitItem(event_id=event_id, payload=gap(), idempotency_key="gap:1")
    with engagement_service(tmp_path, fixed_clock, fixed_uuid_factory) as service:
        created = service.create_from_manifest(manifest, lane=lane)
        capability = service._issue_planning_event_commit_capability()
        with pytest.raises(ValueError, match="unique"):
            capability.commit_planning_events(
                manifest.engagement_id,
                (item, item),
                operation_id=UUID("00000000-0000-0000-0000-000000000020"),
                expected_revision=created.snapshot.revision,
            )
        assert service.load_snapshot(manifest.engagement_id).revision == created.snapshot.revision
