"""Strict contracts for adaptive-planning state and settlement boundaries."""

from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

import pytest
from pydantic import ValidationError

import sedna.planning as planning
from sedna.engagement import JournalRevision, ProofRequirement
from sedna.planning.models import (
    EVIDENCE_SLICE_BYTES,
    MAX_EVIDENCE_BYTES_PER_SETTLEMENT,
    MAX_PLANNER_REQUEST_BYTES,
    MAX_PLANNING_EVENT_BATCH,
    MAX_PLANNING_PAYLOAD_BYTES,
    MAX_PLANNING_RESULT_BYTES,
    MAX_RECENT_EVENT_TEXT_BYTES,
    MAX_RECENT_EVENTS,
    AccessState,
    AccessStateDeltaDraft,
    ArchivedStrategyState,
    AttemptState,
    EvidenceInterpretationState,
    EvidenceSliceInput,
    ExecutionVariantDraft,
    ExecutionVariantState,
    FacetObservationDraft,
    FailedSettlementResult,
    FrontierProjection,
    FrontierProposal,
    FrontierProposalDraft,
    HypothesisDraft,
    IncompatibilityObservationDraft,
    IncompleteSettlementResult,
    InterpretationAudit,
    InterpretationSubject,
    LocalEventIdBinding,
    MissingInformationDraft,
    NothingPendingSettlementResult,
    ObjectiveProgress,
    ObjectiveProofDraft,
    ObservationBatchDraft,
    ObservationDraft,
    ObservationEventConversion,
    ObservedFact,
    OutcomeAssessmentDraft,
    PendingEvidenceRange,
    PlannerCriticVerdict,
    PlannerDraft,
    PlannerFinding,
    PlannerRejectionAudit,
    PlannerRepairAudit,
    PlanningAttemptEventConversion,
    PlanningCallMetadata,
    PlanningGap,
    PlanningResult,
    PlanRequestAudit,
    PrerequisiteProof,
    PrivateValueDraft,
    ProofCandidateAdmission,
    ProofIndexRecord,
    ProofProgress,
    ProofValueReference,
    ProposalPrerequisite,
    ResearchPolicyDecision,
    ResearchSourceObservationDraft,
    RetryPredicate,
    SecretReferenceDraft,
    SettledSettlementResult,
    SettlementResultAdapter,
    SituationProjection,
    StrategyArchive,
    StrategyArchiveTransition,
    StrategyFamilyDraft,
    StrategyFamilyState,
    StrategyLedger,
    StrategyReactivationTransition,
    StrategyReconciliation,
)
from sedna.planning.ports import TerminalReconciliationResult


