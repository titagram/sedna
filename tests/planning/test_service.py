from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from sedna.engagement import (
    EngagementJournalService,
    EngagementManifest,
    EngagementStatus,
    EvidenceAttachedPayload,
    ExecutionLaneKey,
    HostKind,
    JournalEventDraft,
    ProofRequirement,
    SessionCheckpointedPayload,
    ToolCallCompletedPayload,
    ToolCallStartedPayload,
    ToolCorrelation,
)
from sedna.engagement.events import (
    ArchivedStrategyEventRecord,
    EvidenceSliceEventRef,
    InterpretationFailedEventPayload,
)
from sedna.engagement.service import PlanningEventCommitItem
from sedna.knowledge.retrieval import AuthorizationScope, AuthorizationState, ValidatedTarget
from sedna.knowledge.schema.execution import PlaceholderKind
from sedna.planning.commands import CommandBinding, CommandOrigin, CommandSuggestionDraft
from sedna.planning.frontier import FrontierReducer
from sedna.planning.llm import PlanningLlmError
from sedna.planning.models import (
    FacetObservationDraft,
    FrontierProposalDraft,
    IncompleteSettlementResult,
    InterpretationSubject,
    ObservationBatchDraft,
    ObservationDraft,
    PendingEvidenceRange,
    PlannerCriticVerdict,
    PlannerDraft,
    PlannerFinding,
    RetryPredicate,
    RetryPredicateKind,
    SettlementResultAdapter,
    SituationProjection,
    StrategyStatus,
)
from sedna.planning.ports import TerminalReconciliationResult
from sedna.planning.service import PlanningService

FIXED_TIME = datetime(2026, 8, 11, 12, 30, tzinfo=UTC)


def test_task9_public_service_and_reducer_are_exported() -> None:
    from sedna.planning import PlanningService as PublicPlanningService
    from sedna.planning import SituationReducer

    assert PublicPlanningService is PlanningService
    assert SituationReducer.__name__ == "SituationReducer"


class FailingLlm:
    def complete(self, *args, **kwargs):
        raise AssertionError("LLM must not be called without pending evidence")


class UnavailableLlm:
    def complete(self, *args, **kwargs):
        raise RuntimeError("extractor offline")


class EmptyObservationLlm:
    def __init__(self, subject: InterpretationSubject) -> None:
        self.subject = subject
        self.calls = []

    def complete(self, model_type, **kwargs):
        self.calls.append((model_type, kwargs))
        return SimpleNamespace(
            parsed=ObservationBatchDraft(subject=self.subject),
            provider="test-provider",
            model="test-model",
            agent_id="test-agent",
            usage=SimpleNamespace(input_tokens=7, output_tokens=3),
        )


class GroundedObservationLlm(EmptyObservationLlm):
    def complete(self, model_type, **kwargs):
        completion = super().complete(model_type, **kwargs)
        event_id = kwargs["payload"].evidence_slices[0].event_id
        return SimpleNamespace(
            **{
                **completion.__dict__,
                "parsed": ObservationBatchDraft(
                    subject=self.subject,
                    observations=(
                        ObservationDraft(
                            kind="text",
                            text="OpenSSH 9.6 is reachable",
                            event_ids=(event_id,),
                        ),
                    ),
                    facets=(
                        FacetObservationDraft(
                            key="service",
                            value="ssh",
                            event_ids=(event_id,),
                        ),
                    ),
                ),
            }
        )


class ProtectedObservationLlm(EmptyObservationLlm):
    def __init__(self, subject: InterpretationSubject, protected_value: str, kind: str) -> None:
        super().__init__(subject)
        self.protected_value = protected_value
        self.kind = kind

    def complete(self, model_type, **kwargs):
        completion = super().complete(model_type, **kwargs)
        event_id = kwargs["payload"].evidence_slices[0].event_id
        observations = ()
        facets = ()
        if self.kind == "text":
            observations = (
                ObservationDraft(
                    kind="text",
                    text=f"Recovered proof: {self.protected_value}",
                    event_ids=(event_id,),
                ),
            )
        else:
            facets = (
                FacetObservationDraft(
                    key="proof",
                    value=self.protected_value,
                    event_ids=(event_id,),
                ),
            )
        return SimpleNamespace(
            **{
                **completion.__dict__,
                "parsed": ObservationBatchDraft(
                    subject=self.subject,
                    observations=observations,
                    facets=facets,
                ),
            }
        )


class PlannerUnavailableLlm:
    def complete(self, model_type, **kwargs):
        raise PlanningLlmError("transport_failure")


class PlannerProgrammingErrorLlm:
    def complete(self, model_type, **kwargs):
        raise TypeError("local programming error")


class InvalidSubjectLlm:
    def complete(self, model_type, **kwargs):
        return SimpleNamespace(
            parsed=ObservationBatchDraft(
                subject=InterpretationSubject(
                    attachment_event_id=UUID("00000000-0000-4000-8000-000000009999"),
                    evidence_id="evidence-sha256-" + "0" * 64,
                )
            ),
            provider="test-provider",
            model="test-model",
            agent_id="test-agent",
            usage=SimpleNamespace(input_tokens=7, output_tokens=3),
        )


class ConcurrentWriteOnceLlm(EmptyObservationLlm):
    def __init__(self, subject, journal, engagement_id, current_lane, expected_revision) -> None:
        super().__init__(subject)
        self._journal = journal
        self._engagement_id = engagement_id
        self._lane = current_lane
        self._expected_revision = expected_revision

    def complete(self, model_type, **kwargs):
        if not self.calls:
            self._journal.append_hook_events(
                self._engagement_id,
                (
                    JournalEventDraft(
                        lane=self._lane,
                        actor="host_agent",
                        type="session_checkpointed",
                        payload=SessionCheckpointedPayload(
                            completed=False,
                            interrupted=False,
                            reason="concurrent checkpoint",
                        ),
                    ),
                ),
                expected_revision=self._expected_revision,
            )
        return super().complete(model_type, **kwargs)


class SubjectEchoLlm:
    def __init__(self) -> None:
        self.subjects = []

    def complete(self, model_type, **kwargs):
        subject = InterpretationSubject(
            attachment_event_id=kwargs["payload"].evidence_slices[0].event_id,
            evidence_id=kwargs["payload"].evidence_slices[0].evidence_id,
        )
        self.subjects.append(subject)
        return SimpleNamespace(
            parsed=ObservationBatchDraft(subject=subject),
            provider="test-provider",
            model="test-model",
            agent_id="test-agent",
            usage=SimpleNamespace(input_tokens=7, output_tokens=3),
        )


class RecordingTerminalPort:
    def __init__(self) -> None:
        self.calls = []

    def reconcile(self, **kwargs):
        self.calls.append(kwargs)
        return TerminalReconciliationResult(
            action="unchanged",
            authoritative_journal_revision=kwargs["authoritative_revision"],
            lifecycle_status=EngagementStatus.ACTIVE,
        )


class FailedTerminalPort:
    def reconcile(self, **kwargs):
        return TerminalReconciliationResult(
            action="failed",
            authoritative_journal_revision=kwargs["authoritative_revision"],
            lifecycle_status=EngagementStatus.ACTIVE,
            safe_code="terminal_reconciliation_failed",
        )


class JournalMutatingTerminalPort:
    def __init__(self, journal, current_lane, action) -> None:
        self._journal = journal
        self._lane = current_lane
        self._action = action
        self.calls = []

    def reconcile(self, **kwargs):
        self.calls.append(kwargs)
        if self._action == "unchanged":
            snapshot = self._journal.load_snapshot(kwargs["engagement_id"])
        elif self._action == "proof_close_requested":
            self._journal.request_close(
                kwargs["engagement_id"],
                lane=self._lane,
                reason="proofs reconciled",
                expected_revision=kwargs["authoritative_revision"],
            )
            snapshot = self._journal.load_snapshot(kwargs["engagement_id"])
        else:
            raise AssertionError("unsupported test action")
        return TerminalReconciliationResult(
            action=self._action,
            authoritative_journal_revision=snapshot.revision,
            lifecycle_status=snapshot.state.status,
        )


class MismatchedTerminalPort:
    def reconcile(self, **kwargs):
        return TerminalReconciliationResult(
            action="unchanged",
            authoritative_journal_revision=kwargs["authoritative_revision"],
            lifecycle_status=EngagementStatus.CLOSING,
        )


def uuid_factory():
    next_value = 1

    def factory() -> UUID:
        nonlocal next_value
        value = UUID(f"00000000-0000-4000-8000-{next_value:012d}")
        next_value += 1
        return value

    return factory


