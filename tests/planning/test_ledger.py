"""Bounded strategy archive and ledger replay contracts."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from hashlib import sha256
from types import SimpleNamespace
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

import sedna.planning.ledger as ledger_module
from sedna.engagement import (
    EngagementJournalService,
    EngagementManifest,
    EventType,
    ExecutionLaneKey,
    HostKind,
    ProofRequirement,
    RevisionConflictError,
    StrategyArchiveRecordDraft,
)
from sedna.engagement.events import (
    ArchivedStrategyEventRecord,
    AttemptAggregateEventRecord,
    ExecutionVariantEventRecord,
    RetryPredicateEventRecord,
    StrategyArchivedEventPayload,
    StrategyFamilyEventRecord,
    StrategyReactivatedEventPayload,
    StrategyReconciledEventPayload,
    StrategyReconciliationEventOperation,
)
from sedna.engagement.service import PlanningEventCommitItem
from sedna.knowledge.retrieval import AuthorizationScope, AuthorizationState, ValidatedTarget
from sedna.planning.ledger import (
    LEDGER_EFFECT_EVENT_TYPES,
    LEDGER_NO_OP_EVENT_TYPES,
    LedgerReplayError,
    StrategyLedgerReducer,
    archive_digest,
    ledger_digest,
    partition_ledger,
    select_reactivation_candidates,
    validate_reconciliation,
)
from sedna.planning.models import (
    AccessState,
    AttemptSummary,
    ExecutionVariantState,
    ObservedFacet,
    ObservedFact,
    SecretReference,
    SituationProjection,
    StrategyFamilyState,
    StrategyLedger,
    StrategyStatus,
)
from tests.planning.test_journal_events import _conversion, _convert, planning_event_cases


def test_ledger_event_effect_table_exhaustively_covers_report_events() -> None:
    assert LEDGER_EFFECT_EVENT_TYPES.isdisjoint(LEDGER_NO_OP_EVENT_TYPES)
    assert frozenset(EventType) == LEDGER_EFFECT_EVENT_TYPES | LEDGER_NO_OP_EVENT_TYPES
    assert {
        EventType.REPORT_GENERATED,
        EventType.ENGAGEMENT_CLOSED,
        EventType.REPORT_COMMIT_ABANDONED,
    } <= LEDGER_NO_OP_EVENT_TYPES
    saga_event_types = frozenset(
        {
            EventType.PROMOTION_REQUESTED,
            EventType.PROMOTION_CANDIDATE_READY,
            EventType.PROMOTION_SOURCE_COMMITTED,
            EventType.PROMOTION_SEMANTIC_COMMITTED,
            EventType.PROMOTION_INDEX_PENDING,
            EventType.PROMOTION_INDEX_RETRY_FAILED,
            EventType.CASE_PROMOTED,
            EventType.PROMOTION_ATTEMPT_TERMINATED,
            EventType.PROMOTION_ATTEMPT_CANCELLATION_REQUESTED,
            EventType.PROMOTION_REVOCATION_REQUESTED,
            EventType.CASE_PROMOTION_REVOKED,
            EventType.CASE_PROMOTION_SUPERSEDED,
        }
    )
    assert saga_event_types == saga_event_types & LEDGER_NO_OP_EVENT_TYPES


FIXED_TIME = datetime(2026, 8, 11, 12, 30, tzinfo=UTC)


def _uuid_factory():
    value = 1

    def factory() -> UUID:
        nonlocal value
        result = UUID(f"00000000-0000-4000-8000-{value:012d}")
        value += 1
        return result

    return factory


def _manifest() -> EngagementManifest:
    return EngagementManifest(
        engagement_id=UUID("11111111-1111-4111-8111-111111111111"),
        display_name="HTB-Orion",
        initial_objective="Obtain the user flag",
        initial_scope=AuthorizationScope(
            state=AuthorizationState.AUTHORIZED,
            exact_targets=(ValidatedTarget.parse("192.0.2.44"),),
        ),
        required_proofs=(ProofRequirement(proof_id="user-flag", kind="flag", description="flag"),),
        created_at=FIXED_TIME,
        created_by_host={"kind": "hades", "adapter_version": "1"},
    )


def _lane() -> ExecutionLaneKey:
    return ExecutionLaneKey(host_kind=HostKind.HADES, session_id="session", task_id="task")


def test_strategy_archive_first_commit_is_descriptor_confined_and_loadable(
    tmp_path,
) -> None:
    """The archive has a fixed M6A-owned path and deterministic first revision."""
    record = StrategyArchiveRecordDraft(
        entry_id=UUID("00000000-0000-4000-8000-000000000701"),
        payload={"status": "exhausted", "strategy": "ssh-wordlist"},
    )
    manifest = _manifest()
    with EngagementJournalService.open(
        tmp_path / "knowledge", clock=lambda: FIXED_TIME, uuid_factory=_uuid_factory()
    ) as service:
        created = service.create_from_manifest(manifest, lane=_lane())
        committed = service.commit_strategy_archive(
            manifest.engagement_id,
            schema_id="sedna.strategy-archive.v1",
            records=(record,),
            expected_archive_revision=None,
            expected_journal_revision=created.snapshot.revision,
        )
        loaded = service.load_strategy_archive(manifest.engagement_id)

    assert committed.envelope.archive_revision == 1
    assert committed.file_name == "strategy-archive.jsonl"
    assert loaded is not None
    assert loaded.envelope == committed.envelope
    assert loaded.records == (record,)
    assert loaded.complete is True
    assert loaded.next_after_entry_id is None


def test_strategy_archive_cas_pagination_and_mode_are_strict(tmp_path) -> None:
    manifest = _manifest()
    first = StrategyArchiveRecordDraft(
        entry_id=UUID("00000000-0000-4000-8000-000000000710"), payload={"key": "a"}
    )
    second = StrategyArchiveRecordDraft(
        entry_id=UUID("00000000-0000-4000-8000-000000000711"), payload={"key": "b"}
    )
    with EngagementJournalService.open(
        tmp_path / "knowledge", clock=lambda: FIXED_TIME, uuid_factory=_uuid_factory()
    ) as service:
        created = service.create_from_manifest(manifest, lane=_lane())
        initial = service.commit_strategy_archive(
            manifest.engagement_id,
            schema_id="sedna.strategy-archive.v1",
            records=(first, second),
            expected_archive_revision=None,
            expected_journal_revision=created.snapshot.revision,
        )
        page = service.load_strategy_archive(manifest.engagement_id, limit=1)
        assert page is not None
        assert page.records == (first,)
        assert page.complete is False
        restarted = service.load_strategy_archive(
            manifest.engagement_id, after_entry_id=page.next_after_entry_id, limit=1
        )
        assert restarted is not None
        assert restarted.records == (second,)
        with pytest.raises(ValueError, match="cursor"):
            service.load_strategy_archive(
                manifest.engagement_id,
                after_entry_id=UUID("00000000-0000-4000-8000-000000000799"),
            )
        with pytest.raises(RevisionConflictError, match="archive revision"):
            service.commit_strategy_archive(
                manifest.engagement_id,
                schema_id="sedna.strategy-archive.v1",
                records=(first,),
                expected_archive_revision=None,
                expected_journal_revision=created.snapshot.revision,
            )
        with pytest.raises(RevisionConflictError, match="journal revision"):
            service.commit_strategy_archive(
                manifest.engagement_id,
                schema_id="sedna.strategy-archive.v1",
                records=(first,),
                expected_archive_revision=initial.envelope.archive_revision,
                expected_journal_revision={"sequence": 0, "event_hash": "0" * 64},
            )

    path = (
        tmp_path
        / "knowledge"
        / "engagements"
        / str(manifest.engagement_id)
        / "strategy-archive.jsonl"
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(path.read_bytes()) == initial.envelope.byte_size
    assert os.path.basename(path) == "strategy-archive.jsonl"


def test_strategy_archive_rejects_corruption_and_consumes_one_over_sentinel(tmp_path) -> None:
    manifest = _manifest()
    consumed = 0

    def endless():
        nonlocal consumed
        while True:
            consumed += 1
            yield StrategyArchiveRecordDraft(
                entry_id=UUID(f"00000000-0000-4000-8000-{consumed:012d}"), payload={"n": consumed}
            )

    with EngagementJournalService.open(
        tmp_path / "knowledge", clock=lambda: FIXED_TIME, uuid_factory=_uuid_factory()
    ) as service:
        created = service.create_from_manifest(manifest, lane=_lane())
        with pytest.raises(ValueError, match="record count"):
            service.commit_strategy_archive(
                manifest.engagement_id,
                schema_id="sedna.strategy-archive.v1",
                records=endless(),
                expected_archive_revision=None,
                expected_journal_revision=created.snapshot.revision,
            )
        assert consumed == 100_001
        path = (
            tmp_path
            / "knowledge"
            / "engagements"
            / str(manifest.engagement_id)
            / "strategy-archive.jsonl"
        )
        assert not path.exists()
        path.write_bytes(b"not-json\n")
        os.chmod(path, 0o600)
        with pytest.raises(ValueError, match="header"):
            service.load_strategy_archive(manifest.engagement_id)


def test_reactivation_candidates_require_an_explicit_matching_predicate() -> None:
    revision = {"sequence": 2, "event_hash": "a" * 64}
    archived = ArchivedStrategyEventRecord(
        archive_entry_id=UUID("00000000-0000-4000-8000-000000000702"),
        snapshot=StrategyFamilyEventRecord(
            family_id=UUID("00000000-0000-4000-8000-000000000703"),
            stable_key="family:ssh",
            title="SSH access",
            strategic_intent="Obtain SSH access.",
            rationale="SSH is reachable.",
            score=0,
            confidence=0.5,
            status="exhausted",
            last_material_revision=revision,
        ),
        archive_reason="The bounded credential list was exhausted.",
        retry_predicates=(
            RetryPredicateEventRecord(
                predicate_id="credential_available",
                kind="credential_available",
                subject_ref="ssh-password",
                description="A credential reference becomes available.",
            ),
        ),
        archive_summary="Retry if a credential is captured.",
        archived_at_material_revision=revision,
        source_reconciliation_event_id=UUID("00000000-0000-4000-8000-000000000704"),
        archive_entry_digest="b" * 64,
    )
    situation = SituationProjection(
        engagement_id=UUID("00000000-0000-4000-8000-000000000705"),
        authoritative_journal_revision=revision,
        material_event_revision=2,
        state_digest="c" * 64,
        objective_progress={},
        facts=(
            ObservedFact(
                text="ssh-password",
                event_ids=(UUID("00000000-0000-4000-8000-000000000706"),),
            ),
        ),
    )

    assert select_reactivation_candidates((archived,), situation) == ()


@pytest.mark.parametrize(
    ("kind", "predicate_fields"),
    (
        ("fact_present", {}),
        ("fact_changed", {"expected_value_digest": sha256(b"old").hexdigest()}),
        ("prerequisite_satisfied", {}),
        ("evidence_category_present", {"expected_symbolic_value": "progress"}),
        ("credential_available", {}),
        (
            "state_revision_after",
            {"minimum_material_revision": {"sequence": 2, "event_hash": "b" * 64}},
        ),
    ),
)
def test_each_typed_retry_predicate_matches_only_its_explicit_situation_fact(
    kind, predicate_fields
) -> None:
    event_id = UUID("00000000-0000-4000-8000-000000000707")
    revision = {"sequence": 3, "event_hash": "a" * 64}
    predicate = RetryPredicateEventRecord(
        predicate_id=f"retry-{kind}",
        kind=kind,
        subject_ref={
            "fact_present": "fact-ready",
            "fact_changed": "config",
            "prerequisite_satisfied": "operator",
            "evidence_category_present": "evidence",
            "credential_available": "ssh-password",
            "state_revision_after": "material-state",
        }[kind],
        description="Retry only when the typed condition is present.",
        **predicate_fields,
    )
    archived = ArchivedStrategyEventRecord(
        archive_entry_id=UUID("00000000-0000-4000-8000-000000000708"),
        snapshot=StrategyFamilyEventRecord(
            family_id=UUID("00000000-0000-4000-8000-000000000709"),
            stable_key=f"family:{kind}",
            title="Archived strategy",
            strategic_intent="Wait for an explicit retry condition.",
            rationale="The strategy is currently exhausted.",
            score=0,
            confidence=0.5,
            status="exhausted",
            last_material_revision=revision,
        ),
        archive_reason="The explicit retry condition is not currently present.",
        retry_predicates=(predicate,),
        archive_summary="Reactivation requires the exact typed condition.",
        archived_at_material_revision=revision,
        source_reconciliation_event_id=event_id,
        archive_entry_digest="b" * 64,
    )
    matching = SituationProjection(
        engagement_id=UUID("00000000-0000-4000-8000-000000000710"),
        authoritative_journal_revision=revision,
        material_event_revision=3,
        state_digest="c" * 64,
        objective_progress={},
        facts=(ObservedFact(text="fact-ready", event_ids=(event_id,)),),
        facets=(ObservedFacet(key="config", value="new", event_ids=(event_id,)),),
        access_states=(AccessState(subject="operator", state="ready", event_ids=(event_id,)),),
        attempts=(
            AttemptSummary(
                attempt_event_id=event_id,
                outcome="progress",
                summary="Relevant evidence category was observed.",
                event_ids=(event_id,),
            ),
        ),
        secret_references=(
            SecretReference(
                label="ssh-password",
                evidence_id="evidence-sha256-" + "d" * 64,
                value_sha256="e" * 64,
                event_ids=(event_id,),
            ),
        ),
    )
    unrelated = matching.model_copy(
        update={
            "material_event_revision": 2,
            "facts": (),
            "facets": (),
            "access_states": (),
            "attempts": (),
            "secret_references": (),
        }
    )

    assert select_reactivation_candidates((archived,), matching) == (archived,)
    assert select_reactivation_candidates((archived,), unrelated) == ()


def test_reducer_replays_complete_reconciliation_and_bounds_attempt_summaries(
    tmp_path, monkeypatch
) -> None:
    manifest = _manifest()
    family_id = UUID("00000000-0000-4000-8000-000000000720")
    variant_id = UUID("00000000-0000-4000-8000-000000000721")
    family_event_id = UUID("00000000-0000-4000-8000-000000000722")
    variant_event_id = UUID("00000000-0000-4000-8000-000000000723")
    revision = {"sequence": 2, "event_hash": "a" * 64}
    resulting = StrategyLedger(
        families=(
            StrategyFamilyState(
                family_id=family_id,
                runtime_key="family:ssh",
                status="deferred",
                variant_ids=(variant_id,),
            ),
        ),
        variants=(
            ExecutionVariantState(
                variant_id=variant_id,
                family_id=family_id,
                runtime_key="variant:ssh-wordlist",
                status="exhausted",
                historical_attempt_digest="0" * 64,
            ),
        ),
    )
    family = StrategyFamilyEventRecord(
        family_id=family_id,
        stable_key="family:ssh",
        title="SSH access",
        strategic_intent="Obtain SSH access.",
        rationale="SSH is reachable.",
        score=30,
        confidence=0.5,
        status="deferred",
        variant_ids=(variant_id,),
        last_material_revision=revision,
    )
    from sedna.engagement.events import AttemptAggregateEventRecord, ExecutionVariantEventRecord

    variant = ExecutionVariantEventRecord(
        variant_id=variant_id,
        family_id=family_id,
        stable_key="variant:ssh-wordlist",
        title="Bounded wordlist",
        strategic_intent="Try bounded credentials.",
        rationale="A bounded list is available.",
        score=0,
        confidence=0.5,
        status="exhausted",
        attempts=AttemptAggregateEventRecord(total_count=0, history_digest="0" * 64),
        last_material_revision=revision,
    )
    operation_id = UUID("00000000-0000-4000-8000-000000000724")

    def payload(ordinal, snapshot, variant_id_for_operation=None):
        return StrategyReconciledEventPayload(
            request_id=UUID("00000000-0000-4000-8000-000000000725"),
            frontier_id=UUID("00000000-0000-4000-8000-000000000726"),
            reconciliation_id=UUID("00000000-0000-4000-8000-000000000727"),
            item_ordinal=ordinal,
            item_count=2,
            input_ledger_digest=ledger_digest(StrategyLedger()),
            resulting_ledger_digest=ledger_digest(resulting),
            reconciliation_digest="b" * 64,
            operation=StrategyReconciliationEventOperation(
                operation_id=operation_id,
                operation="retain",
                family_id=family_id,
                variant_id=variant_id_for_operation,
                reason="Keep the explicitly represented strategy.",
            ),
            resulting_snapshot=snapshot,
        )

    with EngagementJournalService.open(
        tmp_path / "knowledge", clock=lambda: FIXED_TIME, uuid_factory=_uuid_factory()
    ) as service:
        created = service.create_from_manifest(manifest, lane=_lane())
        committed = service._issue_planning_event_commit_capability().commit_planning_events(
            manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=family_event_id,
                    payload=payload(1, family),
                    idempotency_key="family",
                ),
                PlanningEventCommitItem(
                    event_id=variant_event_id,
                    payload=payload(2, variant, variant_id),
                    idempotency_key="variant",
                ),
            ),
            operation_id=operation_id,
            expected_revision=created.snapshot.revision,
        )
        outcomes = tuple(
            PlanningEventCommitItem(
                event_id=UUID(f"00000000-0000-4000-8000-{730 + ordinal:012d}"),
                idempotency_key=f"outcome-{ordinal}",
                payload={
                    "kind": "outcome_assessed",
                    "attachment_event_id": UUID(f"00000000-0000-4000-8000-{740 + ordinal:012d}"),
                    "terminal_tool_event_id": UUID(f"00000000-0000-4000-8000-{750 + ordinal:012d}"),
                    "decision_id": UUID("00000000-0000-4000-8000-000000000728"),
                    "tool_call_ids": (f"call-{ordinal}",),
                    "category": "no_effect" if ordinal % 3 else "negative_evidence",
                    "summary": f"Attempt {ordinal} had no effect.",
                    "strategic_impact": "No score change without reconciliation.",
                    "source_event_ids": (variant_event_id,),
                    "interpretation_input_digest": "c" * 64,
                },
            )
            for ordinal in range(17)
        )
        settled = service._issue_planning_event_commit_capability().commit_planning_events(
            manifest.engagement_id,
            outcomes,
            operation_id=UUID("00000000-0000-4000-8000-000000000729"),
            expected_revision=committed.snapshot.revision,
        )
        replay = StrategyLedgerReducer.rebuild(settled.snapshot)

    assert replay.families[0].status == "deferred"
    assert replay.variants[0].status == "exhausted"
    assert len(replay.variants[0].recent_attempts) == 8
    variant_state = replay.variants[0]
    evicted = settled.snapshot.events[-17:-8]
    digest = "0" * 64
    for event in evicted:
        payload = event.payload
        attempt = {
            "attempt_event_id": str(
                uuid5(
                    NAMESPACE_URL,
                    f"sedna-strategy-attempt:{payload.decision_id}:{','.join(payload.tool_call_ids)}",
                )
            ),
            "outcome": payload.category,
            "summary": payload.summary,
        }
        digest = sha256(
            json.dumps(
                {"prior_digest": digest, "attempt": attempt},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
    assert variant_state.historical_attempt_count == 9
    assert getattr(variant_state, "outcome_category_totals", None) == {
        "no_effect": 11,
        "negative_evidence": 6,
    }
    assert getattr(variant_state, "historical_oldest_revision", None).model_dump(mode="json") == {
        "sequence": evicted[0].sequence,
        "event_hash": evicted[0].event_hash,
    }
    assert getattr(variant_state, "historical_newest_revision", None).model_dump(mode="json") == {
        "sequence": evicted[-1].sequence,
        "event_hash": evicted[-1].event_hash,
    }
    assert variant_state.historical_attempt_digest == digest

    monkeypatch.setattr(ledger_module, "MAX_HOT_ATTEMPTS", 2)
    compacted = StrategyLedgerReducer.rebuild(settled.snapshot)
    assert [item.summary for item in compacted.variants[0].recent_attempts] == [
        "Attempt 15 had no effect.",
        "Attempt 16 had no effect.",
    ]
    assert compacted.variants[0].historical_attempt_count == 15


@pytest.mark.parametrize(
    ("kind", "family", "source"),
    planning_event_cases(),
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_all_non_ledger_planning_events_are_noops(kind, family, source) -> None:
    """Task8's shared 19-payload fixture has exactly the ledger dispatch it promises."""
    payload = _convert(family, _conversion(family, source))[0]
    if kind in {"strategy_reconciled", "strategy_archived", "strategy_reactivated"}:
        assert isinstance(
            payload,
            (
                StrategyReconciledEventPayload,
                StrategyArchivedEventPayload,
                StrategyReactivatedEventPayload,
            ),
        )
        return
    if kind == "outcome_assessed":
        # An outcome without an explicitly cited reconciliation snapshot cannot create state.
        replay = StrategyLedgerReducer.rebuild(
            SimpleNamespace(
                events=(
                    SimpleNamespace(
                        payload=payload,
                        event_id=UUID("00000000-0000-4000-8000-000000000801"),
                        sequence=1,
                        event_hash="a" * 64,
                    ),
                )
            )
        )
        assert replay == StrategyLedger()
        return
    replay = StrategyLedgerReducer.rebuild(
        SimpleNamespace(
            events=(
                SimpleNamespace(
                    payload=payload,
                    event_id=UUID("00000000-0000-4000-8000-000000000801"),
                    sequence=1,
                    event_hash="a" * 64,
                ),
            )
        )
    )
    assert replay == StrategyLedger()


