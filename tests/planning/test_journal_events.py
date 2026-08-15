"""Closed, source-model planning journal conversion contracts."""

from __future__ import annotations

import json
from hashlib import sha256
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from sedna.engagement import EventPayload, PlanningGapRecordedEventPayload
from sedna.engagement.events import (
    AccessStateDeltaEventRecord,
    ArchivedStrategyEventRecord,
    EvidenceSliceEventRef,
    PrivateValueEventRecord,
    StrategyFamilyEventRecord,
    StrategyReconciliationEventOperation,
    TextFactEventRecord,
)
from sedna.planning import (
    FrontierProposalDraft,
    ObservationBatchDraft,
    ObservationDraft,
    ObservationEventConversion,
    PlannerDraft,
    PlanningAttemptEventConversion,
    PlanningCallMetadata,
    ResearchEventConversion,
    StrategyReconciliation,
    StrategyReconciliationEventConversion,
    payloads_from_observation_batch,
    payloads_from_planning_attempt,
    payloads_from_reconciliation,
    payloads_from_research_observations,
)
from sedna.planning.models import (
    FrontierCriticizedSource,
    FrontierProposedSource,
    FrontierRejectedSource,
    FrontierRepairedSource,
    HypothesisFormedSource,
    InterpretationAudit,
    InterpretationFailedSource,
    InterpretationSucceededSource,
    LocalEventIdBinding,
    MissingInformationIdentifiedSource,
    ObjectiveProofObservedSource,
    ObservationExtractedSource,
    OutcomeAssessedSource,
    PlannerCriticVerdict,
    PlannerProposalAudit,
    PlannerRejectionAudit,
    PlannerRepairAudit,
    PlanningEventSource,
    PlanningGap,
    PlanningGapRecordedSource,
    PlanRequestAudit,
    PlanRequestedSource,
    ProofCandidateAdmission,
    ProofIndexRecord,
    ResearchPolicyDecision,
    ResearchQueryProposedSource,
    ResearchSourceAssessedSource,
    ResearchSourceAssessmentAudit,
    ResearchSourceConsultation,
    ResearchSourceConsultedSource,
    ResearchSourceObservationDraft,
    StrategyArchivedSource,
    StrategyArchiveTransition,
    StrategyReactivatedSource,
    StrategyReactivationTransition,
    StrategyReconciledSource,
    StrategyReconciliationItem,
)

D = "a" * 64
EVIDENCE_ID = "evidence-sha256-" + "c" * 64
E0 = UUID("00000000-0000-0000-0000-000000000001")
REVISION = {"sequence": 1, "event_hash": D}
SCOPE_ID = "scope-" + "a" * 32