def _sha(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _revision() -> JournalRevision:
    return JournalRevision(sequence=2, event_hash=_sha("journal-head"))


def _situation(*, revision: JournalRevision | None = None) -> SituationProjection:
    return SituationProjection(
        engagement_id=uuid4(),
        authoritative_journal_revision=revision or _revision(),
        material_event_revision=2,
        state_digest=_sha("state"),
        objective_progress=ObjectiveProgress(
            requirements=(
                ProofProgress(
                    proof_requirement_id="user-flag",
                    status="pending",
                    historical_assessment_digest=_sha("[]"),
                    rejected_value_overflow_digest=_sha("[]"),
                ),
            )
        ),
    )


def test_planning_limits_are_bounded_by_m6a_contracts() -> None:
    assert MAX_RECENT_EVENTS == 64
    assert MAX_RECENT_EVENT_TEXT_BYTES == 64 * 1024
    assert MAX_PLANNER_REQUEST_BYTES == 512 * 1024
    assert MAX_PLANNING_EVENT_BATCH == 511
    assert MAX_PLANNING_PAYLOAD_BYTES == 60 * 1024
    assert MAX_PLANNING_RESULT_BYTES == 240 * 1024
    assert EVIDENCE_SLICE_BYTES == 32 * 1024
    assert MAX_EVIDENCE_BYTES_PER_SETTLEMENT == 2 * 1024 * 1024


def test_objective_progress_requires_exact_sorted_manifest_requirements() -> None:
    progress = ObjectiveProgress(
        requirements=(
            ProofProgress(
                proof_requirement_id="root-flag",
                status="pending",
                historical_assessment_digest=_sha("[]"),
                rejected_value_overflow_digest=_sha("[]"),
            ),
            ProofProgress(
                proof_requirement_id="user-flag",
                status="supported",
                supporting_event_ids=(uuid4(),),
                historical_assessment_digest=_sha("[]"),
                rejected_value_overflow_digest=_sha("[]"),
            ),
        )
    )
    assert tuple(item.proof_requirement_id for item in progress.requirements) == (
        "root-flag",
        "user-flag",
    )

    with pytest.raises(ValidationError, match="requirements_not_sorted_unique"):
        ObjectiveProgress(requirements=(progress.requirements[1], progress.requirements[0]))


def test_settlement_binds_projection_revision_proofs_and_pending_ranges() -> None:
    situation = _situation()
    proof_ids = ("user-flag",)
    nothing_pending = NothingPendingSettlementResult(
        engagement_id=situation.engagement_id,
        reason="plan",
        authoritative_journal_revision=situation.authoritative_journal_revision,
        situation=situation,
        required_proof_ids=proof_ids,
        all_required_proofs_satisfied=False,
        possible_terminal_evidence=False,
    )
    assert nothing_pending.status == "nothing_pending"

    pending = PendingEvidenceRange(
        evidence_id="evidence-sha256-" + "a" * 64,
        attachment_event_id=uuid4(),
        start=0,
        end=EVIDENCE_SLICE_BYTES + 1,
        media_type="text/plain",
        reason="budget_exhausted",
    )
    incomplete = IncompleteSettlementResult(
        engagement_id=situation.engagement_id,
        reason="plan",
        authoritative_journal_revision=situation.authoritative_journal_revision,
        situation=situation,
        required_proof_ids=proof_ids,
        pending_ranges=(pending,),
        pending_total_count=1,
        pending_inventory_sha256=_sha("pending"),
        incomplete_reason="budget_exhausted",
        all_required_proofs_satisfied=False,
        possible_terminal_evidence=True,
    )
    assert incomplete.pending_ranges == (pending,)

    with pytest.raises(ValidationError, match="pending_cursor_policy"):
        IncompleteSettlementResult(
            engagement_id=situation.engagement_id,
            reason="plan",
            authoritative_journal_revision=situation.authoritative_journal_revision,
            situation=situation,
            required_proof_ids=proof_ids,
            pending_ranges=(pending,),
            pending_total_count=2,
            pending_inventory_sha256=_sha("pending-page"),
            incomplete_reason="budget_exhausted",
            all_required_proofs_satisfied=False,
            possible_terminal_evidence=True,
        )

    with pytest.raises(ValidationError, match="settlement_projection_revision_mismatch"):
        NothingPendingSettlementResult(
            engagement_id=situation.engagement_id,
            reason="plan",
            authoritative_journal_revision=JournalRevision(sequence=3, event_hash=_sha("other")),
            situation=situation,
            required_proof_ids=proof_ids,
            all_required_proofs_satisfied=False,
            possible_terminal_evidence=False,
        )


def test_settlement_rejects_empty_manifest_as_satisfied() -> None:
    situation = SituationProjection(
        engagement_id=uuid4(),
        authoritative_journal_revision=_revision(),
        material_event_revision=2,
        state_digest=_sha("empty-state"),
        objective_progress=ObjectiveProgress(),
    )
    with pytest.raises(ValidationError, match="settlement_proof_completion_mismatch"):
        NothingPendingSettlementResult(
            engagement_id=situation.engagement_id,
            reason="plan",
            authoritative_journal_revision=situation.authoritative_journal_revision,
            situation=situation,
            required_proof_ids=(),
            all_required_proofs_satisfied=True,
            possible_terminal_evidence=False,
        )


def test_strategy_ledger_requires_sorted_bounded_family_and_variant_identity() -> None:
    family = StrategyFamilyState(
        family_id=uuid4(),
        runtime_key="family-web",
        status="available",
        variant_ids=(),
    )
    ledger = StrategyLedger(families=(family,))
    assert ledger.families == (family,)

    with pytest.raises(ValidationError, match="duplicate_strategy_runtime_key"):
        StrategyLedger(families=(family, family.model_copy(update={"family_id": uuid4()})))

    variant = ExecutionVariantState(
        variant_id=uuid4(),
        family_id=family.family_id,
        runtime_key="variant-http",
        status="available",
    )
    with pytest.raises(ValidationError, match="variant_family_not_declared"):
        StrategyLedger(families=(family,), variants=(variant,))


def test_terminal_reconciliation_result_binds_action_to_lifecycle_state() -> None:
    result = TerminalReconciliationResult(
        action="proof_close_requested",
        authoritative_journal_revision=_revision(),
        lifecycle_status="closing",
    )
    assert result.action == "proof_close_requested"

    with pytest.raises(ValidationError, match="terminal_reconciliation_status_policy"):
        TerminalReconciliationResult(
            action="proof_close_finalized",
            authoritative_journal_revision=_revision(),
            lifecycle_status="active",
        )

    with pytest.raises(ValidationError, match="terminal_reconciliation_failure_policy"):
        TerminalReconciliationResult(
            action="failed",
            authoritative_journal_revision=_revision(),
            lifecycle_status="active",
        )


def test_planning_result_requires_exactly_one_status_payload() -> None:
    situation = _situation()
    frontier = FrontierProjection(
        frontier_id=uuid4(),
        engagement_id=situation.engagement_id,
        state_digest=situation.state_digest,
        input_ledger_digest=_sha("ledger-input"),
        resulting_ledger_digest=_sha("ledger-result"),
        constrained_rationale="No frontier candidates are currently applicable.",
    )
    result = PlanningResult(
        status="success",
        engagement_id=situation.engagement_id,
        current_authoritative_journal_revision=situation.authoritative_journal_revision,
        frontier=frontier,
    )
    assert result.frontier == frontier

    with pytest.raises(ValidationError, match="planning_result_shape"):
        PlanningResult(
            status="success",
            engagement_id=situation.engagement_id,
            current_authoritative_journal_revision=situation.authoritative_journal_revision,
            gap=PlanningGap(code="no_viable_strategy", summary="No viable strategy remains."),
        )


def test_failed_settlement_only_omits_projection_for_unavailable_or_corrupt_journal() -> None:
    failed = FailedSettlementResult(
        engagement_id=uuid4(),
        reason="plan",
        authoritative_journal_revision=None,
        situation=None,
        failure_code="journal_unavailable",
        failure_summary="The journal cannot be loaded.",
        all_required_proofs_satisfied=False,
        possible_terminal_evidence=False,
    )
    assert failed.status == "failed"

    situation = _situation()
    with pytest.raises(ValidationError, match="failed_settlement_projection_policy"):
        FailedSettlementResult(
            engagement_id=situation.engagement_id,
            reason="plan",
            authoritative_journal_revision=None,
            situation=None,
            failure_code="evidence_read_failed",
            failure_summary="Evidence cannot be read.",
            all_required_proofs_satisfied=False,
            possible_terminal_evidence=False,
        )


def test_settled_settlement_has_no_pending_ranges() -> None:
    situation = _situation()
    result = SettledSettlementResult(
        engagement_id=situation.engagement_id,
        reason="plan",
        authoritative_journal_revision=situation.authoritative_journal_revision,
        situation=situation,
        required_proof_ids=("user-flag",),
        all_required_proofs_satisfied=False,
        possible_terminal_evidence=False,
    )
    assert result.status == "settled"


def test_private_value_draft_requires_one_exact_candidate_evidence_slice() -> None:
    evidence_id = "evidence-sha256-" + "b" * 64
    private = PrivateValueDraft(
        evidence_id=evidence_id,
        candidate_start=2,
        candidate_end=6,
        claimed_utf8="FLAG",
    )
    source = EvidenceSliceInput(
        evidence_id=evidence_id,
        start=0,
        end=8,
        media_type="text/plain",
        content=b"xxFLAGyy",
    )
    assert private.resolve((source,)) == _sha("FLAG")

    with pytest.raises(ValueError, match="reference_validation_failed"):
        private.resolve(())


def test_proof_value_reference_binds_candidate_slice_to_same_requirement_generation() -> None:
    proof_event_id = uuid4()
    reference = ProofValueReference(
        proof_event_id=proof_event_id,
        proof_requirement_id="user-flag",
        assessment_generation=1,
        assessment="supported",
        evidence_id="evidence-sha256-" + "c" * 64,
        candidate_start=0,
        candidate_end=4,
        value_sha256=_sha("FLAG"),
    )
    progress = ProofProgress(
        proof_requirement_id="user-flag",
        status="supported",
        supporting_event_ids=(proof_event_id,),
        value_references=(reference,),
        historical_assessment_digest=_sha("[]"),
        rejected_value_overflow_digest=_sha("[]"),
    )
    assert progress.value_references == (reference,)


def test_observation_and_hypothesis_require_event_grounding_and_finite_confidence() -> None:
    event_id = uuid4()
    observation = ObservationDraft(kind="text", text="SSH is open.", event_ids=(event_id,))
    hypothesis = HypothesisDraft(
        text="Credential reuse may be possible.",
        confidence=0.75,
        event_ids=(event_id,),
    )
    assert observation.event_ids == (event_id,)
    assert hypothesis.confidence == 0.75

    with pytest.raises(ValidationError):
        ObservationDraft(kind="text", text="Ungrounded", event_ids=())

    with pytest.raises(ValidationError, match="finite_confidence_required"):
        HypothesisDraft(text="Invalid confidence", confidence=float("nan"), event_ids=(event_id,))


def test_typed_observation_batch_requires_grounded_sorted_unique_drafts() -> None:
    event_id = uuid4()
    facet = FacetObservationDraft(
        key="service",
        value="ssh",
        event_ids=(event_id,),
    )
    access = AccessStateDeltaDraft(
        subject="operator",
        state="user-shell",
        event_ids=(event_id,),
    )
    secret = SecretReferenceDraft(
        label="candidate-password",
        value=PrivateValueDraft(
            evidence_id="evidence-sha256-" + "d" * 64,
            candidate_start=0,
            candidate_end=4,
            claimed_utf8="pass",
        ),
        event_ids=(event_id,),
    )
    incompatibility = IncompatibilityObservationDraft(
        subject="exploit-x",
        explanation="Target version is incompatible.",
        event_ids=(event_id,),
    )
    missing = MissingInformationDraft(
        question="Which SSH credential is valid?",
        event_ids=(event_id,),
    )
    research = ResearchSourceObservationDraft(
        source_id="source-123",
        assessment="useful",
        event_ids=(event_id,),
    )
    batch = ObservationBatchDraft(
        observations=(ObservationDraft(kind="text", text="SSH is open.", event_ids=(event_id,)),),
        facets=(facet,),
        access_deltas=(access,),
        secret_references=(secret,),
        incompatibilities=(incompatibility,),
        missing_information=(missing,),
        research_sources=(research,),
    )
    assert batch.facets == (facet,)
    assert batch.secret_references == (secret,)

    with pytest.raises(ValidationError, match="event_ids_not_sorted_unique"):
        FacetObservationDraft(
            key="service",
            value="ssh",
            event_ids=(event_id, event_id),
        )


def test_strategy_ledger_bounds_attempts_and_archives() -> None:
    family_id = uuid4()
    variant_id = uuid4()
    attempts = tuple(
        AttemptState(
            attempt_event_id=uuid4(),
            outcome="no_effect",
            summary=f"Attempt {ordinal}",
        )
        for ordinal in range(8)
    )
    variant = ExecutionVariantState(
        variant_id=variant_id,
        family_id=family_id,
        runtime_key="variant-http",
        status="available",
        recent_attempts=attempts,
        historical_attempt_count=0,
        historical_attempt_digest=_sha("[]"),
    )
    family = StrategyFamilyState(
        family_id=family_id,
        runtime_key="family-web",
        status="available",
        variant_ids=(variant_id,),
    )
    archive = ArchivedStrategyState(
        family_id=family_id,
        summary="An archived web strategy.",
        archived_event_id=uuid4(),
    )
    ledger = StrategyLedger(families=(family,), variants=(variant,), archive=(archive,))
    assert ledger.variants[0].recent_attempts == attempts

    with pytest.raises(ValidationError):
        ExecutionVariantState(
            variant_id=variant_id,
            family_id=family_id,
            runtime_key="variant-overflow",
            status="available",
            recent_attempts=attempts
            + (attempts[0].model_copy(update={"attempt_event_id": uuid4()}),),
            historical_attempt_count=0,
            historical_attempt_digest=_sha("[]"),
        )


def test_strategy_ledger_rejects_more_than_global_hot_attempt_limit() -> None:
    family_id = uuid4()
    variants = tuple(
        ExecutionVariantState(
            variant_id=uuid4(),
            family_id=family_id,
            runtime_key=f"variant-{ordinal}",
            status="available",
            recent_attempts=tuple(
                AttemptState(
                    attempt_event_id=uuid4(),
                    outcome="no_effect",
                    summary=f"Attempt {ordinal}-{attempt_ordinal}",
                )
                for attempt_ordinal in range(8)
            ),
        )
        for ordinal in range(33)
    )
    sorted_variants = tuple(sorted(variants, key=lambda variant: str(variant.variant_id)))
    family = StrategyFamilyState(
        family_id=family_id,
        runtime_key="family-web",
        status="available",
        variant_ids=tuple(variant.variant_id for variant in sorted_variants),
    )
    with pytest.raises(ValidationError, match="strategy_hot_attempt_limit"):
        StrategyLedger(families=(family,), variants=sorted_variants)


def test_frontier_requires_stable_score_order_and_complete_proposal_identity() -> None:
    engagement_id = uuid4()
    family_id = uuid4()
    variant_id = uuid4()
    first = FrontierProposal(
        proposal_id=uuid4(),
        family_id=family_id,
        variant_id=variant_id,
        title="Enumerate SSH authentication.",
        score=90,
        confidence=75,
        rationale="The service is reachable and supports authentication checks.",
    )
    second = FrontierProposal(
        proposal_id=uuid4(),
        family_id=family_id,
        variant_id=variant_id,
        title="Inspect web service.",
        score=80,
        confidence=60,
        rationale="The web service may expose relevant context.",
    )
    frontier = FrontierProjection(
        frontier_id=uuid4(),
        engagement_id=engagement_id,
        state_digest=_sha("state"),
        input_ledger_digest=_sha("input-ledger"),
        resulting_ledger_digest=_sha("result-ledger"),
        proposals=(first, second),
        constrained_rationale="Only two strategies are currently applicable.",
    )
    assert frontier.proposals == (first, second)
    assert (
        FrontierProposalDraft(
            family_runtime_key="family-web",
            variant_runtime_key="variant-ssh",
            title="Enumerate SSH authentication.",
            score=90,
            confidence=75,
            rationale="The service is reachable and supports authentication checks.",
        ).proposal_id
        is None
    )

    utility_ordered = FrontierProjection(
        frontier_id=uuid4(),
        engagement_id=engagement_id,
        state_digest=_sha("state"),
        input_ledger_digest=_sha("input-ledger"),
        resulting_ledger_digest=_sha("result-ledger"),
        proposals=(second, first),
        constrained_rationale="Expected utility, not raw score, determines frontier order.",
    )
    assert utility_ordered.proposals == (second, first)


def test_planner_draft_rejects_semantically_duplicate_proposals() -> None:
    proposal = FrontierProposalDraft(
        family_runtime_key="family-ssh",
        variant_runtime_key="variant-password",
        title="Try SSH authentication",
        score=80,
        confidence=70,
        rationale="Test the currently supported authentication path.",
    )

    with pytest.raises(ValidationError, match="planner_proposals_not_unique"):
        PlannerDraft(proposals=(proposal, proposal.model_copy(update={"score": 70})))


def test_planner_draft_rejects_duplicate_variant_runtime_key_across_families() -> None:
    proposal = FrontierProposalDraft(
        family_runtime_key="family-ssh",
        variant_runtime_key="variant-password",
        title="Try SSH authentication",
        score=80,
        confidence=70,
        rationale="Test the currently supported authentication path.",
    )
    other_family = proposal.model_copy(
        update={
            "family_runtime_key": "family-web",
            "title": "Try web authentication",
        }
    )

    with pytest.raises(ValidationError, match="planner_variant_runtime_keys_not_unique"):
        PlannerDraft(proposals=(proposal, other_family))


def test_proposal_rejects_unlinked_prerequisite_proof_reference() -> None:
    with pytest.raises(ValidationError, match="prerequisite_proof_reference_not_grounded"):
        FrontierProposalDraft(
            family_runtime_key="family-web",
            variant_runtime_key="variant-scope",
            title="Probe scoped target",
            score=80,
            confidence=70,
            rationale="A bounded probe can discriminate the implementation.",
            prerequisites=(
                ProposalPrerequisite(
                    kind="scope_authorized",
                    statement="The target remains in scope.",
                    scope_kind="exact_target",
                    scope_value="10.10.10.10",
                ),
            ),
            prerequisite_proofs=(
                PrerequisiteProof(
                    prerequisite_index=0,
                    proof_kind="scope_authorized",
                    reference_id="scope-missing",
                ),
            ),
        )


def test_proposal_rejects_semantically_mismatched_prerequisite_proof() -> None:
    scope_id = "scope-authorized-target"
    with pytest.raises(ValidationError, match="prerequisite_proof_kind_mismatch"):
        FrontierProposalDraft(
            family_runtime_key="family-web",
            variant_runtime_key="variant-observation",
            title="Use observed service",
            score=80,
            confidence=70,
            rationale="The service observation enables a bounded test.",
            prerequisites=(
                ProposalPrerequisite(
                    kind="event_observed",
                    statement="The service was observed.",
                    event_type="observation_extracted",
                ),
            ),
            prerequisite_proofs=(
                PrerequisiteProof(
                    prerequisite_index=0,
                    proof_kind="scope_authorized",
                    reference_id=scope_id,
                ),
            ),
            scope_reference_ids=(scope_id,),
        )


def test_zero_score_requires_terminal_status_and_typed_retry_predicate() -> None:
    from sedna.planning.models import RetryPredicate

    common = {
        "family_runtime_key": "family:ssh",
        "variant_runtime_key": "variant:rockyou",
        "title": "Password candidates",
        "score": 0,
        "confidence": 90,
        "rationale": "The complete bounded candidate set was exhausted.",
    }

    with pytest.raises(ValidationError, match="zero_score_requires_impossibility"):
        FrontierProposalDraft(**common)
    accepted = FrontierProposalDraft(
        **common,
        status=planning.StrategyStatus.EXHAUSTED,
        terminal_reason="impossibility",
        retry_predicates=(
            RetryPredicate(
                kind=planning.RetryPredicateKind.CREDENTIAL_AVAILABLE,
                value="ssh-password",
            ),
        ),
        event_refs=(uuid4(),),
    )

    assert accepted.status == "exhausted"


def test_zero_score_requires_explicit_impossibility_or_incompatibility() -> None:
    from sedna.planning.models import RetryPredicate

    with pytest.raises(ValidationError, match="zero_score_requires_explicit_terminal_reason"):
        FrontierProposalDraft(
            family_runtime_key="family:ssh",
            variant_runtime_key="variant:password",
            title="Password authentication",
            score=0,
            confidence=90,
            rationale="The complete bounded credential set was rejected.",
            status=planning.StrategyStatus.EXHAUSTED,
            retry_predicates=(
                RetryPredicate(
                    kind=planning.RetryPredicateKind.CREDENTIAL_AVAILABLE,
                    value="ssh-password",
                ),
            ),
            event_refs=(uuid4(),),
        )


def test_critic_verdict_and_reconciliation_reject_silent_loss() -> None:
    finding = PlannerFinding(
        code="scope_mismatch",
        summary="The proposal is not bound to the authorized scope.",
        material=True,
    )
    verdict = PlannerCriticVerdict(accepted=False, findings=(finding,))
    assert verdict.accepted is False

    with pytest.raises(ValidationError, match="critic_acceptance_policy"):
        PlannerCriticVerdict(accepted=True, findings=(finding,))

    family_id = uuid4()
    variant_id = uuid4()
    reconciliation = StrategyReconciliation(
        input_family_ids=(family_id,),
        input_variant_ids=(variant_id,),
        retained_family_ids=(family_id,),
        retained_variant_ids=(variant_id,),
    )
    assert reconciliation.retained_variant_ids == (variant_id,)

    with pytest.raises(ValidationError, match="strategy_reconciliation_silent_loss"):
        StrategyReconciliation(
            input_family_ids=(family_id,),
            input_variant_ids=(variant_id,),
            retained_family_ids=(),
            retained_variant_ids=(variant_id,),
        )


def test_conversion_envelopes_carry_exact_input_reference_indexes() -> None:
    metadata = PlanningCallMetadata(
        purpose="plan",
        provider="local",
        model="structured-model",
        agent_id="agent-1",
        prompt_id="sedna-frontier-planner",
        prompt_version="1",
        response_schema_version="1",
        input_digest=_sha("input"),
        input_tokens=1,
        output_tokens=1,
        elapsed_ms=1,
    )
    reconciliation = StrategyReconciliation(
        input_family_ids=(),
        input_variant_ids=(),
        retained_family_ids=(),
        retained_variant_ids=(),
    )
    conversion = PlanningAttemptEventConversion(
        reconciliation=reconciliation,
        call_metadata=metadata,
        local_event_bindings=(),
        valid_event_ids=(),
        valid_evidence_ids=(),
        valid_scope_reference_ids=("scope-" + "a" * 32,),
        valid_proof_indexes=(
            ProofIndexRecord(
                proof_requirement_id="user-flag",
                assessment_generation=1,
                rejection_inventory_digest=_sha("[]"),
            ),
        ),
        proof_candidate_admissions=(
            ProofCandidateAdmission(
                proof_requirement_id="user-flag",
                assessment_generation=1,
                candidate_sha256=_sha("FLAG"),
                decision="allowed",
            ),
        ),
        valid_decision_ids=(uuid4(),),
        valid_proposal_ids=(uuid4(),),
        valid_family_ids=(uuid4(),),
        valid_variant_ids=(uuid4(),),
        valid_knowledge_ids=("knowledge-1",),
        valid_source_ids=("source-1",),
    )
    assert conversion.valid_scope_reference_ids == ("scope-" + "a" * 32,)
    assert conversion.valid_proof_indexes[0].proof_requirement_id == "user-flag"


def test_observation_conversion_uses_preallocated_local_event_bindings_only() -> None:
    event_id = uuid4()
    binding = LocalEventIdBinding(local_id="observation-1", event_id=event_id)
    metadata = PlanningCallMetadata(
        purpose="observe",
        provider="local",
        model="structured-model",
        agent_id="agent-1",
        prompt_id="sedna-observation-extractor",
        prompt_version="1",
        response_schema_version="1",
        input_digest=_sha("input"),
        input_tokens=10,
        output_tokens=20,
        elapsed_ms=30,
    )
    conversion = ObservationEventConversion(
        batch=ObservationBatchDraft(
            observations=(
                ObservationDraft(kind="text", text="SSH is open.", event_ids=(event_id,)),
            )
        ),
        call_metadata=metadata,
        local_event_bindings=(binding,),
        valid_event_ids=(event_id,),
        valid_evidence_ids=(),
    )
    assert conversion.local_event_bindings == (binding,)

    with pytest.raises(ValidationError, match="local_event_bindings_not_sorted_unique"):
        ObservationEventConversion(
            batch=conversion.batch,
            call_metadata=metadata,
            local_event_bindings=(binding, binding),
            valid_event_ids=(event_id,),
            valid_evidence_ids=(),
        )


def test_interpretation_subject_is_attachment_scoped_even_for_identical_evidence() -> None:
    evidence_id = "evidence-sha256-" + "e" * 64
    first = InterpretationSubject(attachment_event_id=uuid4(), evidence_id=evidence_id)
    second = InterpretationSubject(attachment_event_id=uuid4(), evidence_id=evidence_id)
    assert first != second


def test_objective_proof_draft_must_bind_exactly_one_manifest_requirement() -> None:
    requirement = ProofRequirement(proof_id="user-flag", kind="flag", description="User flag")
    draft = ObjectiveProofDraft(
        proof_requirement_id="user-flag",
        assessment="supported",
        event_ids=(uuid4(),),
    )
    assert draft.bind_manifest((requirement,)) == draft

    with pytest.raises(ValueError, match="proof_requirement_not_in_manifest"):
        draft.model_copy(update={"proof_requirement_id": "root-flag"}).bind_manifest((requirement,))


def test_interpretation_audit_exposes_only_safe_failure_state() -> None:
    subject = InterpretationSubject(
        attachment_event_id=uuid4(),
        evidence_id="evidence-sha256-" + "f" * 64,
    )
    metadata = PlanningCallMetadata(
        purpose="observe",
        provider="local",
        model="structured-model",
        agent_id="agent-1",
        prompt_id="sedna-observation-extractor",
        prompt_version="1",
        response_schema_version="1",
        input_digest=_sha("input"),
        input_tokens=1,
        output_tokens=1,
        elapsed_ms=1,
    )
    audit = InterpretationAudit(
        subject=subject,
        call_metadata=metadata,
        status="failed",
        safe_code="invalid_extractor_output",
    )
    assert audit.safe_code == "invalid_extractor_output"
    with pytest.raises(ValidationError, match="interpretation_audit_status_policy"):
        InterpretationAudit(subject=subject, call_metadata=metadata, status="failed")


def test_planning_audits_are_bounded_and_use_only_safe_codes() -> None:
    metadata = PlanningCallMetadata(
        purpose="plan",
        provider="local",
        model="structured-model",
        agent_id="agent-1",
        prompt_id="sedna-frontier-planner",
        prompt_version="1",
        response_schema_version="1",
        input_digest=_sha("input"),
        input_tokens=1,
        output_tokens=1,
        elapsed_ms=1,
    )
    request = PlanRequestAudit(
        call_metadata=metadata,
        state_digest=_sha("state"),
        ledger_digest=_sha("ledger"),
    )
    repair = PlannerRepairAudit(call_metadata=metadata, critic_finding_codes=("scope_mismatch",))
    rejection = PlannerRejectionAudit(call_metadata=metadata, safe_code="critic_rejected")
    policy = ResearchPolicyDecision(decision_id=uuid4(), allowed=True, rationale="Within policy.")
    assert request.ledger_digest == _sha("ledger")
    assert repair.critic_finding_codes == ("scope_mismatch",)
    assert rejection.safe_code == "critic_rejected"
    assert policy.allowed is True


def test_settlement_result_adapter_round_trips_discriminated_variant() -> None:
    situation = _situation()
    result = SettledSettlementResult(
        engagement_id=situation.engagement_id,
        reason="plan",
        authoritative_journal_revision=situation.authoritative_journal_revision,
        situation=situation,
        required_proof_ids=("user-flag",),
        all_required_proofs_satisfied=False,
        possible_terminal_evidence=False,
    )
    restored = SettlementResultAdapter.validate_python(result.model_dump(mode="json"))
    assert restored == result


def test_settlement_non_failure_variants_accept_required_explicit_null_fields() -> None:
    situation = _situation()
    result = NothingPendingSettlementResult(
        engagement_id=situation.engagement_id,
        reason="plan",
        authoritative_journal_revision=situation.authoritative_journal_revision,
        situation=situation,
        required_proof_ids=("user-flag",),
        all_required_proofs_satisfied=False,
        possible_terminal_evidence=False,
        failure_code=None,
        failure_summary=None,
    )
    assert result.failure_code is None
    assert result.failure_summary is None


def test_proof_rejection_inventory_preserves_event_order_not_lexical_digest_order() -> None:
    progress = ProofProgress(
        proof_requirement_id="user-flag",
        status="pending",
        historical_assessment_digest=_sha("[]"),
        rejected_value_sha256s=("f" * 64, "a" * 64),
        rejected_value_overflow_digest=_sha("[]"),
    )
    assert progress.rejected_value_sha256s == ("f" * 64, "a" * 64)


def test_planning_public_exports_include_core_contracts() -> None:
    assert planning.FrontierProposal is FrontierProposal
    assert planning.InterpretationSubject is InterpretationSubject
    assert planning.PlanningCallMetadata is PlanningCallMetadata
    assert planning.SettlementResultAdapter is SettlementResultAdapter
    assert planning.OutcomeAssessmentDraft is OutcomeAssessmentDraft
    assert planning.ProofIndexRecord is ProofIndexRecord


def test_situation_records_are_attachment_scoped_and_event_grounded() -> None:
    event_id = uuid4()
    subject = InterpretationSubject(
        attachment_event_id=event_id,
        terminal_tool_event_id=uuid4(),
        evidence_id="evidence-sha256-" + "1" * 64,
    )
    state = EvidenceInterpretationState(
        subject=subject,
        status="completed",
        event_ids=(event_id,),
    )
    access = AccessState(subject="operator", state="user-shell", event_ids=(event_id,))
    assert state.subject == subject
    assert access.event_ids == (event_id,)


def test_situation_projection_carries_bounded_derived_state_records() -> None:
    event_id = uuid4()
    projection = SituationProjection(
        engagement_id=uuid4(),
        authoritative_journal_revision=_revision(),
        material_event_revision=2,
        state_digest=_sha("derived-state"),
        objective_progress=ObjectiveProgress(),
        facts=(ObservedFact(text="SSH is open.", event_ids=(event_id,)),),
        access_states=(AccessState(subject="operator", state="user-shell", event_ids=(event_id,)),),
    )
    assert projection.facts[0].text == "SSH is open."
    assert projection.access_states[0].state == "user-shell"


def test_observation_batch_binds_one_subject_and_outcome_assessment() -> None:
    event_id = uuid4()
    subject = InterpretationSubject(
        attachment_event_id=event_id,
        evidence_id="evidence-sha256-" + "2" * 64,
    )
    outcome = OutcomeAssessmentDraft(
        category="progress",
        summary="Authentication state advanced.",
        event_ids=(event_id,),
    )
    batch = ObservationBatchDraft(subject=subject, outcomes=(outcome,))
    assert batch.subject == subject
    assert batch.outcomes == (outcome,)


def test_remaining_strategy_and_proof_contracts_are_data_only_and_bounded() -> None:
    predicate = RetryPredicate(kind="fact_present", value="ssh-open")
    family_draft = StrategyFamilyDraft(runtime_key="family-web", title="Web strategy")
    variant_draft = ExecutionVariantDraft(
        family_runtime_key="family-web", runtime_key="variant-http", title="HTTP variant"
    )
    archive = StrategyArchive(entries=())
    admission = ProofCandidateAdmission(
        proof_requirement_id="user-flag",
        assessment_generation=1,
        candidate_sha256=_sha("FLAG"),
        decision="allowed",
    )
    archive_transition = StrategyArchiveTransition(
        family_id=uuid4(), event_id=uuid4(), rationale="No longer applicable."
    )
    reactivation = StrategyReactivationTransition(
        family_id=uuid4(), event_id=uuid4(), rationale="Prerequisite changed."
    )
    assert predicate.kind == "fact_present"
    assert family_draft.family_id is None
    assert variant_draft.variant_id is None
    assert archive.entries == ()
    assert admission.matched_rejection_event_id is None
    assert archive_transition.family_id != reactivation.family_id
