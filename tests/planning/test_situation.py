from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID

import pytest

from sedna.engagement import (
    EngagementJournalService,
    EngagementManifest,
    EventType,
    EvidenceAttachedPayload,
    ExecutionLaneKey,
    HostKind,
    JournalEventDraft,
    ProofRequirement,
    SessionCheckpointedPayload,
    scope_references,
)
from sedna.engagement.events import (
    AccessStateDeltaEventRecord,
    EngagementReopenedPayload,
    EvidenceSliceEventRef,
    FacetObservationEventRecord,
    FlagRejectedPayload,
    HypothesisFormedEventPayload,
    IncompatibilityObservationEventRecord,
    InterpretationFailedEventPayload,
    InterpretationSucceededEventPayload,
    MissingInformationIdentifiedEventPayload,
    ObjectiveProofObservedEventPayload,
    ObservationExtractedEventPayload,
    OutcomeAssessedEventPayload,
    PlanningCallMetadataEventRecord,
    PrivateValueEventRecord,
    ResearchSourceAssessedEventPayload,
    ResearchSourceConsultedEventPayload,
    SecretReferenceEventRecord,
    SystemCorrelation,
    TextFactEventRecord,
)
from sedna.engagement.reporting.service import ReportClosureFinalizer
from sedna.engagement.service import PlanningEventCommitItem
from sedna.knowledge.retrieval import AuthorizationScope, AuthorizationState, ValidatedTarget
from sedna.planning.situation import (
    SITUATION_EFFECT_EVENT_TYPES,
    SITUATION_NO_OP_EVENT_TYPES,
    SituationReducer,
    proof_value_was_rejected,
    transition_proof_generation,
)


def test_situation_event_effect_table_exhaustively_covers_report_events() -> None:
    assert SITUATION_EFFECT_EVENT_TYPES.isdisjoint(SITUATION_NO_OP_EVENT_TYPES)
    assert frozenset(EventType) == SITUATION_EFFECT_EVENT_TYPES | SITUATION_NO_OP_EVENT_TYPES
    assert {
        EventType.REPORT_GENERATED,
        EventType.ENGAGEMENT_CLOSED,
        EventType.REPORT_COMMIT_ABANDONED,
    } <= SITUATION_NO_OP_EVENT_TYPES
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
    assert saga_event_types == saga_event_types & SITUATION_NO_OP_EVENT_TYPES


FIXED_TIME = datetime(2026, 8, 11, 12, 30, tzinfo=UTC)


def fixed_uuid_factory():
    next_value = 1

    def factory() -> UUID:
        nonlocal next_value
        value = UUID(f"00000000-0000-4000-8000-{next_value:012d}")
        next_value += 1
        return value

    return factory


def manifest() -> EngagementManifest:
    return EngagementManifest(
        engagement_id=UUID("11111111-1111-4111-8111-111111111111"),
        display_name="HTB-Orion",
        initial_objective="Obtain the user and root flags",
        initial_scope=AuthorizationScope(
            state=AuthorizationState.AUTHORIZED,
            exact_targets=(ValidatedTarget.parse("192.0.2.44"),),
        ),
        required_proofs=(
            ProofRequirement(
                proof_id="user-flag", kind="flag", description="A valid HTB user flag"
            ),
            ProofRequirement(
                proof_id="root-flag", kind="flag", description="A valid HTB root flag"
            ),
        ),
        created_at=FIXED_TIME,
        created_by_host={"kind": "hades", "adapter_version": "1"},
    )


def lane() -> ExecutionLaneKey:
    return ExecutionLaneKey(
        host_kind=HostKind.HADES,
        session_id="session-orion",
        task_id="task-root",
    )


@contextmanager
def engagement_service(tmp_path, fixed_clock, fixed_uuid_factory):
    with EngagementJournalService.open(
        tmp_path / "knowledge",
        clock=fixed_clock,
        uuid_factory=fixed_uuid_factory,
    ) as service:
        yield service


def test_empty_situation_replay_is_deterministic_and_manifest_grounded(
    tmp_path,
) -> None:
    with engagement_service(tmp_path, lambda: FIXED_TIME, fixed_uuid_factory()) as journal:
        created = journal.create_from_manifest(manifest(), lane=lane())
        snapshot = journal.load_snapshot(created.snapshot.engagement_id)

        first = SituationReducer.rebuild(snapshot)
        second = SituationReducer.rebuild(snapshot)

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert first.engagement_id == snapshot.engagement_id
    assert first.authoritative_journal_revision == snapshot.revision
    assert first.material_event_revision == 0
    assert tuple(
        (progress.proof_requirement_id, progress.status)
        for progress in first.objective_progress.requirements
    ) == (("root-flag", "pending"), ("user-flag", "pending"))
    assert first.facts == ()
    assert first.facets == ()
    assert first.hypotheses == ()
    assert first.access_states == ()
    assert first.interpretations == ()
    assert first.secret_references == ()
    assert first.attempts == ()
    assert first.incompatibilities == ()


