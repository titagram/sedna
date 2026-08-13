"""Planning service for deterministic situation loading and evidence settlement."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from hashlib import sha256
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sedna.engagement import (
    EngagementJournalService,
    InterpretationFailedEventPayload,
    InterpretationSucceededEventPayload,
    RevisionConflictError,
    SettlementReason,
)
from sedna.engagement.events import EvidenceSliceEventRef, PlanningCallMetadataEventRecord
from sedna.engagement.service import PlanningEventCommitItem
from sedna.planning.journal_events import payloads_from_observation_batch
from sedna.planning.llm import ObservationEvidenceSlice, ObservationRequest
from sedna.planning.models import (
    EVIDENCE_SLICE_BYTES,
    MAX_PLANNING_EVENT_BATCH,
    EvidenceSliceInput,
    FailedSettlementResult,
    IncompleteSettlementResult,
    InterpretationAudit,
    InterpretationSubject,
    InterpretationSucceededSource,
    LocalEventIdBinding,
    NothingPendingSettlementResult,
    ObservationBatchDraft,
    ObservationEventConversion,
    PendingEvidenceRange,
    PlanningCallMetadata,
    SettledSettlementResult,
    SettlementResult,
    SituationProjection,
)
from sedna.planning.ports import TerminalSettlementPort
from sedna.planning.prompts import (
    OBSERVATION_PROMPT,
    OBSERVATION_PROMPT_ID,
    OBSERVATION_PROMPT_VERSION,
)
from sedna.planning.situation import SituationReducer


class _EvidenceReadError(Exception):
    pass


class _JournalAppendError(Exception):
    pass


class PlanningService:
    def __init__(
        self,
        *,
        journal: EngagementJournalService,
        llm: Any,
        clock: Callable[[], datetime],
        terminal_settlement_port: TerminalSettlementPort | None = None,
    ) -> None:
        self._journal = journal
        self._llm = llm
        self._clock = clock
        self._terminal_settlement_port = terminal_settlement_port

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
            _, pending_total, _, _ = self._pending_inventory(
                engagement_id, committed.snapshot
            )
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
            _, pending_total, _, _ = self._pending_inventory(
                engagement_id, committed.snapshot
            )
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
            completion = self._llm.complete(
                ObservationBatchDraft,
                instructions=OBSERVATION_PROMPT,
                payload=request,
                purpose="sedna.planning.observe",
            )
            subject = InterpretationSubject(
                attachment_event_id=descriptor.attachment_event_id,
                evidence_id=descriptor.reference.evidence_id,
            )
            if completion.parsed.subject != subject:
                raise ValueError("observation_subject_mismatch")
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
            conversion = ObservationEventConversion(
                batch=completion.parsed,
                call_metadata=metadata,
                interpretation_audits=(
                    InterpretationAudit(
                        subject=subject,
                        call_metadata=metadata,
                        status="succeeded",
                    ),
                ),
                local_event_bindings=(
                    LocalEventIdBinding(local_id="interpreted", event_id=success_event_id),
                ),
                valid_event_ids=tuple(
                    sorted((descriptor.attachment_event_id, success_event_id), key=str)
                ),
                valid_evidence_ids=(descriptor.reference.evidence_id,),
                evidence_slices=(
                    EvidenceSliceInput(
                        evidence_id=descriptor.reference.evidence_id,
                        start=slice_start,
                        end=slice_start + len(evidence_slice.data),
                        media_type=descriptor.reference.media_type,
                        content=evidence_slice.data,
                    ),
                ),
                sources=(
                    InterpretationSucceededSource(
                        local_id="interpreted",
                        interpretation_id=uuid5(
                            NAMESPACE_URL, f"sedna:interpretation:{source_key}"
                        ),
                        attachment_event_id=descriptor.attachment_event_id,
                        evidence_id=descriptor.reference.evidence_id,
                        covered_slices=(event_ref,),
                        emitted_event_ids=(),
                    ),
                ),
            )
            payload = payloads_from_observation_batch(conversion)[0]
            committed = self._commit_planning_events(
                engagement_id,
                (
                    PlanningEventCommitItem(
                        event_id=success_event_id,
                        payload=payload,
                        idempotency_key=f"planning:interpreted:{success_event_id}",
                    ),
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