def _digest(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def metadata() -> PlanningCallMetadata:
    return PlanningCallMetadata(
        purpose="plan",
        provider="provider",
        model="model",
        agent_id="agent",
        prompt_id="prompt:plan",
        prompt_version="1",
        response_schema_version="1",
        input_digest=D,
        input_tokens=1,
        output_tokens=1,
        elapsed_ms=1,
    )


def reconciliation() -> StrategyReconciliation:
    return StrategyReconciliation(
        input_family_ids=(),
        input_variant_ids=(),
        retained_family_ids=(),
        retained_variant_ids=(),
    )


def gap() -> PlanningGapRecordedEventPayload:
    return PlanningGapRecordedEventPayload(
        code="critic_rejected",
        summary="no frontier",
        retryable=True,
        situation_digest=D,
        ledger_digest=D,
    )


def _slice() -> EvidenceSliceEventRef:
    return EvidenceSliceEventRef(
        evidence_id=EVIDENCE_ID,
        start=0,
        end=3,
        sha256=sha256(b"SSH").hexdigest(),
        media_type="text/plain",
    )


def _family() -> StrategyFamilyEventRecord:
    return StrategyFamilyEventRecord(
        family_id=UUID("00000000-0000-0000-0000-000000000101"),
        stable_key="family:ssh",
        title="SSH strategy",
        strategic_intent="Obtain valid access.",
        rationale="SSH is exposed.",
        score=80,
        confidence=0.8,
        status="available",
        last_material_revision=REVISION,
    )


def _frontier() -> dict[str, object]:
    return {
        "proposal_id": UUID("00000000-0000-0000-0000-000000000102"),
        "rank": 1,
        "family_id": _family().family_id,
        "title": "SSH strategy",
        "strategic_intent": "Obtain valid access.",
        "rationale": "SSH is exposed.",
        "score": 80,
        "confidence": 0.8,
        "expected_information_gain": "Confirm supported authentication paths.",
        "event_refs": (E0,),
        "scope_reference_ids": (SCOPE_ID,),
    }


def planning_event_cases() -> tuple[tuple[str, str, PlanningEventSource], ...]:
    evidence_slice = _slice()
    private_value = PrivateValueEventRecord(
        evidence_slice=evidence_slice, value_sha256=evidence_slice.sha256
    )
    archive_without_digest = {
        "archive_entry_id": UUID("00000000-0000-0000-0000-000000000103"),
        "snapshot": _family(),
        "archive_reason": "Superseded by new evidence.",
        "archive_summary": "Archive SSH strategy.",
        "archived_at_material_revision": REVISION,
        "source_reconciliation_event_id": E0,
    }
    archive = ArchivedStrategyEventRecord(**archive_without_digest, archive_entry_digest="0" * 64)
    archive = archive.model_copy(
        update={
            "archive_entry_digest": _digest(
                archive.model_dump(mode="json", exclude={"archive_entry_digest"})
            )
        }
    )
    operation = StrategyReconciliationEventOperation(
        operation_id=UUID("00000000-0000-0000-0000-000000000104"),
        operation="retain",
        family_id=_family().family_id,
        reason="Still applicable.",
    )
    sources: tuple[tuple[str, PlanningEventSource], ...] = (
        (
            "observation",
            ObservationExtractedSource(
                local_id="observation",
                summary="SSH is open.",
                observation=TextFactEventRecord(subject="ssh", value="SSH is open."),
                confidence=1.0,
                evidence_slices=(evidence_slice,),
                scope_reference_ids=(SCOPE_ID,),
            ),
        ),
        (
            "hypothesis",
            HypothesisFormedSource(
                local_id="hypothesis",
                statement="SSH credentials may be reusable.",
                confidence=0.5,
                supporting_event_ids=(E0,),
                scope_reference_ids=(SCOPE_ID,),
            ),
        ),
        (
            "missing",
            MissingInformationIdentifiedSource(
                local_id="missing",
                question="Which account can authenticate?",
                reason="No account is known.",
                importance=80,
                related_event_ids=(E0,),
                scope_reference_ids=(SCOPE_ID,),
            ),
        ),
        (
            "outcome",
            OutcomeAssessedSource(
                local_id="outcome",
                attachment_event_id=E0,
                terminal_tool_event_id=E0,
                tool_call_ids=("tool:1",),
                category="progress",
                summary="SSH is reachable.",
                strategic_impact="Authentication can be tested.",
                evidence_ids=(EVIDENCE_ID,),
                source_event_ids=(E0,),
            ),
        ),
        (
            "proof",
            ObjectiveProofObservedSource(
                local_id="proof",
                proof_requirement_id="user-flag",
                assessment_generation=1,
                assessment="supported",
                candidate_value=private_value,
                confidence=0.9,
                evidence_ids=(EVIDENCE_ID,),
                source_event_ids=(E0,),
            ),
        ),
        (
            "interpreted",
            InterpretationSucceededSource(
                local_id="interpreted",
                interpretation_id=UUID("00000000-0000-0000-0000-000000000105"),
                attachment_event_id=E0,
                evidence_id=EVIDENCE_ID,
                covered_slices=(evidence_slice,),
                emitted_event_ids=(E0,),
            ),
        ),
        (
            "interpretation_failed",
            InterpretationFailedSource(
                local_id="interpretation_failed",
                interpretation_id=UUID("00000000-0000-0000-0000-000000000106"),
                attachment_event_id=E0,
                evidence_id=EVIDENCE_ID,
                attempted_slices=(evidence_slice,),
                failure_code="llm_unavailable",
                retryable=True,
                safe_summary="The model is unavailable.",
            ),
        ),
        (
            "request",
            PlanRequestedSource(
                local_id="request",
                request_id=UUID("00000000-0000-0000-0000-000000000107"),
                lane_key="lane-" + "b" * 32,
                situation_digest=D,
                material_event_revision=REVISION,
                input_ledger_digest=D,
                canonical_revision=D,
                source_registry_digest=D,
                max_proposals=3,
            ),
        ),
        (
            "proposed",
            FrontierProposedSource(
                local_id="proposed",
                request_id=UUID("00000000-0000-0000-0000-000000000107"),
                frontier_id=UUID("00000000-0000-0000-0000-000000000108"),
                proposal_ordinal=1,
                proposal_count=1,
                proposal=_frontier(),
                situation_digest=D,
                input_ledger_digest=D,
                knowledge_context_digest=D,
            ),
        ),
        (
            "criticized",
            FrontierCriticizedSource(
                local_id="criticized",
                request_id=UUID("00000000-0000-0000-0000-000000000107"),
                frontier_id=UUID("00000000-0000-0000-0000-000000000108"),
                critic_pass=1,
                accepted=True,
                cited_event_ids=(E0,),
            ),
        ),
        (
            "repaired",
            FrontierRepairedSource(
                local_id="repaired",
                request_id=UUID("00000000-0000-0000-0000-000000000107"),
                frontier_id=UUID("00000000-0000-0000-0000-000000000108"),
                critic_event_id=E0,
                proposal_ordinal=1,
                proposal_count=1,
                proposal=_frontier(),
            ),
        ),
        (
            "rejected",
            FrontierRejectedSource(
                local_id="rejected",
                request_id=UUID("00000000-0000-0000-0000-000000000107"),
                frontier_id=UUID("00000000-0000-0000-0000-000000000108"),
                critic_event_ids=(E0,),
                reason_codes=("scope_mismatch",),
            ),
        ),
        (
            "gap",
            PlanningGapRecordedSource(
                local_id="gap",
                request_id=UUID("00000000-0000-0000-0000-000000000107"),
                code="critic_rejected",
                summary="No valid frontier.",
                retryable=True,
                situation_digest=D,
                ledger_digest=D,
                related_event_ids=(E0,),
            ),
        ),
        (
            "reconciled",
            StrategyReconciledSource(
                local_id="reconciled",
                request_id=UUID("00000000-0000-0000-0000-000000000107"),
                frontier_id=UUID("00000000-0000-0000-0000-000000000108"),
                reconciliation_id=UUID("00000000-0000-0000-0000-000000000109"),
                item_ordinal=1,
                item_count=1,
                input_ledger_digest=D,
                resulting_ledger_digest=D,
                operation=operation,
                resulting_snapshot=_family(),
            ),
        ),
        (
            "archived",
            StrategyArchivedSource(
                local_id="archived",
                request_id=UUID("00000000-0000-0000-0000-000000000107"),
                archive_batch_id=UUID("00000000-0000-0000-0000-000000000110"),
                entry_ordinal=1,
                entry_count=1,
                archive_record=archive,
                resulting_archive_digest=D,
            ),
        ),
        (
            "reactivated",
            StrategyReactivatedSource(
                local_id="reactivated",
                request_id=UUID("00000000-0000-0000-0000-000000000107"),
                reactivation_batch_id=UUID("00000000-0000-0000-0000-000000000111"),
                entry_ordinal=1,
                entry_count=1,
                source_archive_event_id=E0,
                triggering_event_ids=(E0,),
                matched_predicate_ids=("predicate:changed",),
                prior_archive_entry_digest=archive.archive_entry_digest,
                resulting_archive_digest=D,
                restored_snapshot=_family(),
            ),
        ),
        (
            "query",
            ResearchQueryProposedSource(
                local_id="query",
                query_id=UUID("00000000-0000-0000-0000-000000000112"),
                normalized_query="linux ssh",
                policy_decision="allowed",
                policy_version="policy:1",
                reason_codes=("in_scope",),
                related_event_ids=(E0,),
                candidate_source_ids=("source-1",),
            ),
        ),
        (
            "consulted",
            ResearchSourceConsultedSource(
                local_id="consulted",
                query_id=UUID("00000000-0000-0000-0000-000000000112"),
                source_id="source-1",
                normalized_locator="https://example.test/ssh",
                content=b"source-body",
                media_type="text/plain",
                evidence_ids=(EVIDENCE_ID,),
                tool_event_ids=(E0,),
            ),
        ),
        (
            "assessed",
            ResearchSourceAssessedSource(
                local_id="assessed",
                query_id=UUID("00000000-0000-0000-0000-000000000112"),
                source_id="source-1",
                consulted_event_id=E0,
                assessment="useful",
                confidence=0.8,
                summary="The source confirms SSH behavior.",
                related_event_ids=(E0,),
                suggested_registry_status="useful",
            ),
        ),
    )
    families = {
        "observation_extracted",
        "hypothesis_formed",
        "missing_information_identified",
        "outcome_assessed",
        "objective_proof_observed",
        "interpretation_succeeded",
        "interpretation_failed",
    }
    planning = {
        "plan_requested",
        "frontier_proposed",
        "frontier_criticized",
        "frontier_repaired",
        "frontier_rejected",
        "planning_gap_recorded",
    }
    reconciliation_kinds = {"strategy_reconciled", "strategy_archived", "strategy_reactivated"}
    return tuple(
        (
            source.kind,
            "observation"
            if source.kind in families
            else "planning"
            if source.kind in planning
            else "reconciliation"
            if source.kind in reconciliation_kinds
            else "research",
            source,
        )
        for _, source in sources
    )


def _conversion(family: str, source: PlanningEventSource):
    allocated = UUID(int=sum(source.local_id.encode("utf-8")) + 2)
    valid_event_ids = tuple(sorted((E0, allocated), key=str))
    common = {
        "call_metadata": metadata(),
        "local_event_bindings": (
            LocalEventIdBinding(local_id=source.local_id, event_id=allocated),
        ),
        "valid_event_ids": valid_event_ids,
        "valid_evidence_ids": (EVIDENCE_ID,),
        "valid_scope_reference_ids": (SCOPE_ID,),
        "valid_proof_indexes": (
            ProofIndexRecord(
                proof_requirement_id="user-flag",
                assessment_generation=1,
                rejection_inventory_digest=D,
            ),
        ),
        "proof_candidate_admissions": (
            ProofCandidateAdmission(
                proof_requirement_id="user-flag",
                assessment_generation=1,
                candidate_sha256=sha256(b"SSH").hexdigest(),
                decision="allowed",
            ),
        ),
        "valid_source_ids": ("source-1",),
        "valid_proposal_ids": (UUID("00000000-0000-0000-0000-000000000102"),),
        "valid_family_ids": (UUID("00000000-0000-0000-0000-000000000101"),),
        "evidence_slices": (
            {
                "evidence_id": EVIDENCE_ID,
                "start": 0,
                "end": 3,
                "media_type": "text/plain",
                "content": b"SSH",
            },
        ),
        "sources": (source,),
    }
    if family == "observation":
        batch = ObservationBatchDraft()
        audits = ()
        if isinstance(source, ObservationExtractedSource):
            batch = ObservationBatchDraft(
                observations=(ObservationDraft(kind="text", text=source.summary, event_ids=(E0,)),)
            )
        elif isinstance(source, HypothesisFormedSource):
            from sedna.planning.models import HypothesisDraft

            batch = ObservationBatchDraft(
                hypotheses=(
                    HypothesisDraft(
                        text=source.statement,
                        confidence=source.confidence,
                        event_ids=source.supporting_event_ids,
                    ),
                )
            )
        elif isinstance(source, MissingInformationIdentifiedSource):
            from sedna.planning.models import MissingInformationDraft

            batch = ObservationBatchDraft(
                missing_information=(
                    MissingInformationDraft(
                        question=source.question,
                        event_ids=source.related_event_ids,
                    ),
                )
            )
        elif isinstance(source, OutcomeAssessedSource):
            from sedna.planning import InterpretationSubject, OutcomeAssessmentDraft

            batch = ObservationBatchDraft(
                subject=InterpretationSubject(
                    attachment_event_id=source.attachment_event_id,
                    terminal_tool_event_id=source.terminal_tool_event_id,
                    evidence_id=EVIDENCE_ID,
                ),
                outcomes=(
                    OutcomeAssessmentDraft(
                        category=source.category,
                        summary=source.summary,
                        event_ids=source.source_event_ids,
                    ),
                ),
            )
        elif isinstance(source, ObjectiveProofObservedSource):
            from sedna.planning.models import ObjectiveProofDraft

            batch = ObservationBatchDraft(
                objective_proofs=(
                    ObjectiveProofDraft(
                        proof_requirement_id=source.proof_requirement_id,
                        assessment=source.assessment,
                        event_ids=source.source_event_ids,
                    ),
                )
            )
        elif isinstance(source, (InterpretationSucceededSource, InterpretationFailedSource)):
            from sedna.planning import InterpretationSubject

            audits = (
                InterpretationAudit(
                    subject=InterpretationSubject(
                        attachment_event_id=source.attachment_event_id,
                        terminal_tool_event_id=source.terminal_tool_event_id,
                        evidence_id=source.evidence_id,
                    ),
                    call_metadata=metadata(),
                    status=(
                        "succeeded"
                        if isinstance(source, InterpretationSucceededSource)
                        else "failed"
                    ),
                    safe_code=(
                        None
                        if isinstance(source, InterpretationSucceededSource)
                        else "invalid_extractor_output"
                    ),
                ),
            )
        return ObservationEventConversion(batch=batch, interpretation_audits=audits, **common)
    if family == "planning":
        audit = None
        planner_draft = None
        planner_proposals = ()
        critic_verdicts = ()
        repair_audits = ()
        rejection_audits = ()
        planning_gaps = ()
        if isinstance(source, PlanRequestedSource):
            audit = PlanRequestAudit(
                call_metadata=metadata(),
                request_id=source.request_id,
                lane_key=source.lane_key,
                state_digest=source.situation_digest,
                material_event_revision=source.material_event_revision,
                ledger_digest=source.input_ledger_digest,
                canonical_revision=source.canonical_revision,
                source_registry_digest=source.source_registry_digest,
                max_proposals=source.max_proposals,
            )
        if isinstance(source, FrontierProposedSource):
            planner_draft = PlannerDraft(
                proposals=(
                    FrontierProposalDraft(
                        family_runtime_key="family-ssh",
                        variant_runtime_key="variant-ssh",
                        title=source.proposal.title,
                        score=source.proposal.score,
                        confidence=round(source.proposal.confidence * 100),
                        rationale=source.proposal.rationale,
                    ),
                )
            )
            planner_proposals = (
                PlannerProposalAudit(
                    request_id=source.request_id,
                    frontier_id=source.frontier_id,
                    proposal_ordinal=source.proposal_ordinal,
                    proposal_count=source.proposal_count,
                    proposal=source.proposal,
                    situation_digest=source.situation_digest,
                    input_ledger_digest=source.input_ledger_digest,
                    knowledge_context_digest=source.knowledge_context_digest,
                ),
            )
        elif isinstance(source, FrontierCriticizedSource):
            critic_verdicts = (
                PlannerCriticVerdict(
                    request_id=source.request_id,
                    frontier_id=source.frontier_id,
                    critic_pass=source.critic_pass,
                    accepted=source.accepted,
                    findings=(),
                    cited_event_ids=source.cited_event_ids,
                ),
            )
        elif isinstance(source, FrontierRepairedSource):
            planner_draft = PlannerDraft(
                proposals=(
                    FrontierProposalDraft(
                        family_runtime_key="family-ssh",
                        variant_runtime_key="variant-ssh",
                        title=source.proposal.title,
                        score=source.proposal.score,
                        confidence=round(source.proposal.confidence * 100),
                        rationale=source.proposal.rationale,
                    ),
                )
            )
            repair_audits = (
                PlannerRepairAudit(
                    call_metadata=metadata(),
                    critic_finding_codes=(),
                    request_id=source.request_id,
                    frontier_id=source.frontier_id,
                    critic_event_id=source.critic_event_id,
                    proposal_ordinal=source.proposal_ordinal,
                    proposal_count=source.proposal_count,
                    proposal=source.proposal,
                ),
            )
        elif isinstance(source, FrontierRejectedSource):
            rejection_audits = (
                PlannerRejectionAudit(
                    call_metadata=metadata(),
                    safe_code="critic_rejected",
                    request_id=source.request_id,
                    frontier_id=source.frontier_id,
                    critic_event_ids=source.critic_event_ids,
                    reason_codes=source.reason_codes,
                ),
            )
        elif isinstance(source, PlanningGapRecordedSource):
            planning_gaps = (
                PlanningGap(
                    request_id=source.request_id,
                    code=source.code,
                    summary=source.summary,
                    retryable=source.retryable,
                    situation_digest=source.situation_digest,
                    ledger_digest=source.ledger_digest,
                    related_event_ids=source.related_event_ids,
                ),
            )
        return PlanningAttemptEventConversion(
            reconciliation=reconciliation(),
            plan_request_audit=audit,
            planner_draft=planner_draft,
            planner_proposals=planner_proposals,
            critic_verdicts=critic_verdicts,
            repair_audits=repair_audits,
            rejection_audits=rejection_audits,
            planning_gaps=planning_gaps,
            **common,
        )
    if family == "reconciliation":
        family_id = (
            source.operation.family_id
            if isinstance(source, StrategyReconciledSource)
            else (
                source.archive_record.snapshot.family_id
                if isinstance(source, StrategyArchivedSource)
                else source.restored_snapshot.family_id
            )
        )
        reconciliation_value = StrategyReconciliation(
            input_family_ids=(family_id,),
            input_variant_ids=(),
            retained_family_ids=(family_id,),
            retained_variant_ids=(),
        )
        archive_transitions = ()
        reactivation_transitions = ()
        if isinstance(source, StrategyReconciledSource):
            reconciliation_value = reconciliation_value.model_copy(
                update={
                    "items": (
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
                        ),
                    )
                }
            )
        elif isinstance(source, StrategyArchivedSource):
            archive_transitions = (
                StrategyArchiveTransition(
                    family_id=source.archive_record.snapshot.family_id,
                    event_id=allocated,
                    rationale=source.archive_record.archive_reason,
                    request_id=source.request_id,
                    archive_batch_id=source.archive_batch_id,
                    entry_ordinal=source.entry_ordinal,
                    entry_count=source.entry_count,
                    archive_record=source.archive_record,
                    resulting_archive_digest=source.resulting_archive_digest,
                ),
            )
        else:
            assert isinstance(source, StrategyReactivatedSource)
            reactivation_transitions = (
                StrategyReactivationTransition(
                    family_id=source.restored_snapshot.family_id,
                    event_id=allocated,
                    rationale="Reactivation follows its matched predicate.",
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
                ),
            )
        return StrategyReconciliationEventConversion(
            reconciliation=reconciliation_value,
            archive_transitions=archive_transitions,
            reactivation_transitions=reactivation_transitions,
            **common,
        )
    research_sources = ()
    policy_decisions = ()
    research_consultations = ()
    research_assessments = ()
    if isinstance(source, ResearchSourceConsultedSource):
        research_sources = (
            ResearchSourceObservationDraft(
                source_id=source.source_id,
                assessment="inconclusive",
                event_ids=(E0,),
            ),
        )
    elif isinstance(source, ResearchSourceAssessedSource):
        research_sources = (
            ResearchSourceObservationDraft(
                source_id=source.source_id,
                assessment="useful" if source.assessment == "useful" else "inconclusive",
                event_ids=(E0,),
            ),
        )
    elif isinstance(source, ResearchQueryProposedSource):
        research_sources = (
            ResearchSourceObservationDraft(
                source_id="source-1",
                assessment="inconclusive",
                event_ids=(E0,),
            ),
        )
        policy_decisions = (
            ResearchPolicyDecision(
                decision_id=UUID("00000000-0000-0000-0000-000000000113"),
                allowed=source.policy_decision == "allowed",
                rationale="The request is within policy.",
                query_id=source.query_id,
                normalized_query=source.normalized_query,
                policy_version=source.policy_version,
                reason_codes=source.reason_codes,
                related_event_ids=source.related_event_ids,
                candidate_source_ids=source.candidate_source_ids,
            ),
        )
    if isinstance(source, ResearchSourceConsultedSource):
        research_consultations = (
            ResearchSourceConsultation(
                query_id=source.query_id,
                source_id=source.source_id,
                normalized_locator=source.normalized_locator,
                content=source.content,
                media_type=source.media_type,
                evidence_ids=source.evidence_ids,
                tool_event_ids=source.tool_event_ids,
            ),
        )
    elif isinstance(source, ResearchSourceAssessedSource):
        research_assessments = (
            ResearchSourceAssessmentAudit(
                query_id=source.query_id,
                source_id=source.source_id,
                consulted_event_id=source.consulted_event_id,
                assessment=source.assessment,
                confidence=source.confidence,
                summary=source.summary,
                related_event_ids=source.related_event_ids,
                suggested_registry_status=source.suggested_registry_status,
            ),
        )
    return ResearchEventConversion(
        research_sources=research_sources,
        policy_decisions=policy_decisions,
        research_consultations=research_consultations,
        research_assessments=research_assessments,
        **common,
    )