def test_observation_changes_material_state_but_checkpoint_only_advances_authority(
    tmp_path,
) -> None:
    observation_event_id = UUID("00000000-0000-4000-8000-000000000101")
    current_manifest = manifest()
    current_lane = lane()
    with engagement_service(tmp_path, lambda: FIXED_TIME, fixed_uuid_factory()) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            b"OpenSSH 9.6 is reachable",
            media_type="text/plain",
            representation="utf-8",
        )
        attached = journal.append_hook_events(
            current_manifest.engagement_id,
            (
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type="evidence_attached",
                    payload=EvidenceAttachedPayload(evidence=evidence),
                ),
            ),
            expected_revision=created.snapshot.revision,
        )
        payload = ObservationExtractedEventPayload(
            summary="OpenSSH is reachable",
            observation=TextFactEventRecord(
                subject="ssh service",
                value="OpenSSH 9.6 is reachable",
            ),
            confidence=1.0,
            evidence_slices=(
                EvidenceSliceEventRef(
                    evidence_id=evidence.evidence_id,
                    start=0,
                    end=evidence.size,
                    sha256=evidence.sha256,
                    media_type=evidence.media_type,
                ),
            ),
            interpretation_input_digest="a" * 64,
        )
        observed = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=observation_event_id,
                    payload=payload,
                    idempotency_key="observation:1",
                ),
            ),
            operation_id=UUID("00000000-0000-4000-8000-000000000102"),
            expected_revision=attached.snapshot.revision,
        )
        material = SituationReducer.rebuild(observed.snapshot)
        checkpointed = journal.append_hook_events(
            current_manifest.engagement_id,
            (
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type="session_checkpointed",
                    payload=SessionCheckpointedPayload(
                        completed=False,
                        interrupted=False,
                        reason="checkpoint",
                    ),
                ),
            ),
            expected_revision=observed.snapshot.revision,
        )
        after_checkpoint = SituationReducer.rebuild(checkpointed.snapshot)

    assert material.material_event_revision == observed.snapshot.revision.sequence
    assert tuple((fact.text, fact.event_ids) for fact in material.facts) == (
        ("OpenSSH 9.6 is reachable", (observation_event_id,)),
    )
    assert after_checkpoint.authoritative_journal_revision == checkpointed.snapshot.revision
    assert after_checkpoint.material_event_revision == material.material_event_revision
    assert after_checkpoint.state_digest == material.state_digest
    assert after_checkpoint.facts == material.facts


def test_facet_observation_remains_structured(tmp_path) -> None:
    event_id = UUID("00000000-0000-4000-8000-000000000201")
    current_manifest = manifest()
    current_lane = lane()
    with engagement_service(tmp_path, lambda: FIXED_TIME, fixed_uuid_factory()) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            b"Linux 6.8 x86_64",
            media_type="text/plain",
            representation="utf-8",
        )
        attached = journal.append_hook_events(
            current_manifest.engagement_id,
            (
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type="evidence_attached",
                    payload=EvidenceAttachedPayload(evidence=evidence),
                ),
            ),
            expected_revision=created.snapshot.revision,
        )
        evidence_slice = EvidenceSliceEventRef(
            evidence_id=evidence.evidence_id,
            start=0,
            end=evidence.size,
            sha256=evidence.sha256,
            media_type=evidence.media_type,
        )
        result = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=event_id,
                    idempotency_key="facet:1",
                    payload=ObservationExtractedEventPayload(
                        summary="Linux observed",
                        observation=FacetObservationEventRecord(
                            dimension="os_family",
                            key="operating system",
                            value="linux",
                            relation="observed",
                        ),
                        confidence=1.0,
                        evidence_slices=(evidence_slice,),
                        interpretation_input_digest="b" * 64,
                    ),
                ),
            ),
            operation_id=UUID("00000000-0000-4000-8000-000000000202"),
            expected_revision=attached.snapshot.revision,
        )

        situation = SituationReducer.rebuild(result.snapshot)

    assert tuple((facet.key, facet.value, facet.event_ids) for facet in situation.facets) == (
        ("operating system", "linux", (event_id,)),
    )
    assert situation.facts == ()


def test_access_observation_remains_scope_bound(tmp_path) -> None:
    event_id = UUID("00000000-0000-4000-8000-000000000301")
    current_manifest = manifest()
    current_lane = lane()
    scope_id = scope_references(current_manifest.initial_scope)[0].reference_id
    with engagement_service(tmp_path, lambda: FIXED_TIME, fixed_uuid_factory()) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            b"SSH shell obtained",
            media_type="text/plain",
            representation="utf-8",
        )
        attached = journal.append_hook_events(
            current_manifest.engagement_id,
            (
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type="evidence_attached",
                    payload=EvidenceAttachedPayload(evidence=evidence),
                ),
            ),
            expected_revision=created.snapshot.revision,
        )
        evidence_slice = EvidenceSliceEventRef(
            evidence_id=evidence.evidence_id,
            start=0,
            end=evidence.size,
            sha256=evidence.sha256,
            media_type=evidence.media_type,
        )
        result = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=event_id,
                    idempotency_key="access:1",
                    payload=ObservationExtractedEventPayload(
                        summary="Shell obtained",
                        observation=AccessStateDeltaEventRecord(
                            scope_reference_id=scope_id,
                            access_kind="shell",
                            transition="gained",
                            principal_label="www-data",
                            service_ref="ssh",
                        ),
                        confidence=1.0,
                        evidence_slices=(evidence_slice,),
                        scope_reference_ids=(scope_id,),
                        interpretation_input_digest="c" * 64,
                    ),
                ),
            ),
            operation_id=UUID("00000000-0000-4000-8000-000000000302"),
            expected_revision=attached.snapshot.revision,
        )

        situation = SituationReducer.rebuild(result.snapshot)

    actual = tuple(
        (access.subject, access.state, access.event_ids) for access in situation.access_states
    )
    assert actual == ((f"{scope_id}/shell", "gained", (event_id,)),)


def test_secret_observation_projects_only_private_locator(tmp_path) -> None:
    event_id = UUID("00000000-0000-4000-8000-000000000401")
    current_manifest = manifest()
    current_lane = lane()
    secret_bytes = b"correct-horse-battery-staple"
    with engagement_service(tmp_path, lambda: FIXED_TIME, fixed_uuid_factory()) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            secret_bytes,
            media_type="text/plain",
            representation="utf-8",
        )
        attached = journal.append_hook_events(
            current_manifest.engagement_id,
            (
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type="evidence_attached",
                    payload=EvidenceAttachedPayload(evidence=evidence),
                ),
            ),
            expected_revision=created.snapshot.revision,
        )
        evidence_slice = EvidenceSliceEventRef(
            evidence_id=evidence.evidence_id,
            start=0,
            end=evidence.size,
            sha256=evidence.sha256,
            media_type=evidence.media_type,
        )
        result = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=event_id,
                    idempotency_key="secret:1",
                    payload=ObservationExtractedEventPayload(
                        summary="Credential material observed",
                        observation=SecretReferenceEventRecord(
                            secret_ref_id="secret:ssh-password",
                            secret_kind="password",
                            label="SSH password",
                            value=PrivateValueEventRecord(
                                evidence_slice=evidence_slice,
                                value_sha256=evidence.sha256,
                            ),
                        ),
                        confidence=1.0,
                        evidence_slices=(evidence_slice,),
                        interpretation_input_digest="d" * 64,
                    ),
                ),
            ),
            operation_id=UUID("00000000-0000-4000-8000-000000000402"),
            expected_revision=attached.snapshot.revision,
        )

        situation = SituationReducer.rebuild(result.snapshot)

    assert tuple(
        (
            secret.label,
            secret.evidence_id,
            secret.candidate_start,
            secret.candidate_end,
            secret.value_sha256,
            secret.event_ids,
        )
        for secret in situation.secret_references
    ) == (
        (
            "SSH password",
            evidence.evidence_id,
            0,
            len(secret_bytes),
            evidence.sha256,
            (event_id,),
        ),
    )
    assert secret_bytes.decode() not in situation.model_dump_json()