def manifest() -> EngagementManifest:
    return EngagementManifest(
        engagement_id=UUID("22222222-2222-4222-8222-222222222222"),
        display_name="HTB-Orion",
        initial_objective="Obtain flags",
        initial_scope=AuthorizationScope(
            state=AuthorizationState.AUTHORIZED,
            exact_targets=(ValidatedTarget.parse("192.0.2.44"),),
        ),
        required_proofs=(
            ProofRequirement(proof_id="user-flag", kind="flag", description="User flag"),
            ProofRequirement(proof_id="root-flag", kind="flag", description="Root flag"),
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
def journal_service(tmp_path):
    with EngagementJournalService.open(
        tmp_path / "knowledge",
        clock=lambda: FIXED_TIME,
        uuid_factory=uuid_factory(),
    ) as service:
        yield service


def attach_text_evidence(journal, current_manifest, current_lane, content=b"pending"):
    created = journal.create_from_manifest(current_manifest, lane=current_lane)
    evidence = journal.write_evidence(
        current_manifest.engagement_id,
        content,
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
    return evidence, attached


def test_nothing_pending_skips_llm_and_persists_exact_situation(tmp_path) -> None:
    current_manifest = manifest()
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=lane())
        service = PlanningService(journal=journal, llm=FailingLlm(), clock=lambda: FIXED_TIME)

        result = service.settle_pending_evidence(
            current_manifest.engagement_id,
            reason="plan",
        )

        decoded = SettlementResultAdapter.validate_json(SettlementResultAdapter.dump_json(result))
        persisted = journal.load_projection(
            current_manifest.engagement_id,
            "state",
            SituationProjection,
        )

    assert decoded == result
    assert result.status == "nothing_pending"
    assert result.authoritative_journal_revision == created.snapshot.revision
    assert result.situation == persisted
    assert result.required_proof_ids == ("root-flag", "user-flag")
    assert result.all_required_proofs_satisfied is False
    assert result.possible_terminal_evidence is False
    assert result.pending_ranges == ()
    assert result.pending_total_count == 0
    state_path = (
        tmp_path / "knowledge" / "engagements" / str(current_manifest.engagement_id) / "state.json"
    )
    assert state_path.is_file()


def test_nothing_pending_reconciles_explicit_required_proof_state(tmp_path) -> None:
    current_manifest = manifest()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=lane())
        terminal = RecordingTerminalPort()
        service = PlanningService(
            journal=journal,
            llm=FailingLlm(),
            terminal_settlement_port=terminal,
            clock=lambda: FIXED_TIME,
        )

        result = service.settle_pending_evidence(current_manifest.engagement_id, reason="plan")

    assert result.status == "nothing_pending"
    assert len(terminal.calls) == 1
    assert terminal.calls[0]["requirement_ids"] == ("root-flag", "user-flag")
    assert terminal.calls[0]["all_required_proofs_satisfied"] is False
    assert terminal.calls[0]["situation"] == result.situation


def test_terminal_failure_returns_a_closed_failed_variant(tmp_path) -> None:
    current_manifest = manifest()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=lane())
        result = PlanningService(
            journal=journal,
            llm=FailingLlm(),
            terminal_settlement_port=FailedTerminalPort(),
            clock=lambda: FIXED_TIME,
        ).settle_pending_evidence(current_manifest.engagement_id, reason="plan")

    assert result.status == "failed"
    assert result.failure_code == "terminal_reconciliation_failed"


@pytest.mark.parametrize(
    ("action", "expected_status"),
    (("unchanged", EngagementStatus.ACTIVE), ("proof_close_requested", EngagementStatus.CLOSING)),
)
def test_terminal_port_reloads_revision_and_lifecycle_after_journal_mutation(
    tmp_path, action, expected_status
) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        terminal = JournalMutatingTerminalPort(journal, current_lane, action)
        result = PlanningService(
            journal=journal,
            llm=FailingLlm(),
            terminal_settlement_port=terminal,
            clock=lambda: FIXED_TIME,
        ).settle_pending_evidence(current_manifest.engagement_id, reason="plan")
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    assert result.status == "nothing_pending"
    assert terminal.calls[0]["all_required_proofs_satisfied"] is False
    assert result.authoritative_journal_revision == snapshot.revision
    assert snapshot.state.status is expected_status
    assert result.situation.authoritative_journal_revision == snapshot.revision


def test_terminal_port_status_mismatch_returns_terminal_reconciliation_failed(tmp_path) -> None:
    current_manifest = manifest()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=lane())
        result = PlanningService(
            journal=journal,
            llm=FailingLlm(),
            terminal_settlement_port=MismatchedTerminalPort(),
            clock=lambda: FIXED_TIME,
        ).settle_pending_evidence(current_manifest.engagement_id, reason="plan")

    assert result.status == "failed"
    assert result.failure_code == "terminal_reconciliation_failed"


def test_load_situation_rebuilds_when_the_cached_state_projection_is_corrupt(tmp_path) -> None:
    current_manifest = manifest()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=lane())
        service = PlanningService(journal=journal, llm=FailingLlm(), clock=lambda: FIXED_TIME)
        first = service.load_situation(current_manifest.engagement_id)
        state_path = (
            tmp_path
            / "knowledge"
            / "engagements"
            / str(current_manifest.engagement_id)
            / "state.json"
        )
        state_path.write_text("not-json", encoding="utf-8")

        rebuilt = service.load_situation(current_manifest.engagement_id)

    assert rebuilt == first


def test_cross_loading_state_and_engagement_state_fails_closed(tmp_path) -> None:
    current_manifest = manifest()
    engagement_dir = tmp_path / "knowledge" / "engagements" / str(current_manifest.engagement_id)
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=lane())
        service = PlanningService(journal=journal, llm=FailingLlm(), clock=lambda: FIXED_TIME)
        canonical = service.load_situation(current_manifest.engagement_id)
        state_path = engagement_dir / "state.json"
        engagement_state_path = engagement_dir / "engagement-state.json"
        planning_bytes = state_path.read_bytes()
        engagement_bytes = engagement_state_path.read_bytes()

        state_path.write_bytes(engagement_bytes)
        assert service.load_situation(current_manifest.engagement_id) == canonical

        engagement_state_path.write_bytes(planning_bytes)
        result = service.settle_pending_evidence(current_manifest.engagement_id, reason="plan")

    assert result.status == "nothing_pending"
    assert state_path.read_bytes() != engagement_bytes
    assert engagement_state_path.read_bytes() == engagement_bytes