def _family_snapshot(
    family_id: UUID,
    *,
    key: str,
    status: str = "deferred",
    supersedes: tuple[UUID, ...] = (),
) -> StrategyFamilyEventRecord:
    return StrategyFamilyEventRecord(
        family_id=family_id,
        stable_key=key,
        title="Strategy family",
        strategic_intent="Validate the strategy with bounded evidence.",
        rationale="The explicit reconciliation controls this state.",
        score=10,
        confidence=0.5,
        status=status,
        supersedes_family_ids=supersedes,
        last_material_revision={"sequence": 1, "event_hash": "a" * 64},
    )


def _family_ledger_state(snapshot: StrategyFamilyEventRecord) -> StrategyFamilyState:
    return StrategyFamilyState(
        family_id=snapshot.family_id,
        runtime_key=snapshot.stable_key,
        status=snapshot.status,
        variant_ids=snapshot.variant_ids,
    )


def _reconciliation_payload(
    ledger: StrategyLedger,
    *,
    operation: str,
    snapshot,
    ordinal: int = 1,
    count: int = 1,
    related: tuple[UUID, ...] = (),
) -> StrategyReconciledEventPayload:
    operation_record = StrategyReconciliationEventOperation(
        operation_id=UUID(f"00000000-0000-4000-8000-{900 + ordinal:012d}"),
        operation=operation,
        family_id=(snapshot.family_id if hasattr(snapshot, "family_id") else snapshot.entity_id),
        variant_id=(
            snapshot.variant_id if isinstance(snapshot, ExecutionVariantEventRecord) else None
        ),
        related_family_ids=related,
        reason="The explicit operation is grounded by the reconciliation evidence.",
        evidence_event_ids=(UUID("00000000-0000-4000-8000-000000000899"),),
    )
    resulting = StrategyLedger(
        families=(
            (_family_ledger_state(snapshot),)
            if isinstance(snapshot, StrategyFamilyEventRecord)
            else ()
        ),
    )
    return StrategyReconciledEventPayload(
        request_id=UUID("00000000-0000-4000-8000-000000000890"),
        frontier_id=UUID("00000000-0000-4000-8000-000000000891"),
        reconciliation_id=UUID("00000000-0000-4000-8000-000000000892"),
        item_ordinal=ordinal,
        item_count=count,
        input_ledger_digest=ledger_digest(ledger),
        resulting_ledger_digest=ledger_digest(resulting),
        reconciliation_digest="b" * 64,
        operation=operation_record,
        resulting_snapshot=snapshot,
    )


