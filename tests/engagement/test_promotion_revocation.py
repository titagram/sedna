from __future__ import annotations

import copy
import inspect
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest

from sedna.engagement.events import (
    CasePromotedPayload,
    CasePromotionRevokedPayload,
    CasePromotionSupersededPayload,
    EngagementVerifiedPayload,
    EventPayloadAdapter,
    EventType,
    JournalEvent,
    JournalEventDraft,
    PromotionAttemptCancellationRequestedPayload,
    PromotionRevocationRequestedPayload,
    RevocationLifecycleIntent,
    SystemCorrelation,
)
from sedna.engagement.models import (
    ExecutionLaneKey,
    HostKind,
    JournalRevision,
    PromotionAttemptState,
    PromotionPublicationLineage,
    PromotionSagaInProgressError,
    PromotionState,
    ProofRequirement,
)
from sedna.engagement.promotion.adapter import (
    JournalPromotionAdapter,
    PromotionCommitCapability,
    PromotionRecoveryCoordinator,
    RevocationAbsenceProof,
    RevocationLifecycleCommitCapability,
    _ReceiptLedger,
)
from sedna.engagement.promotion.models import PromotionSecretInventory
from sedna.engagement.reducer import _Accumulator
from sedna.engagement.reporting.models import OperationalReport
from sedna.engagement.reporting.service import ReportClosureFinalizer
from sedna.engagement.repository import EngagementJournalRepository
from sedna.engagement.service import (
    EVENT_APPEND_OWNER_BY_TYPE,
    EngagementJournalService,
)
from sedna.knowledge.repository import CanonicalKnowledgeRepository
from sedna.knowledge.retrieval.maintenance import RetrievalMaintenanceService
from sedna.knowledge.retrieval.sqlite import SQLiteRetrievalIndex
from sedna.planning.situation import SituationReducer
from tests.knowledge.test_semantic_service import _load_responses, _service

from .test_journal_promotion_adapter import _claim_request
from .test_promotion_input import _build_verified_journal
from .test_promotion_render import _context, _draft

_ATTEMPT_ID = UUID("00000000-0000-4000-8000-000000000601")
_REPLACEMENT_ID = UUID("00000000-0000-4000-8000-000000000602")
_VERIFICATION_ID = UUID("00000000-0000-4000-8000-000000000603")
_FLAG_ID = UUID("00000000-0000-4000-8000-000000000604")
_REQUEST_ID = UUID("00000000-0000-4000-8000-000000000605")
_DIGEST = "a" * 64
_SOURCE_ID = "source-00000000-0000-4000-8000-000000000606"
_LANE = ExecutionLaneKey(
    host_kind=HostKind.HADES,
    session_id="session-1",
    task_id="task-1",
)
_REVISION = JournalRevision(sequence=7, event_hash="b" * 64)


class _PublicPluginContext:
    def __init__(self, root: Path) -> None:
        self.sedna_knowledge_root = root
        self.llm = self
        self.tools: dict[str, Callable[..., object]] = {}
        self.hooks: dict[str, Callable[..., object]] = {}

    def register_tool(
        self,
        *,
        name: str,
        handler: Callable[..., object],
        **_definition: object,
    ) -> None:
        self.tools[name] = handler

    def register_hook(self, name: str, callback: Callable[..., object]) -> None:
        self.hooks[name] = callback

    def complete_structured(self, **kwargs: object) -> object:
        raise AssertionError(f"promotion fence reached the LLM: {kwargs.get('purpose')}")


def _settled_rejection_receipt(service, snapshot, proof_event_id):
    return service._issue_lifecycle_commit_capability().rejection_receipt(
        SimpleNamespace(
            authoritative_journal_revision=snapshot.revision,
            situation=SituationReducer.rebuild(snapshot),
        ),
        proof_event_id,
    )


def _assert_foreign_exact_retries_are_fenced(snapshot, root) -> None:
    """Exercise every plan-named writer through its public production boundary."""

    assert snapshot.state.promotion.active_attempt.stage in {
        "cancellation_requested",
        "revocation_requested",
    }
    lane_event = next(event for event in snapshot.events if event.type == EventType.LANE_BOUND)
    lane = lane_event.payload.lane
    duplicate_lane = JournalEventDraft(
        event_id=lane_event.event_id,
        lane=lane_event.lane,
        actor_id=lane_event.actor_id,
        actor=lane_event.actor,
        type=lane_event.type,
        payload=lane_event.payload,
        system_correlation=lane_event.system_correlation,
        idempotency_key=lane_event.idempotency_key,
    )

    from sedna.knowledge.hades_runtime import HadesKnowledgeRuntime
    from sedna.plugin import register

    context = _PublicPluginContext(root)
    register(context)
    runtime = HadesKnowledgeRuntime.create(context, root)
    try:
        engagements = runtime.engagements
        reporting = runtime.reporting
        assert engagements is not None
        assert reporting is not None
        with (
            EngagementJournalRepository(root) as repository,
            EngagementJournalService.open(root) as journal,
        ):
            physical_root = root.parent
            before_files = tuple(
                (path.relative_to(physical_root).as_posix(), path.read_bytes())
                for path in sorted(physical_root.rglob("*"))
                if path.is_file()
            )

            plan_result = context.tools["sedna_plan_next"](
                {},
                session_id=lane.session_id,
                task_id=lane.task_id,
            )
            assert isinstance(plan_result, str)
            assert json.loads(plan_result) == {
                "ok": False,
                "error": {"code": "promotion_saga_in_progress", "retryable": True},
            }
            manage_result = context.tools["sedna_manage_engagement"](
                action="reopen",
                engagement_id=snapshot.engagement_id,
                reason="public lifecycle fence probe",
                session_id=lane.session_id,
                task_id=lane.task_id,
            )
            assert manage_result == {
                "ok": False,
                "error": {"code": "promotion_saga_in_progress", "retryable": True},
            }

            hook_arguments = {"command": "public stable fence probe"}
            for hook_name, hook_result in (
                ("pre_tool_call", None),
                ("post_tool_call", {"ok": True}),
            ):
                with pytest.raises(PromotionSagaInProgressError):
                    context.hooks[hook_name](
                        tool_name="shell",
                        args=hook_arguments,
                        result=hook_result,
                        tool_call_id="public-fence-stable-call",
                        session_id=lane.session_id,
                        task_id=lane.task_id,
                    )

            public_mutations = (
                lambda: repository.append_batch(
                    snapshot.engagement_id,
                    (duplicate_lane,),
                    expected_revision=JournalRevision(sequence=0, event_hash="0" * 64),
                ),
                lambda: journal.write_evidence(
                    snapshot.engagement_id,
                    b"foreign evidence during lifecycle cleanup",
                    media_type="text/plain",
                    representation="utf-8",
                ),
                lambda: repository.write_projection(
                    snapshot.engagement_id,
                    name="state",
                    owner="planning",
                    envelope={"payload": {}},
                    expected_revision=snapshot.revision,
                ),
                lambda: repository.commit_strategy_archive(
                    snapshot.engagement_id,
                    schema_id="sedna.strategy-archive.v1",
                    records=(),
                    expected_archive_revision=None,
                    expected_journal_revision=snapshot.revision,
                ),
                lambda: reporting.regenerate_markdown(
                    snapshot.engagement_id,
                    snapshot.state.active_report.report_revision,
                ),
                lambda: runtime.planning.plan_next(lane),
                lambda: engagements.reopen(
                    snapshot.engagement_id,
                    lane=lane,
                    reason="direct lifecycle fence probe",
                ),
                lambda: engagements.reject_flag(
                    snapshot.engagement_id,
                    lane=lane,
                    flag_event_id=_FLAG_ID,
                    reason="direct lifecycle fence probe",
                ),
            )
            for mutate in public_mutations:
                with pytest.raises(PromotionSagaInProgressError):
                    mutate()

            assert journal.load_snapshot(snapshot.engagement_id) == snapshot
            assert (
                tuple(
                    (path.relative_to(physical_root).as_posix(), path.read_bytes())
                    for path in sorted(physical_root.rglob("*"))
                    if path.is_file()
                )
                == before_files
            )
    finally:
        runtime.close()


def _assert_private_bounded_transition_projection(
    report: OperationalReport,
    markdown: str,
    events: tuple[JournalEvent, ...],
    *,
    expected_types: tuple[EventType, ...],
    forbidden_values: tuple[str, ...],
) -> None:
    """Prove report transition summaries expose only bounded type/sequence metadata."""

    event_type_by_id = {event.event_id: event.type for event in events}
    transition_items = tuple(
        item
        for item in report.timeline
        if event_type_by_id[item.event_ids[0]]
        in {
            EventType.CASE_PROMOTED,
            EventType.CASE_PROMOTION_REVOKED,
            EventType.CASE_PROMOTION_SUPERSEDED,
        }
    )
    assert tuple(event_type_by_id[item.event_ids[0]] for item in transition_items) == expected_types
    assert all(len(item.event_ids) == 1 for item in transition_items)
    assert all(len(item.summary.encode("utf-8")) <= 64 for item in transition_items)

    transition_json = json.dumps(
        [item.model_dump(mode="json") for item in transition_items],
        ensure_ascii=False,
        sort_keys=True,
    )
    transition_markdown = "\n".join(
        line
        for line in markdown.splitlines()
        if any(event_type.value.replace("_", "\\_") in line for event_type in expected_types)
    )
    assert len(transition_markdown.splitlines()) == len(expected_types)
    serialized = f"{transition_json}\n{transition_markdown}"
    for field_name in (
        "attempt_id",
        "source_id",
        "case_ids",
        "removed_case_ids",
        "canonical_revision",
        "relative_path",
        "sha256",
    ):
        assert field_name not in serialized
    for value in forbidden_values:
        assert value not in serialized