def test_incompatibility_observation_stays_separate_from_facts(tmp_path) -> None:
    event_id = UUID("00000000-0000-4000-8000-000000000501")
    current_manifest = manifest()
    current_lane = lane()
    scope_id = scope_references(current_manifest.initial_scope)[0].reference_id
    with engagement_service(tmp_path, lambda: FIXED_TIME, fixed_uuid_factory()) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            b"Windows payload incompatible with Linux target",
            media_type="text/plain",
            representation="utf-8",
        )
        attached = journal.append_hook_events(
            current_manifest.engagement_id,
            (
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type="evidence_attached",
                    payload=EvidenceAttachedPayload(evidence=evidence),
                ),
            ),
            expected_revision=created.snapshot.revision,
        )
        evidence_slice = EvidenceSliceEventRef(
            evidence_id=evidence.evidence_id,
            start=0,
            end=evidence.size,
            sha256=evidence.sha256,
            media_type=evidence.media_type,
        )
        result = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=event_id,
                    idempotency_key="incompatibility:1",
                    payload=ObservationExtractedEventPayload(
                        summary="Payload incompatible",
                        observation=IncompatibilityObservationEventRecord(
                            subject_ref="execution-example:windows-shell",
                            reason="requires Windows but target is Linux",
                            scope_reference_ids=(scope_id,),
                            event_refs=(attached.created_event_ids[0],),
                        ),
                        confidence=1.0,
                        evidence_slices=(evidence_slice,),
                        scope_reference_ids=(scope_id,),
                        interpretation_input_digest="e" * 64,
                    ),
                ),
            ),
            operation_id=UUID("00000000-0000-4000-8000-000000000502"),
            expected_revision=attached.snapshot.revision,
        )

        situation = SituationReducer.rebuild(result.snapshot)

    assert tuple(
        (item.subject, item.explanation, item.event_ids) for item in situation.incompatibilities
    ) == (
        (
            "execution-example:windows-shell",
            "requires Windows but target is Linux",
            (event_id,),
        ),
    )
    assert situation.facts == ()


def test_hypothesis_preserves_support_and_contradiction_grounding(tmp_path) -> None:
    event_id = UUID("00000000-0000-4000-8000-000000000601")
    current_manifest = manifest()
    with engagement_service(tmp_path, lambda: FIXED_TIME, fixed_uuid_factory()) as journal:
        created = journal.create_from_manifest(current_manifest, lane=lane())
        support_id = created.snapshot.events[0].event_id
        contradiction_id = created.snapshot.events[1].event_id
        result = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=event_id,
                    idempotency_key="hypothesis:1",
                    payload=HypothesisFormedEventPayload(
                        statement="SSH may accept recovered credentials",
                        confidence=0.75,
                        supporting_event_ids=(support_id,),
                        contradicting_event_ids=(contradiction_id,),
                        interpretation_input_digest="f" * 64,
                    ),
                ),
            ),
            operation_id=UUID("00000000-0000-4000-8000-000000000602"),
            expected_revision=created.snapshot.revision,
        )

        situation = SituationReducer.rebuild(result.snapshot)

    assert tuple(
        (hypothesis.text, hypothesis.confidence, hypothesis.event_ids)
        for hypothesis in situation.hypotheses
    ) == (("SSH may accept recovered credentials", 0.75, (event_id,)),)
    assert tuple(
        (belief.hypothesis_event_id, belief.prior, belief.posterior)
        for belief in situation.hypothesis_beliefs
    ) == ((event_id, 0.75, 0.75),)
    assert situation.material_event_revision == result.snapshot.revision.sequence


def test_missing_information_remains_an_unresolved_question(tmp_path) -> None:
    event_id = UUID("00000000-0000-4000-8000-000000000701")
    current_manifest = manifest()
    with engagement_service(tmp_path, lambda: FIXED_TIME, fixed_uuid_factory()) as journal:
        created = journal.create_from_manifest(current_manifest, lane=lane())
        related_id = created.snapshot.events[0].event_id
        result = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=event_id,
                    idempotency_key="missing:1",
                    payload=MissingInformationIdentifiedEventPayload(
                        question="Which HTTP virtual host is active?",
                        reason="The default page does not identify the application",
                        importance=80,
                        related_event_ids=(related_id,),
                        interpretation_input_digest="1" * 64,
                    ),
                ),
            ),
            operation_id=UUID("00000000-0000-4000-8000-000000000702"),
            expected_revision=created.snapshot.revision,
        )

        situation = SituationReducer.rebuild(result.snapshot)

    assert tuple((item.question, item.event_ids) for item in situation.unresolved_information) == (
        ("Which HTTP virtual host is active?", (event_id,)),
    )
    assert situation.facts == ()
    assert situation.hypotheses == ()