def _convert(family: str, conversion: object):
    if family == "observation":
        return payloads_from_observation_batch(conversion)
    if family == "planning":
        return payloads_from_planning_attempt(conversion)
    if family == "reconciliation":
        return payloads_from_reconciliation(conversion)
    return payloads_from_research_observations(conversion)


def test_observation_conversion_rejects_source_not_derived_from_batch() -> None:
    source = next(
        source
        for kind, family, source in planning_event_cases()
        if kind == "observation_extracted" and family == "observation"
    )
    conversion = _conversion("observation", source)
    conversion = conversion.model_copy(update={"batch": ObservationBatchDraft()})
    assert conversion.batch == ObservationBatchDraft()

    with pytest.raises(ValueError, match="observation.*batch|source.*batch"):
        payloads_from_observation_batch(conversion)


def test_observation_access_state_delta_uses_the_closed_record_kind_name() -> None:
    source = ObservationExtractedSource(
        local_id="access-observation",
        summary="SSH reachability is confirmed.",
        observation=AccessStateDeltaEventRecord(
            scope_reference_id=SCOPE_ID,
            access_kind="service_access",
            transition="confirmed",
            service_ref="ssh",
        ),
        confidence=1.0,
        evidence_slices=(_slice(),),
        scope_reference_ids=(SCOPE_ID,),
    )
    conversion = _conversion("observation", source).model_copy(
        update={
            "batch": ObservationBatchDraft(
                observations=(
                    ObservationDraft(kind="access", text=source.summary, event_ids=(E0,)),
                )
            )
        }
    )

    (payload,) = payloads_from_observation_batch(conversion)

    assert payload.observation.record_kind == "access_state_delta"


