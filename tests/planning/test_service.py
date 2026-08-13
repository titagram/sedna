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
)
from sedna.engagement.events import EvidenceSliceEventRef, InterpretationFailedEventPayload
from sedna.engagement.service import PlanningEventCommitItem
from sedna.knowledge.retrieval import AuthorizationScope, AuthorizationState, ValidatedTarget
from sedna.planning.models import (
    InterpretationSubject,
    ObservationBatchDraft,
    SettlementResultAdapter,
    SituationProjection,
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
            llm=InvalidSubjectLlm() if fault == "invalid_output" else EmptyObservationLlm(
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
    assert len(
        [event for event in snapshot.events if event.type == "interpretation_succeeded"]
    ) == 1


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
        len([event for event in snapshot.events if event.type == "interpretation_succeeded"])
        == 1
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
