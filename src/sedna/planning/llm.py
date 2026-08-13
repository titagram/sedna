"""Safe four-role structured planning LLM boundary."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Annotated, Literal, Protocol, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from sedna.engagement.models import ScopeReference
from sedna.knowledge.semantic.llm import StructuredResult, StructuredUsage
from sedna.planning.models import (
    EVIDENCE_SLICE_BYTES,
    MAX_PLANNER_REQUEST_BYTES,
    MAX_RECENT_EVENT_TEXT_BYTES,
    EvidenceId,
    MediaType,
    ObservationBatchDraft,
    PlannerCriticVerdict,
    PlannerDraft,
    SituationProjection,
    StrategyLedger,
)
from sedna.planning.retrieval import PlannerKnowledgeContext

MAX_PLANNER_RESPONSE_BYTES = 128 * 1024

PlanningLlmPurpose = Literal[
    "sedna.planning.observe",
    "sedna.planning.plan",
    "sedna.planning.critic",
    "sedna.planning.repair",
]
ModelT = TypeVar("ModelT", bound=BaseModel)


class _HostStructuredResult(Protocol):
    parsed: object | None
    provider: str
    model: str
    agent_id: str
    usage: object


class HostStructuredLlm(Protocol):
    """Host-owned structured-completion surface used without host imports."""

    def complete_structured(
        self,
        *,
        instructions: str,
        input: Sequence[Mapping[str, object]],
        json_schema: Mapping[str, object] | None,
        json_mode: bool,
        schema_name: str,
        temperature: float | None,
        max_tokens: int | None,
        timeout: float | None,
        purpose: str | None,
    ) -> _HostStructuredResult: ...


class PlanningLlmError(RuntimeError):
    """Closed response-free planning LLM boundary failure."""

    def __init__(
        self,
        reason_code: Literal[
            "transport_failure",
            "missing_parsed_response",
            "invalid_structured_response",
            "planner_input_too_large",
            "planner_output_too_large",
        ],
    ) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class _HostAttribution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Annotated[str, Field(min_length=1, max_length=256)]
    model: Annotated[str, Field(min_length=1, max_length=256)]
    agent_id: Annotated[str, Field(min_length=1, max_length=256)]


class _PlanningRequest(BaseModel):
    """Closed immutable base for planning request envelopes."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )


class ObservationEvidenceSlice(_PlanningRequest):
    """One event-bound bounded evidence slice for observation inference."""

    event_id: UUID
    evidence_id: EvidenceId
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(gt=0)]
    media_type: MediaType
    content: bytes = Field(min_length=1, max_length=EVIDENCE_SLICE_BYTES)

    @model_validator(mode="after")
    def _range_matches_content(self) -> ObservationEvidenceSlice:
        if self.end <= self.start or self.end - self.start != len(self.content):
            raise ValueError("observation_evidence_range_mismatch")
        return self


class ObservationRequest(_PlanningRequest):
    """Bounded evidence supplied to the observation role."""

    evidence_slices: Annotated[tuple[ObservationEvidenceSlice, ...], Field(max_length=64)]


class PlannerRequest(_PlanningRequest):
    """Current settled situation and strategy ledger supplied to the planner role."""

    situation: SituationProjection
    ledger: StrategyLedger
    knowledge_context: PlannerKnowledgeContext
    scope_references: Annotated[tuple[ScopeReference, ...], Field(max_length=512)]
    recent_event_ids: Annotated[tuple[UUID, ...], Field(max_length=64)]
    recent_event_context: Annotated[tuple[str, ...], Field(max_length=64)] = ()
    max_proposals: Annotated[int, Field(ge=3, le=8)]

    @model_validator(mode="after")
    def _recent_event_context_is_bounded(self) -> PlannerRequest:
        if sum(len(item.encode("utf-8")) for item in self.recent_event_context) > (
            MAX_RECENT_EVENT_TEXT_BYTES
        ):
            raise ValueError("recent_event_context_too_large")
        return self


class PlannerCriticRequest(_PlanningRequest):
    """Complete planner output supplied to the independent critic role."""

    draft: PlannerDraft