@pytest.mark.parametrize(
    ("operation", "status", "related"),
    (
        ("retain", "deferred", ()),
        ("update", "deferred", ()),
        ("merge", "deferred", (UUID("00000000-0000-4000-8000-000000000880"),)),
        ("split", "deferred", (UUID("00000000-0000-4000-8000-000000000881"),)),
        ("supersede", "superseded", (UUID("00000000-0000-4000-8000-000000000882"),)),
        ("complete", "completed", ()),
        ("block", "blocked", ()),
        ("archive", "archived", ()),
        ("reactivate", "available", ()),
    ),
)
def test_validate_reconciliation_accepts_all_explicit_operations(
    operation, status, related
) -> None:
    family_id = UUID("00000000-0000-4000-8000-000000000870")
    snapshot = _family_snapshot(
        family_id,
        key=f"family:{operation}",
        status=status,
        supersedes=related,
    )
    payload = _reconciliation_payload(
        StrategyLedger(), operation=operation, snapshot=snapshot, related=related
    )

    assert validate_reconciliation(StrategyLedger(), (payload,)) is None


def test_validate_reconciliation_rejects_omission_identity_change_reparent_duplicate_and_silent_overflow():  # noqa: E501
    first_id = UUID("00000000-0000-4000-8000-000000000840")
    second_id = UUID("00000000-0000-4000-8000-000000000841")
    first = _family_ledger_state(_family_snapshot(first_id, key="family:first"))
    second = _family_ledger_state(_family_snapshot(second_id, key="family:second"))
    ledger = StrategyLedger(families=(first, second))
    omission = _reconciliation_payload(
        ledger, operation="retain", snapshot=_family_snapshot(first_id, key="family:first")
    )
    with pytest.raises(LedgerReplayError, match="omits"):
        validate_reconciliation(ledger, (omission,))

    changed = _reconciliation_payload(
        StrategyLedger(families=(first,)),
        operation="update",
        snapshot=_family_snapshot(UUID("00000000-0000-4000-8000-000000000842"), key="family:first"),
    )
    with pytest.raises(LedgerReplayError, match="runtime ID"):
        validate_reconciliation(StrategyLedger(families=(first,)), (changed,))

    duplicate = _family_snapshot(second_id, key="family:duplicate")
    duplicate_first = _reconciliation_payload(
        StrategyLedger(),
        operation="retain",
        snapshot=_family_snapshot(first_id, key="family:duplicate"),
        count=2,
    )
    duplicate_second = _reconciliation_payload(
        StrategyLedger(), operation="update", snapshot=duplicate, ordinal=2, count=2
    )
    duplicate_payloads = tuple(
        item.model_copy(update={"resulting_ledger_digest": "b" * 64})
        for item in (duplicate_first, duplicate_second)
    )
    with pytest.raises(LedgerReplayError, match="duplicate runtime"):
        validate_reconciliation(StrategyLedger(), duplicate_payloads)

    variant_id = UUID("00000000-0000-4000-8000-000000000843")
    reparented = ExecutionVariantEventRecord(
        variant_id=variant_id,
        family_id=second_id,
        stable_key="variant:reparent",
        title="Reparented variant",
        strategic_intent="This should retain immutable ancestry.",
        rationale="No relationship operation was supplied.",
        score=1,
        confidence=0.5,
        status="deferred",
        attempts=AttemptAggregateEventRecord(total_count=0, history_digest="0" * 64),
        last_material_revision={"sequence": 1, "event_hash": "a" * 64},
    )
    reparent_ledger = StrategyLedger(
        families=(first.model_copy(update={"variant_ids": (variant_id,)}),),
        variants=(
            ExecutionVariantState(
                variant_id=variant_id,
                family_id=first_id,
                runtime_key="variant:reparent",
                status="deferred",
                historical_attempt_digest="0" * 64,
            ),
        ),
    )
    reparent = _reconciliation_payload(reparent_ledger, operation="update", snapshot=reparented)
    with pytest.raises(LedgerReplayError, match="reparents"):
        validate_reconciliation(reparent_ledger, (reparent,))

    overflow = tuple(
        _reconciliation_payload(
            StrategyLedger(),
            operation="retain",
            snapshot=_family_snapshot(
                UUID(f"00000000-0000-4000-8000-{850 + ordinal:012d}"),
                key=f"family:overflow-{ordinal:02d}",
            ),
            ordinal=ordinal,
            count=33,
        )
        for ordinal in range(1, 34)
    )
    overflow = tuple(
        item.model_copy(update={"resulting_ledger_digest": "b" * 64}) for item in overflow
    )
    with pytest.raises(LedgerReplayError, match="bounds"):
        validate_reconciliation(StrategyLedger(), overflow)