def test_research_source_assessment_projects_provenance_but_not_fact(tmp_path) -> None:
    """A Task8 research assessment enriches provenance only, never observed facts."""
    current_manifest = manifest()
    query_id = UUID("00000000-0000-4000-8000-000000000711")
    with engagement_service(tmp_path, lambda: FIXED_TIME, fixed_uuid_factory()) as journal:
        created = journal.create_from_manifest(current_manifest, lane=lane())
        consulted = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=UUID("00000000-0000-4000-0000-000000000712"),
                    idempotency_key="research-consulted",
                    payload=ResearchSourceConsultedEventPayload(
                        query_id=query_id,
                        source_id="source-rfc",
                        normalized_locator="https://example.test/rfc",
                        locator_digest=sha256(b"https://example.test/rfc").hexdigest(),
                        content_digest="a" * 64,
                        media_type="text/plain",
                        evidence_ids=("evidence-sha256-" + "b" * 64,),
                        tool_event_ids=(created.snapshot.events[0].event_id,),
                    ),
                ),
            ),
            operation_id=UUID("00000000-0000-4000-0000-000000000713"),
            expected_revision=created.snapshot.revision,
        )
        assessment_fields = {
            "query_id": query_id,
            "source_id": "source-rfc",
            "consulted_event_id": consulted.created_event_ids[0],
            "assessment": "useful",
            "confidence": 0.9,
            "summary": "The RFC confirms the protocol behavior.",
            "related_event_ids": (consulted.created_event_ids[0],),
            "suggested_registry_status": None,
        }
        assessment = ResearchSourceAssessedEventPayload(
            **assessment_fields,
            assessment_digest=sha256(
                json.dumps(
                    {
                        key: str(value) if isinstance(value, UUID) else value
                        for key, value in assessment_fields.items()
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                    default=str,
                ).encode()
            ).hexdigest(),
        )
        committed = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=UUID("00000000-0000-4000-0000-000000000714"),
                    idempotency_key="research-assessed",
                    payload=assessment,
                ),
            ),
            operation_id=UUID("00000000-0000-4000-0000-000000000715"),
            expected_revision=consulted.snapshot.revision,
        )

        situation = SituationReducer.rebuild(committed.snapshot)

    assert situation.facts == ()
    assert situation.research_sources[0].source_id == "source-rfc"
    assert situation.research_sources[0].event_ids == (committed.created_event_ids[0],)


def test_objective_proof_updates_only_the_exact_requirement(tmp_path) -> None:
    event_id = UUID("00000000-0000-4000-8000-000000000801")
    current_manifest = manifest()
    current_lane = lane()
    proof_bytes = b"0123456789abcdef0123456789abcdef"
    with engagement_service(tmp_path, lambda: FIXED_TIME, fixed_uuid_factory()) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            proof_bytes,
            media_type="text/plain",
            representation="utf-8",
        )
        attached = journal.append_hook_events(
            current_manifest.engagement_id,
            (
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type="evidence_attached",
                    payload=EvidenceAttachedPayload(evidence=evidence),
                ),
            ),
            expected_revision=created.snapshot.revision,
        )
        evidence_slice = EvidenceSliceEventRef(
            evidence_id=evidence.evidence_id,
            start=0,
            end=evidence.size,
            sha256=evidence.sha256,
            media_type=evidence.media_type,
        )
        result = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=event_id,
                    idempotency_key="proof:1",
                    payload=ObjectiveProofObservedEventPayload(
                        proof_requirement_id="user-flag",
                        assessment_generation=1,
                        assessment="supported",
                        candidate_value=PrivateValueEventRecord(
                            evidence_slice=evidence_slice,
                            value_sha256=evidence.sha256,
                        ),
                        confidence=1.0,
                        evidence_ids=(evidence.evidence_id,),
                        source_event_ids=(attached.created_event_ids[0],),
                        interpretation_input_digest="2" * 64,
                    ),
                ),
            ),
            operation_id=UUID("00000000-0000-4000-8000-000000000802"),
            expected_revision=attached.snapshot.revision,
        )

        situation = SituationReducer.rebuild(result.snapshot)

    progress = {
        item.proof_requirement_id: item for item in situation.objective_progress.requirements
    }
    assert progress["root-flag"].status == "pending"
    assert progress["user-flag"].status == "supported"
    assert progress["user-flag"].supporting_event_ids == (event_id,)
    assert progress["user-flag"].value_references[0].model_dump(mode="json") == {
        "proof_event_id": str(event_id),
        "proof_requirement_id": "user-flag",
        "assessment_generation": 1,
        "assessment": "supported",
        "evidence_id": evidence.evidence_id,
        "candidate_start": 0,
        "candidate_end": evidence.size,
        "value_sha256": evidence.sha256,
    }
    assert proof_bytes.decode() not in situation.model_dump_json()


def test_legacy_engagement_reopened_invalidates_all_proof_generations_and_rejects_stale_objectives(
    tmp_path,
) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with engagement_service(tmp_path, lambda: FIXED_TIME, fixed_uuid_factory()) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            b"0123456789abcdef0123456789abcdef",
            media_type="text/plain",
            representation="utf-8",
        )
        attached = journal.append_hook_events(
            current_manifest.engagement_id,
            (
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type="evidence_attached",
                    payload=EvidenceAttachedPayload(evidence=evidence),
                ),
            ),
            expected_revision=created.snapshot.revision,
        )
        proof_payload = ObjectiveProofObservedEventPayload(
            proof_requirement_id="user-flag",
            assessment_generation=1,
            assessment="supported",
            candidate_value=PrivateValueEventRecord(
                evidence_slice=EvidenceSliceEventRef(
                    evidence_id=evidence.evidence_id,
                    start=0,
                    end=evidence.size,
                    sha256=evidence.sha256,
                    media_type="text/plain",
                ),
                value_sha256=evidence.sha256,
            ),
            confidence=1.0,
            evidence_ids=(evidence.evidence_id,),
            source_event_ids=(attached.created_event_ids[0],),
            interpretation_input_digest="e" * 64,
        )
        observed = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=UUID("00000000-0000-4000-8000-000000000811"),
                    idempotency_key="legacy-proof",
                    payload=proof_payload,
                ),
            ),
            operation_id=UUID("00000000-0000-4000-8000-000000000812"),
            expected_revision=attached.snapshot.revision,
        )
        abandoned = journal.abandon_engagement(
            current_manifest.engagement_id,
            lane=current_lane,
            reason="paused for review",
            expected_revision=observed.snapshot.revision,
        )
        reopened = journal.reopen_engagement(
            current_manifest.engagement_id,
            lane=current_lane,
            reason="resume evidence collection",
            expected_revision=abandoned.snapshot.revision,
        )
        situation = SituationReducer.rebuild(reopened.snapshot)
        stale = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=UUID("00000000-0000-4000-8000-000000000813"),
                    idempotency_key="stale-proof",
                    payload=proof_payload,
                ),
            ),
            operation_id=UUID("00000000-0000-4000-8000-000000000814"),
            expected_revision=reopened.snapshot.revision,
        )

    assert tuple(
        (item.proof_requirement_id, item.assessment_generation, item.status)
        for item in situation.objective_progress.requirements
    ) == (("root-flag", 2, "pending"), ("user-flag", 2, "pending"))
    with pytest.raises(ValueError, match="proof_assessment_generation_mismatch"):
        SituationReducer.rebuild(stale.snapshot)