def test_binary_attachment_is_terminally_settled_without_llm_or_duplicates(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
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
        service = PlanningService(journal=journal, llm=FailingLlm(), clock=lambda: FIXED_TIME)

        first = service.settle_pending_evidence(
            current_manifest.engagement_id,
            reason="plan",
        )
        second = service.settle_pending_evidence(
            current_manifest.engagement_id,
            reason="plan",
        )
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    failures = [event for event in snapshot.events if event.type == "interpretation_failed"]
    assert first.status == "settled"
    assert first.authoritative_journal_revision.sequence == attached.snapshot.revision.sequence + 1
    assert second.status == "nothing_pending"
    assert len(failures) == 1
    assert failures[0].payload.failure_code == "unsupported_media"
    assert failures[0].payload.retryable is False
    assert failures[0].payload.attempted_slices == ()
    assert failures[0].payload.call_metadata is None
    assert failures[0].payload.attachment_event_id == attached.created_event_ids[0]
    assert failures[0].payload.evidence_id == evidence.evidence_id


def test_settled_binary_reconciles_required_proofs_after_the_append(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            b"x",
            media_type="image/png",
            representation="binary",
        )
        journal.append_hook_events(
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
        terminal = RecordingTerminalPort()
        result = PlanningService(
            journal=journal,
            llm=FailingLlm(),
            terminal_settlement_port=terminal,
            clock=lambda: FIXED_TIME,
        ).settle_pending_evidence(current_manifest.engagement_id, reason="plan")

    assert result.status == "settled"
    assert len(terminal.calls) == 1
    assert terminal.calls[0]["authoritative_revision"] == result.authoritative_journal_revision


def test_text_attachment_is_read_observed_and_authoritatively_settled(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    content = b"OpenSSH 9.6 is reachable"
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            content,
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
        subject = InterpretationSubject(
            attachment_event_id=attached.created_event_ids[0],
            evidence_id=evidence.evidence_id,
        )
        llm = EmptyObservationLlm(subject)
        terminal = RecordingTerminalPort()
        service = PlanningService(
            journal=journal,
            llm=llm,
            terminal_settlement_port=terminal,
            clock=lambda: FIXED_TIME,
        )

        result = service.settle_pending_evidence(
            current_manifest.engagement_id,
            reason="plan",
        )
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    assert result.status == "settled"
    assert len(llm.calls) == 1
    model_type, call = llm.calls[0]
    assert model_type is ObservationBatchDraft
    assert call["purpose"] == "sedna.planning.observe"
    request_slice = call["payload"].evidence_slices[0]
    assert request_slice.event_id == attached.created_event_ids[0]
    assert request_slice.evidence_id == evidence.evidence_id
    assert request_slice.start == 0
    assert request_slice.end == len(content)
    assert request_slice.content == content
    successes = [event for event in snapshot.events if event.type == "interpretation_succeeded"]
    assert len(successes) == 1
    assert successes[0].payload.attachment_event_id == attached.created_event_ids[0]
    assert successes[0].payload.covered_slices[0].start == 0
    assert successes[0].payload.covered_slices[0].end == len(content)


def test_grounded_observations_and_facets_are_committed_with_interpretation_atomically(
    tmp_path,
) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        evidence, attached = attach_text_evidence(
            journal,
            current_manifest,
            current_lane,
            content=b"OpenSSH 9.6 is reachable",
        )
        subject = InterpretationSubject(
            attachment_event_id=attached.created_event_ids[0],
            evidence_id=evidence.evidence_id,
        )

        result = PlanningService(
            journal=journal,
            llm=GroundedObservationLlm(subject),
            clock=lambda: FIXED_TIME,
        ).settle_pending_evidence(current_manifest.engagement_id, reason="plan")
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    assert result.status == "settled"
    assert [
        event.type
        for event in snapshot.events
        if event.type in {"observation_extracted", "interpretation_succeeded"}
    ] == ["observation_extracted", "observation_extracted", "interpretation_succeeded"]
    assert tuple(item.text for item in result.situation.facts) == ("OpenSSH 9.6 is reachable",)
    assert tuple((item.key, item.value) for item in result.situation.facets) == (
        ("service", "ssh"),
    )
    interpretation = next(
        event for event in snapshot.events if event.type == "interpretation_succeeded"
    )
    assert len(interpretation.payload.emitted_event_ids) == 2


@pytest.mark.parametrize("observation_kind", ("text", "facet"))
def test_protected_values_fail_closed_before_public_observation_persistence(
    tmp_path, observation_kind
) -> None:
    protected_value = "HTB{private-root-flag}"
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        evidence, attached = attach_text_evidence(
            journal,
            current_manifest,
            current_lane,
            content=protected_value.encode(),
        )
        subject = InterpretationSubject(
            attachment_event_id=attached.created_event_ids[0],
            evidence_id=evidence.evidence_id,
        )
        service = PlanningService(
            journal=journal,
            llm=ProtectedObservationLlm(subject, protected_value, observation_kind),
            clock=lambda: FIXED_TIME,
            known_flag_values=(protected_value,),
        )

        result = service.settle_pending_evidence(current_manifest.engagement_id, reason="plan")
        snapshot = journal.load_snapshot(current_manifest.engagement_id)
        situation = service.load_situation(current_manifest.engagement_id)

    assert result.status == "failed"
    assert result.failure_code == "invalid_extractor_output"
    assert situation.facts == ()
    assert situation.facets == ()
    assert not any(event.type == "observation_extracted" for event in snapshot.events)
    assert protected_value not in snapshot.model_dump_json()


def test_planner_unavailability_publishes_an_authoritative_typed_gap(tmp_path) -> None:
    current_manifest = manifest()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=lane())
        result = PlanningService(
            journal=journal,
            llm=PlannerUnavailableLlm(),
            clock=lambda: FIXED_TIME,
        ).plan_next(lane(), max_proposals=3)
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    assert result.status == "gap"
    assert result.gap is not None
    assert result.gap.code == "llm_unavailable"
    assert result.gap.retryable is True
    assert result.current_authoritative_journal_revision == snapshot.revision
    gaps = [event for event in snapshot.events if event.type == "planning_gap_recorded"]
    assert len(gaps) == 1
    assert gaps[0].payload.code == "llm_unavailable"
    assert gaps[0].payload.situation_digest == result.gap.situation_digest
    assert gaps[0].payload.ledger_digest == result.gap.ledger_digest


def test_planner_programming_error_is_not_rewritten_as_llm_unavailable(tmp_path) -> None:
    current_manifest = manifest()
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=lane())
        service = PlanningService(
            journal=journal,
            llm=PlannerProgrammingErrorLlm(),
            clock=lambda: FIXED_TIME,
        )

        with pytest.raises(TypeError, match="local programming error"):
            service.plan_next(lane(), max_proposals=3)

        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    assert snapshot.revision == created.snapshot.revision
    assert not any(event.type == "planning_gap_recorded" for event in snapshot.events)


def test_research_consultation_and_assessment_are_committed_atomically(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    query_id = UUID("00000000-0000-4000-8000-000000000801")
    content = b"OpenSSH documents version 9.6."
    with journal_service(tmp_path) as journal:
        evidence, attached = attach_text_evidence(
            journal,
            current_manifest,
            current_lane,
            content=content,
        )
        service = PlanningService(
            journal=journal,
            llm=PlannerUnavailableLlm(),
            clock=lambda: FIXED_TIME,
        )
        query = service._commit_planning_events(
            current_manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=UUID("00000000-0000-4000-8000-000000000802"),
                    idempotency_key="test:research-query",
                    payload=service._research_query_payload(
                        "OpenSSH 9.6",
                        query_id=query_id,
                        authoritative_aliases=(current_manifest.display_name,),
                        related_event_ids=(attached.created_event_ids[0],),
                    ),
                ),
            ),
            operation_id=UUID("00000000-0000-4000-8000-000000000803"),
            expected_revision=attached.snapshot.revision,
        )
        completed = journal.append_hook_events(
            current_manifest.engagement_id,
            (
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type="tool_call_started",
                    payload=ToolCallStartedPayload(
                        call_id="research-result",
                        tool_name="fixture",
                        correlation=ToolCorrelation.uncertain("missing_stable_identity"),
                        safe_arguments={},
                    ),
                ),
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type="tool_call_completed",
                    payload=ToolCallCompletedPayload(
                        call_id="research-result",
                        correlation=ToolCorrelation.uncertain("missing_stable_identity"),
                        technical_status="returned",
                        duration_ms=1,
                        evidence_id=evidence.evidence_id,
                        evidence_attachment_event_id=attached.created_event_ids[0],
                    ),
                ),
            ),
            expected_revision=query.snapshot.revision,
        )
        before = completed.snapshot

        research_result = {
            "query_id": query_id,
            "source_id": "source-openssh",
            "normalized_locator": "https://example.test/openssh",
            "content": content,
            "media_type": "text/plain",
            "evidence_ids": (evidence.evidence_id,),
            "tool_event_ids": (completed.created_event_ids[1],),
            "assessment": "useful",
            "confidence": 0.9,
            "summary": "The source confirms the observed OpenSSH version.",
            "related_event_ids": (completed.created_event_ids[1],),
            "suggested_registry_status": "useful",
        }
        with pytest.raises(ValueError, match="research_content_not_in_evidence"):
            service.record_research_result(
                current_lane,
                **{**research_result, "content": b"fabricated result"},
            )
        with pytest.raises(ValueError, match="protected_research_value"):
            PlanningService(
                journal=journal,
                llm=PlannerUnavailableLlm(),
                clock=lambda: FIXED_TIME,
                known_flag_values=("HTB{private}",),
            ).record_research_result(
                current_lane,
                **{**research_result, "summary": "Leaked HTB{private}"},
            )

        situation = service.record_research_result(current_lane, **research_result)
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    assert snapshot.revision.sequence == before.revision.sequence + 2
    assert [event.type for event in snapshot.events[-2:]] == [
        "research_source_consulted",
        "research_source_assessed",
    ]
    assert snapshot.events[-1].payload.consulted_event_id == snapshot.events[-2].event_id
    assert situation.authoritative_journal_revision == snapshot.revision
    assert situation.research_sources[0].source_id == "source-openssh"


def test_extractor_failure_returns_last_committed_situation_and_leaves_subject_pending(
    tmp_path,
) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            b"unavailable",
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
        result = PlanningService(
            journal=journal, llm=UnavailableLlm(), clock=lambda: FIXED_TIME
        ).settle_pending_evidence(current_manifest.engagement_id, reason="plan")
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    assert result.status == "failed"
    assert result.failure_code == "extractor_unavailable"
    assert result.situation.authoritative_journal_revision == attached.snapshot.revision
    assert not [event for event in snapshot.events if event.type == "interpretation_succeeded"]


def test_failed_settlement_does_not_invoke_terminal_port(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        evidence, _ = attach_text_evidence(journal, current_manifest, current_lane)
        terminal = RecordingTerminalPort()
        result = PlanningService(
            journal=journal,
            llm=UnavailableLlm(),
            terminal_settlement_port=terminal,
            clock=lambda: FIXED_TIME,
        ).settle_pending_evidence(current_manifest.engagement_id, reason="plan")

    assert result.status == "failed"
    assert evidence.evidence_id
    assert terminal.calls == []


@pytest.mark.parametrize(
    ("fault", "expected_code"),
    (
        ("invalid_output", "invalid_extractor_output"),
        ("read", "evidence_read_failed"),
        ("append", "journal_append_failed"),
    ),
)
def test_settlement_failure_codes_leave_the_attachment_pending(
    tmp_path, monkeypatch, fault, expected_code
) -> None:
    """A broken settlement boundary must neither consume nor misclassify pending evidence."""
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        _, attached = attach_text_evidence(journal, current_manifest, current_lane)
        service = PlanningService(
            journal=journal,
            llm=InvalidSubjectLlm()
            if fault == "invalid_output"
            else EmptyObservationLlm(
                InterpretationSubject(
                    attachment_event_id=attached.created_event_ids[0],
                    evidence_id=journal.load_snapshot(current_manifest.engagement_id)
                    .events[-1]
                    .payload.evidence.evidence_id,
                )
            ),
            clock=lambda: FIXED_TIME,
        )
        if fault == "read":
            monkeypatch.setattr(
                journal,
                "read_evidence_slice",
                lambda *args, **kwargs: (_ for _ in ()).throw(OSError("read failed")),
            )
        elif fault == "append":
            capability = journal._issue_planning_event_commit_capability()
            monkeypatch.setattr(
                capability,
                "commit_planning_events",
                lambda *args, **kwargs: (_ for _ in ()).throw(OSError("append failed")),
            )
            monkeypatch.setattr(
                journal,
                "_issue_planning_event_commit_capability",
                lambda: capability,
            )

        result = service.settle_pending_evidence(current_manifest.engagement_id, reason="plan")
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    assert result.status == "failed"
    assert result.failure_code == expected_code
    assert result.authoritative_journal_revision == attached.snapshot.revision
    assert not [event for event in snapshot.events if event.type == "interpretation_succeeded"]


def test_stale_append_reloads_and_retries_once_without_duplicate_llm_output(tmp_path) -> None:
    """One concurrent journal write causes one fresh LLM attempt and one durable success."""
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        evidence, attached = attach_text_evidence(journal, current_manifest, current_lane)
        subject = InterpretationSubject(
            attachment_event_id=attached.created_event_ids[0], evidence_id=evidence.evidence_id
        )
        llm = ConcurrentWriteOnceLlm(
            subject,
            journal,
            current_manifest.engagement_id,
            current_lane,
            attached.snapshot.revision,
        )
        result = PlanningService(
            journal=journal, llm=llm, clock=lambda: FIXED_TIME
        ).settle_pending_evidence(current_manifest.engagement_id, reason="plan")
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    assert result.status == "settled"
    assert len(llm.calls) == 2
    assert (
        len([event for event in snapshot.events if event.type == "interpretation_succeeded"]) == 1
    )


def test_pending_fairness_orders_never_attempted_before_retryable_attempt_after_restart(
    tmp_path,
) -> None:
    """A persisted retryable attempt cannot starve a subject that has never been attempted."""
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        first_evidence, first_attached = attach_text_evidence(
            journal, current_manifest, current_lane, b"retry later"
        )
        second_evidence = journal.write_evidence(
            current_manifest.engagement_id,
            b"never attempted",
            media_type="text/plain",
            representation="utf-8",
        )
        second_attached = journal.append_hook_events(
            current_manifest.engagement_id,
            (
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type="evidence_attached",
                    payload=EvidenceAttachedPayload(evidence=second_evidence),
                ),
            ),
            expected_revision=first_attached.snapshot.revision,
        )
        retryable = journal._issue_planning_event_commit_capability().commit_planning_events(
            current_manifest.engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=UUID("00000000-0000-4000-8000-000000009001"),
                    idempotency_key="retryable-first-subject",
                    payload=InterpretationFailedEventPayload(
                        interpretation_id=UUID("00000000-0000-4000-8000-000000009002"),
                        attachment_event_id=first_attached.created_event_ids[0],
                        evidence_id=first_evidence.evidence_id,
                        attempted_slices=(
                            EvidenceSliceEventRef(
                                evidence_id=first_evidence.evidence_id,
                                start=0,
                                end=first_evidence.size,
                                sha256=first_evidence.sha256,
                                media_type="text/plain",
                            ),
                        ),
                        failure_code="llm_unavailable",
                        retryable=True,
                        safe_summary="Temporary model failure",
                        call_input_digest="a" * 64,
                    ),
                ),
            ),
            operation_id=UUID("00000000-0000-4000-8000-000000009003"),
            expected_revision=second_attached.snapshot.revision,
        )
        inventory_service = PlanningService(
            journal=journal,
            llm=SubjectEchoLlm(),
            clock=lambda: FIXED_TIME,
        )
        pending_ranges, _, _, _ = inventory_service._pending_inventory(
            current_manifest.engagement_id,
            retryable.snapshot,
        )
        first_pending = next(
            item
            for item in pending_ranges
            if item.attachment_event_id == first_attached.created_event_ids[0]
        )
        assert first_pending.reason == "retryable_interpretation_failure"

    llm = SubjectEchoLlm()
    with journal_service(tmp_path) as restarted:
        result = PlanningService(
            journal=restarted, llm=llm, clock=lambda: FIXED_TIME
        ).settle_pending_evidence(current_manifest.engagement_id, reason="plan")

    assert result.status == "settled"
    assert llm.subjects[0] == InterpretationSubject(
        attachment_event_id=second_attached.created_event_ids[0],
        evidence_id=second_evidence.evidence_id,
    )
    assert llm.subjects[1] == InterpretationSubject(
        attachment_event_id=first_attached.created_event_ids[0],
        evidence_id=first_evidence.evidence_id,
    )
    assert retryable.snapshot.revision.sequence < result.authoritative_journal_revision.sequence


