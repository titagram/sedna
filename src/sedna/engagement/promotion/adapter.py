"""Sealed promotion-to-journal adapter.

This module is the only promotion layer allowed to hold the repository-issued
promotion writer.  The repository receives canonical bytes and data-only
identities; it does not import promotion models.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, fields
from hashlib import sha256
from typing import Any, Literal, Protocol, cast
from uuid import UUID

from sedna.engagement.models import JournalRevision
from sedna.engagement.promotion.models import (
    PROMOTION_COMPILER_VERSION,
    CommittedPromotionSource,
    PromotionClaimOwnership,
    PromotionClaimRequest,
    PromotionClaimResult,
    PromotionCleanupReceipt,
    PromotionDraft,
    PromotionIndexFailureReceipt,
    PromotionIndexPendingReceipt,
    PromotionPublicationReceipt,
    PromotionResult,
    PromotionSemanticReceipt,
    RenderedPromotionSource,
)
from sedna.engagement.promotion.prompts import (
    PROMOTION_CRITIC_PROMPT_VERSION,
    PROMOTION_EXTRACTOR_PROMPT_VERSION,
    PROMOTION_REPAIR_PROMPT_VERSION,
)
from sedna.engagement.promotion.render import (
    PROMOTION_RENDERER_VERSION,
    PromotionRenderIdentity,
    promotion_source_id,
    render_promotion_source,
)
from sedna.engagement.promotion.sanitize import assert_semantic_promotion_safe
from sedna.engagement.promotion.source import (
    build_nonaccepted_promotion_manifest,
    build_promotion_prepared_source,
)
from sedna.engagement.repository import _PromotionJournalWriter
from sedna.knowledge.parsing import PreparedSource
from sedna.knowledge.retrieval.maintenance import (
    MaintenanceIssueCode,
    RetrievalMaintenanceReport,
    RetrievalMaintenanceService,
)
from sedna.knowledge.schema import foundation_manifest_digest
from sedna.knowledge.semantic import SemanticAcceptanceProfile, SemanticIngestionService
from sedna.knowledge.semantic.compiler import SEMANTIC_COMPILER_VERSION
from sedna.knowledge.semantic.drafts import SemanticCompilationResult
from sedna.knowledge.semantic.prompts import (
    CRITIC_PROMPT_VERSION,
    EXTRACTOR_PROMPT_VERSION,
    REPAIR_PROMPT_VERSION,
)

MAX_PROMOTION_INDEX_RETRIES = 3


@dataclass(frozen=True, repr=False)
class PromotionSourceCommitResult:
    source: CommittedPromotionSource
    event_id: UUID
    created: bool


@dataclass(frozen=True)
class PromotionMutationResult:
    event_id: UUID
    revision: JournalRevision
    created: bool = True


ReceiptIssuer = Callable[..., object]
ReceiptAuthenticator = Callable[[object, type], None]
ReceiptValidator = Callable[[object, type, str], None]


class _ReceiptNonce(Protocol):
    operation_nonce: object


class _ReceiptLedger:
    """Authenticate exact in-memory receipt payloads and one-shot operation uses."""

    __slots__ = ("_records", "_token", "_uses")

    def __init__(self, token: object) -> None:
        self._token = token
        self._records: dict[object, tuple[type, tuple[object, ...]]] = {}
        self._uses: dict[object, set[str]] = {}

    @staticmethod
    def _payload(receipt: object) -> tuple[object, ...]:
        return tuple(
            getattr(receipt, item.name)
            for item in fields(receipt)
            if item.name not in {"_issuer_token", "operation_nonce"}
        )

    def issue(self, receipt_type: type, **values: object) -> object:
        nonce = object()
        receipt = receipt_type(
            _issuer_token=self._token,
            operation_nonce=nonce,
            **values,
        )
        self._records[nonce] = (receipt_type, self._payload(receipt))
        self._uses[nonce] = set()
        return receipt

    def authenticate(self, receipt: object, expected_type: type) -> None:
        nonce = getattr(receipt, "operation_nonce", None)
        record = self._records.get(nonce)
        if (
            type(receipt) is not expected_type
            or getattr(receipt, "_issuer_token", None) is not self._token
            or record != (expected_type, self._payload(receipt))
        ):
            raise ValueError("invalid promotion receipt payload binding")

    def consume(self, receipt: object, expected_type: type, action: str) -> None:
        self.require_available(receipt, expected_type, action)
        nonce = cast(_ReceiptNonce, receipt).operation_nonce
        self._uses[nonce].add(action)

    def require_available(self, receipt: object, expected_type: type, action: str) -> None:
        self.authenticate(receipt, expected_type)
        nonce = cast(_ReceiptNonce, receipt).operation_nonce
        if action in self._uses[nonce]:
            raise ValueError("promotion receipt operation nonce was already consumed")


class PromotionCommitCapability:
    """Package-private sealed authority for promotion transactions."""

    __slots__ = ("_issuer_token", "_receipt_ledger", "_writer")

    def __init__(self, writer: _PromotionJournalWriter) -> None:
        if type(writer) is not _PromotionJournalWriter:
            raise ValueError("promotion capability requires a repository-issued writer")
        writer._require_holder()
        self._writer = writer
        self._issuer_token = object()
        self._receipt_ledger = _ReceiptLedger(self._issuer_token)

    def claim(
        self,
        engagement_id: UUID,
        request: PromotionClaimRequest,
        *,
        expected_revision: JournalRevision,
    ) -> PromotionClaimResult:
        request = PromotionClaimRequest.model_validate(
            request.model_dump(mode="python", warnings="error")
        )
        result = self._writer.claim(
            engagement_id,
            request=request.model_dump(mode="json", warnings="error"),
            expected_revision=expected_revision,
        )
        ownership = None
        if result.disposition in {"created", "resumed"}:
            assert result.event is not None and result.attempt is not None
            ownership = PromotionClaimOwnership(
                attempt_id=result.attempt.attempt_id,
                claim_event_id=result.event.event_id,
                _issuer_token=self._issuer_token,
            )
        return PromotionClaimResult(
            disposition=result.disposition,
            attempt=result.attempt,
            claim_event_id=result.event.event_id if result.event is not None else None,
            revision=result.revision,
            ownership=ownership,
        )

    def commit_candidate(
        self,
        engagement_id: UUID,
        *,
        attempt_id: UUID,
        promotion_revision: int,
        draft: PromotionDraft,
        repair_count: int,
        ownership: PromotionClaimOwnership,
        expected_revision: JournalRevision,
    ) -> PromotionMutationResult:
        if (
            type(ownership) is not PromotionClaimOwnership
            or ownership._issuer_token is not self._issuer_token
            or ownership.attempt_id != attempt_id
        ):
            raise ValueError("invalid promotion claim ownership")
        if type(promotion_revision) is not int or promotion_revision < 1:
            raise ValueError("invalid promotion revision")
        draft = PromotionDraft.model_validate(draft.model_dump(mode="python", warnings="error"))
        candidate_bytes = json.dumps(
            draft.model_dump(mode="json", warnings="error"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        result = self._writer.commit_candidate(
            engagement_id,
            attempt_id=attempt_id,
            promotion_revision=promotion_revision,
            candidate_bytes=candidate_bytes,
            candidate_sha256=sha256(candidate_bytes).hexdigest(),
            repair_count=repair_count,
            expected_revision=expected_revision,
        )
        return PromotionMutationResult(
            event_id=result.event.event_id,
            revision=result.revision,
            created=result.created,
        )

    def load_candidate(
        self,
        engagement_id: UUID,
        *,
        ownership: PromotionClaimOwnership,
    ) -> PromotionDraft:
        if (
            type(ownership) is not PromotionClaimOwnership
            or ownership._issuer_token is not self._issuer_token
        ):
            raise ValueError("invalid promotion claim ownership")
        candidate_bytes = self._writer.load_candidate(
            engagement_id,
            attempt_id=ownership.attempt_id,
            claim_event_id=ownership.claim_event_id,
        )
        return PromotionDraft.model_validate_json(candidate_bytes)

    def terminate(
        self,
        engagement_id: UUID,
        *,
        attempt_id: UUID,
        promotion_revision: int,
        disposition: Literal["quarantined", "failed", "abandoned", "cancelled"],
        reason_code: str,
        cleanup_receipt: PromotionCleanupReceipt | None,
        ownership: PromotionClaimOwnership,
        expected_revision: JournalRevision,
    ) -> PromotionMutationResult:
        if (
            type(ownership) is not PromotionClaimOwnership
            or ownership._issuer_token is not self._issuer_token
            or ownership.attempt_id != attempt_id
        ):
            raise ValueError("invalid promotion claim ownership")
        source_id = canonical_revision = None
        if cleanup_receipt is not None:
            try:
                self._require_receipt(cleanup_receipt, PromotionCleanupReceipt, "terminate_cleanup")
            except ValueError:
                raise ValueError("invalid promotion cleanup receipt") from None
            if (
                cleanup_receipt.attempt_id != attempt_id
                or cleanup_receipt.promotion_revision != promotion_revision
            ):
                raise ValueError("invalid promotion cleanup receipt")
            source_id = cleanup_receipt.source_id
            canonical_revision = cleanup_receipt.canonical_revision
        result = self._writer.terminate(
            engagement_id,
            attempt_id=attempt_id,
            promotion_revision=promotion_revision,
            disposition=disposition,
            reason_code=reason_code,
            cleanup_source_id=source_id,
            cleanup_canonical_revision=canonical_revision,
            claim_event_id=ownership.claim_event_id,
            expected_revision=expected_revision,
        )
        if cleanup_receipt is not None:
            self._consume_receipt(
                cleanup_receipt,
                PromotionCleanupReceipt,
                "terminate_cleanup",
            )
        return PromotionMutationResult(
            event_id=result.events[0].event_id,
            revision=result.revision,
        )

    def commit_source(
        self,
        engagement_id: UUID,
        rendered: RenderedPromotionSource,
        *,
        ownership: PromotionClaimOwnership,
        expected_revision: JournalRevision,
    ) -> PromotionSourceCommitResult:
        if (
            type(ownership) is not PromotionClaimOwnership
            or ownership._issuer_token is not self._issuer_token
            or ownership.attempt_id != rendered.provenance.attempt_id
        ):
            raise ValueError("invalid promotion claim ownership")
        rendered = RenderedPromotionSource.model_validate(
            rendered.model_dump(mode="python", warnings="error")
        )
        if rendered.provenance.engagement_id != engagement_id:
            raise ValueError("promotion source does not belong to the engagement")
        provenance_bytes = json.dumps(
            rendered.provenance.model_dump(mode="json", warnings="error"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        committed = self._writer.commit_source(
            engagement_id,
            attempt_id=rendered.provenance.attempt_id,
            source_id=rendered.source_id,
            promotion_revision=rendered.promotion_revision,
            source_bytes=rendered.markdown.encode("utf-8"),
            source_sha256=rendered.source_sha256,
            provenance_bytes=provenance_bytes,
            provenance_sha256=rendered.provenance_sha256,
            verified_revision=rendered.provenance.verified_revision,
            verification_event_id=rendered.provenance.verification_event_id,
            expected_revision=expected_revision,
        )
        return PromotionSourceCommitResult(
            source=CommittedPromotionSource(
                **rendered.model_dump(mode="python"),
                committed_revision=committed.revision,
            ),
            event_id=committed.event.event_id,
            created=committed.created,
        )

    def _receipt_service(
        self,
        semantic: SemanticIngestionService,
        maintenance: RetrievalMaintenanceService,
    ) -> _PromotionReceiptService:
        """Bind receipt issuance to exact trusted service instances and their operations."""
        if type(semantic) is not SemanticIngestionService:
            raise TypeError("semantic must be an exact SemanticIngestionService")
        if type(maintenance) is not RetrievalMaintenanceService:
            raise TypeError("maintenance must be an exact RetrievalMaintenanceService")
        if semantic._repository is not maintenance.repository:
            raise ValueError("promotion receipt services must share one canonical repository")
        return _PromotionReceiptService(
            semantic,
            maintenance,
            self._receipt_ledger.issue,
            self._receipt_ledger.authenticate,
            self._receipt_ledger.consume,
            self._issuer_token,
        )

    def _require_receipt(self, receipt: object, expected_type: type, action: str) -> None:
        if (
            type(receipt) is not expected_type
            or getattr(receipt, "_issuer_token", None) is not self._issuer_token
        ):
            raise ValueError("invalid promotion receipt")
        self._receipt_ledger.require_available(receipt, expected_type, action)

    def _consume_receipt(self, receipt: object, expected_type: type, action: str) -> None:
        self._receipt_ledger.consume(receipt, expected_type, action)

    @staticmethod
    def _mutation(result) -> PromotionMutationResult:
        return PromotionMutationResult(
            event_id=result.events[0].event_id,
            revision=result.revision,
        )

    def commit_semantic(
        self,
        engagement_id: UUID,
        receipt: PromotionSemanticReceipt,
        *,
        expected_revision: JournalRevision,
    ) -> PromotionMutationResult:
        self._require_receipt(receipt, PromotionSemanticReceipt, "commit_semantic")
        mutation = self._mutation(
            self._writer.commit_semantic(
                engagement_id,
                attempt_id=receipt.attempt_id,
                promotion_revision=receipt.promotion_revision,
                source_id=receipt.source_id,
                foundation_manifest_sha256=receipt.foundation_manifest_sha256,
                artifact_ids=receipt.artifact_ids,
                expected_revision=expected_revision,
            )
        )
        self._consume_receipt(receipt, PromotionSemanticReceipt, "commit_semantic")
        return mutation

    def commit_index_pending(
        self,
        engagement_id: UUID,
        receipt: PromotionIndexPendingReceipt,
        *,
        expected_revision: JournalRevision,
    ) -> PromotionMutationResult:
        self._require_receipt(receipt, PromotionIndexPendingReceipt, "commit_index_pending")
        mutation = self._mutation(
            self._writer.commit_index_pending(
                engagement_id,
                attempt_id=receipt.attempt_id,
                promotion_revision=receipt.promotion_revision,
                source_id=receipt.source_id,
                expected_canonical_revision=receipt.expected_canonical_revision,
                expected_revision=expected_revision,
            )
        )
        self._consume_receipt(receipt, PromotionIndexPendingReceipt, "commit_index_pending")
        return mutation

    def commit_index_retry(
        self,
        engagement_id: UUID,
        receipt: PromotionIndexFailureReceipt,
        *,
        expected_revision: JournalRevision,
    ) -> PromotionMutationResult:
        self._require_receipt(receipt, PromotionIndexFailureReceipt, "commit_index_retry")
        mutation = self._mutation(
            self._writer.commit_index_retry(
                engagement_id,
                attempt_id=receipt.attempt_id,
                promotion_revision=receipt.promotion_revision,
                retry_count=receipt.retry_count,
                reason_code=receipt.reason_code,
                expected_revision=expected_revision,
            )
        )
        self._consume_receipt(receipt, PromotionIndexFailureReceipt, "commit_index_retry")
        return mutation

    def commit_promoted(
        self,
        engagement_id: UUID,
        receipt: PromotionPublicationReceipt,
        *,
        expected_revision: JournalRevision,
    ) -> PromotionMutationResult:
        self._require_receipt(receipt, PromotionPublicationReceipt, "commit_promoted")
        mutation = self._mutation(
            self._writer.commit_promoted(
                engagement_id,
                attempt_id=receipt.attempt_id,
                promotion_revision=receipt.promotion_revision,
                source_id=receipt.source_id,
                case_ids=receipt.case_ids,
                expected_revision=expected_revision,
            )
        )
        self._consume_receipt(receipt, PromotionPublicationReceipt, "commit_promoted")
        return mutation

    def load_state(self, engagement_id: UUID):
        """Recover publication progress exclusively from the durable journal."""

        return self._writer.load_snapshot(engagement_id).state.promotion

    def active_attempt(
        self,
        engagement_id: UUID,
        *,
        ownership: PromotionClaimOwnership,
        expected_stages: set[str],
    ) -> tuple[Any, JournalRevision]:
        """Recheck the exact owned durable stage before one physical mutation."""

        if (
            type(ownership) is not PromotionClaimOwnership
            or ownership._issuer_token is not self._issuer_token
        ):
            raise ValueError("invalid promotion claim ownership")
        return self._writer.authenticate_claim(
            engagement_id,
            attempt_id=ownership.attempt_id,
            claim_event_id=ownership.claim_event_id,
            expected_stages=expected_stages,
        )


class _PromotionReceiptService:
    """Mint journal receipts only from completed canonical service operations."""

    __slots__ = (
        "_authenticate",
        "_issue",
        "_maintenance",
        "_semantic",
        "_token",
        "_validate",
    )

    def __init__(
        self,
        semantic: SemanticIngestionService,
        maintenance: RetrievalMaintenanceService,
        issue: ReceiptIssuer,
        authenticate: ReceiptAuthenticator,
        validate: ReceiptValidator,
        token: object,
    ) -> None:
        self._semantic = semantic
        self._maintenance = maintenance
        self._issue = issue
        self._authenticate = authenticate
        self._validate = validate
        self._token = token

    def persist_semantic(
        self,
        prepared: PreparedSource,
        compilation: SemanticCompilationResult,
        *,
        ownership: PromotionClaimOwnership,
        promotion_revision: int,
    ) -> tuple[SemanticCompilationResult, PromotionSemanticReceipt]:
        """Persist one service-issued compilation and attest only the verified result."""
        self._require_ownership(ownership)
        persisted = self._semantic.persist_compilation(
            prepared,
            compilation,
            acceptance_profile=SemanticAcceptanceProfile.JOURNAL_PROMOTION,
        )
        bundle = persisted.bundle
        if persisted.disposition != "verified" or bundle is None:
            raise ValueError("journal promotion semantic persistence was not verified")
        self._require_current(prepared)
        receipt = self._issue(
            PromotionSemanticReceipt,
            attempt_id=ownership.attempt_id,
            promotion_revision=promotion_revision,
            source_id=prepared.manifest.source_id,
            foundation_manifest_sha256=foundation_manifest_digest(prepared.manifest),
            artifact_ids=bundle.compilation_manifest.emitted_artifact_ids,
        )
        assert type(receipt) is PromotionSemanticReceipt
        return persisted, receipt

    def canonical_currentness(
        self,
        prepared: PreparedSource,
        semantic_receipt: PromotionSemanticReceipt,
        *,
        ownership: PromotionClaimOwnership,
        promotion_revision: int,
    ) -> PromotionIndexPendingReceipt:
        """Attest the exact canonical snapshot containing the persisted promotion."""
        self._require_prior_receipt(
            semantic_receipt,
            PromotionSemanticReceipt,
            prepared,
            ownership,
            promotion_revision,
        )
        snapshot, _bundle = self._require_current_snapshot(prepared, semantic_receipt.artifact_ids)
        self._validate(semantic_receipt, PromotionSemanticReceipt, "canonical_currentness")
        receipt = self._issue(
            PromotionIndexPendingReceipt,
            attempt_id=ownership.attempt_id,
            promotion_revision=promotion_revision,
            source_id=prepared.manifest.source_id,
            expected_canonical_revision=snapshot.revision,
        )
        assert type(receipt) is PromotionIndexPendingReceipt
        return receipt

    def recover_semantic(
        self,
        prepared: PreparedSource,
        *,
        artifact_ids: tuple[str, ...],
        ownership: PromotionClaimOwnership,
        promotion_revision: int,
    ) -> PromotionSemanticReceipt:
        """Re-attest only event-bound canonical semantics after a runtime restart."""

        self._require_ownership(ownership)
        _snapshot, bundle = self._require_current_snapshot(prepared, artifact_ids)
        receipt = self._issue(
            PromotionSemanticReceipt,
            attempt_id=ownership.attempt_id,
            promotion_revision=promotion_revision,
            source_id=prepared.manifest.source_id,
            foundation_manifest_sha256=foundation_manifest_digest(prepared.manifest),
            artifact_ids=bundle.compilation_manifest.emitted_artifact_ids,
        )
        assert type(receipt) is PromotionSemanticReceipt
        return receipt

    def exclude_foundation(
        self,
        prepared: PreparedSource,
        *,
        ownership: PromotionClaimOwnership,
        promotion_revision: int,
        reason: str,
    ) -> PromotionCleanupReceipt:
        """Exclude a committed foundation and prove retrieval absence before termination."""

        self._require_ownership(ownership)
        current = self._semantic._repository.load_manifest(prepared.manifest.source_id)
        excluded = build_nonaccepted_promotion_manifest(current, reason=cast(Any, reason))
        if current != excluded:
            self._semantic._repository.transition_source(
                excluded,
                None,
                before_foundation_revision_change=self._maintenance.barrier_source_revision,
            )
            report = self._maintenance.rebuild()
        else:
            report = self._maintenance.audit()
        if not report.succeeded or report.rebuild_required:
            raise ValueError("promotion foundation cleanup could not prove index absence")
        snapshot = self._semantic._repository.semantic_bundle_snapshot()
        if any(bundle.source_id == prepared.manifest.source_id for bundle in snapshot.bundles):
            raise ValueError("excluded promotion foundation remains semantically retrievable")
        receipt = self._issue(
            PromotionCleanupReceipt,
            attempt_id=ownership.attempt_id,
            promotion_revision=promotion_revision,
            source_id=prepared.manifest.source_id,
            canonical_revision=foundation_manifest_digest(excluded),
        )
        assert type(receipt) is PromotionCleanupReceipt
        return receipt

    def rebuild_index(
        self,
        prepared: PreparedSource,
        pending_receipt: PromotionIndexPendingReceipt,
        *,
        ownership: PromotionClaimOwnership,
        promotion_revision: int,
        retry_count: int,
    ) -> tuple[
        RetrievalMaintenanceReport,
        PromotionIndexFailureReceipt | PromotionPublicationReceipt,
    ]:
        """Return a failure or publication receipt from this exact rebuild invocation."""
        self._require_prior_receipt(
            pending_receipt,
            PromotionIndexPendingReceipt,
            prepared,
            ownership,
            promotion_revision,
        )
        if type(retry_count) is not int or retry_count < 1:
            raise ValueError("retry_count must be a positive integer")
        snapshot, _bundle = self._require_current_snapshot(prepared)
        if snapshot.revision != pending_receipt.expected_canonical_revision:
            raise ValueError("canonical revision changed before promotion index rebuild")
        self._validate(pending_receipt, PromotionIndexPendingReceipt, "rebuild_index")
        report = self._maintenance.rebuild()
        if not report.succeeded or report.rebuild_required:
            unavailable = any(
                issue.code is MaintenanceIssueCode.INDEX_UNAVAILABLE for issue in report.issues
            )
            receipt = self._issue(
                PromotionIndexFailureReceipt,
                attempt_id=ownership.attempt_id,
                promotion_revision=promotion_revision,
                retry_count=retry_count,
                reason_code="index_unavailable" if unavailable else "index_rebuild_failed",
            )
            assert type(receipt) is PromotionIndexFailureReceipt
            return report, receipt
        snapshot, bundle = self._require_current_snapshot(prepared)
        if snapshot.revision != pending_receipt.expected_canonical_revision:
            raise ValueError("canonical revision changed during promotion index rebuild")
        receipt = self._issue(
            PromotionPublicationReceipt,
            attempt_id=ownership.attempt_id,
            promotion_revision=promotion_revision,
            source_id=prepared.manifest.source_id,
            case_ids=tuple(item.case_id for item in bundle.cases),
        )
        assert type(receipt) is PromotionPublicationReceipt
        return report, receipt

    def _require_ownership(self, ownership: PromotionClaimOwnership) -> None:
        if (
            type(ownership) is not PromotionClaimOwnership
            or ownership._issuer_token is not self._token
        ):
            raise ValueError("invalid promotion claim ownership")

    def _require_prior_receipt(
        self,
        receipt: object,
        receipt_type: type,
        prepared: PreparedSource,
        ownership: PromotionClaimOwnership,
        promotion_revision: int,
    ) -> None:
        self._require_ownership(ownership)
        if (
            type(receipt) is not receipt_type
            or getattr(receipt, "_issuer_token", None) is not self._token
            or getattr(receipt, "attempt_id", None) != ownership.attempt_id
            or getattr(receipt, "promotion_revision", None) != promotion_revision
            or getattr(receipt, "source_id", prepared.manifest.source_id)
            != prepared.manifest.source_id
        ):
            raise ValueError("promotion receipt does not match its trusted operation chain")
        self._authenticate(receipt, receipt_type)

    def _require_current(self, prepared: PreparedSource) -> None:
        self._semantic._repository.require_journal_promotion_physical_state(prepared.manifest)
        if not self._semantic.is_current(prepared):
            raise ValueError("journal promotion semantic state is not current")

    def _require_current_snapshot(
        self,
        prepared: PreparedSource,
        artifact_ids: tuple[str, ...] | None = None,
    ):
        self._require_current(prepared)
        snapshot = self._semantic._repository.semantic_bundle_snapshot()
        matches = tuple(
            bundle for bundle in snapshot.bundles if bundle.source_id == prepared.manifest.source_id
        )
        if len(matches) != 1:
            raise ValueError("canonical promotion bundle is unavailable")
        bundle = matches[0]
        if (
            artifact_ids is not None
            and bundle.compilation_manifest.emitted_artifact_ids != artifact_ids
        ):
            raise ValueError("canonical promotion artifacts do not match semantic persistence")
        return snapshot, bundle


class JournalPromotionAdapter:
    """Sole orchestration authority for guarded canonical/index publication."""

    def __init__(
        self,
        capability: PromotionCommitCapability,
        *,
        inputs: Any,
        compiler: Any,
        semantic: SemanticIngestionService,
        maintenance: RetrievalMaintenanceService,
        evidence_reader: Callable[..., object],
    ) -> None:
        if type(capability) is not PromotionCommitCapability:
            raise TypeError("capability must be an exact PromotionCommitCapability")
        if not callable(evidence_reader):
            raise TypeError("evidence_reader must be callable")
        self._capability = capability
        self._inputs = inputs
        self._compiler = compiler
        self._semantic = semantic
        self._maintenance = maintenance
        self._evidence_reader = evidence_reader
        self._receipts = capability._receipt_service(semantic, maintenance)

    @staticmethod
    def _claim_request(
        revision: JournalRevision, verification_event_id: UUID
    ) -> PromotionClaimRequest:
        return PromotionClaimRequest(
            verified_revision=revision,
            verification_event_id=verification_event_id,
            compiler_version=PROMOTION_COMPILER_VERSION,
            extractor_prompt_version=PROMOTION_EXTRACTOR_PROMPT_VERSION,
            critic_prompt_version=PROMOTION_CRITIC_PROMPT_VERSION,
            repair_prompt_version=PROMOTION_REPAIR_PROMPT_VERSION,
            renderer_version=PROMOTION_RENDERER_VERSION,
            semantic_compiler_version=SEMANTIC_COMPILER_VERSION,
            semantic_prompt_versions=(
                EXTRACTOR_PROMPT_VERSION,
                CRITIC_PROMPT_VERSION,
                REPAIR_PROMPT_VERSION,
            ),
        )

    @staticmethod
    def _completed(snapshot: Any, verification_event_id: UUID) -> PromotionResult | None:
        for attempt in reversed(snapshot.state.promotion.recent_terminal_attempts):
            if (
                attempt.verification_event_id == verification_event_id
                and attempt.disposition == "promoted"
            ):
                return PromotionResult(
                    disposition="unchanged",
                    attempt_id=attempt.attempt_id,
                    promotion_revision=attempt.promotion_revision,
                    source_id=attempt.source_id,
                    case_ids=attempt.case_ids,
                    journal_revision=snapshot.revision,
                )
        return None

    def promote_verified(
        self,
        engagement_id: UUID,
        *,
        expected_revision: JournalRevision,
        verification_event_id: UUID,
    ) -> PromotionResult:
        """Publish an exact verified snapshot or coalesce with its durable attempt."""

        snapshot = self._capability._writer.load_snapshot(engagement_id)
        completed = self._completed(snapshot, verification_event_id)
        if completed is not None:
            return completed
        active = getattr(snapshot.state.promotion, "active_attempt", None)
        resumable = (
            active is not None
            and active.verification_event_id == verification_event_id
            and active.verified_revision == expected_revision
        )
        if snapshot.revision != expected_revision and not resumable:
            raise ValueError("promotion expected revision is stale")
        projection = self._inputs.project(
            snapshot,
            verification_event_id=verification_event_id,
            evidence_reader=self._evidence_reader,
        )
        claim = self._capability.claim(
            engagement_id,
            self._claim_request(expected_revision, verification_event_id),
            expected_revision=snapshot.revision if resumable else expected_revision,
        )
        if claim.disposition == "retry_exhausted":
            return PromotionResult(disposition="retry_exhausted", journal_revision=claim.revision)
        attempt = claim.attempt
        if attempt is None:
            raise ValueError("promotion claim did not return an attempt")
        if claim.disposition == "existing":
            return PromotionResult(
                disposition="in_progress",
                attempt_id=attempt.attempt_id,
                promotion_revision=attempt.promotion_revision,
                source_id=attempt.source_id,
                case_ids=attempt.case_ids,
                journal_revision=claim.revision,
            )
        ownership = claim.ownership
        if ownership is None:
            raise ValueError("created promotion claim has no ownership")
        return self._run_owned(
            engagement_id,
            expected_revision,
            verification_event_id,
            projection,
            attempt,
            ownership,
            claim.revision,
        )

    def _run_owned(
        self,
        engagement_id: UUID,
        verified_revision: JournalRevision,
        verification_event_id: UUID,
        projection: Any,
        attempt: Any,
        ownership: PromotionClaimOwnership,
        revision: JournalRevision,
    ) -> PromotionResult:
        if attempt.stage == "requested":
            compiled = self._compiler.compile(
                projection.safe_input,
                inventory=projection.inventory,
            )
            if compiled.disposition != "verified" or compiled.draft is None:
                disposition = "quarantined" if compiled.disposition == "quarantined" else "failed"
                mutation = self._capability.terminate(
                    engagement_id,
                    attempt_id=attempt.attempt_id,
                    promotion_revision=attempt.promotion_revision,
                    disposition=disposition,
                    reason_code=compiled.failure_code or "semantic_failure",
                    cleanup_receipt=None,
                    ownership=ownership,
                    expected_revision=revision,
                )
                return PromotionResult(
                    disposition=disposition,
                    attempt_id=attempt.attempt_id,
                    promotion_revision=attempt.promotion_revision,
                    journal_revision=mutation.revision,
                    reason_code=compiled.failure_code,
                )
            draft = compiled.draft
            candidate = self._capability.commit_candidate(
                engagement_id,
                attempt_id=attempt.attempt_id,
                promotion_revision=attempt.promotion_revision,
                draft=draft,
                repair_count=compiled.repair_count,
                ownership=ownership,
                expected_revision=revision,
            )
            revision = candidate.revision
        else:
            draft = self._capability.load_candidate(engagement_id, ownership=ownership)
        rendered = render_promotion_source(
            draft,
            context=projection.safe_input,
            inventory=projection.inventory,
            identity=PromotionRenderIdentity(
                engagement_id=engagement_id,
                attempt_id=attempt.attempt_id,
                verification_event_id=verification_event_id,
                verified_revision=verified_revision,
                source_id=promotion_source_id(engagement_id),
                promotion_revision=attempt.promotion_revision,
            ),
        )
        source = self._capability.commit_source(
            engagement_id,
            rendered,
            ownership=ownership,
            expected_revision=revision,
        )
        return self._publish_owned(
            engagement_id,
            projection,
            attempt,
            ownership,
            source.source,
        )

    def _publish_owned(
        self,
        engagement_id: UUID,
        projection: Any,
        attempt: Any,
        ownership: PromotionClaimOwnership,
        source: CommittedPromotionSource,
    ) -> PromotionResult:
        prepared = build_promotion_prepared_source(source)
        active, revision = self._capability.active_attempt(
            engagement_id,
            ownership=ownership,
            expected_stages={
                "source_committed",
                "semantic_committed",
                "index_pending",
                "retry_failed",
            },
        )
        if (
            active.stage == "retry_failed"
            and active.index_retry_count >= MAX_PROMOTION_INDEX_RETRIES
        ):
            return self._recover_index_exhaustion(
                engagement_id,
                prepared,
                attempt,
                ownership,
            )
        semantic_candidate = None
        if active.stage == "source_committed":
            with self._semantic._repository.promotion_publication_guard(
                prepared.manifest.source_id
            ):
                self._capability.active_attempt(
                    engagement_id,
                    ownership=ownership,
                    expected_stages={"source_committed"},
                )
                self._semantic._repository.transition_source(
                    prepared.manifest,
                    None,
                    before_foundation_revision_change=self._maintenance.barrier_source_revision,
                )
            semantic_candidate = self._semantic.compile_candidate(
                prepared,
                acceptance_profile=SemanticAcceptanceProfile.JOURNAL_PROMOTION,
            )
        if semantic_candidate is not None and semantic_candidate.disposition not in {
            "verified",
            "unchanged",
        }:
            reason = (
                "required_case_missing"
                if semantic_candidate.failure_code == "required_case_missing"
                else (
                    "semantic_quarantined"
                    if semantic_candidate.disposition == "quarantined"
                    else "semantic_failure"
                )
            )
            return self._terminate_foundation(
                engagement_id,
                prepared,
                attempt,
                ownership,
                revision,
                reason=reason,
                disposition=(
                    "quarantined" if semantic_candidate.disposition == "quarantined" else "failed"
                ),
            )
        try:
            if semantic_candidate is not None:
                assert_semantic_promotion_safe(semantic_candidate, projection.inventory)
        except ValueError:
            return self._terminate_foundation(
                engagement_id,
                prepared,
                attempt,
                ownership,
                revision,
                reason="unsafe_material",
                disposition="failed",
            )
        if semantic_candidate is not None:
            with self._semantic._repository.promotion_publication_guard(
                prepared.manifest.source_id
            ):
                _active, revision = self._capability.active_attempt(
                    engagement_id,
                    ownership=ownership,
                    expected_stages={"source_committed"},
                )
                _persisted, semantic_receipt = self._receipts.persist_semantic(
                    prepared,
                    semantic_candidate,
                    ownership=ownership,
                    promotion_revision=attempt.promotion_revision,
                )
                semantic_event = self._capability.commit_semantic(
                    engagement_id,
                    semantic_receipt,
                    expected_revision=revision,
                )
                revision = semantic_event.revision
        else:
            semantic_receipt = self._receipts.recover_semantic(
                prepared,
                artifact_ids=active.artifact_ids,
                ownership=ownership,
                promotion_revision=attempt.promotion_revision,
            )
        if active.stage in {"source_committed", "semantic_committed"}:
            with self._semantic._repository.promotion_publication_guard(
                prepared.manifest.source_id
            ):
                _active, revision = self._capability.active_attempt(
                    engagement_id,
                    ownership=ownership,
                    expected_stages={"semantic_committed"},
                )
                pending_receipt = self._receipts.canonical_currentness(
                    prepared,
                    semantic_receipt,
                    ownership=ownership,
                    promotion_revision=attempt.promotion_revision,
                )
                pending_event = self._capability.commit_index_pending(
                    engagement_id,
                    pending_receipt,
                    expected_revision=revision,
                )
                revision = pending_event.revision
        else:
            pending_receipt = self._receipts.canonical_currentness(
                prepared,
                semantic_receipt,
                ownership=ownership,
                promotion_revision=attempt.promotion_revision,
            )
        active, revision = self._capability.active_attempt(
            engagement_id,
            ownership=ownership,
            expected_stages={"index_pending", "retry_failed"},
        )
        retry_count = active.index_retry_count + 1
        with self._semantic._repository.promotion_publication_guard(prepared.manifest.source_id):
            _active, revision = self._capability.active_attempt(
                engagement_id,
                ownership=ownership,
                expected_stages={"index_pending", "retry_failed"},
            )
            _report, publication = self._receipts.rebuild_index(
                prepared,
                pending_receipt,
                ownership=ownership,
                promotion_revision=attempt.promotion_revision,
                retry_count=retry_count,
            )
            if type(publication) is PromotionIndexFailureReceipt:
                if retry_count >= MAX_PROMOTION_INDEX_RETRIES:
                    retry = self._capability.commit_index_retry(
                        engagement_id,
                        publication,
                        expected_revision=revision,
                    )
                    cleanup = self._receipts.exclude_foundation(
                        prepared,
                        ownership=ownership,
                        promotion_revision=attempt.promotion_revision,
                        reason="index_retry_exhausted",
                    )
                    terminated = self._capability.terminate(
                        engagement_id,
                        attempt_id=attempt.attempt_id,
                        promotion_revision=attempt.promotion_revision,
                        disposition="failed",
                        reason_code="index_retry_exhausted",
                        cleanup_receipt=cleanup,
                        ownership=ownership,
                        expected_revision=retry.revision,
                    )
                    return PromotionResult(
                        disposition="failed",
                        attempt_id=attempt.attempt_id,
                        promotion_revision=attempt.promotion_revision,
                        source_id=prepared.manifest.source_id,
                        journal_revision=terminated.revision,
                        reason_code="index_retry_exhausted",
                    )
                retry = self._capability.commit_index_retry(
                    engagement_id,
                    publication,
                    expected_revision=revision,
                )
                return PromotionResult(
                    disposition="retrying",
                    attempt_id=attempt.attempt_id,
                    promotion_revision=attempt.promotion_revision,
                    source_id=prepared.manifest.source_id,
                    journal_revision=retry.revision,
                    reason_code=publication.reason_code,
                )
            publication = cast(PromotionPublicationReceipt, publication)
            promoted = self._capability.commit_promoted(
                engagement_id,
                publication,
                expected_revision=revision,
            )
            return PromotionResult(
                disposition="promoted",
                attempt_id=attempt.attempt_id,
                promotion_revision=attempt.promotion_revision,
                source_id=prepared.manifest.source_id,
                case_ids=publication.case_ids,
                journal_revision=promoted.revision,
            )

    def _recover_index_exhaustion(
        self,
        engagement_id: UUID,
        prepared: PreparedSource,
        attempt: Any,
        ownership: PromotionClaimOwnership,
    ) -> PromotionResult:
        with self._semantic._repository.promotion_publication_guard(prepared.manifest.source_id):
            active, revision = self._capability.active_attempt(
                engagement_id,
                ownership=ownership,
                expected_stages={"retry_failed"},
            )
            if active.index_retry_count != MAX_PROMOTION_INDEX_RETRIES:
                raise ValueError("promotion retry exhaustion fence no longer matches")
            cleanup = self._receipts.exclude_foundation(
                prepared,
                ownership=ownership,
                promotion_revision=attempt.promotion_revision,
                reason="index_retry_exhausted",
            )
            terminated = self._capability.terminate(
                engagement_id,
                attempt_id=attempt.attempt_id,
                promotion_revision=attempt.promotion_revision,
                disposition="failed",
                reason_code="index_retry_exhausted",
                cleanup_receipt=cleanup,
                ownership=ownership,
                expected_revision=revision,
            )
        return PromotionResult(
            disposition="failed",
            attempt_id=attempt.attempt_id,
            promotion_revision=attempt.promotion_revision,
            source_id=prepared.manifest.source_id,
            journal_revision=terminated.revision,
            reason_code="index_retry_exhausted",
        )

    def _terminate_foundation(
        self,
        engagement_id: UUID,
        prepared: PreparedSource,
        attempt: Any,
        ownership: PromotionClaimOwnership,
        revision: JournalRevision,
        *,
        reason: str,
        disposition: Literal["quarantined", "failed"],
    ) -> PromotionResult:
        del revision
        with self._semantic._repository.promotion_publication_guard(prepared.manifest.source_id):
            _active, current_revision = self._capability.active_attempt(
                engagement_id,
                ownership=ownership,
                expected_stages={"source_committed"},
            )
            cleanup = self._receipts.exclude_foundation(
                prepared,
                ownership=ownership,
                promotion_revision=attempt.promotion_revision,
                reason=reason,
            )
            mutation = self._capability.terminate(
                engagement_id,
                attempt_id=attempt.attempt_id,
                promotion_revision=attempt.promotion_revision,
                disposition=disposition,
                reason_code=reason,
                cleanup_receipt=cleanup,
                ownership=ownership,
                expected_revision=current_revision,
            )
        return PromotionResult(
            disposition=disposition,
            attempt_id=attempt.attempt_id,
            promotion_revision=attempt.promotion_revision,
            source_id=prepared.manifest.source_id,
            journal_revision=mutation.revision,
            reason_code=reason,
        )
