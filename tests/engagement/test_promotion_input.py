from __future__ import annotations

import inspect
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID

import pytest

from sedna.engagement import (
    EngagementJournalService,
    EventType,
    EvidenceAttachedPayload,
    JournalEventDraft,
    ObjectiveProofObservedEventPayload,
    ObservationExtractedEventPayload,
    OutcomeAssessedEventPayload,
    ProofRequirement,
    SystemCorrelation,
)
from sedna.engagement.events import (
    ArchivedStrategyEventRecord,
    CommandSuggestionEventRecord,
    EngagementVerifiedPayload,
    EvidenceSliceEventRef,
    FacetObservationEventRecord,
    FrontierProposalEventRecord,
    FrontierProposedEventPayload,
    PrivateValueEventRecord,
    SecretReferenceEventRecord,
    StrategyArchivedEventPayload,
    StrategyFamilyEventRecord,
    StrategyReactivatedEventPayload,
    StrategyReconciledEventPayload,
    StrategyReconciliationEventOperation,
)
from sedna.engagement.models import EvidenceReference, EvidenceSlice, HostAdaptedCommandRecord
from sedna.engagement.promotion.input import (
    PROMOTION_IGNORED_EVENT_TYPES,
    PROMOTION_PROJECTED_EVENT_TYPES,
    PrivatePromotionProjection,
    PromotionInputProjector,
    _resolve_private_value,
    _summary_for,
)
from sedna.engagement.reporting.service import ReportClosureFinalizer
from sedna.engagement.service import PlanningEventCommitItem

CREDENTIAL = b"OrionAdm!n:Summer2026"
USER_FLAG = b"HTB{user-private-proof}"
ROOT_FLAG = b"0123456789abcdef0123456789abcdef"


def _slice(reference) -> EvidenceSliceEventRef:
    return EvidenceSliceEventRef(
        evidence_id=reference.evidence_id,
        start=0,
        end=reference.size,
        sha256=reference.sha256,
        media_type="text/plain",
    )


def _private(reference) -> PrivateValueEventRecord:
    return PrivateValueEventRecord(evidence_slice=_slice(reference), value_sha256=reference.sha256)