def test_real_service_paginates_beyond_256_evidence_descriptors(tmp_path) -> None:
    """The 257th persisted attachment appears in the service's authoritative pending total."""
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            b"x",
            media_type="text/plain",
            representation="utf-8",
        )
        revision = created.snapshot.revision
        for _ in range(257):
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
                expected_revision=revision,
            )
            revision = attached.snapshot.revision
        llm = SubjectEchoLlm()
        result = PlanningService(
            journal=journal, llm=llm, clock=lambda: FIXED_TIME
        ).settle_pending_evidence(current_manifest.engagement_id, reason="plan")

    assert result.status == "incomplete"
    assert len(llm.subjects) == 64
    assert result.pending_total_count == 193
    assert len(result.pending_ranges) == 193


def test_large_text_resumes_at_first_uninterpreted_byte(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    content = b"a" * (32 * 1024) + b"z"
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            content,
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
        subject = InterpretationSubject(
            attachment_event_id=attached.created_event_ids[0],
            evidence_id=evidence.evidence_id,
        )
        llm = EmptyObservationLlm(subject)
        service = PlanningService(journal=journal, llm=llm, clock=lambda: FIXED_TIME)

        first = service.settle_pending_evidence(
            current_manifest.engagement_id,
            reason="plan",
        )
        second = service.settle_pending_evidence(
            current_manifest.engagement_id,
            reason="plan",
        )

    assert first.status == "settled"
    assert first.pending_ranges == ()
    assert second.status == "nothing_pending"
    assert len(llm.calls) == 2
    assert llm.calls[0][1]["payload"].evidence_slices[0].content == content[: 32 * 1024]
    assert llm.calls[1][1]["payload"].evidence_slices[0].start == 32 * 1024
    assert llm.calls[1][1]["payload"].evidence_slices[0].content == b"z"


def test_settlement_processes_exactly_64_32kib_slices_before_returning_incomplete(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    content = b"a" * (65 * 32 * 1024)
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            content,
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
        llm = EmptyObservationLlm(
            InterpretationSubject(
                attachment_event_id=attached.created_event_ids[0], evidence_id=evidence.evidence_id
            )
        )
        terminal = RecordingTerminalPort()
        service = PlanningService(
            journal=journal,
            llm=llm,
            terminal_settlement_port=terminal,
            clock=lambda: FIXED_TIME,
        )

        result = service.settle_pending_evidence(current_manifest.engagement_id, reason="plan")
        assert terminal.calls == []
        completed = service.settle_pending_evidence(
            current_manifest.engagement_id,
            reason="plan",
        )

    assert result.status == "incomplete"
    assert completed.status == "settled"
    assert len(completed.situation.interpretations[0].event_ids) == 65
    assert len(llm.calls) == 65
    assert tuple((item.start, item.end) for item in result.pending_ranges) == (
        (64 * 32 * 1024, 65 * 32 * 1024),
    )
    assert len(terminal.calls) == 1


def test_zero_byte_text_is_settled_without_an_synthetic_range(tmp_path) -> None:
    """A zero-byte descriptor is complete evidence, not an invalid positive slice."""
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            b"",
            media_type="application/json",
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
        subject = InterpretationSubject(
            attachment_event_id=attached.created_event_ids[0],
            evidence_id=evidence.evidence_id,
        )
        llm = EmptyObservationLlm(subject)
        service = PlanningService(journal=journal, llm=llm, clock=lambda: FIXED_TIME)

        result = service.settle_pending_evidence(current_manifest.engagement_id, reason="plan")
        repeated = service.settle_pending_evidence(current_manifest.engagement_id, reason="plan")
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    assert result.status == "settled"
    assert result.pending_ranges == ()
    assert llm.calls == []
    successes = [event for event in snapshot.events if event.type == "interpretation_succeeded"]
    assert len(successes) == 1
    assert successes[0].payload.covered_slices == ()
    assert repeated.status == "nothing_pending"
    assert (
        len([event for event in snapshot.events if event.type == "interpretation_succeeded"]) == 1
    )


def test_zero_byte_binary_receives_terminal_unsupported_media_assessment(tmp_path) -> None:
    """An empty binary still needs a reportable terminal interpretation outcome."""
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            b"",
            media_type="image/png",
            representation="binary",
        )
        journal.append_hook_events(
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
        service = PlanningService(journal=journal, llm=FailingLlm(), clock=lambda: FIXED_TIME)

        result = service.settle_pending_evidence(current_manifest.engagement_id, reason="plan")
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    assert result.status == "settled"
    failures = [event for event in snapshot.events if event.type == "interpretation_failed"]
    assert len(failures) == 1
    assert failures[0].payload.failure_code == "unsupported_media"
    assert failures[0].payload.attempted_slices == ()


def test_mixed_binary_and_text_settles_every_subject_before_reporting_settled(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        binary = journal.write_evidence(
            current_manifest.engagement_id,
            b"\x89PNG",
            media_type="image/png",
            representation="binary",
        )
        binary_attached = journal.append_hook_events(
            current_manifest.engagement_id,
            (
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type="evidence_attached",
                    payload=EvidenceAttachedPayload(evidence=binary),
                ),
            ),
            expected_revision=created.snapshot.revision,
        )
        text = journal.write_evidence(
            current_manifest.engagement_id,
            b"OpenSSH 9.6",
            media_type="text/plain",
            representation="utf-8",
        )
        text_attached = journal.append_hook_events(
            current_manifest.engagement_id,
            (
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type="evidence_attached",
                    payload=EvidenceAttachedPayload(evidence=text),
                ),
            ),
            expected_revision=binary_attached.snapshot.revision,
        )
        llm = SubjectEchoLlm()

        result = PlanningService(
            journal=journal,
            llm=llm,
            clock=lambda: FIXED_TIME,
        ).settle_pending_evidence(current_manifest.engagement_id, reason="plan")
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    assert result.status == "settled"
    assert llm.subjects == [
        InterpretationSubject(
            attachment_event_id=text_attached.created_event_ids[0],
            evidence_id=text.evidence_id,
        )
    ]
    assert len([event for event in snapshot.events if event.type == "interpretation_failed"]) == 1
    assert (
        len([event for event in snapshot.events if event.type == "interpretation_succeeded"]) == 1
    )


def test_settlement_drains_multiple_pending_subjects_before_reporting_settled(tmp_path) -> None:
    """The 64-slice budget is shared across all descriptors, not reserved for the first."""
    current_manifest = manifest()
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=lane())
        attachments = []
        revision = created.snapshot.revision
        for content in (b"first", b"second"):
            evidence = journal.write_evidence(
                current_manifest.engagement_id,
                content,
                media_type="text/plain",
                representation="utf-8",
            )
            appended = journal.append_hook_events(
                current_manifest.engagement_id,
                (
                    JournalEventDraft(
                        lane=lane(),
                        actor="host_agent",
                        type="evidence_attached",
                        payload=EvidenceAttachedPayload(evidence=evidence),
                    ),
                ),
                expected_revision=revision,
            )
            attachments.append((appended.created_event_ids[0], evidence))
            revision = appended.snapshot.revision

        class PerSubjectLlm:
            def __init__(self):
                self.calls = []

            def complete(self, model_type, **kwargs):
                subject_slice = kwargs["payload"].evidence_slices[0]
                self.calls.append(subject_slice.event_id)
                return SimpleNamespace(
                    parsed=ObservationBatchDraft(
                        subject=InterpretationSubject(
                            attachment_event_id=subject_slice.event_id,
                            evidence_id=subject_slice.evidence_id,
                        )
                    ),
                    provider="test",
                    model="test",
                    agent_id="test",
                    usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                )

        llm = PerSubjectLlm()
        result = PlanningService(
            journal=journal, llm=llm, clock=lambda: FIXED_TIME
        ).settle_pending_evidence(current_manifest.engagement_id, reason="plan")
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    assert result.status == "settled"
    assert llm.calls == [attachments[0][0], attachments[1][0]]
    assert (
        len([event for event in snapshot.events if event.type == "interpretation_succeeded"]) == 2
    )


