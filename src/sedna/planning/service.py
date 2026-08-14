"""Planning service for deterministic situation loading and evidence settlement."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime
from hashlib import sha256
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from sedna.engagement import (
    EngagementJournalService,
    EngagementStatus,
    ExecutionLaneKey,
    InterpretationFailedEventPayload,
    InterpretationSucceededEventPayload,
    JournalRevision,
    RevisionConflictError,
    SettlementReason,
    StrategyArchiveRecordDraft,
)
from sedna.engagement.events import (
    ArchivedStrategyEventRecord,
    AttemptAggregateEventRecord,
    CommandBindingEventRecord,
    CommandPlaceholderEventRecord,
    CommandSuggestionEventRecord,
    EvidenceAttachedPayload,
    EvidenceSliceEventRef,
    ExecutionVariantEventRecord,
    FacetObservationEventRecord,
    FrontierCriticizedEventPayload,
    FrontierProposalEventRecord,
    FrontierProposedEventPayload,
    FrontierRepairedEventPayload,
    ObservationExtractedEventPayload,
    OutcomeAssessedEventPayload,
    PlanningCallMetadataEventRecord,
    PlanRequestedEventPayload,
    PrivateValueEventRecord,
    ResearchQueryProposedEventPayload,
    RetryPredicateEventRecord,
    StrategyArchivedEventPayload,
    StrategyFamilyEventRecord,
    StrategyReconciledEventPayload,
    StrategyReconciliationEventOperation,
    TextFactEventRecord,
    ToolCallCompletedPayload,
)
from sedna.engagement.service import PlanningEventCommitItem
from sedna.planning.commands import validate_command_suggestion
from sedna.planning.frontier import FrontierReducer
from sedna.planning.journal_events import (
    payloads_from_observation_batch,
    payloads_from_planning_attempt,
    payloads_from_reconciliation,
    payloads_from_research_observations,
)
from sedna.planning.ledger import (
    StrategyLedgerReducer,
    archive_digest,
    ledger_digest,
    matching_retry_predicate_ids,
    select_reactivation_candidates,
)
from sedna.planning.llm import (
    ObservationEvidenceSlice,
    ObservationRequest,
    PlannerCriticRequest,
    PlannerRepairRequest,
    PlannerRequest,
    PlanningLlmError,
)
from sedna.planning.models import (
    EVIDENCE_SLICE_BYTES,
    MAX_PLANNING_EVENT_BATCH,
    MAX_PLANNING_RESULT_BYTES,
    ArchivedStrategyState,
    EvidenceSliceInput,
    ExecutionVariantState,
    FailedSettlementResult,
    FrontierCriticizedSource,
    FrontierProjection,
    FrontierProposal,
    FrontierProposedSource,
    FrontierRejectedSource,
    FrontierRepairedSource,
    IncompleteSettlementResult,
    InterpretationAudit,
    InterpretationSubject,
    InterpretationSucceededSource,
    LocalEventIdBinding,
    NothingPendingSettlementResult,
    ObjectiveProofObservedSource,
    ObservationBatchDraft,
    ObservationDraft,
    ObservationEventConversion,
    ObservationExtractedSource,
    OutcomeAssessedSource,
    PendingEvidenceRange,
    PlannerCriticVerdict,
    PlannerDraft,
    PlannerFinding,
    PlannerProposalAudit,
    PlannerRejectionAudit,
    PlannerRepairAudit,
    PlanningAttemptEventConversion,
    PlanningCallMetadata,
    PlanningGap,
    PlanningGapRecordedSource,
    PlanningResult,
    PlanRequestAudit,
    PlanRequestedSource,
    ProofCandidateAdmission,
    ProofIndexRecord,
    ResearchEventConversion,
    ResearchSourceAssessedSource,
    ResearchSourceAssessmentAudit,
    ResearchSourceConsultation,
    ResearchSourceConsultedSource,
    ResearchSourceObservationDraft,
    SettledSettlementResult,
    SettlementResult,
    SituationProjection,
    StrategyFamilyState,
    StrategyLedger,
    StrategyReactivatedSource,
    StrategyReactivationTransition,
    StrategyReconciledSource,
    StrategyReconciliation,
    StrategyReconciliationEventConversion,
    StrategyReconciliationItem,
    StrategyStatus,
)
from sedna.planning.ports import TerminalSettlementPort
from sedna.planning.prompts import (
    OBSERVATION_PROMPT,
    OBSERVATION_PROMPT_ID,
    OBSERVATION_PROMPT_VERSION,
    PLANNER_CRITIC_PROMPT,
    PLANNER_CRITIC_PROMPT_ID,
    PLANNER_CRITIC_PROMPT_VERSION,
    PLANNER_PROMPT,
    PLANNER_PROMPT_ID,
    PLANNER_PROMPT_VERSION,
    PLANNER_REPAIR_PROMPT,
    PLANNER_REPAIR_PROMPT_ID,
    PLANNER_REPAIR_PROMPT_VERSION,
)
from sedna.planning.retrieval import PlannerKnowledgeContext, assemble_planner_knowledge
from sedna.planning.situation import SituationReducer


class _EvidenceReadError(Exception):
    pass


class _JournalAppendError(Exception):
    pass


class _PlanningLlmUnavailableError(RuntimeError):
    """Internal boundary marker for planner-host failures only."""


class PlanningService:
    def __init__(
        self,
        *,
        journal: EngagementJournalService,
        llm: Any,
        clock: Callable[[], datetime],
        terminal_settlement_port: TerminalSettlementPort | None = None,
        canonical_revision: Callable[[], str] = lambda: "0" * 64,
        source_registry_digest: Callable[[], str] = lambda: "0" * 64,
        retrieval: Any | None = None,
        source_registry: Any | None = None,
        research_aliases: tuple[str, ...] = (),
        known_flag_values: tuple[str, ...] = (),
    ) -> None:
        self._journal = journal
        self._llm = llm
        self._clock = clock
        self._terminal_settlement_port = terminal_settlement_port
        self._canonical_revision = canonical_revision
        self._source_registry_digest = source_registry_digest
        self._retrieval = retrieval
        self._source_registry = source_registry
        self._research_aliases = research_aliases
        self._known_flag_values = known_flag_values
        self._frontier_cache: dict[str, FrontierProjection] = {}

    def plan_next(self, lane: ExecutionLaneKey, *, max_proposals: int = 5) -> PlanningResult:
        """Plan outside repository locks with one bounded optimistic restart."""
        if not 3 <= max_proposals <= 8:
            raise ValueError("max_proposals must be between 3 and 8")
        for attempt in range(2):
            try:
                return self._plan_next_once(lane, max_proposals=max_proposals)
            except RevisionConflictError:
                if attempt == 0:
                    continue
                resolution = self._journal.resolve_lane_binding(lane)
                if resolution.mode != "exact" or resolution.engagement_id is None:
                    raise ValueError("engagement_binding_required") from None
                snapshot = self._journal.load_snapshot(resolution.engagement_id)
                situation = SituationReducer.rebuild(snapshot)
                replay = StrategyLedgerReducer.rebuild_state(snapshot)
                return PlanningResult(
                    status="gap",
                    engagement_id=resolution.engagement_id,
                    current_authoritative_journal_revision=snapshot.revision,
                    gap=PlanningGap(
                        code="concurrent_state_change",
                        summary="Authoritative state changed during planning.",
                        retryable=True,
                        situation_digest=situation.state_digest,
                        ledger_digest=replay.ledger_sha256,
                    ),
                )
            except _PlanningLlmUnavailableError:
                return self._publish_llm_unavailable_gap(lane)
        raise AssertionError("bounded planning restart exhausted")

    def _publish_llm_unavailable_gap(self, lane: ExecutionLaneKey) -> PlanningResult:
        resolution = self._journal.resolve_lane_binding(lane)
        if resolution.mode != "exact" or resolution.engagement_id is None:
            raise ValueError("engagement_binding_required") from None
        engagement_id = resolution.engagement_id
        snapshot = self._journal.load_snapshot(engagement_id)
        situation = SituationReducer.rebuild(snapshot)
        replay = StrategyLedgerReducer.rebuild_state(snapshot)
        input_digest = sha256(
            json.dumps(
                {
                    "lane_key": lane.stable_key,
                    "revision": snapshot.revision.model_dump(mode="json"),
                    "situation_digest": situation.state_digest,
                    "ledger_digest": replay.ledger_sha256,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        request_id = uuid5(NAMESPACE_URL, f"sedna:llm-unavailable:{input_digest}")
        gap_event_id = uuid5(request_id, "planning-gap")
        gap = PlanningGap(
            request_id=request_id,
            code="llm_unavailable",
            summary="The planning language model is unavailable.",
            retryable=True,
            situation_digest=situation.state_digest,
            ledger_digest=replay.ledger_sha256,
        )
        metadata = PlanningCallMetadata(
            purpose="plan",
            provider="unavailable",
            model="unavailable",
            agent_id="planning-service",
            prompt_id=PLANNER_PROMPT_ID,
            prompt_version=PLANNER_PROMPT_VERSION,
            response_schema_version="1",
            input_digest=input_digest,
            input_tokens=0,
            output_tokens=0,
            elapsed_ms=0,
        )
        reconciliation = StrategyReconciliation(
            input_family_ids=tuple(
                sorted((item.family_id for item in replay.ledger.families), key=str)
            ),
            input_variant_ids=tuple(
                sorted((item.variant_id for item in replay.ledger.variants), key=str)
            ),
            retained_family_ids=tuple(
                sorted((item.family_id for item in replay.ledger.families), key=str)
            ),
            retained_variant_ids=tuple(
                sorted((item.variant_id for item in replay.ledger.variants), key=str)
            ),
        )
        source = PlanningGapRecordedSource(
            local_id="planning-gap",
            request_id=request_id,
            code="llm_unavailable",
            summary=gap.summary,
            retryable=True,
            situation_digest=situation.state_digest,
            ledger_digest=replay.ledger_sha256,
        )
        conversion = PlanningAttemptEventConversion(
            local_event_bindings=(
                LocalEventIdBinding(local_id="planning-gap", event_id=gap_event_id),
            ),
            valid_event_ids=tuple(
                sorted(
                    (*(event.event_id for event in snapshot.events), gap_event_id),
                    key=str,
                )
            ),
            valid_evidence_ids=tuple(
                sorted(
                    {
                        event.payload.evidence.evidence_id
                        for event in snapshot.events
                        if isinstance(event.payload, EvidenceAttachedPayload)
                    }
                )
            ),
            valid_family_ids=reconciliation.input_family_ids,
            valid_variant_ids=reconciliation.input_variant_ids,
            sources=(source,),
            reconciliation=reconciliation,
            call_metadata=metadata,
            planning_gaps=(gap,),
        )
        payload = payloads_from_planning_attempt(conversion)[0]
        committed = self._commit_planning_events(
            engagement_id,
            (
                PlanningEventCommitItem(
                    event_id=gap_event_id,
                    idempotency_key=f"planning:{request_id}:llm-unavailable",
                    payload=payload,
                ),
            ),
            operation_id=request_id,
            expected_revision=snapshot.revision,
        )
        return PlanningResult(
            status="gap",
            engagement_id=engagement_id,
            current_authoritative_journal_revision=committed.snapshot.revision,
            gap=gap,
        )

    def _complete_planning(self, model_type: Any, **kwargs: Any) -> Any:
        try:
            return self._llm.complete(model_type, **kwargs)
        except PlanningLlmError as exc:
            if exc.reason_code != "transport_failure":
                raise
            raise _PlanningLlmUnavailableError from exc

    def _plan_next_once(self, lane: ExecutionLaneKey, *, max_proposals: int) -> PlanningResult:
        """Settle evidence, then refuse planning across lifecycle terminal barriers."""
        resolution = self._journal.resolve_lane_binding(lane)
        if resolution.mode != "exact" or resolution.engagement_id is None:
            raise ValueError("engagement_binding_required")
        engagement_id = resolution.engagement_id
        settlement = self.settle_pending_evidence(engagement_id, reason="plan")
        snapshot = self._journal.load_snapshot(engagement_id)
        if snapshot.state.status in {
            EngagementStatus.CLOSING,
            EngagementStatus.CLOSED_UNVERIFIED,
            EngagementStatus.CLOSED_VERIFIED,
            EngagementStatus.ABANDONED,
        }:
            return PlanningResult(
                status="gap",
                engagement_id=engagement_id,
                current_authoritative_journal_revision=snapshot.revision,
                gap=PlanningGap(
                    code="engagement_terminal",
                    summary=f"Engagement lifecycle is {snapshot.state.status.value}.",
                    retryable=False,
                ),
            )
        if settlement.status == "incomplete":
            return PlanningResult(
                status="gap",
                engagement_id=engagement_id,
                current_authoritative_journal_revision=snapshot.revision,
                gap=PlanningGap(
                    code="settlement_incomplete",
                    summary="Evidence settlement remains incomplete.",
                    retryable=True,
                    situation_digest=(
                        settlement.situation.state_digest
                        if settlement.situation is not None
                        else None
                    ),
                    pending_ranges=settlement.pending_ranges,
                ),
            )
        if settlement.status == "failed":
            return PlanningResult(
                status="failed",
                engagement_id=engagement_id,
                current_authoritative_journal_revision=snapshot.revision,
                failure_code="settlement_unavailable",
            )
        situation = settlement.situation
        if situation is None:
            raise RuntimeError("successful settlement omitted its situation")
        replay = StrategyLedgerReducer.rebuild_state(snapshot)
        if select_reactivation_candidates(replay.archive_records, situation):
            self._reactivate_archive_candidates(
                engagement_id,
                snapshot=snapshot,
                situation=situation,
                replay=replay,
            )
            snapshot = self._journal.load_snapshot(engagement_id)
            situation = SituationReducer.rebuild(snapshot)
            replay = StrategyLedgerReducer.rebuild_state(snapshot)
        prior_frontier = FrontierReducer.rebuild(snapshot)
        initial_archive = self._journal.load_strategy_archive(engagement_id)
        canonical_revision = self._canonical_revision()
        source_registry_digest = self._source_registry_digest()
        if self._retrieval is not None and self._source_registry is not None:
            knowledge_context = assemble_planner_knowledge(
                situation,
                snapshot.state.scope_references,
                retrieval=self._retrieval,
                source_registry=self._source_registry,
                canonical_revision=self._canonical_revision,
            )
            canonical_revision = knowledge_context.canonical_revision
            source_registry_digest = knowledge_context.source_registry_digest
        else:
            knowledge_values = {
                "canonical_revision": canonical_revision,
                "situation_digest": situation.state_digest,
                "source_registry_digest": source_registry_digest,
            }
            knowledge_context = PlannerKnowledgeContext(
                **knowledge_values,
                context_digest=sha256(
                    json.dumps(knowledge_values, sort_keys=True, separators=(",", ":")).encode(
                        "utf-8"
                    )
                ).hexdigest(),
            )
        valid_event_ids = {event.event_id for event in snapshot.events}
        valid_scope_ids = {item.reference_id for item in snapshot.state.scope_references}
        valid_knowledge_ids = {
            hit.artifact_id
            for hits in (
                knowledge_context.references,
                knowledge_context.case_steps,
                knowledge_context.negative_cases,
                knowledge_context.decision_guidance,
            )
            for hit in hits
        }
        valid_knowledge_ids.update(
            example.example_id for example in knowledge_context.execution_examples
        )
        cache_key = sha256(
            json.dumps(
                {
                    "situation_digest": situation.state_digest,
                    "material_event_revision": situation.material_event_revision,
                    "resulting_ledger_digest": replay.ledger_sha256,
                    "canonical_revision": canonical_revision,
                    "source_registry_digest": source_registry_digest,
                    "max_proposals": max_proposals,
                    "observation_prompt_version": OBSERVATION_PROMPT_VERSION,
                    "planner_prompt_version": PLANNER_PROMPT_VERSION,
                    "critic_prompt_version": PLANNER_CRITIC_PROMPT_VERSION,
                    "repair_prompt_version": PLANNER_REPAIR_PROMPT_VERSION,
                    "situation_schema_version": "1",
                    "ledger_schema_version": "1",
                    "frontier_schema_version": "1",
                    "research_policy_version": "1",
                    "command_policy_version": "1",
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        cached = self._frontier_cache.get(cache_key)
        if cached is not None:
            return PlanningResult(
                status="success",
                engagement_id=engagement_id,
                current_authoritative_journal_revision=snapshot.revision,
                frontier=cached,
            )
        plan_completion = self._complete_planning(
            PlannerDraft,
            instructions=PLANNER_PROMPT,
            payload=PlannerRequest(
                situation=situation,
                ledger=replay.ledger,
                knowledge_context=knowledge_context,
                scope_references=snapshot.state.scope_references,
                recent_event_ids=tuple(event.event_id for event in snapshot.events[-64:]),
                recent_event_context=tuple(
                    f"type={event.type} id={event.event_id}" for event in snapshot.events[-64:]
                ),
                max_proposals=max_proposals,
            ),
            purpose="sedna.planning.plan",
        )
        planned = plan_completion.parsed
        if len(planned.proposals) > max_proposals:
            raise ValueError("planner_exceeded_max_proposals")
        self._validate_planner_draft(
            planned,
            valid_event_ids=valid_event_ids,
            events_by_id={event.event_id: event for event in snapshot.events},
            valid_scope_ids=valid_scope_ids,
            valid_knowledge_ids=valid_knowledge_ids,
            scope_references=snapshot.state.scope_references,
            secret_references=situation.secret_references,
            execution_examples=knowledge_context.execution_examples,
        )
        critic_completion = self._complete_planning(
            PlannerCriticVerdict,
            instructions=PLANNER_CRITIC_PROMPT,
            payload=PlannerCriticRequest(draft=planned),
            purpose="sedna.planning.critic",
        )
        verdict = critic_completion.parsed
        initial_verdict = verdict
        repaired_once = False
        repair_completion = None
        if verdict.accepted:
            provisional_frontier_id = uuid5(NAMESPACE_URL, f"sedna-frontier:{cache_key}")
            existing_families = {item.runtime_key: item for item in replay.ledger.families}
            existing_variants = {item.runtime_key: item for item in replay.ledger.variants}
            provisional_proposals = tuple(
                FrontierProposal(
                    proposal_id=uuid5(provisional_frontier_id, f"proposal:{index}"),
                    family_id=(
                        existing_families[item.family_runtime_key].family_id
                        if item.family_runtime_key in existing_families
                        else uuid5(provisional_frontier_id, f"family:{item.family_runtime_key}")
                    ),
                    variant_id=(
                        existing_variants[item.variant_runtime_key].variant_id
                        if item.variant_runtime_key in existing_variants
                        else uuid5(provisional_frontier_id, f"variant:{item.variant_runtime_key}")
                    ),
                    title=item.title,
                    score=item.score,
                    confidence=item.confidence,
                    rationale=item.rationale,
                )
                for index, item in enumerate(planned.proposals, start=1)
            )
            self._validate_score_changes(planned, provisional_proposals, prior_frontier)
            provisional_request_id = uuid5(provisional_frontier_id, "request")
            provisional_ledger, _ = self._reconcile_frontier(
                replay.ledger,
                planned,
                provisional_proposals,
                request_id=provisional_request_id,
                frontier_id=provisional_frontier_id,
                revision=situation.authoritative_journal_revision,
                event_ref=snapshot.events[0].event_id,
                call_metadata=PlanningCallMetadata.model_validate(
                    self._planning_call_metadata("plan", cache_key, plan_completion).model_dump(
                        mode="json"
                    )
                ),
                event_offset=2 + len(provisional_proposals),
                prior_frontier=prior_frontier,
            )
            provisional_result = PlanningResult(
                status="success",
                engagement_id=engagement_id,
                current_authoritative_journal_revision=snapshot.revision,
                frontier=FrontierProjection(
                    frontier_id=provisional_frontier_id,
                    engagement_id=engagement_id,
                    state_digest=situation.state_digest,
                    input_ledger_digest=replay.ledger_sha256,
                    resulting_ledger_digest=ledger_digest(provisional_ledger),
                    proposals=provisional_proposals,
                    constrained_rationale=(
                        "The accepted frontier contains fewer than three applicable proposals."
                        if len(provisional_proposals) < 3
                        else None
                    ),
                ),
            )
            if (
                len(provisional_result.model_dump_json().encode("utf-8"))
                > MAX_PLANNING_RESULT_BYTES
            ):
                verdict = PlannerCriticVerdict(
                    accepted=False,
                    findings=(
                        PlannerFinding(
                            code="result_too_large",
                            summary="The accepted frontier exceeds the bounded result envelope.",
                            material=True,
                        ),
                    ),
                )
                initial_verdict = verdict
        if not verdict.accepted:
            repaired_once = True
            repair_completion = self._complete_planning(
                PlannerDraft,
                instructions=PLANNER_REPAIR_PROMPT,
                payload=PlannerRepairRequest(draft=planned, critic=verdict),
                purpose="sedna.planning.repair",
            )
            repaired = repair_completion.parsed
            if len(repaired.proposals) > max_proposals:
                raise ValueError("planner_exceeded_max_proposals")
            self._validate_planner_draft(
                repaired,
                valid_event_ids=valid_event_ids,
                events_by_id={event.event_id: event for event in snapshot.events},
                valid_scope_ids=valid_scope_ids,
                valid_knowledge_ids=valid_knowledge_ids,
                scope_references=snapshot.state.scope_references,
                secret_references=situation.secret_references,
                execution_examples=knowledge_context.execution_examples,
            )
            critic_completion = self._complete_planning(
                PlannerCriticVerdict,
                instructions=PLANNER_CRITIC_PROMPT,
                payload=PlannerCriticRequest(draft=repaired),
                purpose="sedna.planning.critic",
            )
            verdict = critic_completion.parsed
            if not verdict.accepted:
                frontier_id = uuid5(NAMESPACE_URL, f"sedna-frontier:{cache_key}")
                request_id = uuid5(frontier_id, "request")
                gap = PlanningGap(
                    request_id=request_id,
                    code="critic_rejected",
                    summary="The critic rejected the repaired frontier.",
                    retryable=True,
                    situation_digest=situation.state_digest,
                    ledger_digest=replay.ledger_sha256,
                    stale_frontier=(
                        None
                        if prior_frontier is None
                        else prior_frontier.model_copy(update={"stale": True})
                    ),
                )
                metadata = PlanningCallMetadata.model_validate(
                    self._planning_call_metadata("critic", cache_key, critic_completion).model_dump(
                        mode="json"
                    )
                )
                critic_event_ids = (
                    uuid5(frontier_id, "rejected-critic:1"),
                    uuid5(frontier_id, "rejected-critic:2"),
                )
                rejected_event_id = uuid5(frontier_id, "rejected-attempt")
                gap_event_id = uuid5(frontier_id, "rejected-gap")
                reason_codes = tuple(item.code for item in verdict.findings)
                reconciliation = StrategyReconciliation(
                    input_family_ids=tuple(
                        sorted((item.family_id for item in replay.ledger.families), key=str)
                    ),
                    input_variant_ids=tuple(
                        sorted((item.variant_id for item in replay.ledger.variants), key=str)
                    ),
                    retained_family_ids=tuple(
                        sorted((item.family_id for item in replay.ledger.families), key=str)
                    ),
                    retained_variant_ids=tuple(
                        sorted((item.variant_id for item in replay.ledger.variants), key=str)
                    ),
                )
                sources = (
                    FrontierRejectedSource(
                        local_id="frontier-rejected",
                        request_id=request_id,
                        frontier_id=frontier_id,
                        critic_event_ids=critic_event_ids,
                        reason_codes=reason_codes,
                    ),
                    PlanningGapRecordedSource(
                        local_id="planning-gap",
                        request_id=request_id,
                        code="critic_rejected",
                        summary=gap.summary,
                        retryable=True,
                        situation_digest=situation.state_digest,
                        ledger_digest=replay.ledger_sha256,
                    ),
                )
                bindings = (
                    LocalEventIdBinding(local_id="frontier-rejected", event_id=rejected_event_id),
                    LocalEventIdBinding(local_id="planning-gap", event_id=gap_event_id),
                )
                conversion = PlanningAttemptEventConversion(
                    local_event_bindings=bindings,
                    valid_event_ids=tuple(
                        sorted(
                            {
                                *(event.event_id for event in snapshot.events),
                                *critic_event_ids,
                                rejected_event_id,
                                gap_event_id,
                            },
                            key=str,
                        )
                    ),
                    valid_evidence_ids=(),
                    valid_family_ids=reconciliation.input_family_ids,
                    valid_variant_ids=reconciliation.input_variant_ids,
                    sources=sources,
                    reconciliation=reconciliation,
                    call_metadata=metadata,
                    rejection_audits=(
                        PlannerRejectionAudit(
                            call_metadata=metadata,
                            safe_code="critic_rejected",
                            request_id=request_id,
                            frontier_id=frontier_id,
                            critic_event_ids=critic_event_ids,
                            reason_codes=reason_codes,
                        ),
                    ),
                    planning_gaps=(gap,),
                )
                rejection_payloads = payloads_from_planning_attempt(conversion)
                committed = self._commit_planning_events(
                    engagement_id,
                    tuple(
                        PlanningEventCommitItem(
                            event_id=binding.event_id,
                            idempotency_key=f"planning:{request_id}:{payload.kind}",
                            payload=payload,
                        )
                        for binding, payload in zip(bindings, rejection_payloads, strict=True)
                    ),
                    operation_id=request_id,
                    expected_revision=snapshot.revision,
                )
                return PlanningResult(
                    status="gap",
                    engagement_id=engagement_id,
                    current_authoritative_journal_revision=committed.snapshot.revision,
                    gap=gap,
                )
            planned = repaired
        frontier_id = uuid5(NAMESPACE_URL, f"sedna-frontier:{cache_key}")
        existing_families = {item.runtime_key: item for item in replay.ledger.families}
        existing_variants = {item.runtime_key: item for item in replay.ledger.variants}
        proposals = tuple(
            FrontierProposal(
                proposal_id=uuid5(frontier_id, f"proposal:{index}"),
                family_id=(
                    existing_families[item.family_runtime_key].family_id
                    if item.family_runtime_key in existing_families
                    else uuid5(frontier_id, f"family:{item.family_runtime_key}")
                ),
                variant_id=(
                    existing_variants[item.variant_runtime_key].variant_id
                    if item.variant_runtime_key in existing_variants
                    else uuid5(frontier_id, f"variant:{item.variant_runtime_key}")
                ),
                title=item.title,
                score=item.score,
                confidence=item.confidence,
                rationale=item.rationale,
            )
            for index, item in enumerate(planned.proposals, start=1)
        )
        self._validate_score_changes(planned, proposals, prior_frontier)
        request_id = uuid5(frontier_id, "request")
        planner_metadata = self._planning_call_metadata("plan", cache_key, plan_completion)
        critic_metadata = self._planning_call_metadata("critic", cache_key, critic_completion)
        planning_event_count = 2 + len(proposals)
        if repaired_once:
            planning_event_count += 1 + len(proposals)
        resulting_ledger, reconciliation_payloads = self._reconcile_frontier(
            replay.ledger,
            planned,
            proposals,
            request_id=request_id,
            frontier_id=frontier_id,
            revision=situation.authoritative_journal_revision,
            event_ref=snapshot.events[0].event_id,
            call_metadata=PlanningCallMetadata.model_validate(
                planner_metadata.model_dump(mode="json")
            ),
            event_offset=planning_event_count,
            prior_frontier=prior_frontier,
        )
        resulting_ledger_digest = ledger_digest(resulting_ledger)
        frontier = FrontierProjection(
            frontier_id=frontier_id,
            engagement_id=engagement_id,
            state_digest=situation.state_digest,
            input_ledger_digest=replay.ledger_sha256,
            resulting_ledger_digest=resulting_ledger_digest,
            proposals=proposals,
            constrained_rationale=(
                "The accepted frontier contains fewer than three applicable proposals."
                if len(proposals) < 3
                else None
            ),
        )
        prospective_result = PlanningResult(
            status="success",
            engagement_id=engagement_id,
            current_authoritative_journal_revision=snapshot.revision,
            frontier=frontier,
        )
        if len(prospective_result.model_dump_json().encode("utf-8")) > MAX_PLANNING_RESULT_BYTES:
            return PlanningResult(
                status="failed",
                engagement_id=engagement_id,
                current_authoritative_journal_revision=snapshot.revision,
                failure_code="result_too_large",
            )
        request_fields = {
            "request_id": request_id,
            "lane_key": lane.stable_key,
            "situation_digest": situation.state_digest,
            "material_event_revision": situation.authoritative_journal_revision.model_copy(
                update={"sequence": situation.material_event_revision}
            ),
            "input_ledger_digest": replay.ledger_sha256,
            "canonical_revision": canonical_revision,
            "source_registry_digest": source_registry_digest,
            "max_proposals": max_proposals,
        }
        request_digest = sha256(
            json.dumps(
                {
                    **request_fields,
                    "request_id": str(request_id),
                    "material_event_revision": request_fields["material_event_revision"].model_dump(
                        mode="json"
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        event_ref = snapshot.events[0].event_id
        proposal_records = tuple(
            self._proposal_event_record(
                item,
                proposal,
                ordinal,
                fallback_event_ref=event_ref,
                scope_references=snapshot.state.scope_references,
                secret_references=situation.secret_references,
                execution_examples=knowledge_context.execution_examples,
            )
            for ordinal, (item, proposal) in enumerate(
                zip(planned.proposals, frontier.proposals, strict=True), start=1
            )
        )
        payloads: list[Any] = [
            PlanRequestedEventPayload(**request_fields, request_digest=request_digest)
        ]
        for ordinal, _proposal in enumerate(frontier.proposals, start=1):
            payloads.append(
                FrontierProposedEventPayload(
                    request_id=request_id,
                    frontier_id=frontier_id,
                    proposal_ordinal=ordinal,
                    proposal_count=len(frontier.proposals),
                    proposal=proposal_records[ordinal - 1],
                    situation_digest=situation.state_digest,
                    input_ledger_digest=replay.ledger_sha256,
                    knowledge_context_digest=knowledge_context.context_digest,
                    draft_digest=sha256(planned.model_dump_json().encode("utf-8")).hexdigest(),
                    call_metadata=planner_metadata,
                    planner_call_digest=sha256(
                        planner_metadata.model_dump_json().encode("utf-8")
                    ).hexdigest(),
                )
            )
        if repaired_once:
            assert repair_completion is not None
            first_critic_id = uuid5(frontier_id, f"event:{len(payloads) + 1}:frontier_criticized")
            payloads.append(
                FrontierCriticizedEventPayload(
                    request_id=request_id,
                    frontier_id=frontier_id,
                    critic_pass=1,
                    accepted=False,
                    finding_codes=tuple(item.code for item in initial_verdict.findings),
                    cited_event_ids=initial_verdict.cited_event_ids,
                    call_metadata=critic_metadata,
                    call_input_digest=critic_metadata.input_digest,
                    call_output_digest=sha256(
                        initial_verdict.model_dump_json().encode("utf-8")
                    ).hexdigest(),
                )
            )
            for ordinal, _proposal in enumerate(frontier.proposals, start=1):
                record = proposal_records[ordinal - 1]
                payloads.append(
                    FrontierRepairedEventPayload(
                        request_id=request_id,
                        frontier_id=frontier_id,
                        critic_event_id=first_critic_id,
                        proposal_ordinal=ordinal,
                        proposal_count=len(frontier.proposals),
                        proposal=record,
                        repaired_draft_digest=sha256(
                            planned.model_dump_json().encode("utf-8")
                        ).hexdigest(),
                        call_metadata=self._planning_call_metadata(
                            "repair", cache_key, repair_completion
                        ),
                        call_input_digest=cache_key,
                        call_output_digest=sha256(
                            record.model_dump_json().encode("utf-8")
                        ).hexdigest(),
                    )
                )
        payloads.append(
            FrontierCriticizedEventPayload(
                request_id=request_id,
                frontier_id=frontier_id,
                critic_pass=2 if repaired_once else 1,
                accepted=True,
                call_metadata=critic_metadata,
                call_input_digest=critic_metadata.input_digest,
                call_output_digest=sha256(verdict.model_dump_json().encode("utf-8")).hexdigest(),
            )
        )
        planning_sources = []
        proposal_audits = []
        repair_audits = []
        critic_verdicts = []
        for index, payload in enumerate(payloads, start=1):
            local_id = f"planning-{index:03d}"
            if isinstance(payload, PlanRequestedEventPayload):
                planning_sources.append(PlanRequestedSource(local_id=local_id, **request_fields))
            elif isinstance(payload, FrontierProposedEventPayload):
                source = FrontierProposedSource(
                    local_id=local_id,
                    request_id=payload.request_id,
                    frontier_id=payload.frontier_id,
                    proposal_ordinal=payload.proposal_ordinal,
                    proposal_count=payload.proposal_count,
                    proposal=payload.proposal,
                    situation_digest=payload.situation_digest,
                    input_ledger_digest=payload.input_ledger_digest,
                    knowledge_context_digest=payload.knowledge_context_digest,
                )
                planning_sources.append(source)
                proposal_audits.append(
                    PlannerProposalAudit.model_validate(
                        source.model_dump(exclude={"kind", "local_id"})
                    )
                )
            elif isinstance(payload, FrontierCriticizedEventPayload):
                planning_sources.append(
                    FrontierCriticizedSource(
                        local_id=local_id,
                        request_id=payload.request_id,
                        frontier_id=payload.frontier_id,
                        critic_pass=payload.critic_pass,
                        accepted=payload.accepted,
                        finding_codes=payload.finding_codes,
                        cited_event_ids=payload.cited_event_ids,
                    )
                )
                source_verdict = initial_verdict if payload.critic_pass == 1 else verdict
                critic_verdicts.append(
                    source_verdict.model_copy(
                        update={
                            "request_id": payload.request_id,
                            "frontier_id": payload.frontier_id,
                            "critic_pass": payload.critic_pass,
                        }
                    )
                )
            elif isinstance(payload, FrontierRepairedEventPayload):
                source = FrontierRepairedSource(
                    local_id=local_id,
                    request_id=payload.request_id,
                    frontier_id=payload.frontier_id,
                    critic_event_id=payload.critic_event_id,
                    proposal_ordinal=payload.proposal_ordinal,
                    proposal_count=payload.proposal_count,
                    proposal=payload.proposal,
                )
                planning_sources.append(source)
                repair_audits.append(
                    PlannerRepairAudit(
                        call_metadata=PlanningCallMetadata.model_validate(
                            payload.call_metadata.model_dump(mode="json")
                        ),
                        critic_finding_codes=tuple(item.code for item in initial_verdict.findings),
                        request_id=source.request_id,
                        frontier_id=source.frontier_id,
                        critic_event_id=source.critic_event_id,
                        proposal_ordinal=source.proposal_ordinal,
                        proposal_count=source.proposal_count,
                        proposal=source.proposal,
                    )
                )
        planning_bindings = tuple(
            LocalEventIdBinding(
                local_id=source.local_id,
                event_id=uuid5(frontier_id, f"event:{index}:{source.kind}"),
            )
            for index, source in enumerate(planning_sources, start=1)
        )
        authoritative_planner_metadata = PlanningCallMetadata.model_validate(
            planner_metadata.model_dump(mode="json")
        )
        planning_conversion = PlanningAttemptEventConversion(
            local_event_bindings=planning_bindings,
            valid_event_ids=tuple(
                sorted(
                    {
                        *(event.event_id for event in snapshot.events),
                        *(item.event_id for item in planning_bindings),
                    },
                    key=str,
                )
            ),
            valid_evidence_ids=(),
            valid_scope_reference_ids=tuple(sorted(valid_scope_ids)),
            valid_knowledge_ids=tuple(sorted(valid_knowledge_ids)),
            valid_proposal_ids=tuple(sorted((item.proposal_id for item in proposals), key=str)),
            valid_family_ids=tuple(sorted((item.family_id for item in proposals), key=str)),
            valid_variant_ids=tuple(sorted((item.variant_id for item in proposals), key=str)),
            sources=tuple(planning_sources),
            reconciliation=StrategyReconciliation(
                input_family_ids=tuple(
                    sorted((item.family_id for item in replay.ledger.families), key=str)
                ),
                input_variant_ids=tuple(
                    sorted((item.variant_id for item in replay.ledger.variants), key=str)
                ),
                retained_family_ids=tuple(
                    sorted((item.family_id for item in replay.ledger.families), key=str)
                ),
                retained_variant_ids=tuple(
                    sorted((item.variant_id for item in replay.ledger.variants), key=str)
                ),
            ),
            call_metadata=authoritative_planner_metadata,
            plan_request_audit=PlanRequestAudit(
                call_metadata=authoritative_planner_metadata,
                state_digest=situation.state_digest,
                ledger_digest=replay.ledger_sha256,
                request_id=request_id,
                lane_key=lane.stable_key,
                material_event_revision=request_fields["material_event_revision"],
                canonical_revision=canonical_revision,
                source_registry_digest=source_registry_digest,
                max_proposals=max_proposals,
            ),
            planner_draft=planned,
            planner_proposals=tuple(proposal_audits),
            critic_verdicts=tuple(critic_verdicts),
            repair_audits=tuple(repair_audits),
        )
        payloads = list(payloads_from_planning_attempt(planning_conversion))
        payloads.extend(reconciliation_payloads)
        resulting_ledger, archive_payloads = self._archive_terminal_variants(
            resulting_ledger,
            replay.archive_records,
            planned,
            proposals,
            reconciliation_payloads,
            request_id=request_id,
            frontier_id=frontier_id,
            revision=situation.authoritative_journal_revision,
            archive_event_offset=len(payloads),
        )
        resulting_ledger_digest = ledger_digest(resulting_ledger)
        frontier = frontier.model_copy(update={"resulting_ledger_digest": resulting_ledger_digest})
        payloads.extend(archive_payloads)
        payloads.extend(
            self._research_query_payload(
                query,
                query_id=uuid5(frontier_id, f"research:{ordinal}"),
                authoritative_aliases=(snapshot.manifest.display_name,),
                related_event_ids=tuple(
                    sorted(
                        {event_id for item in planned.proposals for event_id in item.event_refs},
                        key=str,
                    )
                ),
            )
            for ordinal, query in enumerate(planned.research_queries, start=1)
        )
        items = tuple(
            PlanningEventCommitItem(
                event_id=uuid5(frontier_id, f"event:{index}:{payload.kind}"),
                idempotency_key=f"planning:{request_id}:{index}:{payload.kind}",
                payload=payload,
            )
            for index, payload in enumerate(payloads, start=1)
        )
        if (
            self._canonical_revision() != canonical_revision
            or self._source_registry_digest() != source_registry_digest
        ):
            raise RevisionConflictError
        fresh_archive = self._journal.load_strategy_archive(engagement_id)
        if (None if fresh_archive is None else fresh_archive.envelope.archive_revision) != (
            None if initial_archive is None else initial_archive.envelope.archive_revision
        ):
            raise RevisionConflictError
        archive_commit = self._journal.commit_strategy_archive(
            engagement_id,
            schema_id="sedna.strategy-archive.v1",
            records=tuple(
                StrategyArchiveRecordDraft(
                    entry_id=record.archive_entry_id,
                    payload=record.model_dump(mode="json", warnings="error"),
                )
                for record in (
                    *replay.archive_records,
                    *(payload.archive_record for payload in archive_payloads),
                )
            ),
            expected_archive_revision=(
                None if initial_archive is None else initial_archive.envelope.archive_revision
            ),
            expected_journal_revision=snapshot.revision,
        )
        try:
            committed = self._commit_planning_events(
                engagement_id,
                items,
                operation_id=request_id,
                expected_revision=snapshot.revision,
            )
        except Exception:
            if self._planning_batch_durability(engagement_id, items) is False:
                self._journal.rollback_strategy_archive(
                    engagement_id,
                    failed_archive_revision=archive_commit.envelope.archive_revision,
                    expected_journal_revision=snapshot.revision,
                    previous=initial_archive,
                )
            raise
        committed_ledger = StrategyLedgerReducer.rebuild_state(committed.snapshot)
        if committed_ledger.ledger_sha256 != frontier.resulting_ledger_digest:
            raise RuntimeError("committed ledger digest does not match accepted frontier")
        committed_frontier = FrontierReducer.rebuild(committed.snapshot)
        if committed_frontier != frontier:
            raise RuntimeError("committed frontier does not replay to the accepted frontier")
        assert committed_frontier is not None
        self._journal.commit_projection(
            engagement_id,
            "strategy-ledger",
            committed_ledger.ledger,
            expected_revision=committed.snapshot.revision,
        )

        self._journal.commit_projection(
            engagement_id,
            "frontier",
            committed_frontier,
            expected_revision=committed.snapshot.revision,
        )
        publication_material = json.loads(
            json.dumps(
                {
                    "situation_digest": situation.state_digest,
                    "material_event_revision": situation.material_event_revision,
                    "resulting_ledger_digest": resulting_ledger_digest,
                    "canonical_revision": canonical_revision,
                    "source_registry_digest": source_registry_digest,
                    "max_proposals": max_proposals,
                    "observation_prompt_version": OBSERVATION_PROMPT_VERSION,
                    "planner_prompt_version": PLANNER_PROMPT_VERSION,
                    "critic_prompt_version": PLANNER_CRITIC_PROMPT_VERSION,
                    "repair_prompt_version": PLANNER_REPAIR_PROMPT_VERSION,
                    "situation_schema_version": "1",
                    "ledger_schema_version": "1",
                    "frontier_schema_version": "1",
                    "research_policy_version": "1",
                    "command_policy_version": "1",
                }
            )
        )
        publication_cache_key = sha256(
            json.dumps(publication_material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self._frontier_cache[publication_cache_key] = committed_frontier
        return PlanningResult(
            status="success",
            engagement_id=engagement_id,
            current_authoritative_journal_revision=committed.snapshot.revision,
            frontier=committed_frontier,
        )

    @staticmethod
    def evaluate_research_query(
        query: str,
        *,
        protected_aliases: tuple[str, ...] = (),
        known_flag_values: tuple[str, ...] = (),
    ) -> tuple[Literal["allowed", "rejected"], tuple[str, ...], str]:
        normalized = " ".join(query.lower().split())
        unsafe_terms = ("walkthrough", "writeup", "solution", "flag", "user.txt", "root.txt")
        aliases = tuple(
            " ".join(alias.lower().split()) for alias in protected_aliases if alias.strip()
        )
        flags = tuple(value.lower() for value in known_flag_values if value)
        if any(value in normalized for value in flags):
            return "rejected", ("known_flag_value",), normalized
        if any(alias in normalized for alias in aliases) and any(
            term in normalized for term in unsafe_terms
        ):
            return "rejected", ("current_machine_solution",), normalized
        return "allowed", ("generic_technical_research",), normalized

    def _research_query_payload(
        self,
        query: str,
        *,
        query_id: UUID,
        authoritative_aliases: tuple[str, ...],
        related_event_ids: tuple[UUID, ...],
    ) -> ResearchQueryProposedEventPayload:
        decision, reason_codes, normalized = self.evaluate_research_query(
            query,
            protected_aliases=tuple(sorted({*self._research_aliases, *authoritative_aliases})),
            known_flag_values=self._known_flag_values,
        )
        return ResearchQueryProposedEventPayload(
            query_id=query_id,
            normalized_query=normalized,
            query_digest=sha256(normalized.encode("utf-8")).hexdigest(),
            policy_decision=decision,
            policy_version="1",
            reason_codes=reason_codes,
            related_event_ids=related_event_ids,
        )

    @staticmethod
    def _proposal_event_record(
        draft: Any,
        proposal: FrontierProposal,
        ordinal: int,
        *,
        fallback_event_ref: UUID,
        scope_references: tuple[Any, ...],
        secret_references: tuple[Any, ...],
        execution_examples: tuple[Any, ...],
    ) -> FrontierProposalEventRecord:
        command_records = []
        for command_draft in draft.commands:
            command = validate_command_suggestion(
                command_draft,
                scope_references=scope_references,
                secret_references=secret_references,
                execution_examples=execution_examples,
            )
            names = re.findall(r"\{\{([a-z][a-z0-9_]{0,63})\}\}", command.command_template)
            placeholders = tuple(
                CommandPlaceholderEventRecord(
                    name=name,
                    kind=kind.value,
                    binding_policy=(
                        "authorized_scope"
                        if kind.value == "target"
                        else "never_auto_bind"
                        if kind.value == "source_case_credential"
                        else "host_supplied"
                    ),
                    role=f"Provide {kind.value} for this command.",
                )
                for name, kind in zip(names, command.placeholder_kinds, strict=True)
            )
            command_records.append(
                CommandSuggestionEventRecord(
                    command_id=command.command_id,
                    origin=command.origin.value,
                    capability_hint=command.capability_hint,
                    purpose=command.purpose,
                    command_template=command.command_template,
                    placeholders=placeholders,
                    bindings=tuple(
                        CommandBindingEventRecord(**binding.model_dump())
                        for binding in command.bindings
                    ),
                    rendered_preview=command.rendered_preview,
                    source_example_id=command.source_example_id,
                    knowledge_refs=command.knowledge_refs,
                    validation_note=command.validation_note,
                )
            )
        return FrontierProposalEventRecord(
            proposal_id=proposal.proposal_id,
            rank=ordinal,
            family_id=proposal.family_id,
            variant_id=proposal.variant_id,
            title=proposal.title,
            strategic_intent=draft.strategic_intent or proposal.rationale,
            rationale=proposal.rationale,
            score=proposal.score,
            confidence=proposal.confidence / 100,
            prerequisites=draft.prerequisites,
            expected_information_gain=draft.expected_information_gain,
            expected_evidence=draft.expected_evidence,
            stop_conditions=draft.stop_conditions,
            event_refs=draft.event_refs or (fallback_event_ref,),
            knowledge_refs=draft.knowledge_refs,
            scope_reference_ids=draft.scope_reference_ids,
            commands=tuple(command_records),
        )

    @staticmethod
    def _validate_planner_draft(
        draft: PlannerDraft,
        *,
        valid_event_ids: set[UUID],
        events_by_id: dict[UUID, Any],
        valid_scope_ids: set[str],
        valid_knowledge_ids: set[str],
        scope_references: tuple[Any, ...],
        secret_references: tuple[Any, ...],
        execution_examples: tuple[Any, ...],
    ) -> None:
        command_keys: set[tuple[str, str, str | None]] = set()
        for proposal in draft.proposals:
            if not set(proposal.event_refs) <= valid_event_ids:
                raise ValueError("planner_invented_event_reference")
            if not set(proposal.scope_reference_ids) <= valid_scope_ids:
                raise ValueError("planner_out_of_scope_reference")
            if not set(proposal.knowledge_refs) <= valid_knowledge_ids:
                raise ValueError("planner_invented_knowledge_reference")
            if proposal.score == 0:
                cited_payloads = tuple(
                    events_by_id[event_id].payload for event_id in proposal.event_refs
                )
                grounded = (
                    proposal.terminal_reason == "incompatibility"
                    and any(
                        isinstance(payload, ObservationExtractedEventPayload)
                        and payload.observation.record_kind == "incompatibility"
                        and bool(payload.observation.scope_reference_ids)
                        and set(payload.observation.scope_reference_ids) <= valid_scope_ids
                        for payload in cited_payloads
                    )
                ) or (
                    proposal.terminal_reason == "impossibility"
                    and any(
                        isinstance(payload, OutcomeAssessedEventPayload)
                        and payload.category in {"negative_evidence", "incompatible"}
                        for payload in cited_payloads
                    )
                )
                if not grounded:
                    raise ValueError("zero_score_terminal_not_grounded")
            for command in proposal.commands:
                resolved = validate_command_suggestion(
                    command,
                    scope_references=scope_references,
                    secret_references=secret_references,
                    execution_examples=execution_examples,
                )
                key = (resolved.command_template, resolved.origin.value, resolved.source_example_id)
                if key in command_keys:
                    raise ValueError("planner_commands_not_unique")
                command_keys.add(key)

    @staticmethod
    def _validate_score_changes(
        draft: PlannerDraft,
        proposals: tuple[FrontierProposal, ...],
        prior_frontier: FrontierProjection | None,
    ) -> None:
        prior_scores = (
            {}
            if prior_frontier is None
            else {item.variant_id: item.score for item in prior_frontier.proposals}
        )
        for item, proposal in zip(draft.proposals, proposals, strict=True):
            prior_score = prior_scores.get(proposal.variant_id)
            if prior_score is None:
                if item.previous_score is not None:
                    raise ValueError("new_proposal_declares_previous_score")
                if item.score == 0 and item.status not in {
                    StrategyStatus.BLOCKED,
                    StrategyStatus.EXHAUSTED,
                }:
                    raise ValueError("new_proposal_score_must_be_positive")
                continue
            if item.score != prior_score:
                if item.previous_score != prior_score:
                    raise ValueError("planner_previous_score_mismatch")
                if item.score_explanation is None:
                    raise ValueError("changed_score_requires_explanation")
                if not item.event_refs:
                    raise ValueError("changed_score_requires_cited_event")
            elif item.previous_score is not None and item.previous_score != prior_score:
                raise ValueError("planner_previous_score_mismatch")

    @staticmethod
    def _reconcile_frontier(
        ledger: StrategyLedger,
        draft: PlannerDraft,
        proposals: tuple[FrontierProposal, ...],
        *,
        request_id: UUID,
        frontier_id: UUID,
        revision: Any,
        event_ref: UUID,
        call_metadata: PlanningCallMetadata,
        event_offset: int,
        prior_frontier: FrontierProjection | None,
    ) -> tuple[StrategyLedger, tuple[StrategyReconciledEventPayload, ...]]:
        """Allocate a complete no-loss ledger snapshot for the accepted frontier."""
        family_by_key = {item.runtime_key: item for item in ledger.families}
        variant_by_key = {item.runtime_key: item for item in ledger.variants}
        draft_by_family: dict[str, list[tuple[Any, FrontierProposal]]] = {}
        for item, proposal in zip(draft.proposals, proposals, strict=True):
            draft_by_family.setdefault(item.family_runtime_key, []).append((item, proposal))

        families = dict(family_by_key)
        variants = dict(variant_by_key)
        for family_key, entries in draft_by_family.items():
            prior = families.get(family_key)
            variant_ids = set(() if prior is None else prior.variant_ids)
            variant_ids.update(proposal.variant_id for _, proposal in entries)
            first = entries[0][1]
            families[family_key] = StrategyFamilyState(
                family_id=first.family_id,
                runtime_key=family_key,
                status=StrategyStatus.AVAILABLE,
                variant_ids=tuple(sorted(variant_ids, key=str)),
            )
            for item, proposal in entries:
                variants[item.variant_runtime_key] = ExecutionVariantState(
                    variant_id=proposal.variant_id,
                    family_id=proposal.family_id,
                    runtime_key=item.variant_runtime_key,
                    status=item.status,
                )
        resulting = StrategyLedger(
            families=tuple(sorted(families.values(), key=lambda item: item.runtime_key)),
            variants=tuple(sorted(variants.values(), key=lambda item: item.runtime_key)),
            archive=ledger.archive,
        )
        resulting_digest = ledger_digest(resulting)
        reconciliation_id = uuid5(frontier_id, "reconciliation")
        records: list[tuple[StrategyReconciliationEventOperation, Any]] = []
        proposal_by_family = {proposal.family_id: proposal for proposal in proposals}
        proposal_by_variant = {proposal.variant_id: proposal for proposal in proposals}
        draft_by_variant_id = {
            proposal.variant_id: item
            for item, proposal in zip(draft.proposals, proposals, strict=True)
        }
        prior_by_family = (
            {}
            if prior_frontier is None
            else {proposal.family_id: proposal for proposal in prior_frontier.proposals}
        )
        prior_by_variant = (
            {}
            if prior_frontier is None
            else {proposal.variant_id: proposal for proposal in prior_frontier.proposals}
        )
        for family in resulting.families:
            proposal = proposal_by_family.get(family.family_id) or prior_by_family.get(
                family.family_id
            )
            records.append(
                (
                    StrategyReconciliationEventOperation(
                        operation_id=uuid5(reconciliation_id, f"family:{family.family_id}"),
                        operation="retain",
                        family_id=family.family_id,
                        reason="Retain the complete accepted strategy family.",
                    ),
                    StrategyFamilyEventRecord(
                        family_id=family.family_id,
                        stable_key=family.runtime_key,
                        title=proposal.title if proposal else family.runtime_key,
                        strategic_intent=(
                            proposal.rationale if proposal else "Retain prior strategic intent."
                        ),
                        rationale=proposal.rationale if proposal else "Retain prior ledger state.",
                        score=proposal.score if proposal else 0,
                        confidence=(proposal.confidence / 100 if proposal else 0),
                        status=family.status.value,
                        variant_ids=family.variant_ids,
                        evidence_event_ids=(event_ref,),
                        last_material_revision=revision,
                    ),
                )
            )
        for variant in resulting.variants:
            proposal = proposal_by_variant.get(variant.variant_id) or prior_by_variant.get(
                variant.variant_id
            )
            proposal_draft = draft_by_variant_id.get(variant.variant_id)
            prior_variant = next(
                (item for item in ledger.variants if item.variant_id == variant.variant_id), None
            )
            status_changed = prior_variant is not None and prior_variant.status != variant.status
            evidence_event_ids = (
                proposal_draft.event_refs
                if proposal_draft is not None and proposal_draft.event_refs
                else (event_ref,)
            )
            records.append(
                (
                    StrategyReconciliationEventOperation(
                        operation_id=uuid5(reconciliation_id, f"variant:{variant.variant_id}"),
                        operation="update" if status_changed else "retain",
                        family_id=variant.family_id,
                        variant_id=variant.variant_id,
                        reason=(
                            proposal_draft.score_explanation
                            if status_changed
                            and proposal_draft is not None
                            and proposal_draft.score_explanation is not None
                            else "Retain the complete accepted execution variant."
                        ),
                        evidence_event_ids=evidence_event_ids if status_changed else (),
                    ),
                    ExecutionVariantEventRecord(
                        variant_id=variant.variant_id,
                        family_id=variant.family_id,
                        stable_key=variant.runtime_key,
                        title=proposal.title if proposal else variant.runtime_key,
                        strategic_intent=(
                            proposal.rationale if proposal else "Retain prior execution intent."
                        ),
                        rationale=proposal.rationale if proposal else "Retain prior ledger state.",
                        score=proposal.score if proposal else 0,
                        confidence=(proposal.confidence / 100 if proposal else 0),
                        status=variant.status.value,
                        attempts=AttemptAggregateEventRecord(
                            total_count=0,
                            history_digest=variant.historical_attempt_digest,
                        ),
                        evidence_event_ids=evidence_event_ids,
                        last_material_revision=revision,
                    ),
                )
            )
        sources = tuple(
            StrategyReconciledSource(
                local_id=f"reconcile-{ordinal:03d}",
                request_id=request_id,
                frontier_id=frontier_id,
                reconciliation_id=reconciliation_id,
                item_ordinal=ordinal,
                item_count=len(records),
                input_ledger_digest=ledger_digest(ledger),
                resulting_ledger_digest=resulting_digest,
                operation=operation,
                resulting_snapshot=snapshot,
            )
            for ordinal, (operation, snapshot) in enumerate(records, start=1)
        )
        bindings = tuple(
            LocalEventIdBinding(
                local_id=source.local_id,
                event_id=uuid5(
                    frontier_id,
                    f"event:{event_offset + ordinal}:strategy_reconciled",
                ),
            )
            for ordinal, source in enumerate(sources, start=1)
        )
        reconciliation = StrategyReconciliation(
            input_family_ids=tuple(
                sorted((item.family_id for item in resulting.families), key=str)
            ),
            input_variant_ids=tuple(
                sorted((item.variant_id for item in resulting.variants), key=str)
            ),
            retained_family_ids=tuple(
                sorted((item.family_id for item in resulting.families), key=str)
            ),
            retained_variant_ids=tuple(
                sorted((item.variant_id for item in resulting.variants), key=str)
            ),
            items=tuple(
                StrategyReconciliationItem(
                    request_id=source.request_id,
                    frontier_id=source.frontier_id,
                    reconciliation_id=source.reconciliation_id,
                    item_ordinal=source.item_ordinal,
                    item_count=source.item_count,
                    input_ledger_digest=source.input_ledger_digest,
                    resulting_ledger_digest=source.resulting_ledger_digest,
                    operation=source.operation,
                    resulting_snapshot=source.resulting_snapshot,
                )
                for source in sources
            ),
        )
        valid_events = tuple(sorted({event_ref, *(item.event_id for item in bindings)}, key=str))
        payloads = payloads_from_reconciliation(
            StrategyReconciliationEventConversion(
                local_event_bindings=bindings,
                valid_event_ids=valid_events,
                valid_evidence_ids=(),
                valid_family_ids=tuple(
                    sorted((item.family_id for item in resulting.families), key=str)
                ),
                valid_variant_ids=tuple(
                    sorted((item.variant_id for item in resulting.variants), key=str)
                ),
                sources=sources,
                reconciliation=reconciliation,
                call_metadata=call_metadata,
            )
        )
        return resulting, tuple(
            payload for payload in payloads if isinstance(payload, StrategyReconciledEventPayload)
        )

    def _reactivate_archive_candidates(self, engagement_id, *, snapshot, situation, replay) -> None:
        candidates = select_reactivation_candidates(replay.archive_records, situation)
        request_id = uuid5(NAMESPACE_URL, f"sedna-reactivation:{situation.state_digest}")
        batch_id = uuid5(request_id, "batch")
        remaining = tuple(record for record in replay.archive_records if record not in candidates)
        resulting_digest = archive_digest(remaining)
        archive_events = {
            event.payload.archive_record.archive_entry_id: event.event_id
            for event in snapshot.events
            if isinstance(event.payload, StrategyArchivedEventPayload)
        }
        bindings = tuple(
            LocalEventIdBinding(
                local_id=f"reactivate-{ordinal:03d}",
                event_id=uuid5(batch_id, f"event:{ordinal}"),
            )
            for ordinal in range(1, len(candidates) + 1)
        )
        sources = tuple(
            StrategyReactivatedSource(
                local_id=binding.local_id,
                request_id=request_id,
                reactivation_batch_id=batch_id,
                entry_ordinal=ordinal,
                entry_count=len(candidates),
                source_archive_event_id=archive_events[record.archive_entry_id],
                triggering_event_ids=(snapshot.events[-1].event_id,),
                matched_predicate_ids=matching_retry_predicate_ids(record, situation),
                prior_archive_entry_digest=record.archive_entry_digest,
                resulting_archive_digest=resulting_digest,
                restored_snapshot=record.snapshot.model_copy(update={"status": "available"}),
            )
            for ordinal, (binding, record) in enumerate(
                zip(bindings, candidates, strict=True), start=1
            )
        )
        transitions = tuple(
            StrategyReactivationTransition(
                family_id=source.restored_snapshot.family_id,
                event_id=binding.event_id,
                rationale="Reactivation follows its matched typed retry predicate.",
                request_id=source.request_id,
                reactivation_batch_id=source.reactivation_batch_id,
                entry_ordinal=source.entry_ordinal,
                entry_count=source.entry_count,
                source_archive_event_id=source.source_archive_event_id,
                triggering_event_ids=source.triggering_event_ids,
                matched_predicate_ids=source.matched_predicate_ids,
                prior_archive_entry_digest=source.prior_archive_entry_digest,
                resulting_archive_digest=source.resulting_archive_digest,
                restored_snapshot=source.restored_snapshot,
            )
            for binding, source in zip(bindings, sources, strict=True)
        )
        family_ids = tuple(
            sorted(
                {
                    item.snapshot.family_id
                    for item in candidates
                    if isinstance(
                        item.snapshot, (StrategyFamilyEventRecord, ExecutionVariantEventRecord)
                    )
                },
                key=str,
            )
        )
        variant_ids = tuple(
            sorted(
                {
                    item.snapshot.variant_id
                    for item in candidates
                    if isinstance(item.snapshot, ExecutionVariantEventRecord)
                },
                key=str,
            )
        )
        conversion = StrategyReconciliationEventConversion(
            local_event_bindings=bindings,
            valid_event_ids=tuple(
                sorted(
                    {
                        *(event.event_id for event in snapshot.events),
                        *(binding.event_id for binding in bindings),
                    },
                    key=str,
                )
            ),
            valid_evidence_ids=(),
            valid_family_ids=family_ids,
            valid_variant_ids=variant_ids,
            sources=sources,
            reconciliation=StrategyReconciliation(
                input_family_ids=family_ids,
                input_variant_ids=variant_ids,
                retained_family_ids=family_ids,
                retained_variant_ids=variant_ids,
            ),
            call_metadata=PlanningCallMetadata(
                purpose="plan",
                provider="sedna",
                model="typed-reactivation",
                agent_id="planning-service",
                prompt_id="sedna-reactivation",
                prompt_version="1",
                response_schema_version="1",
                input_digest=situation.state_digest,
                input_tokens=0,
                output_tokens=0,
                elapsed_ms=0,
            ),
            reactivation_transitions=transitions,
        )
        payloads = payloads_from_reconciliation(conversion)
        items = tuple(
            PlanningEventCommitItem(
                event_id=binding.event_id,
                idempotency_key=f"reactivation:{request_id}:{ordinal}",
                payload=payload,
            )
            for ordinal, (binding, payload) in enumerate(
                zip(bindings, payloads, strict=True), start=1
            )
        )
        initial_archive = self._journal.load_strategy_archive(engagement_id)
        if initial_archive is None:
            raise RuntimeError("reactivation candidates require a cold archive projection")
        archive_commit = self._journal.commit_strategy_archive(
            engagement_id,
            schema_id="sedna.strategy-archive.v1",
            records=tuple(
                StrategyArchiveRecordDraft(
                    entry_id=record.archive_entry_id,
                    payload=record.model_dump(mode="json", warnings="error"),
                )
                for record in remaining
            ),
            expected_archive_revision=initial_archive.envelope.archive_revision,
            expected_journal_revision=snapshot.revision,
        )
        try:
            self._commit_planning_events(
                engagement_id,
                items,
                operation_id=request_id,
                expected_revision=snapshot.revision,
            )
        except Exception:
            if self._planning_batch_durability(engagement_id, items) is False:
                self._journal.rollback_strategy_archive(
                    engagement_id,
                    failed_archive_revision=archive_commit.envelope.archive_revision,
                    expected_journal_revision=snapshot.revision,
                    previous=initial_archive,
                )
            raise

    @staticmethod
    def _archive_terminal_variants(
        ledger: StrategyLedger,
        existing_archive_records: tuple[ArchivedStrategyEventRecord, ...],
        draft: PlannerDraft,
        proposals: tuple[FrontierProposal, ...],
        reconciliation_payloads: tuple[StrategyReconciledEventPayload, ...],
        *,
        request_id: UUID,
        frontier_id: UUID,
        revision: Any,
        archive_event_offset: int,
    ) -> tuple[StrategyLedger, tuple[StrategyArchivedEventPayload, ...]]:
        """Move explicitly terminal variants into the cold archive in the accepted transaction."""
        terminal = [
            (item, proposal)
            for item, proposal in zip(draft.proposals, proposals, strict=True)
            if item.status in {StrategyStatus.BLOCKED, StrategyStatus.EXHAUSTED}
        ]
        if not terminal:
            return ledger, ()
        if len(existing_archive_records) + len(terminal) > 16:
            raise ValueError("strategy_archive_capacity_exceeded")

        reconciliation_offset = archive_event_offset - len(reconciliation_payloads)
        snapshots = {
            payload.resulting_snapshot.variant_id: (
                payload.item_ordinal,
                payload.resulting_snapshot,
            )
            for payload in reconciliation_payloads
            if isinstance(payload.resulting_snapshot, ExecutionVariantEventRecord)
        }
        archive_batch_id = uuid5(frontier_id, "archive")
        records: list[ArchivedStrategyEventRecord] = []
        event_ids: list[UUID] = []
        for ordinal, (item, proposal) in enumerate(terminal, start=1):
            reconciliation_ordinal, snapshot = snapshots[proposal.variant_id]
            source_event_id = uuid5(
                frontier_id,
                f"event:{reconciliation_offset + reconciliation_ordinal}:strategy_reconciled",
            )
            archive_event_id = uuid5(
                frontier_id,
                f"event:{archive_event_offset + ordinal}:strategy_archived",
            )
            predicates = []
            for predicate in item.retry_predicates:
                predicate_material = f"{predicate.kind.value}:{predicate.value}".encode()
                predicate_id = f"retry-{sha256(predicate_material).hexdigest()[:32]}"
                values: dict[str, Any] = {
                    "predicate_id": predicate_id,
                    "kind": predicate.kind.value,
                    "subject_ref": predicate.value,
                    "description": f"Retry when {predicate.kind.value} matches {predicate.value}.",
                }
                if predicate.kind.value == "fact_changed":
                    values["expected_value_digest"] = sha256(predicate.value.encode()).hexdigest()
                elif predicate.kind.value == "evidence_category_present":
                    values["expected_symbolic_value"] = predicate.value
                elif predicate.kind.value == "state_revision_after":
                    values["minimum_material_revision"] = revision
                predicates.append(RetryPredicateEventRecord(**values))
            record_values: dict[str, Any] = {
                "archive_entry_id": uuid5(archive_batch_id, f"entry:{proposal.variant_id}"),
                "snapshot": snapshot,
                "archive_reason": item.score_explanation or item.rationale,
                "retry_predicates": tuple(predicates),
                "archive_summary": f"{item.title}: {item.rationale}",
                "archived_at_material_revision": revision,
                "source_reconciliation_event_id": source_event_id,
            }
            record_values["archive_entry_digest"] = sha256(
                json.dumps(
                    record_values,
                    default=lambda value: (
                        value.model_dump(mode="json")
                        if hasattr(value, "model_dump")
                        else str(value)
                    ),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            records.append(ArchivedStrategyEventRecord(**record_values))
            event_ids.append(archive_event_id)

        all_records = (*existing_archive_records, *records)
        resulting_archive_digest = archive_digest(all_records)
        payloads = tuple(
            StrategyArchivedEventPayload(
                request_id=request_id,
                archive_batch_id=archive_batch_id,
                entry_ordinal=ordinal,
                entry_count=len(records),
                archive_record=record,
                resulting_archive_digest=resulting_archive_digest,
            )
            for ordinal, record in enumerate(records, start=1)
        )
        archived_variant_ids = {proposal.variant_id for _, proposal in terminal}
        variants = tuple(
            item for item in ledger.variants if item.variant_id not in archived_variant_ids
        )
        families = tuple(
            family.model_copy(
                update={
                    "variant_ids": tuple(
                        variant_id
                        for variant_id in family.variant_ids
                        if variant_id not in archived_variant_ids
                    )
                }
            )
            for family in ledger.families
        )
        archive = (
            *ledger.archive,
            *(
                ArchivedStrategyState(
                    family_id=record.snapshot.family_id,
                    summary=record.archive_summary,
                    archived_event_id=event_id,
                )
                for record, event_id in zip(records, event_ids, strict=True)
            ),
        )
        return StrategyLedger(families=families, variants=variants, archive=archive), payloads

    @staticmethod
    def _planning_call_metadata(
        purpose: Literal["plan", "critic", "repair"], input_digest: str, completion: Any
    ) -> PlanningCallMetadataEventRecord:
        prompt_id, prompt_version = {
            "plan": (PLANNER_PROMPT_ID, PLANNER_PROMPT_VERSION),
            "critic": (PLANNER_CRITIC_PROMPT_ID, PLANNER_CRITIC_PROMPT_VERSION),
            "repair": (PLANNER_REPAIR_PROMPT_ID, PLANNER_REPAIR_PROMPT_VERSION),
        }[purpose]
        return PlanningCallMetadataEventRecord(
            purpose=purpose,
            provider=completion.provider,
            model=completion.model,
            agent_id=completion.agent_id,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            response_schema_version="1",
            input_digest=input_digest,
            input_tokens=completion.usage.input_tokens,
            output_tokens=completion.usage.output_tokens,
            elapsed_ms=0,
        )

    def record_research_observations(
        self,
        engagement_id: UUID,
        *,
        conversion: ResearchEventConversion,
        expected_revision: JournalRevision,
    ) -> SituationProjection:
        """Validate and atomically append one closed research event conversion."""
        snapshot = self._journal.load_snapshot(engagement_id)
        allocated_event_ids = tuple(binding.event_id for binding in conversion.local_event_bindings)
        existing_event_ids = tuple(event.event_id for event in snapshot.events)
        if set(allocated_event_ids) & set(existing_event_ids):
            raise ValueError("research_event_id_already_exists")
        evidence_ids = tuple(
            sorted(
                {
                    event.payload.evidence.evidence_id
                    for event in snapshot.events
                    if isinstance(event.payload, EvidenceAttachedPayload)
                }
            )
        )
        source_ids = tuple(sorted({draft.source_id for draft in conversion.research_sources}))
        validated = ResearchEventConversion.model_validate(
            {
                **conversion.model_dump(mode="python", warnings="error"),
                "valid_event_ids": tuple(
                    sorted((*existing_event_ids, *allocated_event_ids), key=str)
                ),
                "valid_evidence_ids": evidence_ids,
                "valid_source_ids": source_ids,
            }
        )
        payloads = payloads_from_research_observations(validated)
        binding_by_local_id = {
            binding.local_id: binding.event_id for binding in validated.local_event_bindings
        }
        operation_id = uuid5(
            NAMESPACE_URL,
            f"sedna:research:{engagement_id}:{validated.call_metadata.input_digest}",
        )
        committed = self._commit_planning_events(
            engagement_id,
            tuple(
                PlanningEventCommitItem(
                    event_id=binding_by_local_id[source.local_id],
                    idempotency_key=f"research:{operation_id}:{source.local_id}",
                    payload=payload,
                )
                for source, payload in zip(validated.sources, payloads, strict=True)
            ),
            operation_id=operation_id,
            expected_revision=expected_revision,
        )
        return SituationReducer.rebuild(committed.snapshot)

    def record_research_result(
        self,
        lane: ExecutionLaneKey,
        *,
        query_id: UUID,
        source_id: str,
        normalized_locator: str,
        content: bytes,
        media_type: str,
        evidence_ids: tuple[str, ...],
        tool_event_ids: tuple[UUID, ...],
        assessment: Literal["useful", "contradicted", "stale", "irrelevant", "ambiguous"],
        confidence: float,
        summary: str,
        related_event_ids: tuple[UUID, ...],
        suggested_registry_status: (
            Literal["consulted", "useful", "contradicted", "stale"] | None
        ) = None,
    ) -> SituationProjection:
        """Validate one host-provided research result and append its typed events."""
        resolution = self._journal.resolve_lane_binding(lane)
        if resolution.mode != "exact" or resolution.engagement_id is None:
            raise ValueError("engagement_binding_required")
        engagement_id = resolution.engagement_id
        snapshot = self._journal.load_snapshot(engagement_id)
        events_by_id = {event.event_id: event for event in snapshot.events}

        matching_queries = tuple(
            event
            for event in snapshot.events
            if isinstance(event.payload, ResearchQueryProposedEventPayload)
            and event.payload.query_id == query_id
        )
        if len(matching_queries) != 1:
            raise ValueError("research_query_not_found")
        query = matching_queries[0].payload
        assert isinstance(query, ResearchQueryProposedEventPayload)
        policy_decision, _, normalized_query = self.evaluate_research_query(
            query.normalized_query,
            protected_aliases=tuple(
                sorted({*self._research_aliases, snapshot.manifest.display_name})
            ),
            known_flag_values=self._known_flag_values,
        )
        if (
            query.policy_decision != "allowed"
            or policy_decision != "allowed"
            or normalized_query != query.normalized_query
        ):
            raise ValueError("research_query_not_allowed")
        if query.candidate_source_ids and source_id not in query.candidate_source_ids:
            raise ValueError("research_source_not_allowed_by_query")

        if evidence_ids != tuple(sorted(set(evidence_ids))):
            raise ValueError("research_evidence_ids_not_sorted_unique")
        if tool_event_ids != tuple(sorted(set(tool_event_ids), key=str)):
            raise ValueError("research_tool_event_ids_not_sorted_unique")
        if related_event_ids != tuple(sorted(set(related_event_ids), key=str)):
            raise ValueError("research_related_event_ids_not_sorted_unique")
        if not related_event_ids or any(
            event_id not in events_by_id for event_id in related_event_ids
        ):
            raise ValueError("research_related_event_not_in_snapshot")

        protected_values = tuple(value for value in self._known_flag_values if value)
        if any(
            protected in value
            for protected in protected_values
            for value in (source_id, normalized_locator, summary)
        ) or any(protected.encode("utf-8") in content for protected in protected_values):
            raise ValueError("protected_research_value")

        consultation = ResearchSourceConsultation(
            query_id=query_id,
            source_id=source_id,
            normalized_locator=normalized_locator,
            content=content,
            media_type=media_type,
            evidence_ids=evidence_ids,
            tool_event_ids=tool_event_ids,
        )
        attachment_by_evidence_id = {
            event.payload.evidence.evidence_id: event
            for event in snapshot.events
            if isinstance(event.payload, EvidenceAttachedPayload)
            and event.payload.evidence.evidence_id in evidence_ids
            and event.lane == lane
        }
        attachment_events = tuple(
            attachment_by_evidence_id[evidence_id]
            for evidence_id in evidence_ids
            if evidence_id in attachment_by_evidence_id
        )
        if len(attachment_events) != len(evidence_ids) or any(
            event.payload.evidence.media_type != media_type for event in attachment_events
        ):
            raise ValueError("research_evidence_not_in_lane")
        if not any(
            self._journal.read_evidence_slice(
                engagement_id,
                event.payload.evidence.evidence_id,
                offset=0,
                limit=len(content) + 1,
            ).data
            == content
            for event in attachment_events
        ):
            raise ValueError("research_content_not_in_evidence")
        tool_events = tuple(events_by_id.get(event_id) for event_id in tool_event_ids)
        if not tool_events or any(
            event is None
            or event.lane != lane
            or not isinstance(event.payload, ToolCallCompletedPayload)
            or event.payload.technical_status != "returned"
            for event in tool_events
        ):
            raise ValueError("research_tool_result_not_in_lane")

        input_digest = sha256(
            json.dumps(
                {
                    "query_id": str(query_id),
                    "source_id": source_id,
                    "normalized_locator": normalized_locator,
                    "content_sha256": sha256(content).hexdigest(),
                    "media_type": media_type,
                    "evidence_ids": evidence_ids,
                    "tool_event_ids": tuple(map(str, tool_event_ids)),
                    "assessment": assessment,
                    "confidence": confidence,
                    "summary": summary,
                    "related_event_ids": tuple(map(str, related_event_ids)),
                    "suggested_registry_status": suggested_registry_status,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        consulted_event_id = uuid5(query_id, f"research-result:{input_digest}:consulted")
        assessed_event_id = uuid5(query_id, f"research-result:{input_digest}:assessed")
        assessment_related_ids = tuple(sorted({consulted_event_id, *related_event_ids}, key=str))
        assessment_audit = ResearchSourceAssessmentAudit(
            query_id=query_id,
            source_id=source_id,
            consulted_event_id=consulted_event_id,
            assessment=assessment,
            confidence=confidence,
            summary=summary,
            related_event_ids=assessment_related_ids,
            suggested_registry_status=suggested_registry_status,
        )
        conversion = ResearchEventConversion(
            local_event_bindings=(
                LocalEventIdBinding(local_id="assessed", event_id=assessed_event_id),
                LocalEventIdBinding(local_id="consulted", event_id=consulted_event_id),
            ),
            valid_event_ids=tuple(
                sorted((*events_by_id, consulted_event_id, assessed_event_id), key=str)
            ),
            valid_evidence_ids=evidence_ids,
            valid_source_ids=(source_id,),
            sources=(
                ResearchSourceConsultedSource(local_id="consulted", **consultation.model_dump()),
                ResearchSourceAssessedSource(local_id="assessed", **assessment_audit.model_dump()),
            ),
            research_sources=(
                ResearchSourceObservationDraft(
                    source_id=source_id,
                    assessment={
                        "useful": "useful",
                        "contradicted": "not_useful",
                        "stale": "not_useful",
                        "irrelevant": "not_useful",
                        "ambiguous": "inconclusive",
                    }[assessment],
                    event_ids=tuple(
                        sorted((event.event_id for event in attachment_events), key=str)
                    ),
                ),
            ),
            research_consultations=(consultation,),
            research_assessments=(assessment_audit,),
            call_metadata=PlanningCallMetadata(
                purpose="observe",
                provider="host",
                model="research-result",
                agent_id="planning-service",
                prompt_id="research-result",
                prompt_version="1",
                response_schema_version="1",
                input_digest=input_digest,
                input_tokens=0,
                output_tokens=0,
                elapsed_ms=0,
            ),
        )
        return self.record_research_observations(
            engagement_id,
            conversion=conversion,
            expected_revision=snapshot.revision,
        )

    def load_situation(self, engagement_id: UUID) -> SituationProjection:
        snapshot = self._journal.load_snapshot(engagement_id)
        try:
            cached = self._journal.load_projection(
                engagement_id,
                "state",
                SituationProjection,
            )
        except Exception:
            cached = None
        if (
            cached is not None
            and cached.engagement_id == engagement_id
            and cached.authoritative_journal_revision == snapshot.revision
        ):
            return cached
        situation = SituationReducer.rebuild(snapshot)
        self._journal.commit_projection(
            engagement_id,
            "state",
            situation,
            expected_revision=snapshot.revision,
        )
        return situation

    def load_ledger(self, engagement_id: UUID) -> StrategyLedger:
        """Load or rebuild the bounded hot ledger; the journal remains authority."""
        snapshot = self._journal.load_snapshot(engagement_id)
        try:
            cached = self._journal.load_projection(engagement_id, "strategy-ledger", StrategyLedger)
        except Exception:
            cached = None
        if cached is not None:
            # A projection cannot carry its own authority in the legacy data-only ledger model;
            # rebuild to avoid accepting a stale hot view after journal advancement.
            rebuilt = StrategyLedgerReducer.rebuild(snapshot)
            if cached == rebuilt:
                return cached
        ledger = StrategyLedgerReducer.rebuild(snapshot)
        self._journal.commit_projection(
            engagement_id,
            "strategy-ledger",
            ledger,
            expected_revision=snapshot.revision,
        )
        return ledger

    def settle_pending_evidence(
        self,
        engagement_id: UUID,
        *,
        reason: SettlementReason,
    ) -> SettlementResult:
        try:
            # An extractor runs outside repository locks.  A single CAS retry is
            # therefore expected; deterministic event IDs make it non-duplicating.
            for attempt in range(2):
                try:
                    return self._settle_pending_evidence(
                        engagement_id, reason=reason, remaining_slice_budget=64
                    )
                except RevisionConflictError:
                    if attempt:
                        raise
            raise AssertionError("unreachable")
        except Exception as exc:
            if "terminal_reconciliation_failed" in str(exc):
                failure_code = "terminal_reconciliation_failed"
            elif isinstance(exc, RevisionConflictError):
                failure_code = "concurrent_state_change"
            elif isinstance(exc, _EvidenceReadError):
                failure_code = "evidence_read_failed"
            elif isinstance(exc, _JournalAppendError):
                failure_code = "journal_append_failed"
            elif isinstance(exc, ValueError):
                failure_code = "invalid_extractor_output"
            else:
                failure_code = "extractor_unavailable"
            try:
                situation = self.load_situation(engagement_id)
            except Exception:
                return FailedSettlementResult(
                    engagement_id=engagement_id,
                    reason=reason,
                    authoritative_journal_revision=None,
                    situation=None,
                    failure_code="journal_unavailable",
                    failure_summary="The engagement journal is unavailable",
                    all_required_proofs_satisfied=False,
                    possible_terminal_evidence=False,
                )
            required_proof_ids = tuple(
                item.proof_requirement_id for item in situation.objective_progress.requirements
            )
            return FailedSettlementResult(
                engagement_id=engagement_id,
                reason=reason,
                authoritative_journal_revision=situation.authoritative_journal_revision,
                situation=situation,
                required_proof_ids=required_proof_ids,
                failure_code=failure_code,
                failure_summary="Evidence settlement could not complete safely",
                all_required_proofs_satisfied=self._proofs_satisfied(situation, required_proof_ids),
                possible_terminal_evidence=False,
            )

    def _settle_pending_evidence(
        self,
        engagement_id: UUID,
        *,
        reason: SettlementReason,
        remaining_slice_budget: int,
    ) -> SettlementResult:
        snapshot = self._journal.load_snapshot(engagement_id)
        descriptors = self._all_evidence_descriptors(engagement_id, snapshot.revision)
        completed_subjects: set[tuple[UUID, str]] = set()
        covered_by_subject: dict[tuple[UUID, str], list[tuple[int, int]]] = {}
        for event in snapshot.events:
            if isinstance(event.payload, InterpretationSucceededEventPayload):
                key = (event.payload.attachment_event_id, event.payload.evidence_id)
                covered_by_subject.setdefault(key, []).extend(
                    (item.start, item.end) for item in event.payload.covered_slices
                )
            elif (
                isinstance(event.payload, InterpretationFailedEventPayload)
                and event.payload.failure_code == "unsupported_media"
            ):
                completed_subjects.add(
                    (event.payload.attachment_event_id, event.payload.evidence_id)
                )
        descriptor_by_subject = {
            (item.attachment_event_id, item.reference.evidence_id): item for item in descriptors
        }
        zero_byte_text = tuple(
            item
            for item in descriptors
            if item.reference.size == 0
            and item.reference.media_type in {"text/plain", "application/json"}
            and not any(
                isinstance(event.payload, InterpretationSucceededEventPayload)
                and event.payload.attachment_event_id == item.attachment_event_id
                and event.payload.evidence_id == item.reference.evidence_id
                for event in snapshot.events
            )
        )
        if zero_byte_text:
            items = []
            for descriptor in zero_byte_text[:MAX_PLANNING_EVENT_BATCH]:
                subject = (
                    f"{engagement_id}:{descriptor.attachment_event_id}:"
                    f"{descriptor.reference.evidence_id}"
                )
                input_digest = sha256(f"empty:{subject}".encode()).hexdigest()
                event_id = uuid5(NAMESPACE_URL, f"sedna:empty-interpreted:{subject}")
                items.append(
                    PlanningEventCommitItem(
                        event_id=event_id,
                        idempotency_key=f"planning:empty-interpreted:{input_digest}",
                        payload=InterpretationSucceededEventPayload(
                            interpretation_id=uuid5(
                                NAMESPACE_URL, f"sedna:empty-interpretation:{subject}"
                            ),
                            attachment_event_id=descriptor.attachment_event_id,
                            evidence_id=descriptor.reference.evidence_id,
                            covered_slices=(),
                            emitted_event_ids=(),
                            call_metadata=PlanningCallMetadataEventRecord(
                                purpose="observe",
                                provider="local",
                                model="empty-evidence",
                                agent_id="planning-service",
                                prompt_id=OBSERVATION_PROMPT_ID,
                                prompt_version=OBSERVATION_PROMPT_VERSION,
                                response_schema_version="1",
                                input_digest=input_digest,
                                input_tokens=0,
                                output_tokens=0,
                                elapsed_ms=0,
                            ),
                            call_input_digest=input_digest,
                            call_output_digest=sha256(b"{}").hexdigest(),
                        ),
                    )
                )
            committed = self._commit_planning_events(
                engagement_id,
                tuple(items),
                operation_id=uuid5(
                    NAMESPACE_URL,
                    "sedna:empty-settlement:" + ":".join(str(item.event_id) for item in items),
                ),
                expected_revision=snapshot.revision,
            )
            situation = SituationReducer.rebuild(committed.snapshot)
            self._journal.commit_projection(
                engagement_id,
                "state",
                situation,
                expected_revision=committed.snapshot.revision,
            )
            _, pending_total, _, _ = self._pending_inventory(engagement_id, committed.snapshot)
            if pending_total:
                return self._settle_pending_evidence(
                    engagement_id,
                    reason=reason,
                    remaining_slice_budget=remaining_slice_budget,
                )
            required_proof_ids = tuple(
                sorted(requirement.proof_id for requirement in snapshot.manifest.required_proofs)
            )
            situation = self._reconcile_terminal(
                engagement_id=engagement_id,
                situation=situation,
                requirement_ids=required_proof_ids,
                reason=reason,
            )
            return SettledSettlementResult(
                engagement_id=engagement_id,
                reason=reason,
                authoritative_journal_revision=situation.authoritative_journal_revision,
                situation=situation,
                required_proof_ids=required_proof_ids,
                all_required_proofs_satisfied=self._proofs_satisfied(situation, required_proof_ids),
                possible_terminal_evidence=self._possible_terminal_evidence(committed.snapshot),
            )
        next_offset_by_subject: dict[tuple[UUID, str], int] = {}
        for key, descriptor in descriptor_by_subject.items():
            cursor = 0
            for start, end in sorted(covered_by_subject.get(key, ())):
                if start > cursor:
                    break
                cursor = max(cursor, end)
            if cursor >= descriptor.reference.size and (
                descriptor.reference.size > 0
                or descriptor.reference.media_type in {"text/plain", "application/json"}
            ):
                completed_subjects.add(key)
            else:
                next_offset_by_subject[key] = cursor
        last_attempt_sequence: dict[tuple[UUID, str], int] = {}
        for event in snapshot.events:
            if isinstance(
                event.payload,
                (InterpretationSucceededEventPayload, InterpretationFailedEventPayload),
            ):
                last_attempt_sequence[
                    (event.payload.attachment_event_id, event.payload.evidence_id)
                ] = event.sequence
        attachment_sequence = {event.event_id: event.sequence for event in snapshot.events}
        pending = tuple(
            sorted(
                (
                    descriptor
                    for descriptor in descriptors
                    if (descriptor.attachment_event_id, descriptor.reference.evidence_id)
                    not in completed_subjects
                ),
                key=lambda item: (
                    last_attempt_sequence.get(
                        (item.attachment_event_id, item.reference.evidence_id), 0
                    ),
                    attachment_sequence[item.attachment_event_id],
                    str(item.attachment_event_id),
                    str(item.reference.evidence_id),
                ),
            )
        )
        unsupported = tuple(
            descriptor
            for descriptor in pending
            if descriptor.reference.media_type not in {"text/plain", "application/json"}
        )
        if unsupported:
            items = []
            for descriptor in unsupported[:MAX_PLANNING_EVENT_BATCH]:
                subject = (
                    f"{engagement_id}:{descriptor.attachment_event_id}:"
                    f"{descriptor.reference.evidence_id}"
                )
                input_digest = sha256(subject.encode("utf-8")).hexdigest()
                items.append(
                    PlanningEventCommitItem(
                        event_id=uuid5(NAMESPACE_URL, f"sedna:unsupported:{subject}"),
                        idempotency_key=f"planning:unsupported-media:{input_digest}",
                        payload=InterpretationFailedEventPayload(
                            interpretation_id=uuid5(
                                NAMESPACE_URL, f"sedna:interpretation:{subject}"
                            ),
                            attachment_event_id=descriptor.attachment_event_id,
                            evidence_id=descriptor.reference.evidence_id,
                            attempted_slices=(),
                            failure_code="unsupported_media",
                            retryable=False,
                            safe_summary=(
                                "Binary media is not supported by the observation extractor"
                            ),
                            call_input_digest=input_digest,
                        ),
                    )
                )
            committed = self._commit_planning_events(
                engagement_id,
                tuple(items),
                operation_id=uuid5(
                    NAMESPACE_URL,
                    "sedna:settlement:" + ":".join(str(item.event_id) for item in items),
                ),
                expected_revision=snapshot.revision,
            )
            situation = SituationReducer.rebuild(committed.snapshot)
            self._journal.commit_projection(
                engagement_id,
                "state",
                situation,
                expected_revision=committed.snapshot.revision,
            )
            _, pending_total, _, _ = self._pending_inventory(engagement_id, committed.snapshot)
            if pending_total:
                return self._settle_pending_evidence(
                    engagement_id,
                    reason=reason,
                    remaining_slice_budget=remaining_slice_budget,
                )
            required_proof_ids = tuple(
                sorted(requirement.proof_id for requirement in snapshot.manifest.required_proofs)
            )
            situation = self._reconcile_terminal(
                engagement_id=engagement_id,
                situation=situation,
                requirement_ids=required_proof_ids,
                reason=reason,
            )
            return SettledSettlementResult(
                engagement_id=engagement_id,
                reason=reason,
                authoritative_journal_revision=situation.authoritative_journal_revision,
                situation=situation,
                required_proof_ids=required_proof_ids,
                all_required_proofs_satisfied=self._proofs_satisfied(situation, required_proof_ids),
                possible_terminal_evidence=self._possible_terminal_evidence(committed.snapshot),
            )
        if pending:
            descriptor = pending[0]
            subject_key = (
                descriptor.attachment_event_id,
                descriptor.reference.evidence_id,
            )
            slice_start = next_offset_by_subject[subject_key]
            try:
                evidence_slice = self._journal.read_evidence_slice(
                    engagement_id,
                    descriptor.reference.evidence_id,
                    offset=slice_start,
                    limit=EVIDENCE_SLICE_BYTES,
                )
            except OSError as exc:
                raise _EvidenceReadError from exc
            request = ObservationRequest(
                evidence_slices=(
                    ObservationEvidenceSlice(
                        event_id=descriptor.attachment_event_id,
                        evidence_id=descriptor.reference.evidence_id,
                        start=slice_start,
                        end=slice_start + len(evidence_slice.data),
                        media_type=descriptor.reference.media_type,
                        content=evidence_slice.data,
                    ),
                )
            )
            attachment = next(
                event
                for event in snapshot.events
                if event.event_id == descriptor.attachment_event_id
            )
            terminal = next(
                (
                    event
                    for event in snapshot.events
                    if event.sequence == attachment.sequence + 1
                    and event.lane == attachment.lane
                    and isinstance(event.payload, ToolCallCompletedPayload)
                ),
                None,
            )
            terminal_event_id = None if terminal is None else terminal.event_id
            completion = self._llm.complete(
                ObservationBatchDraft,
                instructions=(
                    f"{OBSERVATION_PROMPT}\n"
                    "Return the authoritative subject exactly. "
                    f"terminal_tool_event_id={terminal_event_id}."
                ),
                payload=request,
                purpose="sedna.planning.observe",
            )
            structured_terminal_claim = bool(
                completion.parsed.outcomes or completion.parsed.objective_proofs
            )
            subject = InterpretationSubject(
                attachment_event_id=descriptor.attachment_event_id,
                terminal_tool_event_id=(terminal_event_id if structured_terminal_claim else None),
                evidence_id=descriptor.reference.evidence_id,
            )
            if completion.parsed.subject != subject:
                raise ValueError("observation_subject_mismatch")
            protected_values = tuple(value for value in self._known_flag_values if value)
            public_observation_values = (
                *(draft.text for draft in completion.parsed.observations),
                *(draft.key for draft in completion.parsed.facets),
                *(draft.value for draft in completion.parsed.facets),
            )
            if any(
                protected in candidate
                for protected in protected_values
                for candidate in public_observation_values
            ):
                raise ValueError("protected_observation_value")
            input_digest = sha256(
                json.dumps(
                    request.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            metadata = PlanningCallMetadata(
                purpose="observe",
                provider=completion.provider,
                model=completion.model,
                agent_id=completion.agent_id,
                prompt_id=OBSERVATION_PROMPT_ID,
                prompt_version=OBSERVATION_PROMPT_VERSION,
                response_schema_version="1",
                input_digest=input_digest,
                input_tokens=completion.usage.input_tokens,
                output_tokens=completion.usage.output_tokens,
                elapsed_ms=0,
            )
            event_ref = EvidenceSliceEventRef(
                evidence_id=descriptor.reference.evidence_id,
                start=slice_start,
                end=slice_start + len(evidence_slice.data),
                sha256=sha256(evidence_slice.data).hexdigest(),
                media_type=descriptor.reference.media_type,
            )
            source_key = (
                f"{engagement_id}:{descriptor.attachment_event_id}:"
                f"{descriptor.reference.evidence_id}:{slice_start}:"
                f"{slice_start + len(evidence_slice.data)}"
            )
            success_event_id = uuid5(NAMESPACE_URL, f"sedna:interpreted:{source_key}")
            terminal_payload = None if terminal is None else terminal.payload
            if terminal_payload is not None and not isinstance(
                terminal_payload, ToolCallCompletedPayload
            ):
                raise RuntimeError("terminal event payload is not a tool completion")
            active_decision = next(
                (item for item in snapshot.state.active_decisions if item.lane == attachment.lane),
                None,
            )
            decision_id = None
            if structured_terminal_claim:
                if terminal_payload is None:
                    raise ValueError("structured_claim_requires_terminal_tool_completion")
                if attachment.lane is None or active_decision is None:
                    raise ValueError("structured_claim_requires_active_lane_decision")
                try:
                    decision_id = UUID(active_decision.decision_id.removeprefix("decision-"))
                except ValueError as exc:
                    raise ValueError("structured_claim_requires_typed_decision_id") from exc

            grounded_drafts = (
                *completion.parsed.observations,
                *completion.parsed.facets,
            )
            structured_drafts = (
                *grounded_drafts,
                *completion.parsed.outcomes,
                *completion.parsed.objective_proofs,
            )
            if any(
                descriptor.attachment_event_id not in draft.event_ids for draft in structured_drafts
            ):
                raise ValueError("structured_claim_requires_subject_attachment_grounding")
            emitted_sources: list[
                ObservationExtractedSource | OutcomeAssessedSource | ObjectiveProofObservedSource
            ] = []
            emitted_event_ids: list[UUID] = []
            local_bindings: list[LocalEventIdBinding] = []
            valid_snapshot_event_ids = {event.event_id for event in snapshot.events}
            for draft in grounded_drafts:
                if not set(draft.event_ids).issubset(valid_snapshot_event_ids):
                    raise ValueError("observation_event_reference_not_in_snapshot")
            observation_keys = tuple(
                (draft.kind, draft.text, draft.event_ids)
                for draft in completion.parsed.observations
            )
            facet_keys = tuple(
                (draft.key, draft.value, draft.event_ids) for draft in completion.parsed.facets
            )
            if len(observation_keys) != len(set(observation_keys)):
                raise ValueError("duplicate_observation")
            if len(facet_keys) != len(set(facet_keys)):
                raise ValueError("duplicate_facet_observation")
            for index, draft in enumerate(completion.parsed.observations):
                if draft.kind != "text":
                    raise ValueError("unsupported_generic_observation_kind")
                event_id = uuid5(success_event_id, f"observation:{index}")
                local_id = f"observation-{index}"
                emitted_event_ids.append(event_id)
                local_bindings.append(LocalEventIdBinding(local_id=local_id, event_id=event_id))
                emitted_sources.append(
                    ObservationExtractedSource(
                        local_id=local_id,
                        summary=draft.text,
                        observation=TextFactEventRecord(
                            subject="evidence observation",
                            value=draft.text,
                        ),
                        confidence=1.0,
                        evidence_slices=(event_ref,),
                    )
                )
            synthesized_facet_observations: list[ObservationDraft] = []
            facet_dimensions = {
                "os_family",
                "os_version",
                "cpu_architecture",
                "execution_environment",
                "service",
                "port",
                "protocol",
                "technology",
                "network_position",
                "security_control",
            }
            for index, draft in enumerate(completion.parsed.facets):
                summary = f"{draft.key}: {draft.value}"
                event_id = uuid5(success_event_id, f"facet:{index}")
                local_id = f"facet-{index}"
                emitted_event_ids.append(event_id)
                local_bindings.append(LocalEventIdBinding(local_id=local_id, event_id=event_id))
                synthesized_facet_observations.append(
                    ObservationDraft(
                        kind="facet",
                        text=summary,
                        event_ids=draft.event_ids,
                    )
                )
                emitted_sources.append(
                    ObservationExtractedSource(
                        local_id=local_id,
                        summary=summary,
                        observation=FacetObservationEventRecord(
                            dimension=(draft.key if draft.key in facet_dimensions else "custom"),
                            key=draft.key,
                            value=draft.value,
                            relation="observed",
                        ),
                        confidence=1.0,
                        evidence_slices=(event_ref,),
                    )
                )
            outcome_keys = tuple(
                (draft.category, draft.summary, draft.event_ids)
                for draft in completion.parsed.outcomes
            )
            if len(outcome_keys) != len(set(outcome_keys)):
                raise ValueError("duplicate_outcome_assessment")
            for index, draft in enumerate(completion.parsed.outcomes):
                if terminal is None or terminal_payload is None or decision_id is None:
                    raise ValueError("outcome_requires_terminal_tool_completion")
                event_id = uuid5(success_event_id, f"outcome:{index}")
                local_id = f"outcome-{index}"
                emitted_event_ids.append(event_id)
                local_bindings.append(LocalEventIdBinding(local_id=local_id, event_id=event_id))
                emitted_sources.append(
                    OutcomeAssessedSource(
                        local_id=local_id,
                        attachment_event_id=descriptor.attachment_event_id,
                        terminal_tool_event_id=terminal.event_id,
                        decision_id=decision_id,
                        tool_call_ids=(terminal_payload.call_id,),
                        category=draft.category,
                        summary=draft.summary,
                        strategic_impact=draft.summary,
                        evidence_ids=(descriptor.reference.evidence_id,),
                        source_event_ids=draft.event_ids,
                    )
                )
            proof_progress = {
                item.proof_requirement_id: item
                for item in SituationReducer.rebuild(snapshot).objective_progress.requirements
            }
            proof_requirement_ids = tuple(
                draft.proof_requirement_id for draft in completion.parsed.objective_proofs
            )
            if len(proof_requirement_ids) != len(set(proof_requirement_ids)):
                raise ValueError("duplicate_proof_requirement_in_observation")
            valid_proof_indexes: list[ProofIndexRecord] = []
            proof_candidate_admissions: list[ProofCandidateAdmission] = []
            for index, draft in enumerate(completion.parsed.objective_proofs):
                proof = proof_progress.get(draft.proof_requirement_id)
                if proof is None:
                    raise ValueError("proof_requirement_not_in_manifest")
                if proof.status != "pending":
                    raise ValueError("proof_requirement_already_assessed")
                if proof.generation_started_event_id is not None:
                    generation_started = next(
                        event
                        for event in snapshot.events
                        if event.event_id == proof.generation_started_event_id
                    )
                    if attachment.sequence <= generation_started.sequence:
                        raise ValueError("stale_proof_candidate_generation")
                if event_ref.sha256 in proof.rejected_value_sha256s:
                    raise ValueError("previously_rejected_proof_value")
                valid_proof_indexes.append(
                    ProofIndexRecord(
                        proof_requirement_id=draft.proof_requirement_id,
                        assessment_generation=proof.assessment_generation,
                        rejection_inventory_digest=sha256(
                            json.dumps(
                                proof.rejected_value_sha256s,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ).hexdigest(),
                    )
                )
                proof_candidate_admissions.append(
                    ProofCandidateAdmission(
                        proof_requirement_id=draft.proof_requirement_id,
                        assessment_generation=proof.assessment_generation,
                        candidate_sha256=event_ref.sha256,
                        decision="allowed",
                    )
                )
                event_id = uuid5(success_event_id, f"proof:{index}")
                local_id = f"proof-{index}"
                emitted_event_ids.append(event_id)
                local_bindings.append(LocalEventIdBinding(local_id=local_id, event_id=event_id))
                emitted_sources.append(
                    ObjectiveProofObservedSource(
                        local_id=local_id,
                        proof_requirement_id=draft.proof_requirement_id,
                        assessment_generation=proof.assessment_generation,
                        assessment=draft.assessment,
                        candidate_value=PrivateValueEventRecord(
                            evidence_slice=event_ref,
                            value_sha256=event_ref.sha256,
                        ),
                        confidence=1.0,
                        evidence_ids=(descriptor.reference.evidence_id,),
                        source_event_ids=draft.event_ids,
                    )
                )
            interpretation_source = InterpretationSucceededSource(
                local_id="interpreted",
                interpretation_id=uuid5(NAMESPACE_URL, f"sedna:interpretation:{source_key}"),
                attachment_event_id=descriptor.attachment_event_id,
                terminal_tool_event_id=(terminal_event_id if structured_terminal_claim else None),
                evidence_id=descriptor.reference.evidence_id,
                covered_slices=(event_ref,),
                emitted_event_ids=tuple(emitted_event_ids),
            )
            bindings = tuple(
                sorted(
                    (
                        *local_bindings,
                        LocalEventIdBinding(local_id="interpreted", event_id=success_event_id),
                    ),
                    key=lambda item: item.local_id,
                )
            )
            sources = (*emitted_sources, interpretation_source)
            conversion = ObservationEventConversion(
                batch=completion.parsed.model_copy(
                    update={
                        "observations": (
                            *completion.parsed.observations,
                            *synthesized_facet_observations,
                        )
                    }
                ),
                call_metadata=metadata,
                interpretation_audits=(
                    InterpretationAudit(
                        subject=subject,
                        call_metadata=metadata,
                        status="succeeded",
                    ),
                ),
                local_event_bindings=bindings,
                valid_event_ids=tuple(
                    sorted(
                        (
                            *(event.event_id for event in snapshot.events),
                            *emitted_event_ids,
                            success_event_id,
                        ),
                        key=str,
                    )
                ),
                valid_evidence_ids=(descriptor.reference.evidence_id,),
                valid_proof_indexes=tuple(
                    sorted(
                        valid_proof_indexes,
                        key=lambda item: (
                            item.proof_requirement_id,
                            item.assessment_generation,
                        ),
                    )
                ),
                proof_candidate_admissions=tuple(proof_candidate_admissions),
                evidence_slices=(
                    EvidenceSliceInput(
                        evidence_id=descriptor.reference.evidence_id,
                        start=slice_start,
                        end=slice_start + len(evidence_slice.data),
                        media_type=descriptor.reference.media_type,
                        content=evidence_slice.data,
                    ),
                ),
                sources=sources,
            )
            payloads = payloads_from_observation_batch(conversion)
            bindings_by_local_id = {binding.local_id: binding for binding in bindings}
            payload_bindings = tuple(bindings_by_local_id[source.local_id] for source in sources)
            committed = self._commit_planning_events(
                engagement_id,
                tuple(
                    PlanningEventCommitItem(
                        event_id=binding.event_id,
                        payload=payload,
                        idempotency_key=f"planning:{payload.kind}:{binding.event_id}",
                    )
                    for binding, payload in zip(payload_bindings, payloads, strict=True)
                ),
                operation_id=uuid5(NAMESPACE_URL, f"sedna:settlement:{success_event_id}"),
                expected_revision=snapshot.revision,
            )
            situation = SituationReducer.rebuild(committed.snapshot)
            self._journal.commit_projection(
                engagement_id,
                "state",
                situation,
                expected_revision=committed.snapshot.revision,
            )
            required_proof_ids = tuple(
                sorted(requirement.proof_id for requirement in snapshot.manifest.required_proofs)
            )
            pending_ranges, pending_total, inventory_digest, cursor = self._pending_inventory(
                engagement_id, committed.snapshot
            )
            if pending_total:
                if remaining_slice_budget > 1:
                    # Re-read authoritative pending state after every append: this both
                    # shares the 64-slice budget across subjects and avoids stale work.
                    return self._settle_pending_evidence(
                        engagement_id,
                        reason=reason,
                        remaining_slice_budget=remaining_slice_budget - 1,
                    )
                return IncompleteSettlementResult(
                    engagement_id=engagement_id,
                    reason=reason,
                    authoritative_journal_revision=situation.authoritative_journal_revision,
                    situation=situation,
                    required_proof_ids=required_proof_ids,
                    pending_ranges=pending_ranges,
                    pending_total_count=pending_total,
                    pending_inventory_sha256=inventory_digest,
                    next_pending_subject=cursor,
                    incomplete_reason="budget_exhausted",
                    all_required_proofs_satisfied=False,
                    possible_terminal_evidence=False,
                )
            situation = self._reconcile_terminal(
                engagement_id=engagement_id,
                situation=situation,
                requirement_ids=required_proof_ids,
                reason=reason,
            )
            return SettledSettlementResult(
                engagement_id=engagement_id,
                reason=reason,
                authoritative_journal_revision=situation.authoritative_journal_revision,
                situation=situation,
                required_proof_ids=required_proof_ids,
                all_required_proofs_satisfied=self._proofs_satisfied(situation, required_proof_ids),
                possible_terminal_evidence=self._possible_terminal_evidence(committed.snapshot),
            )
        situation = self.load_situation(engagement_id)
        required_proof_ids = tuple(
            sorted(requirement.proof_id for requirement in snapshot.manifest.required_proofs)
        )
        situation = self._reconcile_terminal(
            engagement_id=engagement_id,
            situation=situation,
            requirement_ids=required_proof_ids,
            reason=reason,
        )
        return NothingPendingSettlementResult(
            engagement_id=engagement_id,
            reason=reason,
            authoritative_journal_revision=situation.authoritative_journal_revision,
            situation=situation,
            required_proof_ids=required_proof_ids,
            all_required_proofs_satisfied=self._proofs_satisfied(situation, required_proof_ids),
            possible_terminal_evidence=self._possible_terminal_evidence(snapshot),
        )

    @staticmethod
    def _proofs_satisfied(situation: SituationProjection, requirement_ids: tuple[str, ...]) -> bool:
        progress = {
            item.proof_requirement_id: item.status
            for item in situation.objective_progress.requirements
        }
        return bool(requirement_ids) and all(
            progress.get(requirement_id) == "supported" for requirement_id in requirement_ids
        )

    def _commit_planning_events(
        self, engagement_id: UUID, items, *, operation_id, expected_revision
    ):
        try:
            return self._journal._issue_planning_event_commit_capability().commit_planning_events(
                engagement_id,
                items,
                operation_id=operation_id,
                expected_revision=expected_revision,
            )
        except OSError as exc:
            raise _JournalAppendError from exc

    def _planning_batch_durability(self, engagement_id: UUID, items) -> bool | None:
        """Prove a failed append absent before compensating its prepared archive."""
        try:
            durable_event_ids = {
                event.event_id for event in self._journal.load_snapshot(engagement_id).events
            }
        except Exception:
            return None
        expected_event_ids = {item.event_id for item in items}
        if expected_event_ids <= durable_event_ids:
            return True
        if expected_event_ids.isdisjoint(durable_event_ids):
            return False
        return None

    def _all_evidence_descriptors(self, engagement_id: UUID, revision: Any) -> tuple[Any, ...]:
        """Read every bounded M6A descriptor page; never truncate pending inventory at 256."""
        after_sequence = 0
        descriptors: list[Any] = []
        while True:
            page = self._journal.list_evidence_descriptors(
                engagement_id,
                after_sequence=after_sequence,
                through_revision=revision,
                limit=256,
            )
            descriptors.extend(page.items)
            if page.complete:
                return tuple(descriptors)
            if page.next_after_sequence <= after_sequence:
                raise ValueError("evidence_descriptor_pagination_stalled")
            after_sequence = page.next_after_sequence

    def _pending_inventory(
        self, engagement_id: UUID, snapshot: Any
    ) -> tuple[tuple[PendingEvidenceRange, ...], int, str, str | None]:
        """Derive the complete, paginated pending inventory exclusively from journal history."""
        covered: dict[tuple[UUID, str], list[tuple[int, int]]] = {}
        terminal: set[tuple[UUID, str]] = set()
        attempts: dict[tuple[UUID, str], int] = {}
        retryable_failures: set[tuple[UUID, str]] = set()
        for event in snapshot.events:
            if isinstance(event.payload, InterpretationSucceededEventPayload):
                key = (event.payload.attachment_event_id, event.payload.evidence_id)
                covered.setdefault(key, []).extend(
                    (slice_.start, slice_.end) for slice_ in event.payload.covered_slices
                )
                attempts[key] = event.sequence
                retryable_failures.discard(key)
            elif isinstance(event.payload, InterpretationFailedEventPayload):
                key = (event.payload.attachment_event_id, event.payload.evidence_id)
                attempts[key] = event.sequence
                if event.payload.retryable:
                    retryable_failures.add(key)
                else:
                    retryable_failures.discard(key)
                if event.payload.failure_code == "unsupported_media":
                    terminal.add(key)
        material: list[tuple[int, str, str, PendingEvidenceRange]] = []
        for descriptor in self._all_evidence_descriptors(engagement_id, snapshot.revision):
            key = (descriptor.attachment_event_id, descriptor.reference.evidence_id)
            if key in terminal or descriptor.reference.size == 0:
                continue
            cursor = 0
            for start, end in sorted(covered.get(key, ())):
                if start > cursor:
                    break
                cursor = max(cursor, end)
            if cursor >= descriptor.reference.size:
                continue
            pending = PendingEvidenceRange(
                evidence_id=descriptor.reference.evidence_id,
                attachment_event_id=descriptor.attachment_event_id,
                start=cursor,
                end=descriptor.reference.size,
                media_type=descriptor.reference.media_type,
                reason=(
                    "retryable_interpretation_failure"
                    if key in retryable_failures
                    else "budget_exhausted"
                ),
            )
            material.append(
                (
                    attempts.get(key, 0),
                    str(descriptor.attachment_event_id),
                    str(descriptor.reference.evidence_id),
                    pending,
                )
            )
        ordered = tuple(item[3] for item in sorted(material, key=lambda item: item[:3]))
        digest = sha256(
            json.dumps(
                [item.model_dump(mode="json") for item in ordered],
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        page = ordered[:512]
        cursor = None
        if len(ordered) > len(page):
            next_item = ordered[len(page)]
            identity = {
                "attachment_event_id": str(next_item.attachment_event_id),
                "terminal_tool_event_id": None,
                "evidence_id": next_item.evidence_id,
                "start": next_item.start,
            }
            cursor = (
                "pending-"
                + sha256(
                    json.dumps(identity, separators=(",", ":"), sort_keys=True).encode("utf-8")
                ).hexdigest()
            )
        return page, len(ordered), digest, cursor

    @staticmethod
    def _possible_terminal_evidence(snapshot: Any) -> bool:
        return any(
            getattr(event.payload, "possible_terminal_evidence", False) for event in snapshot.events
        )

    def _reconcile_terminal(
        self,
        *,
        engagement_id: UUID,
        situation: SituationProjection,
        requirement_ids: tuple[str, ...],
        reason: SettlementReason,
    ) -> SituationProjection:
        """Run the optional lifecycle seam after all journal locks are released."""
        if self._terminal_settlement_port is None or not requirement_ids:
            return situation
        all_satisfied = self._proofs_satisfied(situation, requirement_ids)
        reconciliation = self._terminal_settlement_port.reconcile(
            engagement_id=engagement_id,
            situation=situation,
            requirement_ids=requirement_ids,
            authoritative_revision=situation.authoritative_journal_revision,
            reason=reason,
            all_required_proofs_satisfied=all_satisfied,
        )
        post_port = self._journal.load_snapshot(engagement_id)
        if (
            reconciliation.action == "failed"
            or reconciliation.authoritative_journal_revision != post_port.revision
            or reconciliation.lifecycle_status != post_port.state.status
        ):
            raise ValueError("terminal_reconciliation_failed")
        rebound = SituationReducer.rebuild(post_port)
        self._journal.commit_projection(
            engagement_id,
            "state",
            rebound,
            expected_revision=post_port.revision,
        )
        return rebound