def test_partition_never_truncates_available_deferred_variants_and_returns_all_cold_ids() -> None:
    families = tuple(
        StrategyFamilyState(
            family_id=UUID(f"00000000-0000-4000-8000-{500 + index:012d}"),
            runtime_key=f"family:{index:03d}",
            status="deferred" if index < 32 else "exhausted",
            variant_ids=tuple(
                UUID(f"00000000-0000-4000-8000-{600 + item:012d}")
                for item in range(80)
                if item % 32 == index
            ),
        )
        for index in range(40)
    )
    variants = tuple(
        ExecutionVariantState(
            variant_id=UUID(f"00000000-0000-4000-8000-{600 + index:012d}"),
            family_id=families[index % 32].family_id,
            runtime_key=f"variant:{index:03d}",
            status="available" if index < 64 else "exhausted",
            historical_attempt_digest="0" * 64,
        )
        for index in range(80)
    )
    synthetic = StrategyLedger.model_construct(families=families, variants=variants, archive=())
    hot, cold_ids = partition_ledger(synthetic)

    assert len(hot.families) == 32
    assert len(hot.variants) == 64
    assert cold_ids == tuple(item.variant_id for item in variants[64:])
    assert all(
        item.status in {StrategyStatus.AVAILABLE, StrategyStatus.DEFERRED} for item in hot.variants
    )