@pytest.mark.parametrize(
    ("kind", "family", "error"),
    (
        ("observation_extracted", "observation", "observation.*batch|source.*batch"),
        ("plan_requested", "planning", "plan request.*audit|source.*audit"),
        ("strategy_reconciled", "reconciliation", "reconciliation.*source|source.*reconciliation"),
        ("research_source_assessed", "research", "research.*source|source.*research"),
    ),
)
def test_conversion_rejects_source_not_represented_by_its_authoritative_model(
    kind: str, family: str, error: str
) -> None:
    """A payload cannot originate in the payload-shaped allocation side channel."""
    source = next(
        source
        for candidate, source_family, source in planning_event_cases()
        if candidate == kind and source_family == family
    )
    conversion = _conversion(family, source)
    if family == "observation":
        conversion = conversion.model_copy(update={"batch": ObservationBatchDraft()})
    elif family == "planning":
        conversion = conversion.model_copy(update={"plan_request_audit": None})
    elif family == "reconciliation":
        conversion = conversion.model_copy(update={"reconciliation": reconciliation()})
    else:
        conversion = conversion.model_copy(update={"research_sources": ()})

    with pytest.raises(ValueError, match=error):
        _convert(family, conversion)


def _planning_source_semantic_mutations(
    source: PlanningEventSource, allocated: UUID
) -> dict[str, object]:
    if isinstance(source, PlanRequestedSource):
        return {
            "request_id": UUID("00000000-0000-0000-0000-000000000200"),
            "lane_key": "lane-" + "c" * 32,
            "situation_digest": "b" * 64,
            "material_event_revision": {"sequence": 2, "event_hash": "b" * 64},
            "input_ledger_digest": "b" * 64,
            "canonical_revision": "b" * 64,
            "source_registry_digest": "b" * 64,
            "max_proposals": 4,
        }
    if isinstance(source, FrontierProposedSource):
        return {
            "request_id": UUID("00000000-0000-0000-0000-000000000201"),
            "frontier_id": UUID("00000000-0000-0000-0000-000000000202"),
            "proposal_ordinal": 2,
            "proposal_count": 2,
            "proposal": source.proposal.model_copy(update={"title": "Different strategy"}),
            "situation_digest": "b" * 64,
            "input_ledger_digest": "b" * 64,
            "knowledge_context_digest": "b" * 64,
        }
    if isinstance(source, FrontierCriticizedSource):
        return {
            "request_id": UUID("00000000-0000-0000-0000-000000000203"),
            "frontier_id": UUID("00000000-0000-0000-0000-000000000204"),
            "critic_pass": 2,
            "accepted": False,
            "finding_codes": ("scope_mismatch",),
            "cited_event_ids": (allocated,),
        }
    if isinstance(source, FrontierRepairedSource):
        return {
            "request_id": UUID("00000000-0000-0000-0000-000000000205"),
            "frontier_id": UUID("00000000-0000-0000-0000-000000000206"),
            "critic_event_id": allocated,
            "proposal_ordinal": 2,
            "proposal_count": 2,
            "proposal": source.proposal.model_copy(update={"title": "Different repair"}),
        }
    if isinstance(source, FrontierRejectedSource):
        return {
            "request_id": UUID("00000000-0000-0000-0000-000000000207"),
            "frontier_id": UUID("00000000-0000-0000-0000-000000000208"),
            "critic_event_ids": (allocated,),
            "reason_codes": ("different_reason",),
        }
    if isinstance(source, PlanningGapRecordedSource):
        return {
            "request_id": None,
            "code": "llm_unavailable",
            "summary": "Different planning gap.",
            "retryable": False,
            "situation_digest": "b" * 64,
            "ledger_digest": "b" * 64,
            "related_event_ids": (allocated,),
        }
    if isinstance(source, ResearchQueryProposedSource):
        return {
            "query_id": UUID("00000000-0000-0000-0000-000000000209"),
            "normalized_query": "different ssh query",
            "policy_decision": "rejected",
            "policy_version": "policy:2",
            "reason_codes": ("different_reason",),
            "related_event_ids": (allocated,),
            "candidate_source_ids": ("source-2",),
        }
    raise TypeError(f"unexpected source: {source.kind}")