def test_rejection_reopen_pair_advances_only_cited_proof_and_blocks_reuse(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    proof_event_id = UUID("00000000-0000-4000-8000-000000000821")
    rejection_event_id = UUID("00000000-0000-4000-8000-000000000822")
    reopen_event_id = UUID("00000000-0000-4000-8000-000000000823")
    with engagement_service(tmp_path, lambda: FIXED_TIME, fixed_uuid_factory()) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            b"0123456789abcdef0123456789abcdef",
            media_type="text/plain",
            representation="utf-8",
        )
        attached = journal.append_hook_events(
            current_manifest.engagement_id,
            (
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type="evidence_attached",
                    payload=EvidenceAttachedPayload(evidence=evidence),
                ),
            ),
            expected_revision=created.snapshot.revision,
        )
        proof_payload = ObjectiveProofObservedEventPayload(
            proof_requirement_id="user-flag",
            assessment_generation=1,
            assessment="supported",
            candidate_value=PrivateValueEventRecord(
                evidence_slice=EvidenceSliceEventRef(
                    evidence_id=evidence.evidence_id,
                    start=0,
                    end=evidence.size,
                    sha256=evidence.sha256,
                    media_type="text/plain",
                ),
                value_sha256=evidence.sha256,
            ),
            confidence=1.0,
            evidence_ids=(evidence.evidence_id,),
            source_event_ids=(attached.created_event_ids[0],),
            interpretation_input_digest="f" * 64,
        )
        observed = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=proof_event_id,
                    idempotency_key="retained-proof",
                    payload=proof_payload,
                ),
            ),
            operation_id=UUID("00000000-0000-4000-8000-000000000824"),
            expected_revision=attached.snapshot.revision,
        )
        closing = journal.request_close(
            current_manifest.engagement_id,
            lane=current_lane,
            reason="test close",
            expected_revision=observed.snapshot.revision,
        )
        closed = ReportClosureFinalizer(
            journal,
            journal._repository._issue_report_commit_capability(),
        ).finalize(snapshot=closing.snapshot)
        operation_id = UUID("00000000-0000-4000-8000-000000000825")
        lifecycle = journal._issue_lifecycle_commit_capability()
        settled_situation = SituationReducer.rebuild(closed)
        receipt = lifecycle.rejection_receipt(
            SimpleNamespace(
                authoritative_journal_revision=closed.revision,
                situation=settled_situation,
            ),
            proof_event_id,
        )
        with pytest.raises(ValueError, match="rejection_requires_current_supported_proof"):
            lifecycle.rejection_receipt(
                SimpleNamespace(
                    authoritative_journal_revision=closed.revision,
                    situation=settled_situation,
                ),
                UUID("00000000-0000-4000-8000-000000000888"),
            )
        rejection_draft = JournalEventDraft(
            event_id=rejection_event_id,
            actor="system",
            type=EventType.FLAG_REJECTED,
            payload=FlagRejectedPayload(
                flag_event_id=proof_event_id,
                rejected_value_sha256=evidence.sha256,
                reason="collect replacement",
            ),
            system_correlation=SystemCorrelation(source="lifecycle", operation_id=operation_id),
        )
        reopen_draft = JournalEventDraft(
            event_id=reopen_event_id,
            actor="system",
            type=EventType.ENGAGEMENT_REOPENED,
            payload=EngagementReopenedPayload(
                reason="collect replacement",
                prior_status="closed_unverified",
                proof_revalidation="retain_rejections",
            ),
            system_correlation=SystemCorrelation(source="lifecycle", operation_id=operation_id),
        )
        assert evidence.sha256 not in repr(receipt)
        forged_pairs = (
            (
                rejection_draft,
                reopen_draft.model_copy(
                    update={
                        "payload": reopen_draft.payload.model_copy(
                            update={"prior_status": "closed_verified"}
                        )
                    }
                ),
            ),
            (
                rejection_draft,
                reopen_draft.model_copy(
                    update={
                        "payload": reopen_draft.payload.model_copy(
                            update={"proof_revalidation": "invalidate_all"}
                        )
                    }
                ),
            ),
            (
                rejection_draft,
                reopen_draft.model_copy(
                    update={
                        "system_correlation": SystemCorrelation(
                            source="lifecycle",
                            operation_id=UUID("00000000-0000-4000-8000-000000000899"),
                        )
                    }
                ),
            ),
            (
                rejection_draft.model_copy(
                    update={
                        "payload": rejection_draft.payload.model_copy(
                            update={"reason": "different rejection reason"}
                        )
                    }
                ),
                reopen_draft,
            ),
        )
        for forged_rejection, forged_reopen in forged_pairs:
            with pytest.raises(ValueError, match="invalid sealed lifecycle batch"):
                lifecycle.commit_rejection_and_reopen(
                    current_manifest.engagement_id,
                    current_lane,
                    forged_rejection,
                    forged_reopen,
                    proof_rejection=receipt,
                    expected_revision=closed.revision,
                )
            assert journal.load_snapshot(current_manifest.engagement_id).revision == closed.revision
        with pytest.raises(ValueError, match="invalid sealed lifecycle batch"):
            lifecycle.commit_rejection_and_reopen(
                current_manifest.engagement_id,
                current_lane,
                rejection_draft,
                reopen_draft,
                proof_rejection=replace(receipt, rejected_value_sha256="a" * 64),
                expected_revision=closed.revision,
            )
        assert journal.load_snapshot(current_manifest.engagement_id).revision == closed.revision
        reopened = lifecycle.commit_rejection_and_reopen(
            current_manifest.engagement_id,
            current_lane,
            rejection_draft,
            reopen_draft,
            proof_rejection=receipt,
            expected_revision=closed.revision,
        )
        with pytest.raises(ValueError, match="invalid sealed lifecycle batch"):
            lifecycle.commit_rejection_and_reopen(
                current_manifest.engagement_id,
                current_lane,
                rejection_draft,
                reopen_draft,
                proof_rejection=receipt,
                expected_revision=reopened.snapshot.revision,
            )
        assert (
            journal.load_snapshot(current_manifest.engagement_id).revision
            == reopened.snapshot.revision
        )
        situation = SituationReducer.rebuild(reopened.snapshot)
        reused = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=UUID("00000000-0000-4000-8000-000000000826"),
                    idempotency_key="reused-proof",
                    payload=proof_payload.model_copy(update={"assessment_generation": 2}),
                ),
            ),
            operation_id=UUID("00000000-0000-4000-8000-000000000827"),
            expected_revision=reopened.snapshot.revision,
        )

    assert tuple(
        (item.proof_requirement_id, item.assessment_generation, item.status)
        for item in situation.objective_progress.requirements
    ) == (("root-flag", 1, "pending"), ("user-flag", 2, "contradicted"))
    with pytest.raises(ValueError, match="proof_value_previously_rejected"):
        SituationReducer.rebuild(reused.snapshot)


