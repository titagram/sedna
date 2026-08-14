"""Ports for dependencies owned by later planning/lifecycle milestones."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from sedna.engagement import (
    EngagementSettlementOutcome,
    EngagementSettlementPort,
    EngagementStatus,
    ExecutionLaneKey,
    JournalRevision,
    SettlementReason,
)
from sedna.planning.models import (
    FailedSettlementResult,
    IncompleteSettlementResult,
    PlanningResult,
    ProofRequirementId,
    SettlementResult,
    SituationProjection,
)

KnowledgeRootResolver = Callable[[], Path]


class PlanningOperations(Protocol):
    def settle_pending_evidence(
        self, engagement_id: UUID, *, reason: SettlementReason
    ) -> SettlementResult: ...

    def plan_next(self, lane: ExecutionLaneKey, *, max_proposals: int = 5) -> PlanningResult: ...


class OwnedPlanningRuntime(Protocol):
    planning: PlanningOperations


PlanningRuntimeFactory = Callable[[Path], AbstractContextManager[OwnedPlanningRuntime]]


class PlanningSettlementAdapter:
    def __init__(self, *, planning: PlanningOperations) -> None:
        self._planning = planning

    def settle(
        self, engagement_id: UUID, *, reason: SettlementReason
    ) -> EngagementSettlementOutcome:
        result = self._planning.settle_pending_evidence(engagement_id, reason=reason)
        if result.status in {"settled", "nothing_pending"}:
            return EngagementSettlementOutcome(status="complete", pending_range_count=0)
        if isinstance(result, IncompleteSettlementResult):
            return EngagementSettlementOutcome(
                status="incomplete",
                pending_range_count=result.pending_total_count,
                next_pending_offset=min(item.start for item in result.pending_ranges),
                next_pending_subject=result.next_pending_subject,
                pending_inventory_sha256=result.pending_inventory_sha256,
                safe_code=(
                    "evidence_budget_exhausted"
                    if result.incomplete_reason == "budget_exhausted"
                    else "interpretation_incomplete"
                ),
            )
        if not isinstance(result, FailedSettlementResult):
            raise TypeError("unknown settlement result")
        if result.failure_code in {"journal_unavailable", "journal_corrupt"}:
            return EngagementSettlementOutcome(
                status="unavailable", pending_range_count=0, safe_code=result.failure_code
            )
        if result.failure_code == "terminal_reconciliation_failed":
            return EngagementSettlementOutcome(
                status="unavailable",
                pending_range_count=0,
                safe_code="settlement_unavailable",
            )
        return EngagementSettlementOutcome(
            status="failed",
            pending_range_count=result.pending_total_count,
            next_pending_offset=(
                min(item.start for item in result.pending_ranges) if result.pending_ranges else None
            ),
            next_pending_subject=result.next_pending_subject,
            pending_inventory_sha256=result.pending_inventory_sha256,
            safe_code="interpretation_failed",
        )


class PlanningSettlementPortFactory:
    def __init__(self, planning_runtime_factory: PlanningRuntimeFactory) -> None:
        self._planning_runtime_factory = planning_runtime_factory

    @contextmanager
    def open(self, resolved_root: Path) -> Iterator[EngagementSettlementPort]:
        with self._planning_runtime_factory(resolved_root) as runtime:
            yield PlanningSettlementAdapter(planning=runtime.planning)


class TerminalReconciliationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    action: Literal[
        "unchanged",
        "proof_close_requested",
        "proof_close_cancelled",
        "proof_close_finalized",
        "failed",
    ]
    authoritative_journal_revision: JournalRevision
    lifecycle_status: EngagementStatus
    safe_code: Literal["terminal_reconciliation_failed"] | None = None

    @model_validator(mode="after")
    def _validate_action_state(self) -> TerminalReconciliationResult:
        if (self.action == "failed") != (self.safe_code is not None):
            raise ValueError("terminal_reconciliation_failure_policy")
        allowed_statuses = {
            "proof_close_requested": {EngagementStatus.CLOSING},
            "proof_close_cancelled": {EngagementStatus.ACTIVE},
            "proof_close_finalized": {
                EngagementStatus.CLOSED_UNVERIFIED,
                EngagementStatus.CLOSED_VERIFIED,
            },
        }
        allowed = allowed_statuses.get(self.action)
        if allowed is not None and self.lifecycle_status not in allowed:
            raise ValueError("terminal_reconciliation_status_policy")
        return self


class TerminalSettlementPort(Protocol):
    def reconcile(
        self,
        *,
        engagement_id: UUID,
        situation: SituationProjection,
        requirement_ids: tuple[ProofRequirementId, ...],
        authoritative_revision: JournalRevision,
        reason: SettlementReason,
        all_required_proofs_satisfied: bool,
    ) -> TerminalReconciliationResult: ...
