"""Bounded, dependency-neutral normalization for untrusted host values."""

from __future__ import annotations

import json
import math
import unicodedata
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_HOST_VALUE_DEPTH = 32
MAX_HOST_VALUE_NODES = 100_000
MAX_HOST_SCALAR_BYTES = 64 * 1024 * 1024
MAX_HOST_NORMALIZED_BYTES = 64 * 1024 * 1024

REDACTED_HOST_SECRET = "[REDACTED:provider-or-host-secret]"
ALWAYS_REDACTED_HOST_KEYS = frozenset(
    {
        "provider_token",
        "provider_credential",
        "provider_api_key",
        "host_token",
        "host_credential",
        "host_runtime_secret",
    }
)
CONTEXTUAL_HOST_SECRET_KEYS = frozenset(
    {"api_key", "apikey", "authorization", "cookie", "secret_access_key", "token"}
)
HOST_SECRET_NAMESPACES = frozenset(
    {"provider", "host_runtime", "transport_auth", "telemetry_auth"}
)
SOURCE_SECRET_KEYS = frozenset(
    {
        *ALWAYS_REDACTED_HOST_KEYS,
        "token",
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "password",
        "secret",
        "secret_access_key",
    }
)
_SANITIZED_CONSTRUCTION_TOKEN = object()


class NormalizationFailure(BaseModel):
    """A closed failure that never retains an unsafe value or partial digest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason_code: Literal[
        "normalization_limit_exceeded", "unsupported_value", "serialization_failed"
    ]


class SanitizedHostValue(BaseModel):
    """A complete bounded value and canonical metadata safe for later correlation."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )

    value: Any = None
    canonical_bytes: bytes | None = None
    canonical_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    byte_length: int = Field(ge=0, le=MAX_HOST_NORMALIZED_BYTES)
    representation: Literal[
        "sanitized_host_json",
        "host_text",
        "host_bytes",
        "canonical_host_json",
        "host_returned_no_result",
    ]
    provider_or_host_secret_redacted: bool = False

    def __init__(self, **data: Any) -> None:
        if data.pop("_construction_token", None) is not _SANITIZED_CONSTRUCTION_TOKEN:
            raise TypeError("SanitizedHostValue instances are created by normalization helpers")
        super().__init__(**data)