def test_interpretation_success_closes_the_exact_attachment_subject(tmp_path) -> None:
    event_id = UUID("00000000-0000-4000-8000-000000000901")
    interpretation_id = UUID("00000000-0000-4000-8000-000000000902")
    current_manifest = manifest()
    current_lane = lane()
    with engagement_service(tmp_path, lambda: FIXED_TIME, fixed_uuid_factory()) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            b"No additional observations",
            media_type="text/plain",
            representation="utf-8",
        )
        attached = journal.append_hook_events(
            current_manifest.engagement_id,
            (
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type="evidence_attached",
                    payload=EvidenceAttachedPayload(evidence=evidence),
                ),
            ),
            expected_revision=created.snapshot.revision,
        )
        evidence_slice = EvidenceSliceEventRef(
            evidence_id=evidence.evidence_id,
            start=0,
            end=evidence.size,
            sha256=evidence.sha256,
            media_type=evidence.media_type,
        )
        result = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=event_id,
                    idempotency_key="interpretation:1",
                    payload=InterpretationSucceededEventPayload(
                        interpretation_id=interpretation_id,
                        attachment_event_id=attached.created_event_ids[0],
                        evidence_id=evidence.evidence_id,
                        covered_slices=(evidence_slice,),
                        emitted_event_ids=(),
                        call_metadata=PlanningCallMetadataEventRecord(
                            purpose="observe",
                            provider="test",
                            model="scripted",
                            agent_id="agent",
                            prompt_id="planning-observation",
                            prompt_version="1",
                            response_schema_version="1",
                            input_digest="3" * 64,
                            input_tokens=1,
                            output_tokens=1,
                            elapsed_ms=1,
                        ),
                        call_input_digest="3" * 64,
                        call_output_digest="4" * 64,
                    ),
                ),
            ),
            operation_id=UUID("00000000-0000-4000-8000-000000000903"),
            expected_revision=attached.snapshot.revision,
        )

        situation = SituationReducer.rebuild(result.snapshot)

    assert len(situation.interpretations) == 1
    interpretation = situation.interpretations[0]
    assert interpretation.status == "completed"
    assert interpretation.event_ids == (event_id,)
    assert interpretation.subject.attachment_event_id == attached.created_event_ids[0]
    assert interpretation.subject.evidence_id == evidence.evidence_id


def test_unsupported_media_failure_terminally_assesses_exact_attachment(tmp_path) -> None:
    event_id = UUID("00000000-0000-4000-8000-000000000a01")
    interpretation_id = UUID("00000000-0000-4000-8000-000000000a02")
    current_manifest = manifest()
    current_lane = lane()
    with engagement_service(tmp_path, lambda: FIXED_TIME, fixed_uuid_factory()) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            b"\x89PNG\r\n\x1a\n",
            media_type="image/png",
            representation="binary",
        )
        attached = journal.append_hook_events(
            current_manifest.engagement_id,
            (
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type="evidence_attached",
                    payload=EvidenceAttachedPayload(evidence=evidence),
                ),
            ),
            expected_revision=created.snapshot.revision,
        )
        result = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=event_id,
                    idempotency_key="interpretation:unsupported:1",
                    payload=InterpretationFailedEventPayload(
                        interpretation_id=interpretation_id,
                        attachment_event_id=attached.created_event_ids[0],
                        evidence_id=evidence.evidence_id,
                        attempted_slices=(),
                        failure_code="unsupported_media",
                        retryable=False,
                        safe_summary="Binary media is not supported by the observation extractor",
                        call_input_digest="5" * 64,
                    ),
                ),
            ),
            operation_id=UUID("00000000-0000-4000-8000-000000000a03"),
            expected_revision=attached.snapshot.revision,
        )

        situation = SituationReducer.rebuild(result.snapshot)

    assert len(situation.interpretations) == 1
    interpretation = situation.interpretations[0]
    assert interpretation.status == "failed"
    assert interpretation.event_ids == (event_id,)
    assert interpretation.subject.attachment_event_id == attached.created_event_ids[0]
    assert interpretation.subject.evidence_id == evidence.evidence_id


