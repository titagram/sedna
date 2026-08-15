"""Strict bounded contracts for verified case promotion."""

from __future__ import annotations

import json
import re
from contextlib import suppress
from dataclasses import dataclass
from typing import Annotated, Literal, Self, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from sedna.engagement.models import EvidenceId, JournalRevision

PROMOTION_DRAFT_SCHEMA_VERSION = "1.0.0"
PROMOTION_SOURCE_SCHEMA_VERSION = "1.0.0"
PROMOTION_PROVENANCE_SCHEMA_VERSION = "1.0.0"
PROMOTION_COMPILER_VERSION = "1"
MAX_PROMOTION_INPUT_BYTES = 512 * 1024
MAX_PROMOTION_DRAFT_BYTES = 512 * 1024
MAX_PROMOTION_SOURCE_BYTES = 1024 * 1024
MAX_PROMOTION_PRIVATE_VALUES = 512
MAX_PROMOTION_PRIVATE_VALUE_BYTES = 16 * 1024
MAX_PROMOTION_PRIVATE_BYTES = 512 * 1024
PromotionText = Annotated[str, Field(min_length=1, max_length=16_384)]

_SYMBOL_RE = re.compile(r"<([A-Z]+(?:_[A-Z0-9]+)*)>")


def _canonical_size(value: BaseModel) -> int:
    return len(
        json.dumps(
            value.model_dump(mode="json", warnings="error"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


_T = TypeVar("_T")


def _require_sorted_unique(values: tuple[_T, ...], field_name: str) -> tuple[_T, ...]:
    if len(values) != len(set(values)) or values != tuple(sorted(values, key=str)):
        raise ValueError(f"{field_name} must be sorted and unique")
    return values


class _PromotionModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        strict=True,
    )

    @field_validator("*", mode="after")
    @classmethod
    def reject_empty_symbolic_tokens(cls, value: object) -> object:
        def visit(item: object) -> None:
            if isinstance(item, str):
                for match in re.finditer(r"<[^>]*>", item):
                    if _SYMBOL_RE.fullmatch(match.group()) is None:
                        raise ValueError("promotion text contains an invalid symbolic token")
            elif isinstance(item, tuple):
                for nested in item:
                    visit(nested)

        visit(value)
        return value


@dataclass(frozen=True, slots=True, repr=False)
class PromotionSecretInventory:
    flags: tuple[str, ...] = ()
    credentials: tuple[str, ...] = ()
    target_identifiers: tuple[str, ...] = ()
    challenge_identifiers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        groups = (
            self.flags,
            self.credentials,
            self.target_identifiers,
            self.challenge_identifiers,
        )
        values = tuple(item for group in groups for item in group)
        if len(values) > MAX_PROMOTION_PRIVATE_VALUES:
            raise ValueError("private value inventory exceeds its count bound")
        total = 0
        for value in values:
            if not isinstance(value, str) or not value:
                raise ValueError("private value must be a non-empty string")
            size: int | None = None
            with suppress(UnicodeEncodeError):
                size = len(value.encode("utf-8"))
            if size is None:
                raise ValueError("private value must be valid UTF-8")
            if size > MAX_PROMOTION_PRIVATE_VALUE_BYTES:
                raise ValueError("private value exceeds its per-value bound")
            total += size
        if total > MAX_PROMOTION_PRIVATE_BYTES:
            raise ValueError("private value inventory exceeds its byte bound")


class PromotionEvidenceItem(_PromotionModel):
    summary: PromotionText
    event_ids: tuple[UUID, ...] = Field(min_length=1, max_length=64)
    evidence_ids: tuple[EvidenceId, ...] = Field(default=(), max_length=64)

    @field_validator("event_ids", "evidence_ids")
    @classmethod
    def validate_ids(cls, value: tuple[object, ...], info) -> tuple[object, ...]:
        return _require_sorted_unique(value, info.field_name)


class PromotionInput(_PromotionModel):
    engagement_id: UUID
    verified_revision: JournalRevision
    verification_event_id: UUID
    display_name: PromotionText
    objective: PromotionText
    context: tuple[PromotionEvidenceItem, ...] = Field(max_length=128)
    decisions: tuple[PromotionEvidenceItem, ...] = Field(max_length=256)
    outcomes: tuple[PromotionEvidenceItem, ...] = Field(max_length=256)
    alternatives: tuple[PromotionEvidenceItem, ...] = Field(max_length=128)

    @model_validator(mode="after")
    def enforce_input_size(self) -> Self:
        if _canonical_size(self) > MAX_PROMOTION_INPUT_BYTES:
            raise ValueError("promotion input exceeds its byte bound")
        return self


class PromotionStepDraft(_PromotionModel):
    ordinal: StrictInt = Field(ge=1, le=512)
    state_before: PromotionText
    observations: tuple[PromotionText, ...] = Field(max_length=64)
    hypotheses: tuple[PromotionText, ...] = Field(max_length=64)
    selected_strategy: PromotionText
    command_examples: tuple[PromotionText, ...] = Field(default=(), max_length=16)
    outcome: PromotionText
    negative_evidence: tuple[PromotionText, ...] = Field(default=(), max_length=64)
    retry_conditions: tuple[PromotionText, ...] = Field(default=(), max_length=64)
    state_after: PromotionText
    event_ids: tuple[UUID, ...] = Field(min_length=1, max_length=128)
    evidence_ids: tuple[EvidenceId, ...] = Field(default=(), max_length=128)

    @field_validator("event_ids", "evidence_ids")
    @classmethod
    def validate_ids(cls, value: tuple[object, ...], info) -> tuple[object, ...]:
        return _require_sorted_unique(value, info.field_name)


class PromotionClaim(_PromotionModel):
    text: PromotionText
    event_ids: tuple[UUID, ...] = Field(min_length=1, max_length=128)
    evidence_ids: tuple[EvidenceId, ...] = Field(default=(), max_length=128)

    @field_validator("event_ids", "evidence_ids")
    @classmethod
    def validate_ids(cls, value: tuple[object, ...], info) -> tuple[object, ...]:
        return _require_sorted_unique(value, info.field_name)


class PromotionDraft(_PromotionModel):
    schema_version: Literal["1.0.0"]
    title: PromotionText
    starting_access: PromotionClaim
    applicability: tuple[PromotionClaim, ...] = Field(min_length=1, max_length=64)
    steps: tuple[PromotionStepDraft, ...] = Field(min_length=1, max_length=512)
    alternate_paths: tuple[PromotionClaim, ...] = Field(default=(), max_length=128)
    transferable_properties: tuple[PromotionClaim, ...] = Field(min_length=1, max_length=128)
    non_transferable_properties: tuple[PromotionClaim, ...] = Field(min_length=1, max_length=128)
    generalizability: Literal["none", "low", "medium", "high"]
    generalizability_basis: PromotionClaim
    verified_outcome: PromotionClaim

    @model_validator(mode="after")
    def validate_draft(self) -> Self:
        ordinals = tuple(item.ordinal for item in self.steps)
        if ordinals != tuple(range(1, len(ordinals) + 1)):
            raise ValueError("promotion step ordinals must be consecutive and unique")
        if _canonical_size(self) > MAX_PROMOTION_DRAFT_BYTES:
            raise ValueError("promotion draft exceeds its byte bound")
        return self


class PromotionCriticFinding(_PromotionModel):
    code: Literal[
        "unsupported_claim",
        "invalid_provenance",
        "secret_leak",
        "target_leak",
        "overgeneralization",
        "lost_negative_evidence",
        "missing_retry_condition",
        "missing_applicability",
        "command_presented_as_guaranteed",
    ]
    message: PromotionText
    step_ordinals: tuple[StrictInt, ...] = Field(default=(), max_length=512)

    @field_validator("step_ordinals")
    @classmethod
    def validate_ordinals(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(item < 1 or item > 512 for item in value):
            raise ValueError("critic step ordinal is out of range")
        return _require_sorted_unique(value, "step_ordinals")


class PromotionCriticVerdict(_PromotionModel):
    accepted: bool
    findings: tuple[PromotionCriticFinding, ...] = Field(max_length=128)

    @model_validator(mode="after")
    def validate_acceptance(self) -> Self:
        if self.accepted != (not self.findings):
            raise ValueError("accepted must be true exactly when findings are empty")
        return self


class PromotionCompilationResult(_PromotionModel):
    disposition: Literal["verified", "quarantined", "failed"]
    draft: PromotionDraft | None = None
    critic: PromotionCriticVerdict | None = None
    repair_count: StrictInt = Field(ge=0, le=1)
    failure_code: (
        Literal[
            "transport_failure",
            "invalid_structured_response",
            "invalid_provenance",
            "unsafe_material",
            "critic_rejected",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def validate_disposition(self) -> Self:
        if self.disposition == "verified":
            valid = self.draft is not None and self.critic is not None and self.critic.accepted
            valid = valid and self.failure_code is None
        elif self.disposition == "quarantined":
            valid = (
                self.draft is None
                and self.critic is not None
                and not self.critic.accepted
                and self.failure_code == "critic_rejected"
            )
        else:
            valid = self.draft is None and self.failure_code is not None
        if not valid:
            raise ValueError("disposition has an invalid result shape")
        return self