class _NormalizationError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _canonical_json(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise _NormalizationError("serialization_failed") from exc
    if len(encoded) > MAX_HOST_NORMALIZED_BYTES:
        raise _NormalizationError("normalization_limit_exceeded")
    return encoded


def _bounded_scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            size = len(value.encode("utf-8"))
        except UnicodeError as exc:
            raise _NormalizationError("unsupported_value") from exc
        if size > MAX_HOST_SCALAR_BYTES:
            raise _NormalizationError("normalization_limit_exceeded")
        return value
    if isinstance(value, int):
        if value < -(2**63) or value > 2**63 - 1:
            raise _NormalizationError("normalization_limit_exceeded")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _NormalizationError("unsupported_value")
        return value
    raise _NormalizationError("unsupported_value")


def _sanitize_structure(value: Any) -> tuple[Any, bool]:
    nodes = 0
    active_containers: set[int] = set()
    redacted = False
    root: list[Any] = [None]
    stack: list[tuple[str, Any, int, bool, Any, Any]] = [
        ("enter", value, 0, False, root, 0)
    ]

    while stack:
        action, item, depth, namespace_sensitive, parent, key = stack.pop()
        if action == "exit":
            active_containers.remove(item)
            continue

        nodes += 1
        if nodes > MAX_HOST_VALUE_NODES or depth > MAX_HOST_VALUE_DEPTH:
            raise _NormalizationError("normalization_limit_exceeded")

        if isinstance(item, dict):
            if len(item) > MAX_HOST_VALUE_NODES:
                raise _NormalizationError("normalization_limit_exceeded")
            identity = id(item)
            if identity in active_containers:
                raise _NormalizationError("normalization_limit_exceeded")
            if not all(isinstance(item_key, str) for item_key in item):
                raise _NormalizationError("unsupported_value")
            normalized_to_original: dict[str, str] = {}
            for original_key in item:
                normalized_key = unicodedata.normalize("NFC", original_key)
                if normalized_key in normalized_to_original:
                    raise _NormalizationError("unsupported_value")
                if len(normalized_key.encode("utf-8")) > MAX_HOST_SCALAR_BYTES:
                    raise _NormalizationError("normalization_limit_exceeded")
                normalized_to_original[normalized_key] = original_key

            casefolded = {
                normalized_key.casefold(): original_key
                for normalized_key, original_key in normalized_to_original.items()
            }
            credential_scope = item.get(casefolded.get("credential_scope", ""))
            scoped_object = (
                isinstance(credential_scope, str)
                and credential_scope.casefold() in {"provider", "host_runtime"}
            )
            result: dict[str, Any] = {}
            parent[key] = result
            active_containers.add(identity)
            stack.append(("exit", identity, depth, namespace_sensitive, None, None))
            for normalized_key in sorted(normalized_to_original, reverse=True):
                original_key = normalized_to_original[normalized_key]
                folded = normalized_key.casefold()
                if folded in ALWAYS_REDACTED_HOST_KEYS or (
                    folded in CONTEXTUAL_HOST_SECRET_KEYS
                    and (namespace_sensitive or scoped_object)
                ):
                    result[normalized_key] = REDACTED_HOST_SECRET
                    redacted = True
                    continue
                stack.append(
                    (
                        "enter",
                        item[original_key],
                        depth + 1,
                        namespace_sensitive or folded in HOST_SECRET_NAMESPACES,
                        result,
                        normalized_key,
                    )
                )
            continue

        if isinstance(item, (list, tuple)):
            if len(item) > MAX_HOST_VALUE_NODES:
                raise _NormalizationError("normalization_limit_exceeded")
            identity = id(item)
            if identity in active_containers:
                raise _NormalizationError("normalization_limit_exceeded")
            result_list: list[Any] = [None] * len(item)
            parent[key] = result_list
            active_containers.add(identity)
            stack.append(("exit", identity, depth, namespace_sensitive, None, None))
            for index in reversed(range(len(item))):
                stack.append(
                    (
                        "enter",
                        item[index],
                        depth + 1,
                        namespace_sensitive,
                        result_list,
                        index,
                    )
                )
            continue

        parent[key] = _bounded_scalar(item)

    return root[0], redacted


def sanitize_host_arguments(value: Any) -> SanitizedHostValue | NormalizationFailure:
    """Return a fully sanitized bounded JSON value, or a non-leaking typed failure."""

    try:
        sanitized, redacted = _sanitize_structure(value)
        encoded = _canonical_json(sanitized)
    except _NormalizationError as exc:
        return NormalizationFailure(reason_code=exc.reason_code)
    return SanitizedHostValue(
        _construction_token=_SANITIZED_CONSTRUCTION_TOKEN,
        value=sanitized,
        canonical_bytes=encoded,
        canonical_digest=sha256(encoded).hexdigest(),
        byte_length=len(encoded),
        representation="sanitized_host_json",
        provider_or_host_secret_redacted=redacted,
    )


def normalize_host_payload(value: Any) -> SanitizedHostValue | NormalizationFailure:
    """Normalize one host-delivered result while preserving its delivered representation."""

    if value is None:
        return SanitizedHostValue(
            _construction_token=_SANITIZED_CONSTRUCTION_TOKEN,
            value=None,
            canonical_bytes=None,
            canonical_digest=None,
            byte_length=0,
            representation="host_returned_no_result",
        )
    if isinstance(value, bytes):
        if len(value) > MAX_HOST_NORMALIZED_BYTES:
            return NormalizationFailure(reason_code="normalization_limit_exceeded")
        return SanitizedHostValue(
            _construction_token=_SANITIZED_CONSTRUCTION_TOKEN,
            value=None,
            canonical_bytes=value,
            canonical_digest=sha256(value).hexdigest(),
            byte_length=len(value),
            representation="host_bytes",
        )
    if isinstance(value, str):
        try:
            encoded = value.encode("utf-8")
        except UnicodeError:
            return NormalizationFailure(reason_code="unsupported_value")
        if len(encoded) > MAX_HOST_NORMALIZED_BYTES:
            return NormalizationFailure(reason_code="normalization_limit_exceeded")
        return SanitizedHostValue(
            _construction_token=_SANITIZED_CONSTRUCTION_TOKEN,
            value=value,
            canonical_bytes=encoded,
            canonical_digest=sha256(encoded).hexdigest(),
            byte_length=len(encoded),
            representation="host_text",
        )

    sanitized = sanitize_host_arguments(value)
    if isinstance(sanitized, NormalizationFailure):
        return sanitized
    return sanitized.model_copy(update={"representation": "canonical_host_json"})


__all__ = [
    "ALWAYS_REDACTED_HOST_KEYS",
    "CONTEXTUAL_HOST_SECRET_KEYS",
    "HOST_SECRET_NAMESPACES",
    "MAX_HOST_NORMALIZED_BYTES",
    "MAX_HOST_SCALAR_BYTES",
    "MAX_HOST_VALUE_DEPTH",
    "MAX_HOST_VALUE_NODES",
    "NormalizationFailure",
    "REDACTED_HOST_SECRET",
    "SOURCE_SECRET_KEYS",
    "SanitizedHostValue",
    "normalize_host_payload",
    "sanitize_host_arguments",
]