def _build_verified_journal(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
    *,
    host_adapted_command: HostAdaptedCommandRecord | None = None,
):
    manager = EngagementJournalService.open(
        tmp_path / "knowledge", clock=fixed_clock, uuid_factory=fixed_uuid_factory
    )
    service = manager.__enter__()
    opened = service.create_engagement(
        display_name="HTB-Orion",
        objective="Obtain the user and root flags on orion.htb",
        scope=authorized_scope,
        lane=lane,
        required_proofs=(
            ProofRequirement(proof_id="user-flag", kind="flag", description="User proof"),
            ProofRequirement(proof_id="root-flag", kind="flag", description="Root proof"),
        ),
    )
    references = tuple(
        service.write_evidence(
            opened.snapshot.engagement_id,
            value,
            media_type="text/plain",
            representation="utf-8",
        )
        for value in (CREDENTIAL, USER_FLAG, ROOT_FLAG)
    )
    attached = service.append_hook_events(
        opened.snapshot.engagement_id,
        tuple(
            JournalEventDraft(
                lane=lane,
                actor="host_agent",
                type=EventType.EVIDENCE_ATTACHED,
                payload=EvidenceAttachedPayload(evidence=reference),
            )
            for reference in references
        ),
        expected_revision=opened.snapshot.revision,
    )
    decided = service.record_decision(
        opened.snapshot.engagement_id,
        lane=lane,
        strategy="Reuse OrionAdm!n:Summer2026 against 192.0.2.44",
        rationale="The credential was discovered on HTB-Orion",
        host_adapted_command=host_adapted_command,
        expected_revision=attached.snapshot.revision,
    )
    credential_ref, user_ref, root_ref = references
    attachment_ids = attached.created_event_ids
    event_ids = {
        "facet": UUID("00000000-0000-4000-8000-000000003001"),
        "credential": UUID("00000000-0000-4000-8000-000000003002"),
        "outcome": UUID("00000000-0000-4000-8000-000000003003"),
        "user_proof": UUID("00000000-0000-4000-8000-000000003004"),
        "root_proof": UUID("00000000-0000-4000-8000-000000003005"),
    }
    planning = service._issue_planning_event_commit_capability().commit_planning_events(
        opened.snapshot.engagement_id,
        (
            PlanningEventCommitItem(
                event_id=event_ids["facet"],
                idempotency_key="promotion:facet",
                payload=ObservationExtractedEventPayload(
                    summary="Linux x86_64 service on orion.htb",
                    observation=FacetObservationEventRecord(
                        dimension="os_family",
                        key="platform",
                        value="Linux x86_64",
                        relation="observed",
                    ),
                    confidence=1.0,
                    evidence_slices=(_slice(credential_ref),),
                    scope_reference_ids=(),
                    interpretation_input_digest="1" * 64,
                ),
            ),
            PlanningEventCommitItem(
                event_id=event_ids["credential"],
                idempotency_key="promotion:credential",
                payload=ObservationExtractedEventPayload(
                    summary="Credential OrionAdm!n:Summer2026 discovered for Orion",
                    observation=SecretReferenceEventRecord(
                        secret_ref_id="secret:ssh:orion",
                        secret_kind="password",
                        label="Orion SSH password",
                        value=_private(credential_ref),
                        scope_reference_ids=(),
                    ),
                    confidence=1.0,
                    evidence_slices=(_slice(credential_ref),),
                    scope_reference_ids=(),
                    interpretation_input_digest="2" * 64,
                ),
            ),
            PlanningEventCommitItem(
                event_id=event_ids["outcome"],
                idempotency_key="promotion:outcome",
                payload=OutcomeAssessedEventPayload(
                    attachment_event_id=attachment_ids[0],
                    terminal_tool_event_id=attachment_ids[0],
                    decision_id=None,
                    tool_call_ids=("call-promotion",),
                    category="negative_evidence",
                    summary="Anonymous access to 192.0.2.44 failed",
                    strategic_impact="Retry when OrionAdm!n:Summer2026 is available",
                    evidence_ids=(credential_ref.evidence_id,),
                    source_event_ids=(attachment_ids[0],),
                    interpretation_input_digest="3" * 64,
                ),
            ),
            PlanningEventCommitItem(
                event_id=event_ids["user_proof"],
                idempotency_key="promotion:user-proof",
                payload=ObjectiveProofObservedEventPayload(
                    proof_requirement_id="user-flag",
                    assessment_generation=1,
                    assessment="supported",
                    candidate_value=_private(user_ref),
                    confidence=1.0,
                    evidence_ids=(user_ref.evidence_id,),
                    source_event_ids=(attachment_ids[1],),
                    interpretation_input_digest="4" * 64,
                ),
            ),
            PlanningEventCommitItem(
                event_id=event_ids["root_proof"],
                idempotency_key="promotion:root-proof",
                payload=ObjectiveProofObservedEventPayload(
                    proof_requirement_id="root-flag",
                    assessment_generation=1,
                    assessment="supported",
                    candidate_value=_private(root_ref),
                    confidence=1.0,
                    evidence_ids=(root_ref.evidence_id,),
                    source_event_ids=(attachment_ids[2],),
                    interpretation_input_digest="5" * 64,
                ),
            ),
        ),
        operation_id=UUID("00000000-0000-4000-8000-000000003010"),
        expected_revision=decided.snapshot.revision,
    )
    closing = service.request_close(
        opened.snapshot.engagement_id,
        lane=lane,
        reason="verified objectives complete",
        expected_revision=planning.snapshot.revision,
    ).snapshot
    closed = ReportClosureFinalizer(
        service, service._repository._issue_report_commit_capability()
    ).finalize(snapshot=closing)
    report = closed.state.active_report
    assert report is not None
    verification_event_id = UUID("00000000-0000-4000-8000-000000003011")
    verified = (
        service._issue_lifecycle_commit_capability()
        .commit_verified(
            opened.snapshot.engagement_id,
            JournalEventDraft(
                event_id=verification_event_id,
                actor="system",
                type=EventType.ENGAGEMENT_VERIFIED,
                payload=EngagementVerifiedPayload(
                    report_id=report.report_id,
                    report_revision=report.report_revision,
                    verification_kind="platform",
                    verification_reference="platform-proof-42",
                ),
                system_correlation=SystemCorrelation(
                    source="lifecycle",
                    operation_id=UUID("00000000-0000-4000-8000-000000003012"),
                ),
            ),
            expected_revision=closed.revision,
        )
        .snapshot
    )
    return manager, service, verified, verification_event_id, references, event_ids