def test_binary_settlement_chunks_more_than_one_planning_batch(tmp_path, monkeypatch) -> None:
    from sedna.planning.models import MAX_PLANNING_EVENT_BATCH

    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            b"x",
            media_type="image/png",
            representation="binary",
        )
        revision = created.snapshot.revision
        for _ in range(MAX_PLANNING_EVENT_BATCH + 1):
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
                expected_revision=revision,
            )
            revision = attached.snapshot.revision
        capability = journal._issue_planning_event_commit_capability()
        original_commit = capability.commit_planning_events
        batch_sizes: list[int] = []

        def bounded_commit(engagement_id, items, **kwargs):
            batch_sizes.append(len(items))
            return original_commit(engagement_id, items, **kwargs)

        monkeypatch.setattr(capability, "commit_planning_events", bounded_commit)
        monkeypatch.setattr(
            journal,
            "_issue_planning_event_commit_capability",
            lambda: capability,
        )

        result = PlanningService(
            journal=journal,
            llm=FailingLlm(),
            clock=lambda: FIXED_TIME,
        ).settle_pending_evidence(current_manifest.engagement_id, reason="plan")

    assert result.status == "settled"
    assert batch_sizes == [MAX_PLANNING_EVENT_BATCH, 1]


def test_identical_evidence_on_distinct_attachments_remains_distinct_when_unsupported(
    tmp_path,
) -> None:
    """Content addressing may deduplicate bytes, never attachment interpretation subjects."""
    current_manifest = manifest()
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=lane())
        evidence = journal.write_evidence(
            current_manifest.engagement_id,
            b"\x89PNG",
            media_type="image/png",
            representation="binary",
        )
        revision = created.snapshot.revision
        attachment_ids = []
        for _ in range(2):
            appended = journal.append_hook_events(
                current_manifest.engagement_id,
                (
                    JournalEventDraft(
                        lane=lane(),
                        actor="host_agent",
                        type="evidence_attached",
                        payload=EvidenceAttachedPayload(evidence=evidence),
                    ),
                ),
                expected_revision=revision,
            )
            attachment_ids.append(appended.created_event_ids[0])
            revision = appended.snapshot.revision
        result = PlanningService(
            journal=journal, llm=FailingLlm(), clock=lambda: FIXED_TIME
        ).settle_pending_evidence(current_manifest.engagement_id, reason="plan")
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    failures = [event.payload for event in snapshot.events if event.type == "interpretation_failed"]
    assert result.status == "settled"
    assert {item.attachment_event_id for item in failures} == set(attachment_ids)
    assert len(result.situation.interpretations) == 2


def test_state_projection_is_rebuilt_byte_identically_from_events_only(tmp_path) -> None:
    current_manifest = manifest()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=lane())
        service = PlanningService(journal=journal, llm=FailingLlm(), clock=lambda: FIXED_TIME)
        first = service.load_situation(current_manifest.engagement_id)
        state_path = (
            tmp_path
            / "knowledge"
            / "engagements"
            / str(current_manifest.engagement_id)
            / "state.json"
        )
        canonical = state_path.read_bytes()
        state_path.unlink()
        rebuilt = service.load_situation(current_manifest.engagement_id)

    assert rebuilt == first
    assert state_path.read_bytes() == canonical


def test_pending_inventory_pages_513_subjects_with_true_total_digest_and_opaque_cursor() -> None:
    """The 512-record result page never truncates the authoritative inventory."""
    from hashlib import sha256

    descriptors = tuple(
        SimpleNamespace(
            attachment_event_id=UUID(f"00000000-0000-4000-8000-{index:012d}"),
            reference=SimpleNamespace(
                evidence_id="evidence-sha256-" + sha256(f"evidence:{index}".encode()).hexdigest(),
                size=1,
                media_type="text/plain",
            ),
        )
        for index in range(1, 514)
    )
    service = PlanningService(journal=SimpleNamespace(), llm=FailingLlm(), clock=lambda: FIXED_TIME)
    service._all_evidence_descriptors = lambda engagement_id, revision: descriptors

    page, total, digest, cursor = service._pending_inventory(
        UUID("22222222-2222-4222-8222-222222222222"),
        SimpleNamespace(events=(), revision=SimpleNamespace(sequence=0)),
    )

    assert len(page) == 512
    assert total == 513
    assert len(digest) == 64
    assert cursor is not None and cursor.startswith("pending-") and len(cursor) == 72
    assert all(str(item.attachment_event_id) not in cursor for item in page)


def test_empty_manifest_never_invokes_terminal_port(tmp_path) -> None:
    current_manifest = manifest().model_copy(update={"required_proofs": ()})
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=lane())
        terminal = RecordingTerminalPort()
        result = PlanningService(
            journal=journal,
            llm=FailingLlm(),
            terminal_settlement_port=terminal,
            clock=lambda: FIXED_TIME,
        ).settle_pending_evidence(current_manifest.engagement_id, reason="plan")

    assert result.status == "nothing_pending"
    assert terminal.calls == []


