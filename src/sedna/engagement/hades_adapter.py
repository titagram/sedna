"""Hades control tools and observer-hook adapter for engagement journals."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sedna.engagement.events import (
    CONTROL_TOOL_NAMES,
    CONTROL_TOOL_POLICY_VERSION,
    ControlToolInvokedPayload,
    EvidenceAttachedPayload,
    JournalEventDraft,
    SessionCheckpointedPayload,
    SessionFinalizedPayload,
    SessionStartedPayload,
    ToolCallCompletedPayload,
    ToolCallStartedPayload,
    ToolCorrelation,
)
from sedna.engagement.models import (
    MAX_HEALTH_ENTRIES_PER_STORE,
    MAX_HEALTH_ENTRIES_TOTAL,
    MAX_HEALTH_OCCURRENCES,
    MAX_HOST_RESULT_BYTES,
    MAX_JOURNAL_EVENTS,
    MAX_PUBLIC_INVENTORY_ITEMS,
    MAX_REQUIRED_PROOFS,
    MAX_TOOL_DURATION_MS,
    AuthorizationScope,
    EngagementStatus,
    HostAdaptedCommandRecord,
    HostKind,
    JournalRevision,
    ProofRequirement,
    Sha256Hex,
)
from sedna.engagement.normalization import (
    NormalizationFailure,
    SanitizedHostValue,
    normalize_host_payload,
    sanitize_host_arguments,
)
from sedna.engagement.service import (
    EngagementJournalService,
    EngagementSettlementPortFactory,
)
from sedna.engagement.sources import (
    SharedSourceEntry,
    SharedSourceRegistry,
    SourceRegistryLimitError,
)

HookHealthCode = Literal[
    "journal_unavailable",
    "journal_corrupt",
    "evidence_capture_failed",
    "in_flight_limit_exceeded",
    "unmatched_completion",
    "ambiguous_binding",
    "unbound_lane",
    "unknown_child_status",
    "settlement_incomplete",
    "settlement_failed",
    "settlement_unavailable",
    "logbook_rebuild_conflict",
]

_HEALTH_CODES = frozenset(HookHealthCode.__args__)  # type: ignore[attr-defined]


class _ManageEngagementInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    action: Literal[
        "create",
        "resume",
        "inspect",
        "list",
        "close",
        "reopen",
        "abandon",
        "change_scope",
        "change_objective",
        "unbind",
        "resolve_call",
    ]
    engagement_id: UUID | None = None
    display_name: Annotated[str | None, Field(min_length=1, max_length=256)] = None
    objective: Annotated[str | None, Field(min_length=1, max_length=8192)] = None
    authorization: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=2048)], ...],
        Field(max_length=256),
    ] = ()
    required_proofs: Annotated[
        tuple[ProofRequirement, ...], Field(max_length=MAX_REQUIRED_PROOFS)
    ] = ()
    reason: Annotated[str | None, Field(min_length=1, max_length=1024)] = None
    authorization_basis: Annotated[str | None, Field(min_length=1, max_length=2048)] = None
    call_id: Annotated[str | None, Field(pattern=r"^call-[0-9a-f]{64}$")] = None
    resolution: Literal["timed_out", "abandoned"] | None = None
    after_engagement_id: UUID | None = None
    after_call_id: Annotated[str | None, Field(pattern=r"^call-[0-9a-f]{64}$")] = None
    limit: int = Field(default=64, ge=1, le=64)

    @model_validator(mode="after")
    def validate_action(self) -> _ManageEngagementInput:
        if self.action == "create" and (
            not self.display_name or not self.objective or not self.authorization
        ):
            raise ValueError("create requires display_name, objective, authorization")
        if self.action == "resolve_call" and not (
            self.call_id and self.resolution and self.reason
        ):
            raise ValueError("resolve_call requires call_id, resolution, reason")
        if self.action == "inspect" and (
            self.after_call_id is not None and self.after_engagement_id is not None
        ):
            raise ValueError("inspect accepts at most one pagination cursor")
        if self.action != "inspect" and self.after_call_id is not None:
            raise ValueError("after_call_id is accepted only by inspect")
        return self


class RecordDecisionInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    engagement_id: UUID | None = None
    proposal_id: UUID | None = None
    custom_strategy: Annotated[str | None, Field(min_length=1, max_length=8192)] = None
    rationale: Annotated[str | None, Field(min_length=1, max_length=8192)] = None
    host_adapted_command: HostAdaptedCommandRecord | None = None

    @model_validator(mode="after")
    def validate_branch(self) -> RecordDecisionInput:
        if self.proposal_id is not None:
            if self.custom_strategy is not None or self.rationale is not None:
                raise ValueError("proposal selection forbids custom strategy and rationale")
        else:
            if not (self.custom_strategy and self.rationale):
                raise ValueError("custom selection requires strategy and rationale")
        return self


class AddSourceInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    name: Annotated[str, Field(min_length=1, max_length=256)]
    locator: Annotated[str, Field(min_length=1, max_length=4096)]
    topics: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=128)], ...],
        Field(max_length=64),
    ] = ()
    notes: Annotated[str, Field(max_length=8192)] = ""


class PublicStringInventoryPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    items: tuple[Annotated[str, Field(min_length=1, max_length=512)], ...] = Field(
        max_length=MAX_PUBLIC_INVENTORY_ITEMS
    )
    total_count: int = Field(ge=0, le=MAX_JOURNAL_EVENTS)
    next_after_id: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    omitted_items_sha256: Sha256Hex | None = None


class EngagementPublicSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    engagement_id: UUID
    display_name: Annotated[str, Field(min_length=1, max_length=256)]
    status: EngagementStatus
    revision: JournalRevision
    bound_lanes: PublicStringInventoryPage
    active_decisions: PublicStringInventoryPage
    in_flight_call_ids: PublicStringInventoryPage


def _bounded_page(
    values: tuple[str, ...], *, limit: int = MAX_PUBLIC_INVENTORY_ITEMS
) -> PublicStringInventoryPage:
    ordered = tuple(sorted(values))
    page = ordered[:limit]
    omitted = ordered[limit:]
    digest = (
        sha256(",".join(omitted).encode("utf-8")).hexdigest() if omitted else None
    )
    return PublicStringInventoryPage(
        items=page,
        total_count=len(ordered),
        next_after_id=page[-1] if len(ordered) > limit else None,
        omitted_items_sha256=digest,
    )


class _HealthEntry:
    __slots__ = ("code", "count")

    def __init__(self, code: str) -> None:
        self.code = code
        self.count = 1


class _HealthMap:
    """Thread-safe bounded insertion-ordered health map keyed by store+session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], _HealthEntry] = {}

    def record(self, store_digest: str, session_id: str, code: str) -> None:
        if code not in _HEALTH_CODES:
            raise ValueError("unknown hook health code")
        with self._lock:
            key = (store_digest, session_id)
            entry = self._entries.get(key)
            if entry is not None and entry.code == code:
                if entry.count < MAX_HEALTH_OCCURRENCES:
                    entry.count += 1
                return
            if entry is not None:
                del self._entries[key]
            self._entries[key] = _HealthEntry(code)
            self._evict()

    def purge(self, store_digest: str, session_id: str) -> None:
        with self._lock:
            self._entries.pop((store_digest, session_id), None)

    def peek(self, store_digest: str, session_id: str) -> tuple[str, int] | None:
        with self._lock:
            entry = self._entries.get((store_digest, session_id))
            if entry is None:
                return None
            return entry.code, entry.count

    def _evict(self) -> None:
        while len(self._entries) > MAX_HEALTH_ENTRIES_TOTAL:
            oldest = next(iter(self._entries))
            del self._entries[oldest]
        per_store: dict[str, int] = {}
        for store, _ in self._entries:
            per_store[store] = per_store.get(store, 0) + 1
        for store, count in per_store.items():
            while count > MAX_HEALTH_ENTRIES_PER_STORE:
                for key in list(self._entries):
                    if key[0] == store:
                        del self._entries[key]
                        count -= 1
                        break