@pytest.mark.parametrize(
    "kind",
    (
        "plan_requested",
        "frontier_proposed",
        "frontier_criticized",
        "frontier_repaired",
        "frontier_rejected",
        "planning_gap_recorded",
        "research_query_proposed",
    ),
)
def test_semantic_source_fields_must_match_the_authoritative_typed_model(kind: str) -> None:
    """Every duplicated source field is rejected when its typed model disagrees."""
    source, family = next(
        (candidate, candidate_family)
        for candidate_kind, candidate_family, candidate in planning_event_cases()
        if candidate_kind == kind
    )
    conversion = _conversion(family, source)
    allocated = conversion.local_event_bindings[0].event_id
    mutations = _planning_source_semantic_mutations(source, allocated)

    for field_name, replacement in mutations.items():
        mutated = source.model_copy(update={field_name: replacement})
        update: dict[str, object] = {"sources": (mutated,)}
        if field_name == "candidate_source_ids":
            update["valid_source_ids"] = ("source-1", "source-2")
        mismatched_conversion = conversion.model_copy(update=update)

        with pytest.raises(ValueError, match="source not represented"):
            _convert(family, mismatched_conversion)


@pytest.mark.parametrize(
    ("kind", "field_name", "replacement"),
    (
        (
            "strategy_reconciled",
            "operation",
            lambda source: source.operation.model_copy(update={"reason": "Different reason."}),
        ),
        (
            "strategy_archived",
            "archive_record",
            lambda source: source.archive_record.model_copy(
                update={"archive_summary": "Different archive summary."}
            ),
        ),
        (
            "strategy_reactivated",
            "restored_snapshot",
            lambda source: source.restored_snapshot.model_copy(update={"title": "Different title"}),
        ),
    ),
)
def test_reconciliation_sources_require_full_transition_semantics(
    kind: str, field_name: str, replacement: object
) -> None:
    source, family = next(
        (candidate, candidate_family)
        for candidate_kind, candidate_family, candidate in planning_event_cases()
        if candidate_kind == kind
    )
    assert family == "reconciliation"
    conversion = _conversion(family, source)
    mutated = source.model_copy(update={field_name: replacement(source)})

    with pytest.raises(ValueError, match="source not represented"):
        _convert(family, conversion.model_copy(update={"sources": (mutated,)}))