class PlannerRepairRequest(_PlanningRequest):
    """Planner output and critic verdict supplied to the bounded repair role."""

    draft: PlannerDraft
    critic: PlannerCriticVerdict


_CALL_CONTRACTS: Mapping[str, tuple[type[_PlanningRequest], type[BaseModel]]] = {
    "sedna.planning.observe": (ObservationRequest, ObservationBatchDraft),
    "sedna.planning.plan": (PlannerRequest, PlannerDraft),
    "sedna.planning.critic": (PlannerCriticRequest, PlannerCriticVerdict),
    "sedna.planning.repair": (PlannerRepairRequest, PlannerDraft),
}


class PlanningLlmAdapter:
    """Validate planning completions without provider/model routing overrides."""

    def __init__(
        self,
        host: HostStructuredLlm,
        *,
        max_tokens: int = 8_000,
        timeout: float = 120.0,
    ) -> None:
        self._host = host
        self._max_tokens = max_tokens
        self._timeout = timeout

    def complete(
        self,
        model_type: type[ModelT],
        *,
        instructions: str,
        payload: _PlanningRequest,
        purpose: PlanningLlmPurpose,
    ) -> StructuredResult[ModelT]:
        contract = _CALL_CONTRACTS.get(purpose)
        if contract is None or type(payload) is not contract[0] or model_type is not contract[1]:
            raise TypeError("purpose, payload, and response model must match planning contract")
        try:
            payload_data = (
                type(payload)
                .model_validate(payload.model_dump(mode="python", warnings="error"))
                .model_dump(mode="json", warnings="error")
            )
            serialized_payload = json.dumps(
                payload_data, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
            if len(serialized_payload.encode("utf-8")) > MAX_PLANNER_REQUEST_BYTES:
                raise PlanningLlmError("planner_input_too_large")
            response_schema = json.dumps(
                model_type.model_json_schema(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except PlanningLlmError:
            raise
        except Exception:
            raise TypeError("payload must be a safe planning request payload") from None
        try:
            host_result = self._host.complete_structured(
                instructions=(
                    f"{instructions}\n\nReturn one JSON object matching this schema exactly:\n"
                    f"{response_schema}"
                ),
                input=[{"type": "text", "text": serialized_payload}],
                json_schema=None,
                json_mode=True,
                schema_name=model_type.__name__,
                temperature=0,
                max_tokens=self._max_tokens,
                timeout=self._timeout,
                purpose=purpose,
            )
        except Exception:
            raise PlanningLlmError("transport_failure") from None
        parsed_response = getattr(host_result, "parsed", None)
        if parsed_response is None:
            raise PlanningLlmError("missing_parsed_response")
        try:
            response_data = (
                parsed_response.model_dump(mode="json", warnings="error")
                if isinstance(parsed_response, BaseModel)
                else parsed_response
            )
            response_json = json.dumps(response_data, allow_nan=False)
            if len(response_json.encode("utf-8")) > MAX_PLANNER_RESPONSE_BYTES:
                raise PlanningLlmError("planner_output_too_large")
            parsed = model_type.model_validate(json.loads(response_json))
            usage = StructuredUsage.model_validate(host_result.usage)
            attribution = _HostAttribution(
                provider=host_result.provider,
                model=host_result.model,
                agent_id=host_result.agent_id,
            )
        except PlanningLlmError:
            raise
        except (AttributeError, TypeError, ValidationError, ValueError):
            raise PlanningLlmError("invalid_structured_response") from None
        return StructuredResult(
            parsed=parsed,
            provider=attribution.provider,
            model=attribution.model,
            agent_id=attribution.agent_id,
            usage=usage,
            audit=MappingProxyType({"purpose": purpose}),
        )


__all__ = [
    "HostStructuredLlm",
    "ObservationEvidenceSlice",
    "ObservationRequest",
    "PlannerCriticRequest",
    "PlannerDraft",
    "PlannerRequest",
    "PlannerRepairRequest",
    "PlanningLlmAdapter",
    "PlanningLlmError",
]