def _reject_intent() -> RevocationLifecycleIntent:
    return RevocationLifecycleIntent.build(
        operation="reject",
        lane=_LANE,
        reopen_reason="platform rejected proof",
        proof_revalidation="retain_rejections",
        receipt_authoritative_revision=_REVISION,
        situation_sha256=_DIGEST,
        proof_requirement_id="proof-root",
        assessment_generation=2,
        flag_event_id=_FLAG_ID,
        rejected_value_sha256="c" * 64,
    )


def test_task6_promotion_events_round_trip_with_exact_append_authority() -> None:
    intent = _reject_intent()
    payloads = (
        PromotionAttemptCancellationRequestedPayload(
            attempt_id=_ATTEMPT_ID,
            promotion_revision=3,
            verification_event_id=_VERIFICATION_ID,
            stage="semantic_committed",
            reason="flag_rejected",
            lifecycle_intent=intent,
        ),
        PromotionRevocationRequestedPayload(
            attempt_id=_ATTEMPT_ID,
            source_id=_SOURCE_ID,
            promoted_case_ids=("case-old",),
            reason="flag_rejected",
            lifecycle_intent=intent,
        ),
        CasePromotionRevokedPayload(
            attempt_id=_ATTEMPT_ID,
            source_id=_SOURCE_ID,
            removed_case_ids=("case-old",),
            canonical_revision=_DIGEST,
        ),
        CasePromotionSupersededPayload(
            prior_attempt_id=_ATTEMPT_ID,
            replacement_attempt_id=_REPLACEMENT_ID,
            source_id=_SOURCE_ID,
            removed_case_ids=("case-old",),
        ),
    )

    expected_owners = {
        "promotion_attempt_cancellation_requested": "promotion_commit_capability",
        "promotion_revocation_requested": "promotion_commit_capability",
        "case_promotion_revoked": "revocation_lifecycle_commit_capability",
        "case_promotion_superseded": "promotion_commit_capability",
    }
    for payload in payloads:
        assert EventPayloadAdapter.validate_json(payload.model_dump_json()) == payload
        assert EVENT_APPEND_OWNER_BY_TYPE[payload.kind] == expected_owners[payload.kind]


def test_report_visible_transition_payloads_enforce_the_128_case_bound() -> None:
    bounded_case_ids = tuple(f"case-{index:03d}" for index in range(128))
    overflowing_case_ids = (*bounded_case_ids, "case-128")

    assert (
        len(
            CasePromotedPayload(
                attempt_id=_ATTEMPT_ID,
                source_id=_SOURCE_ID,
                promotion_revision=1,
                case_ids=bounded_case_ids,
            ).case_ids
        )
        == 128
    )
    assert (
        len(
            CasePromotionRevokedPayload(
                attempt_id=_ATTEMPT_ID,
                source_id=_SOURCE_ID,
                removed_case_ids=bounded_case_ids,
                canonical_revision=_DIGEST,
            ).removed_case_ids
        )
        == 128
    )
    assert (
        len(
            CasePromotionSupersededPayload(
                prior_attempt_id=_ATTEMPT_ID,
                replacement_attempt_id=_REPLACEMENT_ID,
                source_id=_SOURCE_ID,
                removed_case_ids=bounded_case_ids,
            ).removed_case_ids
        )
        == 128
    )

    with pytest.raises(ValueError):
        CasePromotedPayload(
            attempt_id=_ATTEMPT_ID,
            source_id=_SOURCE_ID,
            promotion_revision=1,
            case_ids=overflowing_case_ids,
        )
    with pytest.raises(ValueError):
        CasePromotionRevokedPayload(
            attempt_id=_ATTEMPT_ID,
            source_id=_SOURCE_ID,
            removed_case_ids=overflowing_case_ids,
            canonical_revision=_DIGEST,
        )
    with pytest.raises(ValueError):
        CasePromotionSupersededPayload(
            prior_attempt_id=_ATTEMPT_ID,
            replacement_attempt_id=_REPLACEMENT_ID,
            source_id=_SOURCE_ID,
            removed_case_ids=overflowing_case_ids,
        )


def test_revocation_intent_digest_authenticates_complete_payload() -> None:
    intent = _reject_intent()

    assert RevocationLifecycleIntent.model_validate_json(intent.model_dump_json()) == intent
    assert intent.intent_sha256 == RevocationLifecycleIntent.canonical_digest(intent)

    changed = intent.model_copy(update={"reopen_reason": "different"})
    try:
        RevocationLifecycleIntent.model_validate(changed.model_dump(mode="python"))
    except ValueError as error:
        assert "digest" in str(error)
    else:
        raise AssertionError("altered revocation lifecycle intent was accepted")


def test_task6_promotion_projection_accepts_recoverable_and_terminal_stages() -> None:
    common = {
        "attempt_id": _ATTEMPT_ID,
        "attempt_ordinal": 1,
        "promotion_revision": 3,
        "idempotency_key": _DIGEST,
        "verified_revision": _REVISION,
        "verification_event_id": _VERIFICATION_ID,
        "claim_event_id": _REQUEST_ID,
    }

    cancellation = PromotionAttemptState(
        **common,
        stage="cancellation_requested",
        cancellation_request_event_id=_REQUEST_ID,
    )
    revoked = PromotionAttemptState(
        **common,
        stage="revoked",
        source_id=_SOURCE_ID,
        case_ids=("case-old",),
        revocation_request_event_id=_REQUEST_ID,
        cleanup_event_id=UUID("00000000-0000-4000-8000-000000000607"),
        cleanup_canonical_revision=_DIGEST,
        disposition="cancelled",
    )
    superseded = revoked.model_copy(
        update={
            "stage": "superseded",
            "replacement_attempt_id": _REPLACEMENT_ID,
        }
    )

    assert cancellation.stage == "cancellation_requested"
    assert revoked.stage == "revoked"
    assert superseded.stage == "superseded"


def test_task6_promotion_projection_rejects_incomplete_stage_ownership() -> None:
    common = {
        "attempt_id": _ATTEMPT_ID,
        "attempt_ordinal": 1,
        "promotion_revision": 3,
        "idempotency_key": _DIGEST,
        "verified_revision": _REVISION,
        "verification_event_id": _VERIFICATION_ID,
        "claim_event_id": _REQUEST_ID,
    }

    with pytest.raises(ValueError, match="cancellation request event"):
        PromotionAttemptState.model_validate({**common, "stage": "cancellation_requested"})
    with pytest.raises(ValueError, match="cleanup fields"):
        PromotionAttemptState.model_validate(
            {
                **common,
                "stage": "revoked",
                "source_id": _SOURCE_ID,
                "case_ids": ("case-old",),
                "revocation_request_event_id": _REQUEST_ID,
                "disposition": "cancelled",
            }
        )


def _promotion_event(manifest, payload, event_id: UUID) -> JournalEvent:
    return JournalEvent.model_construct(
        event_id=event_id,
        sequence=8,
        occurred_at=datetime(2026, 8, 15, tzinfo=UTC),
        engagement_id=manifest.engagement_id,
        previous_hash="d" * 64,
        event_hash="e" * 64,
        actor="system",
        type=EventType(payload.kind),
        payload=payload,
    )


def test_supersession_uses_pinned_lineage_after_prior_terminal_was_folded(manifest) -> None:
    replacement = PromotionAttemptState(
        attempt_id=_REPLACEMENT_ID,
        attempt_ordinal=66,
        promotion_revision=4,
        idempotency_key="f" * 64,
        verified_revision=JournalRevision(sequence=70, event_hash="9" * 64),
        verification_event_id=UUID("00000000-0000-4000-8000-000000000608"),
        claim_event_id=UUID("00000000-0000-4000-8000-000000000609"),
        stage="index_pending",
        source_id=_SOURCE_ID,
        case_ids=("case-new",),
    )
    accumulator = _Accumulator.from_manifest(manifest)
    accumulator.promotion_attempts = [replacement]
    accumulator.latest_successful_publication = PromotionPublicationLineage(
        attempt_id=_ATTEMPT_ID,
        promotion_revision=3,
        source_id=_SOURCE_ID,
        case_ids=("case-old",),
    )

    accumulator._apply_promotion(
        _promotion_event(
            manifest,
            CasePromotionSupersededPayload(
                prior_attempt_id=_ATTEMPT_ID,
                replacement_attempt_id=_REPLACEMENT_ID,
                source_id=_SOURCE_ID,
                removed_case_ids=("case-old",),
            ),
            UUID("00000000-0000-4000-8000-000000000610"),
        )
    )

    assert accumulator.promotion_attempts == [replacement]