def test_promotion_event_classification_exhaustively_covers_the_closed_union() -> None:
    assert PROMOTION_PROJECTED_EVENT_TYPES.isdisjoint(PROMOTION_IGNORED_EVENT_TYPES)
    assert frozenset(EventType) == PROMOTION_PROJECTED_EVENT_TYPES | PROMOTION_IGNORED_EVENT_TYPES
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
    assert saga_event_types == saga_event_types & PROMOTION_IGNORED_EVENT_TYPES


def test_projected_strategic_records_preserve_nested_event_provenance() -> None:
    owner = UUID("10000000-0000-4000-8000-000000000001")
    frontier_ref = UUID("10000000-0000-4000-8000-000000000002")
    operation_ref = UUID("10000000-0000-4000-8000-000000000003")
    archive_ref = UUID("10000000-0000-4000-8000-000000000004")
    trigger_ref = UUID("10000000-0000-4000-8000-000000000005")
    snapshot_ref = UUID("10000000-0000-4000-8000-000000000006")
    family = StrategyFamilyEventRecord.model_construct(
        title="Reusable strategy",
        rationale="Grounded rationale",
        evidence_event_ids=(snapshot_ref,),
    )
    command = CommandSuggestionEventRecord.model_construct(
        command_template="tool {target}", origin="model_generated"
    )
    cases = (
        (
            EventType.FRONTIER_PROPOSED,
            FrontierProposedEventPayload.model_construct(
                proposal=FrontierProposalEventRecord.model_construct(
                    title="Candidate",
                    rationale="Why it may work",
                    commands=(command,),
                    event_refs=(frontier_ref,),
                )
            ),
            {owner, frontier_ref},
        ),
        (
            EventType.STRATEGY_RECONCILED,
            StrategyReconciledEventPayload.model_construct(
                operation=StrategyReconciliationEventOperation.model_construct(
                    reason="Grounded change", evidence_event_ids=(operation_ref,)
                ),
                resulting_snapshot=family,
            ),
            {owner, operation_ref, snapshot_ref},
        ),
        (
            EventType.STRATEGY_ARCHIVED,
            StrategyArchivedEventPayload.model_construct(
                archive_record=ArchivedStrategyEventRecord.model_construct(
                    archive_summary="Archived after verified failure",
                    source_reconciliation_event_id=archive_ref,
                    snapshot=family,
                )
            ),
            {owner, archive_ref, snapshot_ref},
        ),
        (
            EventType.STRATEGY_REACTIVATED,
            StrategyReactivatedEventPayload.model_construct(
                source_archive_event_id=archive_ref,
                triggering_event_ids=(trigger_ref,),
                restored_snapshot=family,
            ),
            {owner, archive_ref, trigger_ref, snapshot_ref},
        ),
    )

    for kind, payload, expected in cases:
        item = _summary_for(SimpleNamespace(type=kind, event_id=owner, payload=payload))
        assert set(item.event_ids) == expected
    assert (
        "tool {target}"
        in _summary_for(
            SimpleNamespace(type=cases[0][0], event_id=owner, payload=cases[0][1])
        ).summary
    )