class HadesEngagementAdapter:
    """Registers Sedna control tools and fail-open observer hooks on a Hades context."""

    def __init__(
        self,
        context: Any,
        *,
        root_resolver: Callable[[], Path],
        settlement_port_factory: EngagementSettlementPortFactory | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._context = context
        self._root_resolver = root_resolver
        self._settlement_port_factory = settlement_port_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._health = _HealthMap()

    # -- registration -----------------------------------------------------

    def register(self) -> None:
        self._context.register_tool(
            name="sedna_manage_engagement",
            description=(
                "Create, resume, inspect, list, close, reopen, abandon, or resolve "
                "calls for the active engagement."
            ),
            schema={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "create",
                                "resume",
                                "inspect",
                                "list",
                                "close",
                                "reopen",
                                "abandon",
                                "change_scope",
                                "change_objective",
                                "unbind",
                                "resolve_call",
                            ],
                        },
                        "engagement_id": {"type": "string", "format": "uuid"},
                        "display_name": {"type": "string", "maxLength": 256},
                        "objective": {"type": "string", "maxLength": 8192},
                        "authorization": {
                            "type": "array",
                            "items": {"type": "string", "maxLength": 2048},
                            "maxItems": 256,
                        },
                        "required_proofs": {"type": "array", "items": {"type": "object"}},
                        "reason": {"type": "string", "maxLength": 1024},
                        "authorization_basis": {"type": "string", "maxLength": 2048},
                        "call_id": {"type": "string", "pattern": "^call-[0-9a-f]{64}$"},
                        "resolution": {"type": "string", "enum": ["timed_out", "abandoned"]},
                        "after_engagement_id": {"type": "string", "format": "uuid"},
                        "after_call_id": {"type": "string", "pattern": "^call-[0-9a-f]{64}$"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 64},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                }
            },
            handler=self._handle_manage,
        )
        self._context.register_tool(
            name="sedna_record_decision",
            description="Record one strategy decision bound to the calling lane.",
            schema={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "engagement_id": {"type": "string", "format": "uuid"},
                        "proposal_id": {"type": "string", "format": "uuid"},
                        "custom_strategy": {"type": "string", "maxLength": 8192},
                        "rationale": {"type": "string", "maxLength": 8192},
                        "host_adapted_command": {"type": "object"},
                    },
                    "additionalProperties": False,
                }
            },
            handler=self._handle_decision,
        )
        self._context.register_tool(
            name="sedna_add_source",
            description="Suggest a shared external source in the sources registry.",
            schema={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "minLength": 1, "maxLength": 256},
                        "locator": {"type": "string", "minLength": 1, "maxLength": 4096},
                        "topics": {
                            "type": "array",
                            "items": {"type": "string", "maxLength": 128},
                            "maxItems": 64,
                        },
                        "notes": {"type": "string", "maxLength": 8192},
                    },
                    "required": ["name", "locator"],
                    "additionalProperties": False,
                }
            },
            handler=self._handle_add_source,
        )

        self._context.register_hook("pre_tool_call", self._pre_tool_call)
        self._context.register_hook("post_tool_call", self._post_tool_call)
        self._context.register_hook("pre_llm_call", self._pre_llm_call)
        self._context.register_hook("on_session_start", self._on_session_start)
        self._context.register_hook("on_session_end", self._on_session_end)
        self._context.register_hook("on_session_finalize", self._on_session_finalize)
        self._context.register_hook("on_session_reset", self._on_session_reset)
        self._context.register_hook("subagent_start", self._subagent_start)
        self._context.register_hook("subagent_stop", self._subagent_stop)

    # -- invocation helpers -----------------------------------------------

    def _invoke(self) -> tuple[Path, str, Any]:
        root = self._root_resolver()
        if not isinstance(root, Path):
            raise ValueError("root resolver must return a Path")
        root = root.resolve()
        store_digest = sha256(str(root).encode("utf-8")).hexdigest()
        service = EngagementJournalService.open(
            root, clock=self._clock, uuid_factory=uuid4
        )
        return root, store_digest, service

    def _lane(self, *, session_id: str | None, task_id: str | None) -> Any | None:
        from sedna.engagement.models import ExecutionLaneKey

        if not session_id:
            return None
        return ExecutionLaneKey.from_host(
            host_kind=HostKind.HADES,
            session_id=session_id,
            task_id=task_id or "",
        )

    def _scope(self, authorization: tuple[str, ...]) -> AuthorizationScope:
        from sedna.knowledge.retrieval.models import (
            AuthorizationState,
            ValidatedTarget,
        )

        targets = tuple(ValidatedTarget.parse(value) for value in authorization)
        if any(not target.is_valid for target in targets):
            raise ValueError("invalid_target")
        return AuthorizationScope(
            state=AuthorizationState.AUTHORIZED,
            exact_targets=targets,
        )
    def _summary(
        self, service: EngagementJournalService, engagement_id: UUID
    ) -> EngagementPublicSummary:
        snapshot = service.load_snapshot(engagement_id)
        return EngagementPublicSummary(
            engagement_id=snapshot.engagement_id,
            display_name=snapshot.manifest.display_name,
            status=snapshot.state.status,
            revision=snapshot.revision,
            bound_lanes=_bounded_page(
                tuple(binding.lane.stable_key for binding in snapshot.state.bound_lanes)
            ),
            active_decisions=_bounded_page(
                tuple(
                    decision.decision_id for decision in snapshot.state.active_decisions
                )
            ),
            in_flight_call_ids=_bounded_page(snapshot.state.in_flight_call_ids),
        )

    def _result(self, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        if len(encoded) > MAX_HOST_RESULT_BYTES:
            return {"ok": False, "error": {"code": "result_too_large", "retryable": False}}
        return json.loads(encoded)

    def _error(self, code: str, *, retryable: bool = False) -> dict[str, Any]:
        return {"ok": False, "error": {"code": code, "retryable": retryable}}

    # -- control tool handlers --------------------------------------------

    def _handle_manage(self, **kwargs: Any) -> dict[str, Any]:
        session_id = kwargs.pop("session_id", None)
        task_id = kwargs.pop("task_id", None)
        lane = self._lane(session_id=session_id, task_id=task_id)
        if lane is None:
            return self._error("host_context_required", retryable=False)
        try:
            payload = _ManageEngagementInput.model_validate(kwargs)
        except Exception:
            return self._error("invalid_input", retryable=False)
        try:
            with self._invoke()[2] as service:
                if payload.action == "create":
                    scope = self._scope(payload.authorization)
                    created = service.create_engagement(
                        display_name=payload.display_name or "",
                        objective=payload.objective or "",
                        scope=scope,
                        lane=lane,
                        required_proofs=payload.required_proofs,
                    )
                    return self._result(
                        {
                            "ok": True,
                            "engagement": self._summary(
                                service, created.engagement_id
                            ).model_dump(mode="json"),
                        }
                    )
                if payload.action == "inspect":
                    if payload.engagement_id is None:
                        return self._error("engagement_not_found")
                    return self._result(
                        {
                            "ok": True,
                            "engagement": self._summary(
                                service, payload.engagement_id
                            ).model_dump(mode="json"),
                        }
                    )
                if payload.action == "list":
                    page = service.list_engagements(
                        after_engagement_id=payload.after_engagement_id,
                        limit=payload.limit,
                    )
                    return self._result(
                        {
                            "ok": True,
                            "engagements": [
                                {
                                    "engagement_id": str(item.engagement_id),
                                    "display_name": item.display_name,
                                    "status": item.status,
                                }
                                for item in page.items
                            ],
                            "total_count": page.total_count,
                            "next_after_engagement_id": (
                                str(page.next_after_engagement_id)
                                if page.next_after_engagement_id
                                else None
                            ),
                        }
                    )
                if payload.action == "close":
                    if payload.engagement_id is None:
                        return self._error("engagement_not_found")
                    result = service.request_close(
                        payload.engagement_id,
                        lane=lane,
                        reason=payload.reason or "manual close",
                    )
                    return self._result(
                        {
                            "ok": True,
                            "engagement": self._summary(
                                service, result.engagement_id
                            ).model_dump(mode="json"),
                        }
                    )
                if payload.action == "reopen":
                    if payload.engagement_id is None:
                        return self._error("engagement_not_found")
                    result = service.reopen_engagement(
                        payload.engagement_id,
                        lane=lane,
                        reason=payload.reason or "manual reopen",
                    )
                    return self._result(
                        {
                            "ok": True,
                            "engagement": self._summary(
                                service, result.engagement_id
                            ).model_dump(mode="json"),
                        }
                    )
                if payload.action == "abandon":
                    if payload.engagement_id is None:
                        return self._error("engagement_not_found")
                    result = service.abandon_engagement(
                        payload.engagement_id,
                        lane=lane,
                        reason=payload.reason or "manual abandon",
                    )
                    return self._result(
                        {
                            "ok": True,
                            "engagement": self._summary(
                                service, result.engagement_id
                            ).model_dump(mode="json"),
                        }
                    )
                if payload.action == "resolve_call":
                    if payload.engagement_id is None or not payload.call_id:
                        return self._error("call_not_found")
                    result = service.terminate_tool_call(
                        payload.engagement_id,
                        payload.call_id,
                        resolution=payload.resolution or "abandoned",
                        reason=payload.reason or "operator resolution",
                        lane=lane,
                    )
                    return self._result(
                        {
                            "ok": True,
                            "engagement": self._summary(
                                service, result.engagement_id
                            ).model_dump(mode="json"),
                        }
                    )
                return self._error("invalid_transition", retryable=False)
        except Exception as exc:
            return _mapped_error(exc)

    def _handle_decision(self, **kwargs: Any) -> dict[str, Any]:
        session_id = kwargs.pop("session_id", None)
        task_id = kwargs.pop("task_id", None)
        lane = self._lane(session_id=session_id, task_id=task_id)
        if lane is None:
            return self._error("host_context_required", retryable=False)
        try:
            payload = RecordDecisionInput.model_validate(kwargs)
        except Exception:
            return self._error("invalid_input", retryable=False)
        try:
            with self._invoke()[2] as service:
                resolved = service.resolve_lane_binding(lane)
                if resolved.engagement_id is None:
                    return self._error("lane_unbound", retryable=True)
                engagement_id = payload.engagement_id or resolved.engagement_id
                if (
                    payload.engagement_id is not None
                    and payload.engagement_id != resolved.engagement_id
                ):
                    return self._error("engagement_conflict", retryable=False)
                result = service.record_decision(
                    engagement_id,
                    lane=lane,
                    proposal_id=payload.proposal_id,
                    strategy=payload.custom_strategy,
                    rationale=payload.rationale,
                    host_adapted_command=payload.host_adapted_command,
                )
                return self._result(
                    {
                        "ok": True,
                        "decision_id": result.snapshot.state.active_decisions[-1].decision_id
                        if result.snapshot.state.active_decisions
                        else None,
                        "engagement": self._summary(
                            service, engagement_id
                        ).model_dump(mode="json"),
                    }
                )
        except Exception as exc:
            return _mapped_error(exc)

    def _handle_add_source(self, **kwargs: Any) -> dict[str, Any]:
        try:
            payload = AddSourceInput.model_validate(kwargs)
        except Exception:
            return self._error("invalid_input", retryable=False)
        try:
            root, _, _ = self._invoke()
            from sedna.engagement.repository import EngagementJournalRepository

            with EngagementJournalRepository(root) as repository:
                registry = SharedSourceRegistry(repository)
                entry = SharedSourceEntry.suggested(
                    name=payload.name,
                    locator=payload.locator,
                    topics=payload.topics,
                    notes=payload.notes,
                )
                result = registry.add_or_update(entry)
                return self._result(
                    {
                        "ok": True,
                        "source_id": result.entry.source_id,
                        "changed": result.changed,
                    }
                )
        except (SourceRegistryLimitError, ValueError) as exc:
            code = getattr(exc, "reason_code", None) or getattr(exc, "code", None)
            return self._error(code or "source_registry_failed", retryable=True)

    # -- observer hooks ---------------------------------------------------
    def _pre_tool_call(
        self,
        tool_name: str,
        args: Any,
        task_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            self._record_pre_tool(tool_name, args, task_id, kwargs)
        except Exception:
            return None

    def _record_pre_tool(
        self, tool_name: str, args: Any, task_id: str | None, kwargs: dict[str, Any]
    ) -> None:
        session_id = kwargs.get("session_id")
        if not session_id:
            return None
        store_digest = self._pinned_store_digest()
        if tool_name in CONTROL_TOOL_NAMES:
            self._record_control_invocation(tool_name, session_id, task_id, kwargs)
            return None
        lane = self._lane(session_id=session_id, task_id=task_id)
        if lane is None:
            self._health.record(store_digest, session_id, "unbound_lane")
            return None
        try:
            with self._invoke()[2] as service:
                resolved = service.resolve_lane_binding(lane)
                if resolved.engagement_id is None:
                    self._health.record(store_digest, session_id, "unbound_lane")
                    return None
                engagement_id = resolved.engagement_id
                sanitized = sanitize_host_arguments(args)
                correlation = self._correlation(
                    lane, tool_name, sanitized, kwargs
                )
                if (
                    isinstance(sanitized, SanitizedHostValue)
                    and sanitized.canonical_bytes is not None
                ):
                    reference = service.write_evidence(
                        engagement_id,
                        sanitized.canonical_bytes,
                        media_type="application/json",
                        representation="sanitized_host_json",
                    )
                    evidence_draft = JournalEventDraft(
                        lane=lane,
                        actor="host_agent",
                        type="evidence_attached",
                        payload=EvidenceAttachedPayload(evidence=reference),
                    )
                else:
                    evidence_draft = None
                call_id = correlation.call_id or f"call-{uuid4().hex * 2}"
                start = JournalEventDraft(
                    lane=lane,
                    actor="host_agent",
                    type="tool_call_started",
                    payload=ToolCallStartedPayload(
                        call_id=call_id,
                        tool_name=tool_name,
                        correlation=correlation,
                        safe_arguments={},
                    ),
                )
                drafts = [draft for draft in (evidence_draft, start) if draft is not None]
                if correlation.kind.value == "uncertain":
                    from sedna.engagement.events import UncertainCorrelationPayload

                    drafts.append(
                        JournalEventDraft(
                            lane=lane,
                            actor="host_agent",
                            type="uncertain_correlation",
                            payload=UncertainCorrelationPayload(
                                call_id=call_id,
                                reason_code=correlation.reason or "missing_stable_identity",
                            ),
                        )
                    )
                service.append_hook_events(engagement_id, tuple(drafts))
        except Exception:
            self._health.record(store_digest, session_id, "journal_unavailable")

    def _record_control_invocation(
        self,
        tool_name: str,
        session_id: str,
        task_id: str | None,
        kwargs: dict[str, Any],
    ) -> None:
        lane = self._lane(session_id=session_id, task_id=task_id)
        if lane is None:
            return None
        with self._invoke()[2] as service:
            resolved = service.resolve_lane_binding(lane)
            if resolved.engagement_id is None:
                return None
            sanitized = sanitize_host_arguments(kwargs.get("args") or {})
            correlation = self._correlation(lane, tool_name, sanitized, kwargs)
            draft = JournalEventDraft(
                lane=lane,
                actor="host_agent",
                type="control_tool_invoked",
                payload=ControlToolInvokedPayload(
                    control_tool=tool_name,
                    policy_version=CONTROL_TOOL_POLICY_VERSION,
                    correlation=correlation,
                ),
                idempotency_key=(
                    f"control:{CONTROL_TOOL_POLICY_VERSION}:"
                    f"{correlation.stable_key or correlation.reason or 'uncertain'}:"
                    f"{tool_name}"
                ),
            )
            service.append_hook_events(resolved.engagement_id, (draft,))

    def _correlation(
        self,
        lane: Any,
        tool_name: str,
        sanitized: SanitizedHostValue | NormalizationFailure,
        kwargs: dict[str, Any],
    ) -> ToolCorrelation:
        return ToolCorrelation.from_hook(
            lane=lane,
            tool_name=tool_name,
            sanitized_arguments=sanitized,
            tool_call_id=kwargs.get("tool_call_id"),
            turn_id=kwargs.get("turn_id"),
            api_request_id=kwargs.get("api_request_id"),
            api_call_count=kwargs.get("api_call_count"),
            tool_call_ordinal=kwargs.get("tool_call_ordinal"),
        )

    def _post_tool_call(
        self,
        tool_name: str,
        args: Any,
        result: Any,
        task_id: str | None = None,
        duration_ms: int | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            self._record_post_tool(
                tool_name, args, result, task_id, duration_ms, kwargs
            )
        except Exception:
            return None

    def _record_post_tool(
        self,
        tool_name: str,
        args: Any,
        result: Any,
        task_id: str | None,
        duration_ms: int | None,
        kwargs: dict[str, Any],
    ) -> None:
        session_id = kwargs.get("session_id")
        if not session_id:
            return None
        lane = self._lane(session_id=session_id, task_id=task_id)
        if lane is None:
            return None
        with self._invoke()[2] as service:
            resolved = service.resolve_lane_binding(lane)
            if resolved.engagement_id is None:
                return None
            engagement_id = resolved.engagement_id
            sanitized = sanitize_host_arguments(args or {})
            correlation = self._correlation(lane, tool_name, sanitized, kwargs)
            call_id = self._find_start_call_id(service, engagement_id, correlation)
            if call_id is None:
                call_id = correlation.call_id or f"call-{uuid4().hex * 2}"
            normalized = normalize_host_payload(result)
            if isinstance(normalized, NormalizationFailure):
                from sedna.engagement.events import EvidenceCaptureFailedPayload

                failed = JournalEventDraft(
                    lane=lane,
                    actor="host_agent",
                    type="evidence_capture_failed",
                    payload=EvidenceCaptureFailedPayload(
                        call_id=call_id,
                        capture_role="result",
                        reason_code="normalization_limit_exceeded",
                    ),
                )
                completed = self._completion_draft(lane, call_id, "unknown", duration_ms)
                service.append_hook_events(engagement_id, (failed, completed))
                return None
            if normalized.representation == "host_returned_no_result":
                completed = self._completion_draft(lane, call_id, "unknown", duration_ms)
                service.append_hook_events(engagement_id, (completed,))
                return None
            reference = service.write_evidence(
                engagement_id,
                normalized.canonical_bytes or b"",
                media_type=_media_type(normalized.representation),
                representation=normalized.representation,
            )
            attached = JournalEventDraft(
                lane=lane,
                actor="host_agent",
                type="evidence_attached",
                payload=EvidenceAttachedPayload(evidence=reference),
            )
            completed = self._completion_draft(lane, call_id, "returned", duration_ms)
            service.append_hook_events(engagement_id, (attached, completed))

    def _find_start_call_id(
        self,
        service: EngagementJournalService,
        engagement_id: UUID,
        correlation: ToolCorrelation,
    ) -> str | None:
        if correlation.stable_key is None:
            return None
        snapshot = service.load_snapshot(engagement_id)
        for event in reversed(snapshot.events):
            if event.type.value != "tool_call_started":
                continue
            if event.payload.correlation.stable_key == correlation.stable_key:
                return event.payload.call_id
        return None

    def _completion_draft(
        self,
        lane: Any,
        call_id: str,
        status: str,
        duration_ms: int | None,
    ) -> JournalEventDraft:
        bounded_duration = 0
        if duration_ms is not None:
            bounded_duration = max(0, min(int(duration_ms), MAX_TOOL_DURATION_MS))
        return JournalEventDraft(
            lane=lane,
            actor="host_agent",
            type="tool_call_completed",
            payload=ToolCallCompletedPayload(
                call_id=call_id,
                correlation=ToolCorrelation.uncertain("missing_stable_identity"),
                technical_status=status,
                duration_ms=bounded_duration,
            ),
        )

    def _pre_llm_call(self, **kwargs: Any) -> dict[str, Any] | None:
        session_id = kwargs.get("session_id")
        if not session_id:
            return None
        store_digest = self._pinned_store_digest()
        health = self._health.peek(store_digest, session_id)
        if health is None:
            return None
        code, _ = health
        if code in {"journal_unavailable", "journal_corrupt", "settlement_unavailable"}:
            return {
                "context": (
                    "Engagement journaling is not reliably journaled; "
                    "results may be incomplete."
                ),
                "kind": "sedna_engagement_health_v1",
                "code": code,
            }
        return None

    # -- session and child hooks (fail-open) ------------------------------

    def _on_session_start(self, session_id: str, task_id: str | None = None, **kwargs: Any) -> None:
        try:
            self._record_session_start(session_id, task_id)
        except Exception:
            return None

    def _record_session_start(self, session_id: str, task_id: str | None) -> None:
        lane = self._lane(session_id=session_id, task_id=task_id)
        if lane is None:
            return None
        with self._invoke()[2] as service:
            resolved = service.resolve_lane_binding(lane)
            if resolved.engagement_id is None:
                return None
            draft = JournalEventDraft(
                lane=lane,
                actor="host_agent",
                type="session_started",
                payload=SessionStartedPayload(
                    model="unknown",
                    platform="cli",
                ),
                idempotency_key=f"session-start:{lane.stable_key}",
            )
            service.append_hook_events(resolved.engagement_id, (draft,))

    def _on_session_end(
        self,
        session_id: str,
        task_id: str | None = None,
        completed: bool = False,
        interrupted: bool = False,
        reason: str | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            self._record_session_end(session_id, task_id, completed, interrupted, reason)
        except Exception:
            return None

    def _record_session_end(
        self,
        session_id: str,
        task_id: str | None,
        completed: bool,
        interrupted: bool,
        reason: str | None,
    ) -> None:
        if completed and interrupted:
            return None
        lane = self._lane(session_id=session_id, task_id=task_id)
        if lane is None:
            return None
        with self._invoke()[2] as service:
            resolved = service.resolve_lane_binding(lane)
            if resolved.engagement_id is None:
                return None
            draft = JournalEventDraft(
                lane=lane,
                actor="host_agent",
                type="session_checkpointed",
                payload=SessionCheckpointedPayload(
                    completed=completed,
                    interrupted=interrupted,
                    reason=(reason or "session ended")[:2048],
                ),
            )
            service.append_hook_events(resolved.engagement_id, (draft,))

    def _on_session_finalize(
        self, session_id: str, platform: str | None = None, reason: str | None = None, **kwargs: Any
    ) -> None:
        try:
            self._record_session_finalize(session_id, platform, reason)
        except Exception:
            return None

    def _record_session_finalize(
        self, session_id: str, platform: str | None, reason: str | None
    ) -> None:
        lane = self._lane(session_id=session_id, task_id=None)
        if lane is None:
            return None
        store_digest = self._pinned_store_digest()
        with self._invoke()[2] as service:
            resolved = service.resolve_lane_binding(lane)
            if resolved.engagement_id is None:
                self._health.record(store_digest, session_id, "unbound_lane")
                return None
            draft = JournalEventDraft(
                lane=lane,
                actor="host_agent",
                type="session_finalized",
                payload=SessionFinalizedPayload(
                    reason=(reason or "finalized")[:2048],
                    settlement_status="not_configured",
                ),
            )
            service.append_hook_events(resolved.engagement_id, (draft,))
            self._health.purge(store_digest, session_id)

    def _on_session_reset(
        self,
        session_id: str,
        old_session_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        store_digest = self._pinned_store_digest()
        self._health.purge(store_digest, session_id)

    def _subagent_start(self, **kwargs: Any) -> None:
        return None

    def _subagent_stop(self, **kwargs: Any) -> None:
        return None

    def _pinned_store_digest(self) -> str:
        root = self._root_resolver()
        return sha256(str(root).encode("utf-8")).hexdigest()


def _media_type(representation: str) -> str:
    if representation == "host_text":
        return "text/plain"
    if representation in {"sanitized_host_json", "canonical_host_json"}:
        return "application/json"
    return "application/octet-stream"


def _mapped_error(exc: Exception) -> dict[str, Any]:
    from sedna.engagement.service import (
        EngagementAmbiguousError,
        EngagementNotFoundError,
    )

    name = type(exc).__name__
    if isinstance(exc, EngagementNotFoundError):
        return {"ok": False, "error": {"code": "engagement_not_found", "retryable": False}}
    if isinstance(exc, EngagementAmbiguousError):
        return {"ok": False, "error": {"code": "engagement_ambiguous", "retryable": False}}
    if "proposal" in name.lower() or "proposal" in str(exc).lower():
        return {"ok": False, "error": {"code": "proposal_not_found", "retryable": False}}
    if "revision" in name.lower() or "conflict" in name.lower():
        return {"ok": False, "error": {"code": "engagement_conflict", "retryable": True}}
    if "quota" in str(exc).lower():
        return {"ok": False, "error": {"code": "evidence_capture_failed", "retryable": True}}
    if "journal" in str(exc).lower():
        return {"ok": False, "error": {"code": "journal_unavailable", "retryable": True}}
    return {"ok": False, "error": {"code": "invalid_transition", "retryable": False}}


__all__ = [
    "AddSourceInput",
    "EngagementPublicSummary",
    "HadesEngagementAdapter",
    "HookHealthCode",
    "PublicStringInventoryPage",
    "RecordDecisionInput",
]