def test_promoted_revocation_request_becomes_active_until_cleanup(manifest) -> None:
    promoted = PromotionAttemptState(
        attempt_id=_ATTEMPT_ID,
        attempt_ordinal=1,
        promotion_revision=3,
        idempotency_key=_DIGEST,
        verified_revision=_REVISION,
        verification_event_id=_VERIFICATION_ID,
        claim_event_id=_REQUEST_ID,
        stage="promoted",
        source_id=_SOURCE_ID,
        case_ids=("case-old",),
        disposition="promoted",
    )
    lineage = PromotionPublicationLineage(
        attempt_id=_ATTEMPT_ID,
        promotion_revision=3,
        source_id=_SOURCE_ID,
        case_ids=("case-old",),
    )
    accumulator = _Accumulator.from_manifest(manifest)
    accumulator.promotion_attempts = [promoted]
    accumulator.latest_successful_publication = lineage
    request = PromotionRevocationRequestedPayload(
        attempt_id=_ATTEMPT_ID,
        source_id=_SOURCE_ID,
        promoted_case_ids=("case-old",),
        reason="flag_rejected",
        lifecycle_intent=_reject_intent(),
    )

    accumulator._apply_promotion(_promotion_event(manifest, request, _REQUEST_ID))

    active = accumulator.promotion_attempts[-1]
    assert accumulator.promotion_attempts[-2] == promoted
    assert active.stage == "revocation_requested"
    assert active.revocation_request_event_id == _REQUEST_ID
    assert active.disposition is None
    assert accumulator.latest_successful_publication == lineage

    cleanup_id = UUID("00000000-0000-4000-8000-000000000607")
    accumulator._apply_promotion(
        _promotion_event(
            manifest,
            CasePromotionRevokedPayload(
                attempt_id=_ATTEMPT_ID,
                source_id=_SOURCE_ID,
                removed_case_ids=("case-old",),
                canonical_revision=_DIGEST,
            ),
            cleanup_id,
        )
    )

    cleaned = accumulator.promotion_attempts[-1]
    assert cleaned.stage == "revoked"
    assert cleaned.cleanup_event_id == cleanup_id
    assert cleaned.cleanup_canonical_revision == _DIGEST
    assert cleaned.disposition == "cancelled"


def test_task6_capabilities_expose_exact_revocation_and_supersession_contracts() -> None:
    assert list(inspect.signature(PromotionCommitCapability.request_cancellation).parameters) == [
        "self",
        "engagement_id",
        "lane",
        "attempt_id",
        "operation",
        "reopen_reason",
        "proof_rejection",
        "expected_revision",
    ]
    assert list(inspect.signature(PromotionCommitCapability.request_revocation).parameters) == [
        "self",
        "engagement_id",
        "lane",
        "attempt_id",
        "operation",
        "reopen_reason",
        "proof_rejection",
        "expected_revision",
    ]
    assert list(
        inspect.signature(PromotionCommitCapability.commit_superseded_and_promoted).parameters
    ) == ["self", "engagement_id", "replacement", "expected_revision"]
    assert list(
        inspect.signature(
            RevocationLifecycleCommitCapability.commit_cleanup_reject_and_reopen
        ).parameters
    ) == ["self", "engagement_id", "request_event_id", "absence_proof", "expected_revision"]
    assert RevocationAbsenceProof.__dataclass_params__.frozen is True