def test_archive_and_reactivation_batches_require_atomic_complete_companions() -> None:
    family_id = UUID("00000000-0000-4000-8000-000000000860")
    snapshot = _family_snapshot(family_id, key="family:cold", status="archived")
    reconciliation = _reconciliation_payload(
        StrategyLedger(), operation="archive", snapshot=snapshot
    )
    reconciliation_event_id = UUID("00000000-0000-4000-8000-000000000861")
    predicate = RetryPredicateEventRecord(
        predicate_id="fact-ready",
        kind="fact_present",
        subject_ref="fact-ready",
        description="The explicit fact permits reactivation.",
    )
    archive_record = ArchivedStrategyEventRecord(
        archive_entry_id=UUID("00000000-0000-4000-8000-000000000862"),
        snapshot=snapshot,
        archive_reason="The family is cold until the fact is observed.",
        retry_predicates=(predicate,),
        archive_summary="Cold family.",
        archived_at_material_revision={"sequence": 1, "event_hash": "a" * 64},
        source_reconciliation_event_id=reconciliation_event_id,
        archive_entry_digest="0" * 64,
    )
    archive_record = archive_record.model_copy(
        update={
            "archive_entry_digest": sha256(
                ledger_module._canonical(
                    archive_record.model_dump(mode="json", exclude={"archive_entry_digest"})
                )
            ).hexdigest()
        }
    )
    archive = StrategyArchivedEventPayload(
        request_id=UUID("00000000-0000-4000-8000-000000000863"),
        archive_batch_id=UUID("00000000-0000-4000-8000-000000000864"),
        entry_ordinal=1,
        entry_count=1,
        archive_record=archive_record,
        resulting_archive_digest=archive_digest((archive_record,)),
    )
    archive_event_id = UUID("00000000-0000-4000-8000-000000000865")
    reactivation = StrategyReactivatedEventPayload(
        request_id=UUID("00000000-0000-4000-8000-000000000866"),
        reactivation_batch_id=UUID("00000000-0000-4000-8000-000000000867"),
        entry_ordinal=1,
        entry_count=1,
        source_archive_event_id=archive_event_id,
        triggering_event_ids=(UUID("00000000-0000-4000-8000-000000000868"),),
        matched_predicate_ids=("fact-ready",),
        prior_archive_entry_digest=archive_record.archive_entry_digest,
        resulting_archive_digest=archive_digest(()),
        restored_snapshot=snapshot.model_copy(update={"status": "available"}),
    )
    events = (
        SimpleNamespace(
            payload=reconciliation,
            event_id=reconciliation_event_id,
            sequence=1,
            event_hash="a" * 64,
        ),
        SimpleNamespace(
            payload=archive, event_id=archive_event_id, sequence=2, event_hash="b" * 64
        ),
        SimpleNamespace(
            payload=reactivation,
            event_id=UUID("00000000-0000-4000-8000-000000000869"),
            sequence=3,
            event_hash="c" * 64,
        ),
    )
    replay = StrategyLedgerReducer.rebuild_state(SimpleNamespace(events=events))
    assert replay.ledger.families[0].family_id == family_id
    assert replay.archive_records == ()

    invalid_batches = (
        (
            events[:1]
            + (
                events[1].__class__(
                    **{
                        **events[1].__dict__,
                        "payload": archive.model_copy(update={"entry_count": 2}),
                    }
                ),
            )
        ),
        (events[:1] + (events[1], events[1])),
        (
            events[:1]
            + (
                events[1].__class__(
                    **{
                        **events[1].__dict__,
                        "payload": archive.model_copy(
                            update={"resulting_archive_digest": "0" * 64}
                        ),
                    }
                ),
            )
        ),
        (
            events[:1]
            + (
                events[1].__class__(
                    **{
                        **events[1].__dict__,
                        "payload": archive.model_copy(
                            update={
                                "archive_record": archive_record.model_copy(
                                    update={
                                        "source_reconciliation_event_id": UUID(
                                            "00000000-0000-4000-8000-000000000899"
                                        )
                                    }
                                )
                            }
                        ),
                    }
                ),
            )
        ),
    )
    for invalid in invalid_batches:
        with pytest.raises(LedgerReplayError):
            StrategyLedgerReducer.rebuild_state(SimpleNamespace(events=invalid))