def test_projector_rejects_unclassified_event_before_private_evidence_access(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    manager, _, snapshot, verification_event_id, _, _ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    unknown = snapshot.events[0].model_copy(update={"type": "future_strategic_event"})
    snapshot = snapshot.model_copy(update={"events": (unknown, *snapshot.events[1:])})
    calls = []

    def reader(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("unknown event must fail before private evidence access")

    try:
        with pytest.raises(ValueError, match="unclassified journal event"):
            PromotionInputProjector().project(
                snapshot,
                verification_event_id=verification_event_id,
                evidence_reader=reader,
            )
    finally:
        manager.__exit__(None, None, None)

    assert calls == []


def test_projector_builds_only_symbolized_input_from_exact_verified_journal(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    manager, service, snapshot, verification_event_id, references, event_ids = (
        _build_verified_journal(tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory)
    )
    calls: list[tuple[UUID, str, int, int]] = []

    def reader(engagement_id, evidence_id, *, offset, limit):
        calls.append((engagement_id, evidence_id, offset, limit))
        return service.read_evidence_slice(engagement_id, evidence_id, offset=offset, limit=limit)

    try:
        projection = PromotionInputProjector().project(
            snapshot,
            verification_event_id=verification_event_id,
            evidence_reader=reader,
        )
    finally:
        manager.__exit__(None, None, None)

    assert isinstance(projection, PrivatePromotionProjection)
    safe = projection.safe_input
    assert safe.engagement_id == snapshot.engagement_id
    assert safe.verified_revision == snapshot.revision
    assert safe.verification_event_id == verification_event_id
    assert calls == [(snapshot.engagement_id, ref.evidence_id, 0, ref.size) for ref in references]
    serialized = safe.model_dump_json()
    for private in (
        CREDENTIAL.decode(),
        USER_FLAG.decode(),
        ROOT_FLAG.decode(),
        "192.0.2.44",
        "orion.htb",
        "HTB-Orion",
        "Orion",
    ):
        assert private.casefold() not in serialized.casefold()
    assert "<CREDENTIAL_" in serialized
    assert "<FLAG_" in serialized
    assert "<TARGET_" in serialized
    assert "<CHALLENGE_" in serialized
    projected_ids = {
        event_id
        for section in (safe.context, safe.decisions, safe.outcomes, safe.alternatives)
        for item in section
        for event_id in item.event_ids
    }
    assert set(event_ids.values()) <= projected_ids
    outcome = next(item for item in safe.outcomes if event_ids["outcome"] in item.event_ids)
    assert outcome.evidence_ids == (references[0].evidence_id,)
    assert not hasattr(projection, "model_dump")
    assert CREDENTIAL.decode() not in repr(projection)
    assert CREDENTIAL.decode() not in repr(projection.inventory)


def test_projector_excludes_host_adapted_command_templates(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    case_local_secret = "UNTRACKED-CASE-LOCAL-SECRET"
    manager, service, snapshot, verification_event_id, _, _ = _build_verified_journal(
        tmp_path,
        authorized_scope,
        lane,
        fixed_clock,
        fixed_uuid_factory,
        host_adapted_command=HostAdaptedCommandRecord(
            command_template=f"ssh admin:{case_local_secret}@{{target}}",
            placeholder_names=("target",),
            adaptation_note="Host-local binding must not enter promotion",
        ),
    )
    try:
        projection = PromotionInputProjector().project(
            snapshot,
            verification_event_id=verification_event_id,
            evidence_reader=service.read_evidence_slice,
        )
    finally:
        manager.__exit__(None, None, None)

    serialized = projection.safe_input.model_dump_json()
    assert case_local_secret not in serialized
    assert "ssh admin:" not in serialized
    assert "Host-local binding" not in serialized


@pytest.mark.parametrize(
    ("media_type", "representation", "capture_limitations"),
    (
        ("application/octet-stream", "utf-8", ()),
        ("text/plain", "host_bytes", ()),
        ("text/plain", "utf-8", ("truncated",)),
    ),
)
def test_projector_rejects_unsupported_private_slice_media_before_reading(
    tmp_path,
    authorized_scope,
    lane,
    fixed_clock,
    fixed_uuid_factory,
    media_type,
    representation,
    capture_limitations,
) -> None:
    manager, _, snapshot, verification_event_id, references, event_ids = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    calls = []
    private_identity = references[0].evidence_id
    forged_events = []
    for event in snapshot.events:
        payload = event.payload
        if (
            event.type is EventType.EVIDENCE_ATTACHED
            and payload.evidence.evidence_id == private_identity
        ):
            payload = payload.model_copy(
                update={
                    "evidence": payload.evidence.model_copy(
                        update={
                            "media_type": media_type,
                            "representation": representation,
                            "capture_limitations": capture_limitations,
                        }
                    )
                }
            )
        elif event.event_id == event_ids["credential"]:
            value = payload.observation.value
            payload = payload.model_copy(
                update={
                    "observation": payload.observation.model_copy(
                        update={
                            "value": value.model_copy(
                                update={
                                    "evidence_slice": value.evidence_slice.model_copy(
                                        update={"media_type": media_type}
                                    )
                                }
                            )
                        }
                    )
                }
            )
        forged_events.append(event.model_copy(update={"payload": payload}))
    snapshot = snapshot.model_copy(update={"events": tuple(forged_events)})

    def reader(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("unsupported private media must fail before evidence access")

    try:
        with pytest.raises(ValueError, match="private evidence slice") as caught:
            PromotionInputProjector().project(
                snapshot,
                verification_event_id=verification_event_id,
                evidence_reader=reader,
            )
    finally:
        manager.__exit__(None, None, None)

    assert calls == []
    assert CREDENTIAL.decode() not in str(caught.value)
    assert private_identity not in str(caught.value)
    assert "evidence/" not in str(caught.value)


def test_projector_validates_all_private_descriptors_before_any_read(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    manager, _, snapshot, verification_event_id, references, event_ids = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    private_identity = references[-1].evidence_id
    forged_events = []
    for event in snapshot.events:
        payload = event.payload
        if (
            event.type is EventType.EVIDENCE_ATTACHED
            and payload.evidence.evidence_id == private_identity
        ):
            payload = payload.model_copy(
                update={
                    "evidence": payload.evidence.model_copy(
                        update={"media_type": "application/octet-stream"}
                    )
                }
            )
        elif event.event_id == event_ids["root_proof"]:
            candidate = payload.candidate_value
            payload = payload.model_copy(
                update={
                    "candidate_value": candidate.model_copy(
                        update={
                            "evidence_slice": candidate.evidence_slice.model_copy(
                                update={"media_type": "application/octet-stream"}
                            )
                        }
                    )
                }
            )
        forged_events.append(event.model_copy(update={"payload": payload}))
    snapshot = snapshot.model_copy(update={"events": tuple(forged_events)})
    calls = []

    def reader(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("all descriptors must be validated before the first read")

    try:
        with pytest.raises(ValueError, match="private evidence slice"):
            PromotionInputProjector().project(
                snapshot,
                verification_event_id=verification_event_id,
                evidence_reader=reader,
            )
    finally:
        manager.__exit__(None, None, None)

    assert calls == []


def test_projector_rejects_conflicting_private_attachment_identity_before_reading(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    manager, service, snapshot, verification_event_id, references, _ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    private_identity = references[0].evidence_id
    attachment = next(
        event
        for event in snapshot.events
        if event.type is EventType.EVIDENCE_ATTACHED
        and event.payload.evidence.evidence_id == private_identity
    )
    conflicting = attachment.model_copy(
        update={
            "event_id": UUID("ffffffff-ffff-4fff-8fff-fffffffffff0"),
            "payload": attachment.payload.model_copy(
                update={
                    "evidence": attachment.payload.evidence.model_copy(
                        update={"representation": "host_bytes"}
                    )
                }
            ),
        }
    )
    snapshot = snapshot.model_copy(
        update={"events": (snapshot.events[0], conflicting, *snapshot.events[1:])}
    )
    calls = []

    def reader(*args, **kwargs):
        calls.append((args, kwargs))
        return service.read_evidence_slice(*args, **kwargs)

    try:
        with pytest.raises(ValueError, match="private evidence slice") as caught:
            PromotionInputProjector().project(
                snapshot,
                verification_event_id=verification_event_id,
                evidence_reader=reader,
            )
    finally:
        manager.__exit__(None, None, None)

    assert calls == []
    assert private_identity not in str(caught.value)


def test_projector_rejects_wrong_watermark_and_private_slice_failures(
    tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
) -> None:
    manager, service, snapshot, verification_event_id, references, _ = _build_verified_journal(
        tmp_path, authorized_scope, lane, fixed_clock, fixed_uuid_factory
    )
    reference = references[0]
    valid = service.read_evidence_slice(
        snapshot.engagement_id, reference.evidence_id, offset=0, limit=reference.size
    )
    invalid = (
        valid.model_copy(update={"complete": False}),
        valid.model_copy(update={"offset": 1}),
        valid.model_copy(update={"evidence_id": "evidence-sha256-" + "f" * 64}),
        valid.model_copy(update={"data": b"forged-value"}),
        valid.model_copy(update={"data": b"\xff"}),
    )
    try:
        with pytest.raises(ValueError, match="closed_verified"):
            PromotionInputProjector().project(
                snapshot.model_copy(
                    update={"state": snapshot.state.model_copy(update={"status": "active"})}
                ),
                verification_event_id=verification_event_id,
                evidence_reader=service.read_evidence_slice,
            )
        forged_verification = snapshot.events[-1].model_copy(
            update={
                "payload": snapshot.events[-1].payload.model_copy(
                    update={"report_id": UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")}
                )
            }
        )
        with pytest.raises(ValueError, match="active report"):
            PromotionInputProjector().project(
                snapshot.model_copy(
                    update={"events": (*snapshot.events[:-1], forged_verification)}
                ),
                verification_event_id=verification_event_id,
                evidence_reader=service.read_evidence_slice,
            )
        for bad_slice in invalid:
            with pytest.raises(ValueError, match="private evidence slice") as caught:
                PromotionInputProjector().project(
                    snapshot,
                    verification_event_id=verification_event_id,
                    evidence_reader=lambda *_args, item=bad_slice, **_kwargs: item,
                )
            assert CREDENTIAL.decode() not in str(caught.value)
            assert CREDENTIAL.decode() not in repr(caught.value)
            assert reference.evidence_id not in str(caught.value)

        private_path = "evidence/private-source-secret.txt"

        def unavailable_reader(*_args, **_kwargs):
            raise RuntimeError(
                f"reader exposed {CREDENTIAL.decode()} at {private_path} "
                f"from {reference.evidence_id}"
            )

        with pytest.raises(ValueError, match="private evidence slice is unavailable") as caught:
            PromotionInputProjector().project(
                snapshot,
                verification_event_id=verification_event_id,
                evidence_reader=unavailable_reader,
            )
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        for private in (CREDENTIAL.decode(), private_path, reference.evidence_id):
            assert private not in str(caught.value)
            assert private not in repr(caught.value)
    finally:
        manager.__exit__(None, None, None)


def test_projector_exposes_no_caller_path_source_or_inventory_parameter() -> None:
    signature = inspect.signature(PromotionInputProjector.project)
    assert tuple(signature.parameters) == (
        "self",
        "snapshot",
        "verification_event_id",
        "evidence_reader",
    )
    prohibited = {"path", "source", "source_id", "namespace", "inventory", "bytes"}
    assert prohibited.isdisjoint(signature.parameters)


def test_private_slice_digest_covers_exact_recorded_range() -> None:
    digest = sha256(CREDENTIAL).hexdigest()
    assert digest == ("evidence-sha256-" + digest).removeprefix("evidence-sha256-")


def test_invalid_utf8_private_slice_leaves_no_raw_exception_chain() -> None:
    private_bytes = b"\xffPRIVATE-RAW-SLICE"
    digest = sha256(private_bytes).hexdigest()
    evidence_id = f"evidence-sha256-{digest}"
    reference = EvidenceReference(
        evidence_id=evidence_id,
        sha256=digest,
        size=len(private_bytes),
        media_type="text/plain",
        representation="utf-8",
        relative_path="evidence/private-invalid-utf8.txt",
    )
    record = PrivateValueEventRecord(
        evidence_slice=EvidenceSliceEventRef(
            evidence_id=evidence_id,
            start=0,
            end=len(private_bytes),
            sha256=digest,
            media_type="text/plain",
        ),
        value_sha256=digest,
    )

    with pytest.raises(ValueError, match="not strict UTF-8") as caught:
        _resolve_private_value(
            UUID("11111111-1111-4111-8111-111111111111"),
            record,
            reference,
            lambda *_args, **_kwargs: EvidenceSlice(
                evidence_id=evidence_id,
                offset=0,
                data=private_bytes,
                complete=True,
            ),
        )

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_projector_rejects_non_verified_event_identity_without_leaking_it() -> None:
    projector = PromotionInputProjector()
    snapshot = SimpleNamespace(
        engagement_id=UUID("11111111-1111-4111-8111-111111111111"),
        revision=SimpleNamespace(sequence=1),
        state=SimpleNamespace(status="closed_verified"),
        events=(),
    )
    with pytest.raises(ValueError, match="verification event"):
        projector.project(
            snapshot,
            verification_event_id=UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
            evidence_reader=lambda *_args, **_kwargs: None,
        )