def test_repository_authors_reopen_cancellation_request_from_live_claim(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    capability = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    claim = capability.claim(
        verified.engagement_id,
        _claim_request(verified.revision, verification_event_id),
        expected_revision=verified.revision,
    )
    assert claim.attempt is not None
    try:
        requested = capability.request_cancellation(
            verified.engagement_id,
            lane=lane,
            attempt_id=claim.attempt.attempt_id,
            operation="reopen",
            reopen_reason="operator requested a verified reopen",
            proof_rejection=None,
            expected_revision=claim.revision,
        )
        snapshot = service.load_snapshot(verified.engagement_id)
        event = snapshot.events[-1]

        assert requested.revision == snapshot.revision
        assert event.type == "promotion_attempt_cancellation_requested"
        assert event.payload.attempt_id == claim.attempt.attempt_id
        assert event.payload.lifecycle_intent.operation == "reopen"
        assert event.payload.lifecycle_intent.reopen_reason == (
            "operator requested a verified reopen"
        )
        assert event.payload.lifecycle_intent.proof_revalidation == "invalidate_all"
        assert snapshot.state.status.value == "closed_verified"
        assert snapshot.state.promotion.active_attempt.stage == "cancellation_requested"
        _assert_foreign_exact_retries_are_fenced(snapshot, tmp_path / "knowledge")

        proof = capability._receipt_ledger.issue(
            RevocationAbsenceProof,
            engagement_id=verified.engagement_id,
            verification_event_id=verification_event_id,
            attempt_id=claim.attempt.attempt_id,
            purpose="request_cleanup",
            request_event_id=requested.event_id,
            source_id=None,
            removed_case_ids=(),
            canonical_state="absent",
            canonical_revision=None,
            index_generation=0,
            index_audit_sha256="f" * 64,
            guard_nonce=UUID("00000000-0000-4000-8000-000000000611"),
        )
        cleanup = RevocationLifecycleCommitCapability(
            capability._writer,
            capability._issuer_token,
            capability._receipt_ledger,
        ).commit_cleanup_and_reopen(
            verified.engagement_id,
            requested.event_id,
            proof,
            requested.revision,
        )
        reopened = service.load_snapshot(verified.engagement_id)

        assert cleanup.revision == reopened.revision
        assert reopened.state.status.value == "active"
        assert reopened.state.promotion.active_attempt is None
        assert [event.type.value for event in reopened.events[-2:]] == [
            "promotion_attempt_terminated",
            "engagement_reopened",
        ]
    finally:
        manager.__exit__(None, None, None)


def test_cancellation_request_atomically_reserves_an_unbound_lane(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    capability = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    claim = capability.claim(
        verified.engagement_id,
        _claim_request(verified.revision, verification_event_id),
        expected_revision=verified.revision,
    )
    assert claim.attempt is not None
    reserved_lane = ExecutionLaneKey(
        host_kind=HostKind.HADES,
        session_id="reserved-revocation-session",
        task_id="reserved-revocation-task",
    )
    try:
        requested = capability.request_cancellation(
            verified.engagement_id,
            lane=reserved_lane,
            attempt_id=claim.attempt.attempt_id,
            operation="reopen",
            reopen_reason="reserve the cleanup owner",
            proof_rejection=None,
            expected_revision=claim.revision,
        )
        snapshot = service.load_snapshot(verified.engagement_id)

        assert requested.revision == snapshot.revision
        assert requested.revision.sequence == claim.revision.sequence + 2
        assert [event.type.value for event in snapshot.events[-2:]] == [
            "lane_bound",
            "promotion_attempt_cancellation_requested",
        ]
        assert snapshot.events[-2].payload.lane == reserved_lane
        assert snapshot.events[-1].payload.lifecycle_intent.lane == reserved_lane
    finally:
        manager.__exit__(None, None, None)


def test_cancellation_request_rejects_a_lane_bound_to_another_engagement_without_mutation(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    capability = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    claim = capability.claim(
        verified.engagement_id,
        _claim_request(verified.revision, verification_event_id),
        expected_revision=verified.revision,
    )
    assert claim.attempt is not None
    foreign_lane = ExecutionLaneKey(
        host_kind=HostKind.HADES,
        session_id="foreign-revocation-session",
        task_id="foreign-revocation-task",
    )
    foreign = service.create_engagement(
        display_name="foreign lane owner",
        objective="Own the competing revocation lane",
        scope=authorized_scope,
        lane=foreign_lane,
        required_proofs=(
            ProofRequirement(
                proof_id="foreign-proof",
                kind="custom",
                description="Foreign proof",
            ),
        ),
    ).snapshot
    before = service.load_snapshot(verified.engagement_id)
    try:
        with pytest.raises(ValueError, match="already bound to another engagement"):
            capability.request_cancellation(
                verified.engagement_id,
                lane=foreign_lane,
                attempt_id=claim.attempt.attempt_id,
                operation="reopen",
                reopen_reason="must not steal another engagement lane",
                proof_rejection=None,
                expected_revision=claim.revision,
            )
        assert service.load_snapshot(verified.engagement_id) == before
        assert service.load_snapshot(foreign.engagement_id) == foreign
    finally:
        manager.__exit__(None, None, None)


def test_exact_cancellation_request_retry_returns_the_existing_lane_bound_batch(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    capability = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    claim = capability.claim(
        verified.engagement_id,
        _claim_request(verified.revision, verification_event_id),
        expected_revision=verified.revision,
    )
    assert claim.attempt is not None
    retry_lane = ExecutionLaneKey(
        host_kind=HostKind.HADES,
        session_id="retry-revocation-session",
        task_id="retry-revocation-task",
    )
    values = {
        "lane": retry_lane,
        "attempt_id": claim.attempt.attempt_id,
        "operation": "reopen",
        "reopen_reason": "retry the exact cleanup request",
        "proof_rejection": None,
        "expected_revision": claim.revision,
    }
    try:
        created = capability.request_cancellation(verified.engagement_id, **values)
        after_created = service.load_snapshot(verified.engagement_id)
        replayed = capability.request_cancellation(verified.engagement_id, **values)

        assert replayed.event_id == created.event_id
        assert replayed.revision == created.revision
        assert service.load_snapshot(verified.engagement_id) == after_created
    finally:
        manager.__exit__(None, None, None)


@pytest.mark.parametrize(
    ("field", "forged_value"),
    (
        ("engagement_id", UUID("00000000-0000-4000-8000-000000000621")),
        ("authoritative_revision", JournalRevision(sequence=0, event_hash="0" * 64)),
        ("situation_sha256", "0" * 64),
        ("proof_requirement_id", "user-flag"),
        ("assessment_generation", 2),
        ("proof_event_id", UUID("00000000-0000-4000-8000-000000000622")),
        ("rejected_value_sha256", "0" * 64),
        ("_issuer_token", object()),
    ),
)
def test_cancellation_request_rejects_every_tampered_settled_receipt_field_without_mutation(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
    field,
    forged_value,
) -> None:
    manager, service, verified, verification_event_id, _references, event_ids = (
        _build_verified_journal(tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory)
    )
    capability = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    claim = capability.claim(
        verified.engagement_id,
        _claim_request(verified.revision, verification_event_id),
        expected_revision=verified.revision,
    )
    assert claim.attempt is not None
    snapshot = service.load_snapshot(verified.engagement_id)
    settlement = SimpleNamespace(
        authoritative_journal_revision=snapshot.revision,
        situation=SituationReducer.rebuild(snapshot),
    )
    receipt = service._issue_lifecycle_commit_capability().rejection_receipt(
        settlement,
        event_ids["root_proof"],
    )
    forged = replace(receipt, **{field: forged_value})
    before = service.load_snapshot(verified.engagement_id)
    try:
        with pytest.raises(ValueError, match="authentic current receipt"):
            capability.request_cancellation(
                verified.engagement_id,
                lane=lane,
                attempt_id=claim.attempt.attempt_id,
                operation="reject",
                reopen_reason="reject forged proof authority",
                proof_rejection=forged,
                expected_revision=claim.revision,
            )
        assert service.load_snapshot(verified.engagement_id) == before
    finally:
        manager.__exit__(None, None, None)


def test_recovery_coordinator_finishes_persisted_cancellation_after_request_response_loss(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    root = tmp_path / "knowledge"
    capability = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    claim = capability.claim(
        verified.engagement_id,
        _claim_request(verified.revision, verification_event_id),
        expected_revision=verified.revision,
    )
    assert claim.attempt is not None
    fired = False

    def lose_request_response(point: str) -> None:
        nonlocal fired
        if point == "append_before_response" and not fired:
            fired = True
            raise RuntimeError("simulated cancellation request response loss")

    try:
        service._repository._fault = lose_request_response
        with pytest.raises(RuntimeError, match="simulated cancellation request response loss"):
            capability.request_cancellation(
                verified.engagement_id,
                lane=lane,
                attempt_id=claim.attempt.attempt_id,
                operation="reopen",
                reopen_reason="recover the durable cancellation intent",
                proof_rejection=None,
                expected_revision=claim.revision,
            )
        persisted = service.load_snapshot(verified.engagement_id)
        assert persisted.state.promotion.active_attempt.stage == "cancellation_requested"
    finally:
        manager.__exit__(None, None, None)

    restarted_manager = EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    )
    restarted = restarted_manager.__enter__()
    canonical = CanonicalKnowledgeRepository(root)
    semantic, _host = _service(canonical, [])
    index = SQLiteRetrievalIndex(root / "retrieval.sqlite")
    maintenance = RetrievalMaintenanceService(canonical, index)
    adapter = JournalPromotionAdapter(
        PromotionCommitCapability(restarted._repository._issue_promotion_journal_writer()),
        inputs=object(),
        compiler=object(),
        semantic=semantic,
        maintenance=maintenance,
        evidence_reader=restarted.read_evidence_slice,
    )
    try:
        recovered = PromotionRecoveryCoordinator(
            journal=restarted,
            adapter=adapter,
        ).resume_for_engagement(verified.engagement_id)
        snapshot = restarted.load_snapshot(verified.engagement_id)

        assert recovered.disposition == "revoked"
        assert recovered.journal_revision == snapshot.revision
        assert snapshot.state.status.value == "active"
        assert snapshot.state.promotion.active_attempt is None
        assert (
            sum(
                event.type == "promotion_attempt_cancellation_requested"
                for event in snapshot.events
            )
            == 1
        )
        assert [event.type.value for event in snapshot.events[-2:]] == [
            "promotion_attempt_terminated",
            "engagement_reopened",
        ]
    finally:
        index.close()
        canonical.close()
        restarted_manager.__exit__(None, None, None)


def test_direct_empty_reject_retry_after_response_loss_returns_event_bound_result(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    manager, service, verified, verification_event_id, _references, event_ids = (
        _build_verified_journal(tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory)
    )
    capability = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    rejection = _settled_rejection_receipt(service, verified, event_ids["root_proof"])
    proof = capability._receipt_ledger.issue(
        RevocationAbsenceProof,
        engagement_id=verified.engagement_id,
        verification_event_id=verification_event_id,
        attempt_id=None,
        purpose="direct_empty",
        request_event_id=None,
        source_id=None,
        removed_case_ids=(),
        canonical_state="absent",
        canonical_revision=None,
        index_generation=0,
        index_audit_sha256="e" * 64,
        guard_nonce=UUID("00000000-0000-4000-8000-000000000612"),
    )
    try:
        revocation = RevocationLifecycleCommitCapability(
            capability._writer,
            capability._issuer_token,
            capability._receipt_ledger,
        )
        fired = False

        def lose_response(point: str) -> None:
            nonlocal fired
            if point == "append_before_response" and not fired:
                fired = True
                raise RuntimeError("simulated direct-empty response loss")

        service._repository._fault = lose_response
        with pytest.raises(RuntimeError, match="simulated direct-empty response loss"):
            revocation.commit_empty_reject_and_reopen(
                verified.engagement_id,
                lane,
                "new evidence",
                rejection,
                proof,
                verified.revision,
            )
        after_loss = service.load_snapshot(verified.engagement_id)
        service._repository._fault = lambda _point: None
        committed = revocation.commit_empty_reject_and_reopen(
            verified.engagement_id,
            lane,
            "new evidence",
            rejection,
            proof,
            verified.revision,
        )
        snapshot = service.load_snapshot(verified.engagement_id)

        assert committed.revision == snapshot.revision
        assert committed.created_event_ids == ()
        assert committed.existing_event_ids == tuple(
            event.event_id for event in snapshot.events[-2:]
        )
        assert snapshot == after_loss
        assert snapshot.state.status.value == "active"
        assert [event.type.value for event in snapshot.events[-2:]] == [
            "flag_rejected",
            "engagement_reopened",
        ]
        assert sum(event.type == "engagement_reopened" for event in snapshot.events) == 1
    finally:
        manager.__exit__(None, None, None)


def test_public_direct_empty_reject_retry_after_response_loss_replays_exact_batch(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, service, verified, _verification_event_id, _references, event_ids = (
        _build_verified_journal(tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory)
    )
    root = tmp_path / "knowledge"
    canonical = CanonicalKnowledgeRepository(root)
    semantic, _host = _service(canonical, [])
    index = SQLiteRetrievalIndex(root / "retrieval.sqlite")
    maintenance = RetrievalMaintenanceService(canonical, index)
    adapter = JournalPromotionAdapter(
        PromotionCommitCapability(service._repository._issue_promotion_journal_writer()),
        inputs=object(),
        compiler=object(),
        semantic=semantic,
        maintenance=maintenance,
        evidence_reader=service.read_evidence_slice,
    )
    cleanup_calls: list[str] = []
    original_invalidate = RetrievalMaintenanceService.invalidate_source_projection
    monkeypatch.setattr(
        RetrievalMaintenanceService,
        "invalidate_source_projection",
        lambda self, source_id: (
            cleanup_calls.append(source_id) or original_invalidate(self, source_id)
        ),
    )
    rejection = _settled_rejection_receipt(service, verified, event_ids["root_proof"])
    fired = False

    def lose_response(point: str) -> None:
        nonlocal fired
        if point == "append_before_response" and not fired:
            fired = True
            raise RuntimeError("simulated public direct-empty response loss")

    try:
        service._repository._fault = lose_response
        with pytest.raises(RuntimeError, match="simulated public direct-empty response loss"):
            adapter.revoke_after_settlement(
                verified.engagement_id,
                lane=lane,
                expected_revision=verified.revision,
                operation="reject",
                reason="new evidence",
                proof_rejection=rejection,
            )
        after_loss = service.load_snapshot(verified.engagement_id)
        service._repository._fault = lambda _point: None

        replayed = adapter.revoke_after_settlement(
            verified.engagement_id,
            lane=lane,
            expected_revision=verified.revision,
            operation="reject",
            reason="new evidence",
            proof_rejection=rejection,
        )

        assert replayed.snapshot == after_loss
        assert replayed.created_event_ids == ()
        assert replayed.existing_event_ids == tuple(
            event.event_id for event in after_loss.events[-2:]
        )
        assert [event.type.value for event in after_loss.events[-2:]] == [
            "flag_rejected",
            "engagement_reopened",
        ]
        assert sum(event.type == "engagement_reopened" for event in after_loss.events) == 1
        assert cleanup_calls == []
    finally:
        index.close()
        canonical.close()
        manager.__exit__(None, None, None)


def test_concurrent_public_direct_empty_reopen_commits_one_exact_batch(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    manager, service, verified, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    root = tmp_path / "knowledge"
    canonical = CanonicalKnowledgeRepository(root)
    semantic, _host = _service(canonical, [])
    index = SQLiteRetrievalIndex(root / "retrieval.sqlite")
    adapter = JournalPromotionAdapter(
        PromotionCommitCapability(service._repository._issue_promotion_journal_writer()),
        inputs=object(),
        compiler=object(),
        semantic=semantic,
        maintenance=RetrievalMaintenanceService(canonical, index),
        evidence_reader=service.read_evidence_slice,
    )
    rendezvous = Barrier(2)

    def reopen():
        rendezvous.wait()
        return adapter.revoke_after_settlement(
            verified.engagement_id,
            lane=lane,
            expected_revision=verified.revision,
            operation="reopen",
            reason="correct evidence",
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(lambda _index: reopen(), range(2)))

        final = service.load_snapshot(verified.engagement_id)
        assert all(result.snapshot == final for result in results)
        assert sorted(len(result.created_event_ids) for result in results) == [0, 1]
        assert sorted(len(result.existing_event_ids) for result in results) == [0, 1]
        assert sum(event.type == EventType.ENGAGEMENT_REOPENED for event in final.events) == 1
    finally:
        index.close()
        canonical.close()
        manager.__exit__(None, None, None)


def test_public_direct_empty_retry_rejects_arbitrary_stale_revision_before_cleanup(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, service, verified, _verification_event_id, _references, event_ids = (
        _build_verified_journal(tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory)
    )
    root = tmp_path / "knowledge"
    canonical = CanonicalKnowledgeRepository(root)
    semantic, _host = _service(canonical, [])
    index = SQLiteRetrievalIndex(root / "retrieval.sqlite")
    maintenance = RetrievalMaintenanceService(canonical, index)
    adapter = JournalPromotionAdapter(
        PromotionCommitCapability(service._repository._issue_promotion_journal_writer()),
        inputs=object(),
        compiler=object(),
        semantic=semantic,
        maintenance=maintenance,
        evidence_reader=service.read_evidence_slice,
    )
    rejection = _settled_rejection_receipt(service, verified, event_ids["root_proof"])
    try:
        adapter.revoke_after_settlement(
            verified.engagement_id,
            lane=lane,
            expected_revision=verified.revision,
            operation="reject",
            reason="new evidence",
            proof_rejection=rejection,
        )
        stale = JournalRevision(
            sequence=verified.revision.sequence - 1,
            event_hash=verified.events[-2].event_hash,
        )
        monkeypatch.setattr(
            adapter,
            "_prove_revocation_absence",
            lambda **_kwargs: pytest.fail("stale retry reached physical cleanup"),
        )

        with pytest.raises(ValueError, match="stale"):
            adapter.revoke_after_settlement(
                verified.engagement_id,
                lane=lane,
                expected_revision=stale,
                operation="reject",
                reason="new evidence",
                proof_rejection=rejection,
            )
    finally:
        index.close()
        canonical.close()
        manager.__exit__(None, None, None)


def test_promoted_revocation_recovers_after_restart_then_repromotes_new_lineage(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    manager, service, verified, verification_event_id, references, event_ids = (
        _build_verified_journal(tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory)
    )
    journal_root = tmp_path / "knowledge"
    canonical_root = journal_root
    index_root = tmp_path / "retrieval"

    def real_adapter(journal):
        canonical = CanonicalKnowledgeRepository(canonical_root)
        semantic, host = _service(canonical, [])
        semantic_responses = _load_responses("windows-walkthrough.json")

        def complete_structured(**kwargs):
            purpose = str(kwargs["purpose"])
            response = copy.deepcopy(
                semantic_responses[0] if purpose.endswith(".extract") else semantic_responses[1]
            )
            if purpose.endswith(".extract"):
                payload = json.loads(kwargs["input"][0]["text"])
                segment_indexes = list(range(len(payload["segments"])))

                def bind_all_segments(value):
                    if isinstance(value, dict):
                        for key, item in value.items():
                            if key == "segment_indexes":
                                value[key] = segment_indexes
                            else:
                                bind_all_segments(item)
                    elif isinstance(value, list):
                        for item in value:
                            bind_all_segments(item)

                bind_all_segments(response)
            return SimpleNamespace(
                parsed=response,
                provider="scripted-provider",
                model="scripted-semantic",
                agent_id="scripted-agent",
                usage=SimpleNamespace(input_tokens=23, output_tokens=17),
                audit=None,
            )

        host.complete_structured = complete_structured
        index = SQLiteRetrievalIndex(index_root / "retrieval.sqlite")
        maintenance = RetrievalMaintenanceService(canonical, index)

        def project(snapshot, *, verification_event_id, evidence_reader):
            del evidence_reader
            return SimpleNamespace(
                safe_input=_context().model_copy(
                    update={
                        "engagement_id": snapshot.engagement_id,
                        "verified_revision": snapshot.revision,
                        "verification_event_id": verification_event_id,
                    }
                ),
                inventory=PromotionSecretInventory(),
            )

        compiler = SimpleNamespace(
            compile=lambda *_args, **_kwargs: SimpleNamespace(
                disposition="verified",
                draft=_draft(),
                repair_count=0,
                failure_code=None,
            )
        )
        return (
            JournalPromotionAdapter(
                PromotionCommitCapability(journal._repository._issue_promotion_journal_writer()),
                inputs=SimpleNamespace(project=project),
                compiler=compiler,
                semantic=semantic,
                maintenance=maintenance,
                evidence_reader=journal.read_evidence_slice,
            ),
            canonical,
            index,
        )

    adapter, canonical, index = real_adapter(service)
    try:
        promoted_v1 = adapter.promote_verified(
            verified.engagement_id,
            expected_revision=verified.revision,
            verification_event_id=verification_event_id,
        )
        assert promoted_v1.disposition == "promoted"
        assert promoted_v1.promotion_revision == 1
        assert promoted_v1.case_ids
        assert promoted_v1.source_id is not None
        promoted_snapshot = service.load_snapshot(verified.engagement_id)
        promotion_report_ref = ReportClosureFinalizer(
            service, service._repository._issue_report_commit_capability()
        ).commit_later_revision(snapshot=promoted_snapshot, reason="manual_report")
        promotion_report_path = journal_root / promotion_report_ref.json_relative_path
        promotion_report_bytes = promotion_report_path.read_bytes()
        promotion_report = OperationalReport.model_validate_json(promotion_report_bytes)
        promotion_markdown = (journal_root / promotion_report_ref.markdown_relative_path).read_text(
            encoding="utf-8"
        )
        promotion_timeline = {
            event.type
            for item in promotion_report.timeline
            for event in promoted_snapshot.events
            if event.event_id in item.event_ids
        }
        assert promotion_report.journal_revision == promoted_snapshot.revision
        assert EventType.CASE_PROMOTED in promotion_timeline
        assert EventType.CASE_PROMOTION_REVOKED not in promotion_timeline
        assert EventType.CASE_PROMOTION_SUPERSEDED not in promotion_timeline
        private_evidence_values = tuple(
            value
            for reference in references
            for value in (str(reference.evidence_id), reference.relative_path, reference.sha256)
        )
        _assert_private_bounded_transition_projection(
            promotion_report,
            promotion_markdown,
            promoted_snapshot.events,
            expected_types=(EventType.CASE_PROMOTED,),
            forbidden_values=(
                promoted_v1.source_id,
                *promoted_v1.case_ids,
                *private_evidence_values,
            ),
        )
        promoted_snapshot = service.load_snapshot(verified.engagement_id)
        rejection = service._issue_lifecycle_commit_capability().rejection_receipt(
            SimpleNamespace(
                authoritative_journal_revision=promoted_snapshot.revision,
                situation=SituationReducer.rebuild(promoted_snapshot),
            ),
            event_ids["root_proof"],
        )
        fired = False

        def lose_request_response(point: str) -> None:
            nonlocal fired
            if point == "append_before_response" and not fired:
                fired = True
                raise RuntimeError("simulated promoted revocation response loss")

        service._repository._fault = lose_request_response
        with pytest.raises(RuntimeError, match="simulated promoted revocation response loss"):
            adapter.revoke_after_settlement(
                verified.engagement_id,
                lane=lane,
                expected_revision=promoted_snapshot.revision,
                operation="reject",
                reason="published proof was rejected",
                proof_rejection=rejection,
            )
        requested = service.load_snapshot(verified.engagement_id)
        assert requested.state.promotion.active_attempt.stage == "revocation_requested"
        _assert_foreign_exact_retries_are_fenced(requested, journal_root)
    finally:
        index.close()
        canonical.close()
        manager.__exit__(None, None, None)

    restarted_manager = EngagementJournalService.open(
        journal_root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    )
    restarted = restarted_manager.__enter__()
    restarted_adapter, restarted_canonical, restarted_index = real_adapter(restarted)
    try:
        recovered = PromotionRecoveryCoordinator(
            journal=restarted,
            adapter=restarted_adapter,
        ).resume_for_engagement(verified.engagement_id)
        active = restarted.load_snapshot(verified.engagement_id)
        source_id = promoted_v1.source_id
        assert source_id is not None
        assert recovered.disposition == "revoked"
        assert active.state.status.value == "active"
        assert active.state.promotion.active_attempt is None
        assert restarted_canonical.semantic_bundle_snapshot().bundles == ()
        assert all(
            state.source_id != source_id for state in restarted_index.snapshot_state().source_states
        )

        closing = restarted.request_close(
            active.engagement_id,
            lane=lane,
            reason="corrected evidence is complete",
            expected_revision=active.revision,
        ).snapshot
        closed = ReportClosureFinalizer(
            restarted, restarted._repository._issue_report_commit_capability()
        ).finalize(snapshot=closing)
        report = closed.state.active_report
        assert report is not None
        reclose_report = OperationalReport.model_validate_json(
            (journal_root / report.json_relative_path).read_bytes()
        )
        reclose_markdown = (journal_root / report.markdown_relative_path).read_text(
            encoding="utf-8"
        )
        reclose_timeline = {
            event.type
            for item in reclose_report.timeline
            for event in closed.events
            if event.event_id in item.event_ids
        }
        assert EventType.CASE_PROMOTION_REVOKED in reclose_timeline
        assert EventType.CASE_PROMOTION_SUPERSEDED not in reclose_timeline
        revoked_event = next(
            event for event in closed.events if event.type == EventType.CASE_PROMOTION_REVOKED
        )
        assert isinstance(revoked_event.payload, CasePromotionRevokedPayload)
        _assert_private_bounded_transition_projection(
            reclose_report,
            reclose_markdown,
            closed.events,
            expected_types=(EventType.CASE_PROMOTED, EventType.CASE_PROMOTION_REVOKED),
            forbidden_values=(
                promoted_v1.source_id,
                *promoted_v1.case_ids,
                revoked_event.payload.canonical_revision,
                *private_evidence_values,
            ),
        )
        assert promotion_report_path.read_bytes() == promotion_report_bytes
        verification_v2 = UUID("00000000-0000-4000-8000-000000000650")
        reverified = (
            restarted._issue_lifecycle_commit_capability()
            .commit_verified(
                active.engagement_id,
                JournalEventDraft(
                    event_id=verification_v2,
                    actor="system",
                    type=EventType.ENGAGEMENT_VERIFIED,
                    payload=EngagementVerifiedPayload(
                        report_id=report.report_id,
                        report_revision=report.report_revision,
                        verification_kind="platform",
                        verification_reference="platform-proof-v2",
                    ),
                    system_correlation=SystemCorrelation(
                        source="lifecycle",
                        operation_id=UUID("00000000-0000-4000-8000-000000000651"),
                    ),
                ),
                expected_revision=closed.revision,
            )
            .snapshot
        )
        direct_empty = restarted_adapter.revoke_after_settlement(
            reverified.engagement_id,
            lane=lane,
            expected_revision=reverified.revision,
            operation="reopen",
            reason="reverified publication intentionally remained empty",
        )
        assert direct_empty.snapshot.state.status.value == "active"
        assert restarted_canonical.semantic_bundle_snapshot().bundles == ()
        assert all(
            state.source_id != source_id for state in restarted_index.snapshot_state().source_states
        )

        closing_v3 = restarted.request_close(
            active.engagement_id,
            lane=lane,
            reason="replacement evidence is complete",
            expected_revision=direct_empty.snapshot.revision,
        ).snapshot
        closed_v3 = ReportClosureFinalizer(
            restarted, restarted._repository._issue_report_commit_capability()
        ).finalize(snapshot=closing_v3)
        report_v3 = closed_v3.state.active_report
        assert report_v3 is not None
        verification_v3 = UUID("00000000-0000-4000-8000-000000000652")
        reverified_v3 = (
            restarted._issue_lifecycle_commit_capability()
            .commit_verified(
                active.engagement_id,
                JournalEventDraft(
                    event_id=verification_v3,
                    actor="system",
                    type=EventType.ENGAGEMENT_VERIFIED,
                    payload=EngagementVerifiedPayload(
                        report_id=report_v3.report_id,
                        report_revision=report_v3.report_revision,
                        verification_kind="platform",
                        verification_reference="platform-proof-v3",
                    ),
                    system_correlation=SystemCorrelation(
                        source="lifecycle",
                        operation_id=UUID("00000000-0000-4000-8000-000000000653"),
                    ),
                ),
                expected_revision=closed_v3.revision,
            )
            .snapshot
        )
        promoted_v2 = restarted_adapter.promote_verified(
            reverified_v3.engagement_id,
            expected_revision=reverified_v3.revision,
            verification_event_id=verification_v3,
        )
        final = restarted.load_snapshot(verified.engagement_id)

        assert promoted_v2.disposition == "promoted"
        assert promoted_v2.promotion_revision == 2
        assert promoted_v2.attempt_id != promoted_v1.attempt_id
        assert final.state.promotion.latest_successful_publication.attempt_id == (
            promoted_v2.attempt_id
        )
        assert final.state.promotion.latest_successful_publication.case_ids == (
            promoted_v2.case_ids
        )
        assert all(case_id not in promoted_v2.case_ids for case_id in promoted_v1.case_ids)
        assert sum(event.type == "case_promotion_revoked" for event in final.events) == 1
        assert sum(event.type == "case_promoted" for event in final.events) == 2
        assert sum(event.type == "engagement_reopened" for event in final.events) == 2
        replacement_report_ref = ReportClosureFinalizer(
            restarted, restarted._repository._issue_report_commit_capability()
        ).commit_later_revision(snapshot=final, reason="manual_report")
        replacement_report = OperationalReport.model_validate_json(
            (journal_root / replacement_report_ref.json_relative_path).read_bytes()
        )
        replacement_markdown = (
            journal_root / replacement_report_ref.markdown_relative_path
        ).read_text(encoding="utf-8")
        replacement_timeline_ids = {
            event_id for item in replacement_report.timeline for event_id in item.event_ids
        }
        superseded_index = next(
            index
            for index, event in enumerate(final.events)
            if event.type == EventType.CASE_PROMOTION_SUPERSEDED
        )
        superseded, replacement = final.events[superseded_index : superseded_index + 2]
        assert replacement.type == EventType.CASE_PROMOTED
        assert replacement.sequence == superseded.sequence + 1
        assert {superseded.event_id, replacement.event_id} <= replacement_timeline_ids
        assert reclose_report.journal_revision.sequence < superseded.sequence
        assert promotion_report_path.read_bytes() == promotion_report_bytes
        assert promoted_v2.source_id is not None
        _assert_private_bounded_transition_projection(
            replacement_report,
            replacement_markdown,
            final.events,
            expected_types=(
                EventType.CASE_PROMOTED,
                EventType.CASE_PROMOTION_REVOKED,
                EventType.CASE_PROMOTION_SUPERSEDED,
                EventType.CASE_PROMOTED,
            ),
            forbidden_values=(
                promoted_v1.source_id,
                *promoted_v1.case_ids,
                revoked_event.payload.canonical_revision,
                promoted_v2.source_id,
                *promoted_v2.case_ids,
                *private_evidence_values,
            ),
        )
    finally:
        restarted_index.close()
        restarted_canonical.close()
        restarted_manager.__exit__(None, None, None)


def test_real_direct_empty_revocation_survives_restart_reverification_and_reuse(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    manager, service, verified, _verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    root = tmp_path / "knowledge"

    def real_adapter(journal):
        canonical = CanonicalKnowledgeRepository(root)
        semantic, _host = _service(canonical, [])
        index = SQLiteRetrievalIndex(root / "retrieval.sqlite")
        maintenance = RetrievalMaintenanceService(canonical, index)
        capability = PromotionCommitCapability(
            journal._repository._issue_promotion_journal_writer()
        )
        return (
            JournalPromotionAdapter(
                capability,
                inputs=object(),
                compiler=object(),
                semantic=semantic,
                maintenance=maintenance,
                evidence_reader=journal.read_evidence_slice,
            ),
            canonical,
            index,
        )

    adapter, canonical, index = real_adapter(service)
    try:
        first = adapter.revoke_after_settlement(
            verified.engagement_id,
            lane=lane,
            expected_revision=verified.revision,
            operation="reopen",
            reason="first verified publication is empty",
        )
        assert first.snapshot.state.status.value == "active"
    finally:
        index.close()
        canonical.close()
        manager.__exit__(None, None, None)

    restarted_manager = EngagementJournalService.open(
        root, clock=fixed_clock, uuid_factory=fixed_uuid_factory
    )
    restarted = restarted_manager.__enter__()
    restarted_adapter, restarted_canonical, restarted_index = real_adapter(restarted)
    try:
        active = restarted.load_snapshot(verified.engagement_id)
        assert active.state.status.value == "active"
        assert active.state.promotion.active_attempt is None
        assert active.state.promotion.recent_terminal_attempts == ()

        closing = restarted.request_close(
            active.engagement_id,
            lane=lane,
            reason="reverified objectives complete",
            expected_revision=active.revision,
        ).snapshot
        closed = ReportClosureFinalizer(
            restarted, restarted._repository._issue_report_commit_capability()
        ).finalize(snapshot=closing)
        report = closed.state.active_report
        assert report is not None
        verification_event_id = UUID("00000000-0000-4000-8000-000000000640")
        reverified = (
            restarted._issue_lifecycle_commit_capability()
            .commit_verified(
                active.engagement_id,
                JournalEventDraft(
                    event_id=verification_event_id,
                    actor="system",
                    type=EventType.ENGAGEMENT_VERIFIED,
                    payload=EngagementVerifiedPayload(
                        report_id=report.report_id,
                        report_revision=report.report_revision,
                        verification_kind="platform",
                        verification_reference="platform-proof-43",
                    ),
                    system_correlation=SystemCorrelation(
                        source="lifecycle",
                        operation_id=UUID("00000000-0000-4000-8000-000000000641"),
                    ),
                ),
                expected_revision=closed.revision,
            )
            .snapshot
        )

        second = restarted_adapter.revoke_after_settlement(
            reverified.engagement_id,
            lane=lane,
            expected_revision=reverified.revision,
            operation="reopen",
            reason="second verified publication is empty",
        )

        verification_events = tuple(
            event for event in second.snapshot.events if event.type == "engagement_verified"
        )
        reopen_events = tuple(
            event for event in second.snapshot.events if event.type == "engagement_reopened"
        )
        assert second.snapshot.state.status.value == "active"
        assert second.snapshot.state.promotion.active_attempt is None
        assert second.snapshot.state.promotion.recent_terminal_attempts == ()
        assert len(verification_events) == 2
        assert verification_events[0].event_id != verification_events[1].event_id
        assert len(reopen_events) == 2
        assert reopen_events[0].event_id != reopen_events[1].event_id
        assert reopen_events[0].system_correlation.operation_id != (
            reopen_events[1].system_correlation.operation_id
        )
    finally:
        restarted_index.close()
        restarted_canonical.close()
        restarted_manager.__exit__(None, None, None)


@pytest.mark.parametrize("stage", ("requested", "candidate_ready", "source_committed"))
def test_pre_foundation_current_attempt_reports_current_foundation_absent(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    manifest = object()
    repository = SimpleNamespace(
        load_manifest=lambda _source_id: manifest,
        semantic_bundle_snapshot=lambda: SimpleNamespace(bundles=()),
    )
    index = SimpleNamespace(
        generation=4,
        source_states=(),
        audit=SimpleNamespace(model_dump=lambda **_kwargs: {"generation": 4}),
    )
    maintenance = SimpleNamespace(
        audit=lambda: SimpleNamespace(succeeded=True, rebuild_required=False),
        index=SimpleNamespace(snapshot_state=lambda: index),
    )
    ledger = _ReceiptLedger(object())
    adapter = object.__new__(JournalPromotionAdapter)
    cast(Any, adapter)._semantic = SimpleNamespace(_repository=repository)
    cast(Any, adapter)._maintenance = maintenance
    cast(Any, adapter)._capability = SimpleNamespace(_receipt_ledger=ledger)
    monkeypatch.setattr(
        "sedna.engagement.promotion.adapter.build_nonaccepted_promotion_manifest",
        lambda current, **_kwargs: current,
    )
    monkeypatch.setattr(
        "sedna.engagement.promotion.adapter.foundation_manifest_digest",
        lambda _manifest: _DIGEST,
    )
    active = SimpleNamespace(
        attempt_id=_ATTEMPT_ID,
        stage=stage,
        source_id=_SOURCE_ID if stage == "source_committed" else None,
        case_ids=(),
    )

    proof = adapter._prove_revocation_absence(
        engagement_id=UUID("00000000-0000-4000-8000-000000000620"),
        active=active,
        request_event_id=_REQUEST_ID,
        verification_event_id=_VERIFICATION_ID,
        purpose="request_cleanup",
    )

    assert proof.canonical_state == "absent"
    assert proof.canonical_revision is None
    assert proof.source_id is None
    assert proof.removed_case_ids == ()
    assert proof.attempt_id == _ATTEMPT_ID


def test_direct_empty_reopen_accepts_revoked_pinned_lineage_from_prior_verification(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    repository = service._repository
    current = service.load_snapshot(verified.engagement_id)
    revoked = PromotionAttemptState(
        attempt_id=_ATTEMPT_ID,
        attempt_ordinal=1,
        promotion_revision=1,
        idempotency_key=_DIGEST,
        verified_revision=JournalRevision(sequence=2, event_hash="d" * 64),
        verification_event_id=UUID("00000000-0000-4000-8000-000000000621"),
        claim_event_id=UUID("00000000-0000-4000-8000-000000000622"),
        stage="revoked",
        source_id=_SOURCE_ID,
        case_ids=("case-old",),
        revocation_request_event_id=_REQUEST_ID,
        cleanup_event_id=UUID("00000000-0000-4000-8000-000000000623"),
        cleanup_canonical_revision="c" * 64,
        disposition="cancelled",
    )
    lineage = PromotionPublicationLineage(
        attempt_id=_ATTEMPT_ID,
        promotion_revision=1,
        source_id=_SOURCE_ID,
        case_ids=("case-old",),
    )
    snapshot = current.model_copy(
        update={
            "state": current.state.model_copy(
                update={
                    "promotion": PromotionState(
                        recent_terminal_attempts=(revoked,),
                        latest_successful_publication=lineage,
                    )
                }
            )
        }
    )
    capability = PromotionCommitCapability(repository._issue_promotion_journal_writer())
    revocation = RevocationLifecycleCommitCapability(
        capability._writer,
        capability._issuer_token,
        capability._receipt_ledger,
    )
    physical_checks: list[str] = []

    @contextmanager
    def guard(_source_id):
        physical_checks.append("guard")
        yield

    def missing_manifest(_source_id):
        physical_checks.append("manifest")
        raise FileNotFoundError

    semantic_repository = SimpleNamespace(
        promotion_publication_guard=guard,
        load_manifest=missing_manifest,
        semantic_bundle_snapshot=lambda: SimpleNamespace(bundles=()),
    )
    index = SimpleNamespace(
        generation=4,
        source_states=(),
        audit=SimpleNamespace(model_dump=lambda **_kwargs: {"generation": 4}),
    )
    maintenance = SimpleNamespace(
        invalidate_source_projection=lambda _source_id: physical_checks.append("index") or True,
        audit=lambda: SimpleNamespace(succeeded=True, rebuild_required=False),
        index=SimpleNamespace(snapshot_state=lambda: index),
    )
    adapter = object.__new__(JournalPromotionAdapter)
    cast(Any, adapter)._capability = capability
    cast(Any, adapter)._revocation_commits = revocation
    cast(Any, adapter)._semantic = SimpleNamespace(_repository=semantic_repository)
    cast(Any, adapter)._maintenance = maintenance
    appended = SimpleNamespace(
        created_event_ids=(UUID("00000000-0000-4000-8000-000000000624"),),
        existing_event_ids=(),
    )
    monkeypatch.setattr(repository, "load_snapshot", lambda _engagement_id: snapshot)
    monkeypatch.setattr(repository, "append_lifecycle_batch", lambda *_args, **_kwargs: appended)
    try:
        result = adapter.revoke_after_settlement(
            verified.engagement_id,
            lane=lane,
            expected_revision=snapshot.revision,
            operation="reopen",
            reason="new verification had no publication",
        )
    finally:
        manager.__exit__(None, None, None)

    assert result.created_event_ids == appended.created_event_ids
    assert physical_checks == ["guard", "manifest"]


def test_current_terminal_pre_foundation_attempt_uses_direct_empty_reopen() -> None:
    engagement_id = UUID("00000000-0000-4000-8000-000000000625")
    current_verification_id = UUID("00000000-0000-4000-8000-000000000626")
    terminal = PromotionAttemptState(
        attempt_id=_ATTEMPT_ID,
        attempt_ordinal=2,
        promotion_revision=2,
        idempotency_key="f" * 64,
        verified_revision=_REVISION,
        verification_event_id=current_verification_id,
        claim_event_id=UUID("00000000-0000-4000-8000-000000000627"),
        stage="terminated",
        disposition="failed",
        reason_code="transport_failure",
    )
    verification = SimpleNamespace(type="engagement_verified", event_id=current_verification_id)
    snapshot = SimpleNamespace(
        revision=_REVISION,
        events=(verification,),
        state=SimpleNamespace(
            status=SimpleNamespace(value="closed_verified"),
            promotion=PromotionState(recent_terminal_attempts=(terminal,)),
        ),
    )
    calls: list[dict[str, object]] = []

    @contextmanager
    def guard(_source_id):
        yield

    adapter = object.__new__(JournalPromotionAdapter)
    cast(Any, adapter)._capability = SimpleNamespace(
        _writer=SimpleNamespace(load_snapshot=lambda _engagement_id: snapshot)
    )
    cast(Any, adapter)._semantic = SimpleNamespace(
        _repository=SimpleNamespace(promotion_publication_guard=guard)
    )
    cast(Any, adapter)._prove_revocation_absence = lambda **values: calls.append(values) or object()
    cast(Any, adapter)._revocation_commits = SimpleNamespace(
        commit_empty_and_reopen=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("commit reached")
        )
    )

    with pytest.raises(RuntimeError, match="commit reached"):
        adapter.revoke_after_settlement(
            engagement_id,
            lane=_LANE,
            expected_revision=_REVISION,
            operation="reopen",
            reason="retry with corrected evidence",
        )

    assert calls == [
        {
            "engagement_id": engagement_id,
            "active": terminal,
            "request_event_id": None,
            "verification_event_id": current_verification_id,
            "purpose": "direct_empty",
        }
    ]


def test_direct_empty_capability_rejects_forged_excluded_cross_shape(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    capability = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    forged = capability._receipt_ledger.issue(
        RevocationAbsenceProof,
        engagement_id=verified.engagement_id,
        verification_event_id=verification_event_id,
        attempt_id=None,
        purpose="direct_empty",
        request_event_id=None,
        source_id=_SOURCE_ID,
        removed_case_ids=("case-old",),
        canonical_state="absent",
        canonical_revision=None,
        index_generation=0,
        index_audit_sha256="e" * 64,
        guard_nonce=UUID("00000000-0000-4000-8000-000000000628"),
    )
    revocation = RevocationLifecycleCommitCapability(
        capability._writer,
        capability._issuer_token,
        capability._receipt_ledger,
    )
    try:
        with pytest.raises(ValueError, match="invalid repository-bound"):
            revocation.commit_empty_and_reopen(
                verified.engagement_id,
                lane,
                "new evidence",
                forged,
                verified.revision,
            )
    finally:
        manager.__exit__(None, None, None)


def test_request_cleanup_proof_allows_existing_excluded_foundation_without_case_ids() -> None:
    token = object()
    ledger = _ReceiptLedger(token)
    proof = ledger.issue(
        RevocationAbsenceProof,
        engagement_id=_ATTEMPT_ID,
        verification_event_id=_VERIFICATION_ID,
        attempt_id=_ATTEMPT_ID,
        purpose="request_cleanup",
        request_event_id=_REQUEST_ID,
        source_id=_SOURCE_ID,
        removed_case_ids=(),
        canonical_state="excluded",
        canonical_revision=_DIGEST,
        index_generation=0,
        index_audit_sha256="e" * 64,
        guard_nonce=UUID("00000000-0000-4000-8000-000000000635"),
    )
    capability = object.__new__(RevocationLifecycleCommitCapability)
    cast(Any, capability)._proof_ledger = ledger

    capability._require_absence_proof(
        _ATTEMPT_ID,
        _REQUEST_ID,
        proof,
        "request_cleanup",
    )
    ledger.consume(proof, RevocationAbsenceProof, "revocation_cleanup")
    with pytest.raises(ValueError, match="operation nonce was already consumed"):
        capability._require_absence_proof(
            _ATTEMPT_ID,
            _REQUEST_ID,
            proof,
            "request_cleanup",
        )


def test_recovered_cancellation_uses_its_original_accepted_foundation_stage() -> None:
    engagement_id = UUID("00000000-0000-4000-8000-000000000629")
    verification_id = UUID("00000000-0000-4000-8000-000000000630")
    request_id = UUID("00000000-0000-4000-8000-000000000631")
    active = PromotionAttemptState(
        attempt_id=_ATTEMPT_ID,
        attempt_ordinal=1,
        promotion_revision=1,
        idempotency_key=_DIGEST,
        verified_revision=_REVISION,
        verification_event_id=verification_id,
        claim_event_id=UUID("00000000-0000-4000-8000-000000000632"),
        stage="cancellation_requested",
        source_id=_SOURCE_ID,
        case_ids=("case-old",),
        cancellation_request_event_id=request_id,
    )
    intent = SimpleNamespace(operation="reopen", lane=_LANE, reopen_reason="correct evidence")
    snapshot = SimpleNamespace(
        revision=_REVISION,
        events=(
            SimpleNamespace(type="engagement_verified", event_id=verification_id),
            SimpleNamespace(
                type="promotion_attempt_cancellation_requested",
                event_id=request_id,
                payload=SimpleNamespace(stage="semantic_committed", lifecycle_intent=intent),
            ),
        ),
        state=SimpleNamespace(
            status=SimpleNamespace(value="closed_verified"),
            promotion=SimpleNamespace(active_attempt=active),
        ),
    )
    proof_stages: list[str] = []
    guard_order: list[str] = []

    @contextmanager
    def guard(_source_id):
        guard_order.append("guard-enter")
        try:
            yield
        finally:
            guard_order.append("guard-exit")

    adapter = object.__new__(JournalPromotionAdapter)
    cast(Any, adapter)._capability = SimpleNamespace(
        _writer=SimpleNamespace(load_snapshot=lambda _engagement_id: snapshot)
    )
    cast(Any, adapter)._semantic = SimpleNamespace(
        _repository=SimpleNamespace(promotion_publication_guard=guard)
    )
    cast(Any, adapter)._prove_revocation_absence = lambda **values: (
        proof_stages.append(values["active"].stage) or guard_order.append("proof") or object()
    )
    cast(Any, adapter)._revocation_commits = SimpleNamespace(
        commit_cleanup_and_reopen=lambda *_args, **_kwargs: (
            guard_order.append("commit") or (_ for _ in ()).throw(RuntimeError("commit reached"))
        )
    )

    with pytest.raises(RuntimeError, match="commit reached"):
        adapter.revoke_after_settlement(
            engagement_id,
            lane=_LANE,
            expected_revision=_REVISION,
            operation="reopen",
            reason="correct evidence",
        )

    assert proof_stages == ["semantic_committed"]
    assert guard_order == ["guard-enter", "proof", "commit", "guard-exit"]


def test_direct_empty_proof_is_bound_to_its_capability_holder(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    writer = service._repository._issue_promotion_journal_writer()
    issuer = PromotionCommitCapability(writer)
    other_holder = PromotionCommitCapability(writer)
    proof = issuer._receipt_ledger.issue(
        RevocationAbsenceProof,
        engagement_id=verified.engagement_id,
        verification_event_id=verification_event_id,
        attempt_id=None,
        purpose="direct_empty",
        request_event_id=None,
        source_id=None,
        removed_case_ids=(),
        canonical_state="absent",
        canonical_revision=None,
        index_generation=0,
        index_audit_sha256="e" * 64,
        guard_nonce=UUID("00000000-0000-4000-8000-000000000633"),
    )
    revocation = RevocationLifecycleCommitCapability(
        other_holder._writer,
        other_holder._issuer_token,
        other_holder._receipt_ledger,
    )
    before = service.load_snapshot(verified.engagement_id)
    try:
        with pytest.raises(ValueError, match="invalid promotion receipt payload binding"):
            revocation.commit_empty_and_reopen(
                verified.engagement_id,
                lane,
                "new evidence",
                proof,
                verified.revision,
            )
        assert service.load_snapshot(verified.engagement_id) == before
    finally:
        manager.__exit__(None, None, None)


def test_direct_empty_proof_rejects_stale_revision_without_consumption(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    manager, service, verified, verification_event_id, *_ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    capability = PromotionCommitCapability(service._repository._issue_promotion_journal_writer())
    proof = capability._receipt_ledger.issue(
        RevocationAbsenceProof,
        engagement_id=verified.engagement_id,
        verification_event_id=verification_event_id,
        attempt_id=None,
        purpose="direct_empty",
        request_event_id=None,
        source_id=None,
        removed_case_ids=(),
        canonical_state="absent",
        canonical_revision=None,
        index_generation=0,
        index_audit_sha256="e" * 64,
        guard_nonce=UUID("00000000-0000-4000-8000-000000000634"),
    )
    revocation = RevocationLifecycleCommitCapability(
        capability._writer,
        capability._issuer_token,
        capability._receipt_ledger,
    )
    before = service.load_snapshot(verified.engagement_id)
    try:
        with pytest.raises(ValueError, match="absence fence"):
            revocation.commit_empty_and_reopen(
                verified.engagement_id,
                lane,
                "new evidence",
                proof,
                JournalRevision(sequence=1, event_hash="0" * 64),
            )
        assert service.load_snapshot(verified.engagement_id) == before
        capability._receipt_ledger.require_available(
            proof,
            RevocationAbsenceProof,
            "revocation_cleanup",
        )
    finally:
        manager.__exit__(None, None, None)