def test_conversion_envelopes_reject_prebuilt_journal_payload_pass_through() -> None:
    with pytest.raises(ValidationError, match="journal_payloads"):
        PlanningAttemptEventConversion(
            reconciliation=reconciliation(),
            call_metadata=metadata(),
            journal_payloads=(gap(),),
            local_event_bindings=(),
            valid_event_ids=(),
            valid_evidence_ids=(),
        )


def test_planning_package_exports_only_public_conversion_functions() -> None:
    from sedna import planning

    assert planning.payloads_from_observation_batch is payloads_from_observation_batch
    assert planning.payloads_from_planning_attempt is payloads_from_planning_attempt
    assert planning.payloads_from_reconciliation is payloads_from_reconciliation
    assert planning.payloads_from_research_observations is payloads_from_research_observations
    assert not hasattr(planning, "PlanRequestedSource")
    assert not hasattr(planning, "ResearchSourceAssessedSource")


@pytest.mark.parametrize(("kind", "family", "source"), planning_event_cases())
def test_every_closed_source_row_materializes_a_typed_payload(
    kind: str, family: str, source: PlanningEventSource
) -> None:
    conversion = _conversion(family, source)

    (payload,) = _convert(family, conversion)

    assert payload.kind == kind
    assert (
        TypeAdapter(EventPayload).validate_json(
            json.dumps(payload.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)
        )
        == payload
    )
    assert conversion.local_event_bindings[0].event_id in conversion.valid_event_ids
    for name, value in payload.model_dump(mode="python").items():
        if name.endswith("digest"):
            assert value == D or value == sha256(b"SSH").hexdigest() or len(value) == 64
    if hasattr(payload, "source_event_ids"):
        assert set(payload.source_event_ids).issubset(conversion.valid_event_ids)
    if hasattr(payload, "related_event_ids"):
        assert set(payload.related_event_ids).issubset(conversion.valid_event_ids)
    if hasattr(payload, "evidence_ids"):
        assert set(payload.evidence_ids).issubset(conversion.valid_evidence_ids)