def test_retain_rejection_advances_only_cited_proof_generation() -> None:
    from sedna.planning.models import ObjectiveProgress, ProofProgress, ProofValueReference

    proof_event_id = UUID("00000000-0000-4000-8000-000000000b01")
    rejection_event_id = UUID("00000000-0000-4000-8000-000000000b02")
    transition_event_id = UUID("00000000-0000-4000-8000-000000000b03")
    candidate_digest = "6" * 64
    empty_digest = "7" * 64
    cited = ProofProgress(
        proof_requirement_id="user-flag",
        status="supported",
        supporting_event_ids=(proof_event_id,),
        value_references=(
            ProofValueReference(
                proof_event_id=proof_event_id,
                proof_requirement_id="user-flag",
                assessment_generation=1,
                assessment="supported",
                evidence_id="evidence-sha256-" + "8" * 64,
                candidate_start=0,
                candidate_end=32,
                value_sha256=candidate_digest,
            ),
        ),
        historical_assessment_digest=empty_digest,
        rejected_value_overflow_digest=empty_digest,
    )
    untouched = ProofProgress(
        proof_requirement_id="root-flag",
        status="supported",
        supporting_event_ids=(UUID("00000000-0000-4000-8000-000000000b04"),),
        historical_assessment_digest=empty_digest,
        rejected_value_overflow_digest=empty_digest,
    )
    progress = ObjectiveProgress(requirements=(untouched, cited))

    transitioned = transition_proof_generation(
        progress,
        policy="retain_rejections",
        transition_event_id=transition_event_id,
        rejected_requirement_id="user-flag",
        rejection_event_id=rejection_event_id,
        rejected_value_sha256=candidate_digest,
    )

    by_id = {item.proof_requirement_id: item for item in transitioned.requirements}
    assert by_id["root-flag"] == untouched
    rejected = by_id["user-flag"]
    assert rejected.assessment_generation == 2
    assert rejected.generation_started_event_id == transition_event_id
    assert rejected.status == "contradicted"
    assert rejected.supporting_event_ids == ()
    assert rejected.contradicting_event_ids == (rejection_event_id,)
    assert rejected.value_references == ()
    assert rejected.rejected_value_sha256s == (candidate_digest,)
    assert rejected.historical_assessment_count == 1
    assert rejected.historical_assessment_digest != empty_digest


def test_invalidate_all_advances_every_requirement_to_pending() -> None:
    from sedna.planning.models import ObjectiveProgress, ProofProgress

    empty_digest = "9" * 64
    requirements = tuple(
        ProofProgress(
            proof_requirement_id=proof_id,
            status="supported",
            supporting_event_ids=(event_id,),
            historical_assessment_digest=empty_digest,
            rejected_value_sha256s=(("a" * 64,) if proof_id == "user-flag" else ()),
            rejected_value_overflow_digest=empty_digest,
        )
        for proof_id, event_id in (
            ("root-flag", UUID("00000000-0000-4000-8000-000000000c01")),
            ("user-flag", UUID("00000000-0000-4000-8000-000000000c02")),
        )
    )
    transition_event_id = UUID("00000000-0000-4000-8000-000000000c03")

    transitioned = transition_proof_generation(
        ObjectiveProgress(requirements=requirements),
        policy="invalidate_all",
        transition_event_id=transition_event_id,
    )

    assert tuple(item.status for item in transitioned.requirements) == ("pending", "pending")
    assert all(item.assessment_generation == 2 for item in transitioned.requirements)
    assert all(
        item.generation_started_event_id == transition_event_id
        for item in transitioned.requirements
    )
    assert all(item.supporting_event_ids == () for item in transitioned.requirements)
    assert all(item.contradicting_event_ids == () for item in transitioned.requirements)
    assert transitioned.requirements[1].rejected_value_sha256s == ("a" * 64,)


def test_proof_rejection_admission_validates_authoritative_inventory() -> None:
    from sedna.planning.models import ProofProgress, ProofRejectionRecord

    digest = "b" * 64
    record = ProofRejectionRecord(
        proof_requirement_id="user-flag",
        assessment_generation=1,
        rejection_event_id=UUID("00000000-0000-4000-8000-000000000d01"),
        rejected_proof_event_id=UUID("00000000-0000-4000-8000-000000000d02"),
        rejected_value_sha256=digest,
    )
    progress = ProofProgress(
        proof_requirement_id="user-flag",
        status="pending",
        historical_assessment_digest="c" * 64,
        rejected_value_sha256s=(digest,),
        rejected_value_overflow_digest="d" * 64,
    )

    assert (
        proof_value_was_rejected(
            progress,
            candidate_value_sha256=digest,
            authoritative_rejections=(record,),
        )
        is True
    )
    assert (
        proof_value_was_rejected(
            progress,
            candidate_value_sha256="e" * 64,
            authoritative_rejections=(record,),
        )
        is False
    )


def test_proof_rejection_overflow_requires_exact_authoritative_digest() -> None:
    from sedna.planning.models import ProofProgress, ProofRejectionRecord

    records = tuple(
        ProofRejectionRecord(
            proof_requirement_id="user-flag",
            assessment_generation=index + 1,
            rejection_event_id=UUID(int=index + 1),
            rejected_proof_event_id=UUID(int=index + 101),
            rejected_value_sha256=f"{index:064x}",
        )
        for index in range(35)
    )
    overflow_rows = [record.model_dump(mode="json") for record in records[:3]]
    overflow_digest = sha256(
        json.dumps(
            overflow_rows,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    progress = ProofProgress(
        proof_requirement_id="user-flag",
        status="pending",
        historical_assessment_digest="f" * 64,
        rejected_value_sha256s=tuple(record.rejected_value_sha256 for record in records[-32:]),
        rejected_value_overflow_count=3,
        rejected_value_overflow_digest=overflow_digest,
    )

    assert (
        proof_value_was_rejected(
            progress,
            candidate_value_sha256=records[0].rejected_value_sha256,
            authoritative_rejections=records,
        )
        is True
    )

    corrupt = progress.model_copy(update={"rejected_value_overflow_digest": "0" * 64})
    with pytest.raises(ValueError, match="proof_rejection_overflow_digest_mismatch"):
        proof_value_was_rejected(
            corrupt,
            candidate_value_sha256=records[0].rejected_value_sha256,
            authoritative_rejections=records,
        )


def test_outcome_assessment_projects_categorical_attempt_with_exact_links(tmp_path) -> None:
    event_id = UUID("00000000-0000-4000-8000-000000000e01")
    current_manifest = manifest()
    with engagement_service(tmp_path, lambda: FIXED_TIME, fixed_uuid_factory()) as journal:
        created = journal.create_from_manifest(current_manifest, lane=lane())
        attachment_id = created.snapshot.events[0].event_id
        terminal_id = created.snapshot.events[1].event_id
        result = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=event_id,
                    idempotency_key="outcome:1",
                    payload=OutcomeAssessedEventPayload(
                        attachment_event_id=attachment_id,
                        terminal_tool_event_id=terminal_id,
                        tool_call_ids=("tool:syntax",),
                        category="execution_error",
                        summary="Command syntax was invalid",
                        strategic_impact="No strategy evidence was produced",
                        source_event_ids=(terminal_id,),
                        interpretation_input_digest="1" * 64,
                    ),
                ),
            ),
            operation_id=UUID("00000000-0000-4000-8000-000000000e02"),
            expected_revision=created.snapshot.revision,
        )

        situation = SituationReducer.rebuild(result.snapshot)

    assert len(situation.attempts) == 1
    attempt = situation.attempts[0]
    assert attempt.event_ids == (event_id,)
    assert attempt.attempt_event_id == terminal_id
    assert attempt.outcome == "execution_error"
    assert attempt.summary == "Command syntax was invalid"