def test_plan_next_returns_ephemeral_terminal_gap_before_planner_calls(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        closing = journal.request_close(
            current_manifest.engagement_id,
            lane=current_lane,
            reason="manual close",
            expected_revision=created.snapshot.revision,
        )
        service = PlanningService(journal=journal, llm=FailingLlm(), clock=lambda: FIXED_TIME)

        result = service.plan_next(current_lane)
        after = journal.load_snapshot(current_manifest.engagement_id)

    assert result.status == "gap"
    assert result.gap is not None and result.gap.code == "engagement_terminal"
    assert result.current_authoritative_journal_revision == closing.snapshot.revision
    assert after.revision == closing.snapshot.revision


def test_plan_next_maps_incomplete_settlement_to_retryable_typed_gap(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        service = PlanningService(journal=journal, llm=FailingLlm(), clock=lambda: FIXED_TIME)
        snapshot = journal.load_snapshot(current_manifest.engagement_id)
        situation = service.load_situation(current_manifest.engagement_id)
        service.settle_pending_evidence = lambda engagement_id, reason: IncompleteSettlementResult(
            engagement_id=engagement_id,
            reason=reason,
            authoritative_journal_revision=snapshot.revision,
            situation=situation,
            required_proof_ids=("root-flag", "user-flag"),
            pending_ranges=(
                PendingEvidenceRange(
                    evidence_id="evidence-sha256-" + "a" * 64,
                    attachment_event_id=UUID("00000000-0000-4000-8000-000000000042"),
                    start=0,
                    end=1,
                    media_type="text/plain",
                    reason="budget_exhausted",
                ),
            ),
            pending_total_count=1,
            pending_inventory_sha256="b" * 64,
            incomplete_reason="budget_exhausted",
            all_required_proofs_satisfied=False,
            possible_terminal_evidence=False,
        )

        result = service.plan_next(current_lane)

    assert result.status == "gap"
    assert result.gap is not None
    assert result.gap.code == "settlement_incomplete"
    assert result.gap.retryable is True
    assert len(result.gap.pending_ranges) == 1


class AcceptedPlannerLlm:
    def __init__(self) -> None:
        self.purposes = []
        self.payloads = []

    def complete(self, model_type, **kwargs):
        self.purposes.append(kwargs["purpose"])
        self.payloads.append(kwargs["payload"])
        if model_type is PlannerDraft:
            parsed = PlannerDraft(
                proposals=tuple(
                    FrontierProposalDraft(
                        family_runtime_key=f"family-{index}",
                        variant_runtime_key=f"variant-{index}",
                        title=f"Proposal {index}",
                        score=100 - index,
                        confidence=80,
                        rationale="Collect discriminating evidence.",
                    )
                    for index in range(1, 4)
                )
            )
        else:
            parsed = PlannerCriticVerdict(accepted=True)
        return SimpleNamespace(
            parsed=parsed,
            provider="test-provider",
            model="test-model",
            agent_id="test-agent",
            usage=SimpleNamespace(input_tokens=7, output_tokens=3),
        )


class TerminalPlannerLlm(AcceptedPlannerLlm):
    def complete(self, model_type, **kwargs):
        if model_type is not PlannerDraft:
            return super().complete(model_type, **kwargs)
        self.purposes.append(kwargs["purpose"])
        self.payloads.append(kwargs["payload"])
        cited_event_id = kwargs["payload"].recent_event_ids[0]
        parsed = PlannerDraft(
            proposals=(
                FrontierProposalDraft(
                    family_runtime_key="family-alternate-2",
                    variant_runtime_key="variant-alternate-2",
                    title="Alternate proposal 2",
                    score=80,
                    confidence=75,
                    rationale="Preserve an independent alternative.",
                ),
                FrontierProposalDraft(
                    family_runtime_key="family-alternate-3",
                    variant_runtime_key="variant-alternate-3",
                    title="Alternate proposal 3",
                    score=70,
                    confidence=65,
                    rationale="Preserve a second independent alternative.",
                ),
                FrontierProposalDraft(
                    family_runtime_key="family-terminal",
                    variant_runtime_key="variant-terminal",
                    title="Terminal proposal",
                    score=60,
                    confidence=90,
                    rationale="The bounded path is incompatible.",
                    status=StrategyStatus.BLOCKED,
                    retry_predicates=(
                        RetryPredicate(
                            kind=RetryPredicateKind.STATE_REVISION_AFTER,
                            value="new-state",
                        ),
                    ),
                    event_refs=(cited_event_id,),
                ),
            )
        )
        return SimpleNamespace(
            parsed=parsed,
            provider="test-provider",
            model="test-model",
            agent_id="test-agent",
            usage=SimpleNamespace(input_tokens=7, output_tokens=3),
        )


class UngroundedZeroScorePlannerLlm(TerminalPlannerLlm):
    def complete(self, model_type, **kwargs):
        completion = super().complete(model_type, **kwargs)
        if model_type is not PlannerDraft:
            return completion
        terminal = completion.parsed.proposals[-1].model_copy(
            update={"score": 0, "terminal_reason": "incompatibility"}
        )
        return SimpleNamespace(
            **{
                **completion.__dict__,
                "parsed": completion.parsed.model_copy(
                    update={"proposals": (*completion.parsed.proposals[:-1], terminal)}
                ),
            }
        )


class GroundedCommandPlannerLlm(AcceptedPlannerLlm):
    def complete(self, model_type, **kwargs):
        if model_type is not PlannerDraft:
            return super().complete(model_type, **kwargs)
        self.purposes.append(kwargs["purpose"])
        self.payloads.append(kwargs["payload"])
        request = kwargs["payload"]
        scope = request.scope_references[0]
        parsed = PlannerDraft(
            proposals=(
                FrontierProposalDraft(
                    family_runtime_key="family-grounded",
                    variant_runtime_key="variant-grounded",
                    title="Probe the authorized target",
                    score=91,
                    confidence=84,
                    rationale="The observed service merits a bounded probe.",
                    strategic_intent="Collect discriminating service evidence.",
                    prerequisites=("The target remains in scope.",),
                    expected_information_gain="Resolve the service implementation.",
                    expected_evidence=("A service banner is captured.",),
                    stop_conditions=("Stop after one bounded request.",),
                    event_refs=(request.recent_event_ids[0],),
                    scope_reference_ids=(scope.reference_id,),
                    commands=(
                        CommandSuggestionDraft(
                            origin=CommandOrigin.MODEL_GENERATED,
                            command_template="probe {{target}}",
                            placeholder_kinds=(PlaceholderKind.TARGET,),
                            bindings=(
                                CommandBinding(
                                    placeholder_name="target",
                                    source="scope_reference",
                                    reference_id=scope.reference_id,
                                ),
                            ),
                            capability_hint="service-probe",
                            purpose="Collect the service banner.",
                            validation_note="Run once against the authorized target.",
                        ),
                    ),
                ),
                FrontierProposalDraft(
                    family_runtime_key="family-alternate-2",
                    variant_runtime_key="variant-alternate-2",
                    title="Alternate proposal 2",
                    score=80,
                    confidence=75,
                    rationale="Preserve an independent alternative.",
                ),
                FrontierProposalDraft(
                    family_runtime_key="family-alternate-3",
                    variant_runtime_key="variant-alternate-3",
                    title="Alternate proposal 3",
                    score=70,
                    confidence=65,
                    rationale="Preserve a second independent alternative.",
                ),
            ),
            research_queries=(
                "OpenSSH 9.2 protocol CVE details",
                "HTB-Orion root.txt walkthrough",
            ),
        )
        return SimpleNamespace(
            parsed=parsed,
            provider="test-provider",
            model="test-model",
            agent_id="test-agent",
            usage=SimpleNamespace(input_tokens=7, output_tokens=3),
        )


def test_plan_next_persists_grounded_proposal_and_exact_command_record(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        result = PlanningService(
            journal=journal,
            llm=GroundedCommandPlannerLlm(),
            clock=lambda: FIXED_TIME,
            research_aliases=("HTB-Orion", "Orion"),
        ).plan_next(current_lane, max_proposals=3)
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    assert result.status == "success"
    proposed = next(event.payload for event in snapshot.events if event.type == "frontier_proposed")
    record = proposed.proposal
    assert record.strategic_intent == "Collect discriminating service evidence."
    assert record.prerequisites == ("The target remains in scope.",)
    assert record.expected_evidence == ("A service banner is captured.",)
    assert record.stop_conditions == ("Stop after one bounded request.",)
    assert record.scope_reference_ids
    assert len(record.commands) == 1
    command = record.commands[0]
    assert command.capability_hint == "service-probe"
    assert command.command_template == "probe {{target}}"
    assert command.placeholders[0].binding_policy == "authorized_scope"
    assert command.bindings[0].reference_id == record.scope_reference_ids[0]
    assert command.rendered_preview != command.command_template
    research = [
        event.payload for event in snapshot.events if event.type == "research_query_proposed"
    ]
    assert [item.policy_decision for item in research] == ["allowed", "rejected"]
    assert research[1].reason_codes == ("current_machine_solution",)


def test_plan_next_routes_accepted_attempt_through_event_converter(tmp_path, monkeypatch) -> None:
    from sedna.planning import service as service_module

    converted = []
    original = service_module.payloads_from_planning_attempt

    def recording_converter(conversion):
        converted.append(conversion)
        return original(conversion)

    monkeypatch.setattr(service_module, "payloads_from_planning_attempt", recording_converter)
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        result = PlanningService(
            journal=journal,
            llm=AcceptedPlannerLlm(),
            clock=lambda: FIXED_TIME,
        ).plan_next(current_lane, max_proposals=3)

    assert result.status == "success"
    assert converted
    assert any(conversion.plan_request_audit is not None for conversion in converted)
    assert sum(len(conversion.planner_proposals) for conversion in converted) == 3
    assert sum(len(conversion.critic_verdicts) for conversion in converted) == 1


def test_archive_cas_conflict_does_not_publish_frontier_events(tmp_path, monkeypatch) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        service = PlanningService(
            journal=journal,
            llm=AcceptedPlannerLlm(),
            clock=lambda: FIXED_TIME,
        )

        def reject_archive(*args, **kwargs):
            from sedna.engagement.repository import RevisionConflictError

            raise RevisionConflictError("strategy archive revision is stale")

        monkeypatch.setattr(journal, "commit_strategy_archive", reject_archive)
        result = service.plan_next(current_lane, max_proposals=3)
        after = journal.load_snapshot(current_manifest.engagement_id)

    assert result.status == "gap"
    assert result.gap is not None and result.gap.code == "concurrent_state_change"
    assert after.revision == created.snapshot.revision
    assert not any(event.type.startswith("frontier_") for event in after.events)


def test_planning_append_failure_does_not_advance_prepared_cold_archive(
    tmp_path, monkeypatch
) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        service = PlanningService(
            journal=journal,
            llm=TerminalPlannerLlm(),
            clock=lambda: FIXED_TIME,
        )
        monkeypatch.setattr(
            service,
            "_commit_planning_events",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("append failed")),
        )

        with pytest.raises(OSError, match="append failed"):
            service.plan_next(current_lane, max_proposals=3)
        after = journal.load_snapshot(current_manifest.engagement_id)
        archive = journal.load_strategy_archive(current_manifest.engagement_id)

    assert after.revision == created.snapshot.revision
    assert archive is None


def test_planning_append_commit_then_raise_preserves_authoritative_cold_archive(
    tmp_path, monkeypatch
) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        service = PlanningService(
            journal=journal,
            llm=TerminalPlannerLlm(),
            clock=lambda: FIXED_TIME,
        )
        fired = False

        def fail_after_authoritative_append(point: str) -> None:
            nonlocal fired
            if point == "append_before_response" and not fired:
                fired = True
                raise OSError("append response failed")

        monkeypatch.setattr(journal._repository, "_fault", fail_after_authoritative_append)

        with pytest.raises(Exception) as raised:
            service.plan_next(current_lane, max_proposals=3)
        after = journal.load_snapshot(current_manifest.engagement_id)
        archive = journal.load_strategy_archive(current_manifest.engagement_id)

    archived_events = [event for event in after.events if event.type == "strategy_archived"]
    assert fired
    assert isinstance(raised.value.__cause__, OSError)
    assert str(raised.value.__cause__) == "append response failed"
    assert len(archived_events) == 1
    assert archive is not None and len(archive.records) == 1
    assert archive.records[0].payload == archived_events[0].payload.archive_record.model_dump(
        mode="json", warnings="error"
    )


def test_concurrent_journal_append_does_not_prevent_archive_compensation(
    tmp_path, monkeypatch
) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        service = PlanningService(
            journal=journal,
            llm=TerminalPlannerLlm(),
            clock=lambda: FIXED_TIME,
        )

        def append_concurrently_then_fail(*args, **kwargs):
            journal.append_hook_events(
                current_manifest.engagement_id,
                (
                    JournalEventDraft(
                        lane=current_lane,
                        actor="host_agent",
                        type="session_checkpointed",
                        payload=SessionCheckpointedPayload(
                            completed=False,
                            interrupted=False,
                            reason="concurrent checkpoint",
                        ),
                    ),
                ),
                expected_revision=created.snapshot.revision,
            )
            raise OSError("append failed")

        monkeypatch.setattr(service, "_commit_planning_events", append_concurrently_then_fail)
        with pytest.raises(OSError, match="append failed"):
            service.plan_next(current_lane, max_proposals=3)
        after = journal.load_snapshot(current_manifest.engagement_id)
        archive = journal.load_strategy_archive(current_manifest.engagement_id)

    assert after.revision.sequence == created.snapshot.revision.sequence + 1
    assert archive is None
    assert not any(event.type == "strategy_archived" for event in after.events)


def test_plan_next_rejects_zero_score_terminal_without_authoritative_grounding(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        service = PlanningService(
            journal=journal,
            llm=UngroundedZeroScorePlannerLlm(),
            clock=lambda: FIXED_TIME,
        )

        with pytest.raises(ValueError, match="zero_score_terminal_not_grounded"):
            service.plan_next(current_lane, max_proposals=3)
        after = journal.load_snapshot(current_manifest.engagement_id)

    assert after.revision == created.snapshot.revision


def test_plan_next_reactivates_typed_retry_and_removes_cold_record(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        first = PlanningService(
            journal=journal,
            llm=TerminalPlannerLlm(),
            clock=lambda: FIXED_TIME,
        ).plan_next(current_lane, max_proposals=3)
        assert first.status == "success"
        archived = journal.load_strategy_archive(current_manifest.engagement_id)
        assert archived is not None and len(archived.records) == 1
        archived_record = ArchivedStrategyEventRecord.model_validate(archived.records[0].payload)
        minimum_revision = archived_record.retry_predicates[0].minimum_material_revision
        assert minimum_revision is not None
        settled = PlanningService(
            journal=journal,
            llm=FailingLlm(),
            clock=lambda: FIXED_TIME,
        ).settle_pending_evidence(current_manifest.engagement_id, reason="plan")
        assert settled.situation is not None
        situation = SituationProjection.model_validate(
            {
                **settled.situation.model_dump(mode="python"),
                "material_event_revision": minimum_revision.sequence + 1,
            }
        )
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            "sedna.planning.service.SituationReducer.rebuild", lambda snapshot: situation
        )
        advanced = journal.append_hook_events(
            current_manifest.engagement_id,
            (
                JournalEventDraft(
                    lane=current_lane,
                    actor="host_agent",
                    type="session_checkpointed",
                    payload=SessionCheckpointedPayload(
                        completed=False,
                        interrupted=False,
                        reason="material state advanced",
                    ),
                ),
            ),
            expected_revision=first.current_authoritative_journal_revision,
        )

        second = PlanningService(
            journal=journal,
            llm=AcceptedPlannerLlm(),
            clock=lambda: FIXED_TIME,
        ).plan_next(current_lane, max_proposals=3)
        snapshot = journal.load_snapshot(current_manifest.engagement_id)
        from sedna.planning.ledger import StrategyLedgerReducer

        replay = StrategyLedgerReducer.rebuild_state(snapshot)
        cold = journal.load_strategy_archive(current_manifest.engagement_id)
        monkeypatch.undo()

    assert second.status == "success"
    assert (
        advanced.snapshot.revision.sequence < second.current_authoritative_journal_revision.sequence
    )
    assert any(event.type == "strategy_reactivated" for event in snapshot.events)
    reactivated = next(
        event.payload for event in snapshot.events if event.type == "strategy_reactivated"
    )
    assert reactivated.restored_snapshot.status == "available"
    assert replay.archive_records == ()
    assert cold is not None and cold.records == ()


def test_plan_next_rejects_unreturnable_result_before_any_planning_event(
    tmp_path, monkeypatch
) -> None:
    from sedna.planning import service as service_module

    monkeypatch.setattr(service_module, "MAX_PLANNING_RESULT_BYTES", 1)
    current_manifest = manifest()
    current_lane = lane()
    llm = AcceptedPlannerLlm()
    with journal_service(tmp_path) as journal:
        created = journal.create_from_manifest(current_manifest, lane=current_lane)
        result = PlanningService(
            journal=journal,
            llm=llm,
            clock=lambda: FIXED_TIME,
        ).plan_next(current_lane, max_proposals=3)
        after = journal.load_snapshot(current_manifest.engagement_id)

    assert result.status == "failed"
    assert result.failure_code == "result_too_large"
    assert after.revision == created.snapshot.revision
    assert llm.purposes == [
        "sedna.planning.plan",
        "sedna.planning.critic",
        "sedna.planning.repair",
        "sedna.planning.critic",
    ]


def test_plan_next_accepts_planner_then_critic_and_reuses_composite_cache(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    llm = AcceptedPlannerLlm()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        service = PlanningService(journal=journal, llm=llm, clock=lambda: FIXED_TIME)
        result = service.plan_next(current_lane, max_proposals=3)
        cached = service.plan_next(current_lane, max_proposals=3)
        snapshot = journal.load_snapshot(current_manifest.engagement_id)
        frontier_path = (
            tmp_path
            / "knowledge"
            / "engagements"
            / str(current_manifest.engagement_id)
            / "frontier.json"
        )
        canonical = frontier_path.read_bytes()
        frontier_path.unlink()
        replayed = FrontierReducer.rebuild(snapshot)
        journal.commit_projection(
            current_manifest.engagement_id,
            "frontier",
            replayed,
            expected_revision=snapshot.revision,
        )

    assert result.status == "success"
    assert cached.frontier == result.frontier
    assert llm.purposes == ["sedna.planning.plan", "sedna.planning.critic"]
    planner_request = llm.payloads[0]
    assert planner_request.max_proposals == 3
    assert planner_request.scope_references
    assert planner_request.recent_event_ids
    assert (
        planner_request.knowledge_context.situation_digest == planner_request.situation.state_digest
    )
    assert planner_request.knowledge_context.context_digest != "0" * 64
    assert replayed == result.frontier
    assert frontier_path.read_bytes() == canonical
    assert [event.type for event in snapshot.events[-11:-6]] == [
        "plan_requested",
        "frontier_proposed",
        "frontier_proposed",
        "frontier_proposed",
        "frontier_criticized",
    ]
    assert [event.type for event in snapshot.events[-6:]] == ["strategy_reconciled"] * 6
    proposed = next(event.payload for event in snapshot.events if event.type == "frontier_proposed")
    assert proposed.call_metadata.provider == "test-provider"
    assert proposed.call_metadata.model == "test-model"
    assert proposed.call_metadata.agent_id == "test-agent"
    assert proposed.call_metadata.input_tokens == 7
    assert proposed.call_metadata.output_tokens == 3


def test_frontier_replay_rejects_accepted_batch_without_plan_request(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        PlanningService(
            journal=journal,
            llm=AcceptedPlannerLlm(),
            clock=lambda: FIXED_TIME,
        ).plan_next(current_lane, max_proposals=3)
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    without_request = SimpleNamespace(
        engagement_id=snapshot.engagement_id,
        revision=snapshot.revision,
        events=tuple(event for event in snapshot.events if event.type != "plan_requested"),
    )
    with pytest.raises(ValueError, match="plan request"):
        FrontierReducer.rebuild(without_request)


def test_frontier_replay_rejects_duplicate_plan_request(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        PlanningService(
            journal=journal,
            llm=AcceptedPlannerLlm(),
            clock=lambda: FIXED_TIME,
        ).plan_next(current_lane, max_proposals=3)
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    request = next(event for event in snapshot.events if event.type == "plan_requested")
    duplicated = SimpleNamespace(
        engagement_id=snapshot.engagement_id,
        revision=snapshot.revision,
        events=(*snapshot.events, request),
    )
    with pytest.raises(ValueError, match="exactly one plan request"):
        FrontierReducer.rebuild(duplicated)


def test_frontier_replay_rejects_proposal_appended_after_accepted_critic(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        PlanningService(
            journal=journal,
            llm=AcceptedPlannerLlm(),
            clock=lambda: FIXED_TIME,
        ).plan_next(current_lane, max_proposals=3)
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    proposed = next(event for event in snapshot.events if event.type == "frontier_proposed")
    reordered = SimpleNamespace(
        engagement_id=snapshot.engagement_id,
        revision=snapshot.revision,
        events=tuple(event for event in snapshot.events if event is not proposed) + (proposed,),
    )
    with pytest.raises(ValueError, match="sequence"):
        FrontierReducer.rebuild(reordered)


def test_frontier_replay_rejects_reconciliation_before_accepted_critic(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        PlanningService(
            journal=journal,
            llm=AcceptedPlannerLlm(),
            clock=lambda: FIXED_TIME,
        ).plan_next(current_lane, max_proposals=3)
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    reconciliation = tuple(
        event for event in snapshot.events if event.type == "strategy_reconciled"
    )
    accepted_critic = next(
        event
        for event in snapshot.events
        if event.type == "frontier_criticized" and event.payload.accepted
    )
    without_reconciliation = tuple(
        event for event in snapshot.events if event.type != "strategy_reconciled"
    )
    critic_index = without_reconciliation.index(accepted_critic)
    reordered = SimpleNamespace(
        engagement_id=snapshot.engagement_id,
        revision=snapshot.revision,
        events=(
            *without_reconciliation[:critic_index],
            *reconciliation,
            *without_reconciliation[critic_index:],
        ),
    )

    with pytest.raises(ValueError, match="sequence"):
        FrontierReducer.rebuild(reordered)


def test_frontier_replay_rejects_attempt_without_atomic_operation_identity(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        PlanningService(
            journal=journal,
            llm=AcceptedPlannerLlm(),
            clock=lambda: FIXED_TIME,
        ).plan_next(current_lane, max_proposals=3)
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    uncorrelated = SimpleNamespace(
        engagement_id=snapshot.engagement_id,
        revision=snapshot.revision,
        events=tuple(
            SimpleNamespace(
                sequence=event.sequence,
                event_id=event.event_id,
                type=event.type,
                payload=event.payload,
                system_correlation=None,
            )
            if event.type.startswith("frontier_") or event.type == "strategy_reconciled"
            else event
            for event in snapshot.events
        ),
    )
    with pytest.raises(ValueError, match="atomic operation identity"):
        FrontierReducer.rebuild(uncorrelated)


def test_plan_next_cache_varies_with_canonical_and_source_revisions(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    revisions = {"canonical": "1" * 64, "sources": "2" * 64}
    llm = AcceptedPlannerLlm()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        service = PlanningService(
            journal=journal,
            llm=llm,
            clock=lambda: FIXED_TIME,
            canonical_revision=lambda: revisions["canonical"],
            source_registry_digest=lambda: revisions["sources"],
        )
        service.plan_next(current_lane, max_proposals=3)
        service.plan_next(current_lane, max_proposals=3)
        revisions["canonical"] = "3" * 64
        service.plan_next(current_lane, max_proposals=3)
        revisions["sources"] = "4" * 64
        service.plan_next(current_lane, max_proposals=3)

    assert llm.purposes.count("sedna.planning.plan") == 3


class RepairedPlannerLlm(AcceptedPlannerLlm):
    def __init__(self) -> None:
        super().__init__()
        self.critic_pass = 0

    def complete(self, model_type, **kwargs):
        if model_type is PlannerCriticVerdict:
            self.purposes.append(kwargs["purpose"])
            self.critic_pass += 1
            parsed = (
                PlannerCriticVerdict(
                    accepted=False,
                    findings=(
                        PlannerFinding(
                            code="weak_grounding", summary="Grounding needs repair.", material=True
                        ),
                    ),
                )
                if self.critic_pass == 1
                else PlannerCriticVerdict(accepted=True)
            )
            return SimpleNamespace(
                parsed=parsed,
                provider="test-provider",
                model="test-model",
                agent_id="test-agent",
                usage=SimpleNamespace(input_tokens=7, output_tokens=3),
            )
        return super().complete(model_type, **kwargs)


class TwiceRejectedPlannerLlm(RepairedPlannerLlm):
    def complete(self, model_type, **kwargs):
        if model_type is PlannerCriticVerdict:
            self.purposes.append(kwargs["purpose"])
            return SimpleNamespace(
                parsed=PlannerCriticVerdict(
                    accepted=False,
                    findings=(
                        PlannerFinding(
                            code="unsafe_output", summary="Still unsafe.", material=True
                        ),
                    ),
                ),
                provider="test-provider",
                model="test-model",
                agent_id="test-agent",
                usage=SimpleNamespace(input_tokens=7, output_tokens=3),
            )
        return super().complete(model_type, **kwargs)


class ConcurrentPlannerLlm(AcceptedPlannerLlm):
    def __init__(self, journal, engagement_id, current_lane, *, changes: int) -> None:
        super().__init__()
        self._journal = journal
        self._engagement_id = engagement_id
        self._lane = current_lane
        self._remaining = changes

    def complete(self, model_type, **kwargs):
        if model_type is PlannerDraft and self._remaining:
            snapshot = self._journal.load_snapshot(self._engagement_id)
            self._journal.append_hook_events(
                self._engagement_id,
                (
                    JournalEventDraft(
                        lane=self._lane,
                        actor="host_agent",
                        type="session_checkpointed",
                        payload=SessionCheckpointedPayload(
                            completed=False,
                            interrupted=False,
                            reason="concurrent planner checkpoint",
                        ),
                    ),
                ),
                expected_revision=snapshot.revision,
            )
            self._remaining -= 1
        return super().complete(model_type, **kwargs)


def test_plan_next_performs_at_most_one_repair_then_final_critic(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    llm = RepairedPlannerLlm()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        result = PlanningService(journal=journal, llm=llm, clock=lambda: FIXED_TIME).plan_next(
            current_lane, max_proposals=3
        )
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    assert result.status == "success"
    assert FrontierReducer.rebuild(snapshot) == result.frontier
    attempt_types = [event.type for event in snapshot.events if event.type != "strategy_reconciled"]
    assert attempt_types[-9:] == [
        "plan_requested",
        "frontier_proposed",
        "frontier_proposed",
        "frontier_proposed",
        "frontier_criticized",
        "frontier_repaired",
        "frontier_repaired",
        "frontier_repaired",
        "frontier_criticized",
    ]
    assert [event.type for event in snapshot.events[-6:]] == ["strategy_reconciled"] * 6
    assert llm.purposes == [
        "sedna.planning.plan",
        "sedna.planning.critic",
        "sedna.planning.repair",
        "sedna.planning.critic",
    ]


def test_frontier_replay_rejects_repair_without_rejected_first_critic(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        PlanningService(
            journal=journal,
            llm=RepairedPlannerLlm(),
            clock=lambda: FIXED_TIME,
        ).plan_next(current_lane, max_proposals=3)
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    without_first_critic = SimpleNamespace(
        engagement_id=snapshot.engagement_id,
        revision=snapshot.revision,
        events=tuple(
            event
            for event in snapshot.events
            if not (event.type == "frontier_criticized" and not event.payload.accepted)
        ),
    )
    with pytest.raises(ValueError, match="rejected first critic"):
        FrontierReducer.rebuild(without_first_critic)


@pytest.mark.parametrize(
    ("changes", "expected_status", "planner_calls"),
    ((1, "success", 2), (2, "gap", 2)),
)
def test_plan_next_restarts_once_after_concurrent_state_change(
    tmp_path, changes, expected_status, planner_calls
) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        llm = ConcurrentPlannerLlm(
            journal,
            current_manifest.engagement_id,
            current_lane,
            changes=changes,
        )
        result = PlanningService(journal=journal, llm=llm, clock=lambda: FIXED_TIME).plan_next(
            current_lane, max_proposals=3
        )
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    assert result.status == expected_status
    assert llm.purposes.count("sedna.planning.plan") == planner_calls
    if expected_status == "gap":
        assert result.gap is not None and result.gap.code == "concurrent_state_change"
        assert not any(event.type.startswith("frontier_") for event in snapshot.events)


@pytest.mark.parametrize("changing_revision", ("canonical", "sources"))
def test_plan_next_rejects_revision_change_before_commit(tmp_path, changing_revision) -> None:
    current_manifest = manifest()
    current_lane = lane()
    calls = {"canonical": 0, "sources": 0}

    def revision(name: str) -> str:
        calls[name] += 1
        generation = calls[name] if name == changing_revision else 1
        return f"{generation:064x}"

    llm = AcceptedPlannerLlm()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        result = PlanningService(
            journal=journal,
            llm=llm,
            clock=lambda: FIXED_TIME,
            canonical_revision=lambda: revision("canonical"),
            source_registry_digest=lambda: revision("sources"),
        ).plan_next(current_lane, max_proposals=3)
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    assert result.status == "gap"
    assert result.gap is not None and result.gap.code == "concurrent_state_change"
    assert llm.purposes.count("sedna.planning.plan") == 2
    assert not any(event.type.startswith("frontier_") for event in snapshot.events)


def test_second_critic_rejection_returns_gap_without_frontier_publication(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    llm = TwiceRejectedPlannerLlm()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        result = PlanningService(journal=journal, llm=llm, clock=lambda: FIXED_TIME).plan_next(
            current_lane, max_proposals=3
        )
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    assert result.status == "gap"
    assert result.gap is not None and result.gap.code == "critic_rejected"
    assert not any(
        event.type in {"frontier_proposed", "frontier_repaired"} for event in snapshot.events
    )


def test_second_critic_rejection_persists_closed_audit_without_frontier(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        result = PlanningService(
            journal=journal,
            llm=TwiceRejectedPlannerLlm(),
            clock=lambda: FIXED_TIME,
        ).plan_next(current_lane, max_proposals=3)
        snapshot = journal.load_snapshot(current_manifest.engagement_id)

    assert result.status == "gap"
    assert [
        event.type
        for event in snapshot.events
        if event.type in {"frontier_rejected", "planning_gap_recorded"}
    ] == ["frontier_rejected", "planning_gap_recorded"]
    assert FrontierReducer.rebuild(snapshot) is None


def test_second_critic_rejection_returns_the_prior_frontier_marked_stale(tmp_path) -> None:
    current_manifest = manifest()
    current_lane = lane()
    with journal_service(tmp_path) as journal:
        journal.create_from_manifest(current_manifest, lane=current_lane)
        service = PlanningService(
            journal=journal,
            llm=AcceptedPlannerLlm(),
            clock=lambda: FIXED_TIME,
        )
        accepted = service.plan_next(current_lane, max_proposals=3)
        service._llm = TwiceRejectedPlannerLlm()
        service._canonical_revision = lambda: "3" * 64

        rejected = service.plan_next(current_lane, max_proposals=3)

    assert accepted.frontier is not None
    assert rejected.status == "gap" and rejected.gap is not None
    assert rejected.gap.stale_frontier is not None
    assert rejected.gap.stale_frontier.stale is True
    assert rejected.gap.stale_frontier.model_copy(update={"stale": False}) == accepted.frontier