def test_converter_rejects_source_from_wrong_family() -> None:
    source = next(
        source for kind, _, source in planning_event_cases() if kind == "planning_gap_recorded"
    )
    with pytest.raises(ValidationError, match="research"):
        ResearchEventConversion(
            research_sources=(),
            call_metadata=metadata(),
            local_event_bindings=(LocalEventIdBinding(local_id=source.local_id, event_id=E0),),
            valid_event_ids=(E0,),
            valid_evidence_ids=(),
            sources=(source,),
        )


def test_objective_proof_source_requires_the_current_admitted_proof_index() -> None:
    source = next(
        source
        for kind, family, source in planning_event_cases()
        if kind == "objective_proof_observed" and family == "observation"
    )
    conversion = _conversion("observation", source).model_copy(
        update={"proof_candidate_admissions": ()}
    )

    with pytest.raises(ValueError, match="proof_candidate_admission_not_in_index"):
        payloads_from_observation_batch(conversion)


def test_observation_source_rejects_an_evidence_slice_digest_not_grounded_in_input_bytes() -> None:
    source = next(
        source
        for kind, family, source in planning_event_cases()
        if kind == "observation_extracted" and family == "observation"
    )
    bad_slice = source.evidence_slices[0].model_copy(update={"sha256": D})
    conversion = _conversion(
        "observation", source.model_copy(update={"evidence_slices": (bad_slice,)})
    )

    with pytest.raises(ValueError, match="reference_validation_failed"):
        payloads_from_observation_batch(conversion)