def test_interpretation_state_aggregates_each_attachment_and_requires_contiguous_coverage(
    tmp_path,
) -> None:
    """A slice is not terminal until this exact attachment is wholly covered."""
    current_manifest = manifest()
    with engagement_service(tmp_path, lambda: FIXED_TIME, fixed_uuid_factory()) as journal:
        created = journal.create_from_manifest(current_manifest, lane=lane())
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            b"abcdefgh",
            media_type="text/plain",
            representation="utf-8",
        )
        attached = journal.append_hook_events(
            current_manifest.engagement_id,
            (
                JournalEventDraft(
                    lane=lane(),
                    actor="host_agent",
                    type="evidence_attached",
                    payload=EvidenceAttachedPayload(evidence=evidence),
                ),
            ),
            expected_revision=created.snapshot.revision,
        )
        attachment_id = attached.created_event_ids[0]

        def succeeded(event_id: UUID, start: int, end: int) -> PlanningEventCommitItem:
            return PlanningEventCommitItem(
                event_id=event_id,
                idempotency_key=f"slice:{start}:{end}",
                payload=InterpretationSucceededEventPayload(
                    interpretation_id=UUID(f"00000000-0000-4000-8000-{start + 0x1100:012d}"),
                    attachment_event_id=attachment_id,
                    evidence_id=evidence.evidence_id,
                    covered_slices=(
                        EvidenceSliceEventRef(
                            evidence_id=evidence.evidence_id,
                            start=start,
                            end=end,
                            sha256=sha256(b"abcdefgh"[start:end]).hexdigest(),
                            media_type="text/plain",
                        ),
                    ),
                    emitted_event_ids=(),
                    call_metadata=PlanningCallMetadataEventRecord(
                        purpose="observe",
                        provider="test",
                        model="test",
                        agent_id="test",
                        prompt_id="planning-observation",
                        prompt_version="1",
                        response_schema_version="1",
                        input_digest="a" * 64,
                        input_tokens=1,
                        output_tokens=1,
                        elapsed_ms=1,
                    ),
                    call_input_digest="a" * 64,
                    call_output_digest="b" * 64,
                ),
            )

        first = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (succeeded(UUID("00000000-0000-4000-8000-000000001101"), 0, 4),),
            operation_id=UUID("00000000-0000-4000-8000-000000001102"),
            expected_revision=attached.snapshot.revision,
        )
        partial = SituationReducer.rebuild(first.snapshot)
        second = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (succeeded(UUID("00000000-0000-4000-8000-000000001103"), 4, 8),),
            operation_id=UUID("00000000-0000-4000-8000-000000001104"),
            expected_revision=first.snapshot.revision,
        )
        completed = SituationReducer.rebuild(second.snapshot)

    assert len(partial.interpretations) == 1
    assert partial.interpretations[0].status == "pending"
    assert len(completed.interpretations) == 1
    assert completed.interpretations[0].status == "completed"
    assert completed.interpretations[0].event_ids == (
        UUID("00000000-0000-4000-8000-000000001101"),
        UUID("00000000-0000-4000-8000-000000001103"),
    )


def test_partial_proof_and_observation_slices_use_their_own_digest_not_whole_sidecar(
    tmp_path,
) -> None:
    """Task 9 grounds a candidate at its range, not at the entire sidecar digest."""
    current_manifest = manifest()
    with engagement_service(tmp_path, lambda: FIXED_TIME, fixed_uuid_factory()) as journal:
        created = journal.create_from_manifest(current_manifest, lane=lane())
        data = b"prefix-flag-value-suffix"
        evidence = journal.write_evidence(
            current_manifest.engagement_id, data, media_type="text/plain", representation="utf-8"
        )
        attached = journal.append_hook_events(
            current_manifest.engagement_id,
            (
                JournalEventDraft(
                    lane=lane(),
                    actor="host_agent",
                    type="evidence_attached",
                    payload=EvidenceAttachedPayload(evidence=evidence),
                ),
            ),
            expected_revision=created.snapshot.revision,
        )
        start, end = 7, 17
        slice_ref = EvidenceSliceEventRef(
            evidence_id=evidence.evidence_id,
            start=start,
            end=end,
            sha256=sha256(data[start:end]).hexdigest(),
            media_type="text/plain",
        )
        result = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=UUID("00000000-0000-4000-8000-000000001201"),
                    idempotency_key="partial-proof",
                    payload=ObjectiveProofObservedEventPayload(
                        proof_requirement_id="user-flag",
                        assessment_generation=1,
                        assessment="supported",
                        candidate_value=PrivateValueEventRecord(
                            evidence_slice=slice_ref, value_sha256=slice_ref.sha256
                        ),
                        confidence=1.0,
                        evidence_ids=(evidence.evidence_id,),
                        source_event_ids=(attached.created_event_ids[0],),
                        interpretation_input_digest="c" * 64,
                    ),
                ),
            ),
            operation_id=UUID("00000000-0000-4000-8000-000000001202"),
            expected_revision=attached.snapshot.revision,
        )
        situation = SituationReducer.rebuild(result.snapshot)

    value = next(
        item
        for item in situation.objective_progress.requirements
        if item.proof_requirement_id == "user-flag"
    ).value_references[0]
    assert (value.candidate_start, value.candidate_end, value.value_sha256) == (
        start,
        end,
        slice_ref.sha256,
    )
