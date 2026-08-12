"""Ports for dependencies owned by later planning/lifecycle milestones."""

from __future__ import annotations

from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from sedna.engagement import EngagementStatus, JournalRevision, SettlementReason
from sedna.planning.models import ProofRequirementId, SituationProjection


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
