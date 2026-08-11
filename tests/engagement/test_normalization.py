from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from sedna.engagement import (
    MAX_HOST_VALUE_DEPTH,
    MAX_HOST_VALUE_NODES,
    CorrelationKind,
    NormalizationFailure,
    SanitizedHostValue,
    ToolCorrelation,
    normalize_host_payload,
    sanitize_host_arguments,
)


def test_structural_redaction_removes_provider_secrets_but_preserves_target_credentials() -> None:
    provider_secret = "provider-secret-material"
    target_credential = "Basic dXNlcjpwYXNz"
    sanitized = sanitize_host_arguments(
        {
            "provider": {"api_key": provider_secret},
            "request": {"authorization": target_credential},
            "host_runtime_secret": "runtime-secret-material",
        }
    )
    rendered = sanitized.canonical_bytes.decode()
    assert provider_secret not in rendered
    assert "runtime-secret-material" not in rendered
    assert target_credential in rendered
    assert "[REDACTED:provider-or-host-secret]" in rendered


def test_contextual_secrets_are_redacted_at_any_depth_beneath_provider_namespace() -> None:
    secret = "deep-provider-secret"
    sanitized = sanitize_host_arguments(
        {"provider": {"client": {"transport": {"authorization": secret}}}}
    )
    assert secret not in sanitized.canonical_bytes.decode()
    assert "[REDACTED:provider-or-host-secret]" in sanitized.canonical_bytes.decode()


def test_redacted_value_or_its_digest_never_appears_in_result() -> None:
    import hashlib

    secret = "especially-sensitive-provider-token"
    sanitized = sanitize_host_arguments({"provider_token": secret})
    rendered = sanitized.model_dump_json()
    assert secret not in rendered
    assert hashlib.sha256(secret.encode()).hexdigest() not in rendered


def test_mapping_order_is_deterministic() -> None:
    first = sanitize_host_arguments({"z": 1, "a": {"d": 4, "b": 2}})
    second = sanitize_host_arguments({"a": {"b": 2, "d": 4}, "z": 1})
    assert first == second
    assert json.loads(first.canonical_bytes) == {"a": {"b": 2, "d": 4}, "z": 1}


def test_sanitized_host_values_cannot_be_forged_by_callers() -> None:
    with pytest.raises((TypeError, ValidationError)):
        SanitizedHostValue(
            value={"provider_token": "not-sanitized"},
            canonical_bytes=b"{}",
            canonical_digest="a" * 64,
            byte_length=2,
            representation="sanitized_host_json",
        )


def test_legitimate_sanitized_value_revalidates_but_forged_copy_does_not() -> None:
    sanitized = sanitize_host_arguments({"command": "id"})
    assert SanitizedHostValue.model_validate(sanitized) == sanitized
    forged = sanitized.model_copy(update={"canonical_digest": "b" * 64})
    with pytest.raises(ValidationError):
        SanitizedHostValue.model_validate(forged)


def test_model_copy_forgery_is_rejected_at_correlation_boundary(lane) -> None:
    sanitized = sanitize_host_arguments({"command": "id"})
    forged = sanitized.model_copy(
        update={
            "value": {"provider_token": "forged-secret"},
            "canonical_bytes": b'{"command":"id"}',
            "canonical_digest": "b" * 64,
        }
    )
    correlation = ToolCorrelation.from_hook(
        lane=lane,
        tool_name="terminal",
        sanitized_arguments=forged,
        tool_call_id="",
        turn_id="turn",
        api_request_id="request",
        api_call_count=1,
        tool_call_ordinal=0,
    )
    assert correlation.kind is CorrelationKind.UNCERTAIN
    assert correlation.reason == "normalization_failed"
    assert "forged-secret" not in correlation.model_dump_json()


def test_casefold_colliding_keys_fail_deterministically_without_secret_leakage() -> None:
    secret_a = "first-provider-secret"
    secret_b = "second-provider-secret"
    first = sanitize_host_arguments(
        {"Provider": {"token": secret_a}, "provider": {"token": secret_b}}
    )
    second = sanitize_host_arguments(
        {"provider": {"token": secret_b}, "Provider": {"token": secret_a}}
    )
    assert isinstance(first, NormalizationFailure)
    assert first == second
    rendered = first.model_dump_json()
    assert secret_a not in rendered
    assert secret_b not in rendered


def test_invalid_unicode_mapping_key_returns_closed_typed_failure() -> None:
    result = sanitize_host_arguments({"\ud800": "provider-secret"})
    assert isinstance(result, NormalizationFailure)
    assert result.reason_code == "unsupported_value"
    assert "provider-secret" not in result.model_dump_json()


def test_cycles_are_typed_failures_without_raw_or_digest_material() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)
    result = sanitize_host_arguments(cyclic)
    assert isinstance(result, NormalizationFailure)
    assert result.reason_code == "normalization_limit_exceeded"
    assert "repr" not in result.model_dump_json()
    assert "digest" not in result.model_dump_json()


def test_depth_overflow_is_a_typed_failure() -> None:
    value: object = "leaf"
    for _ in range(MAX_HOST_VALUE_DEPTH + 1):
        value = [value]
    result = sanitize_host_arguments(value)
    assert isinstance(result, NormalizationFailure)
    assert result.reason_code == "normalization_limit_exceeded"


def test_node_overflow_is_a_typed_failure() -> None:
    result = sanitize_host_arguments([None] * MAX_HOST_VALUE_NODES)
    assert isinstance(result, NormalizationFailure)
    assert result.reason_code == "normalization_limit_exceeded"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "non-string-key"}, object()])
def test_unsupported_or_non_json_values_fail_closed(value) -> None:
    result = sanitize_host_arguments(value)
    assert isinstance(result, NormalizationFailure)
    assert result.reason_code in {"normalization_limit_exceeded", "unsupported_value"}


def test_payload_normalization_preserves_representation() -> None:
    text = normalize_host_payload("hello")
    binary = normalize_host_payload(b"\xff\x00")
    structured = normalize_host_payload({"b": 2, "a": 1})
    empty = normalize_host_payload(None)

    assert isinstance(text, SanitizedHostValue)
    assert text.canonical_bytes == b"hello"
    assert text.representation == "host_text"
    assert binary.canonical_bytes == b"\xff\x00"
    assert binary.representation == "host_bytes"
    assert structured.canonical_bytes == b'{"a":1,"b":2}'
    assert structured.representation == "canonical_host_json"
    assert empty.canonical_bytes is None
    assert empty.representation == "host_returned_no_result"
