from __future__ import annotations

import json
from hashlib import sha256

import pytest
from pydantic import ValidationError

from sedna.engagement import (
    MAX_HOST_VALUE_DEPTH,
    MAX_HOST_VALUE_NODES,
    MAX_JOURNAL_EVENT_BYTES,
    MAX_SAFE_ARGUMENT_BYTES,
    CorrelationKind,
    NormalizationFailure,
    SanitizedHostValue,
    ToolCorrelation,
    bounded_safe_argument_summary,
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


def test_safe_argument_summary_admits_complete_records_until_bounds() -> None:
    sanitized = sanitize_host_arguments(
        {
            "command": "id",
            "large": {"pad": "x" * 20_000},
            "note": "kept",
        }
    )
    assert isinstance(sanitized, SanitizedHostValue)
    summary, omission = bounded_safe_argument_summary(sanitized.value)

    assert summary == {"command": "id", "note": "kept"}
    assert omission is not None
    assert omission.omitted_count == 1
    assert omission.omitted_sha256 == sha256(
        json.dumps(
            [{"pad": "x" * 20_000}],
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def test_safe_argument_summary_is_byte_stable_and_within_journal_bounds() -> None:
    sanitized = sanitize_host_arguments(
        {"command": "curl", "url": "https://192.0.2.44/", "b": [1, 2, 3]}
    )
    assert isinstance(sanitized, SanitizedHostValue)
    first_summary, first_omission = bounded_safe_argument_summary(sanitized.value)
    second_summary, second_omission = bounded_safe_argument_summary(sanitized.value)

    assert first_summary == second_summary
    assert first_omission == second_omission
    encoded = json.dumps(
        first_summary,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert len(encoded) <= MAX_SAFE_ARGUMENT_BYTES
    assert len(encoded) <= MAX_JOURNAL_EVENT_BYTES


def test_safe_argument_summary_omits_records_beyond_depth_and_items() -> None:
    sanitized = sanitize_host_arguments(
        {
            "deep": {"a": {"b": {"c": {"d": {"e": "leaf"}}}}},
            "f0": 0,
            "f1": 1,
            "f2": 2,
            "f3": 3,
        }
    )
    assert isinstance(sanitized, SanitizedHostValue)
    summary, omission = bounded_safe_argument_summary(sanitized.value)

    assert "deep" not in summary
    assert omission is not None
    assert omission.omitted_count >= 1
    assert omission.omitted_sha256
    assert summary["f0"] == 0


def test_safe_argument_summary_omission_digest_is_deterministic_and_covers_omissions() -> None:
    sanitized = sanitize_host_arguments(
        {
            "command": "id",
            "deep": {"a": {"b": {"c": {"d": "leaf"}}}},
        }
    )
    assert isinstance(sanitized, SanitizedHostValue)
    first_summary, first_omission = bounded_safe_argument_summary(sanitized.value)
    second_summary, second_omission = bounded_safe_argument_summary(sanitized.value)

    assert first_summary == second_summary
    assert first_omission == second_omission
    assert first_omission is not None
    assert first_omission.omitted_sha256 == sha256(
        json.dumps(
            [{"a": {"b": {"c": {"d": "leaf"}}}}],
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def test_safe_argument_summary_never_leaks_secret_or_secret_digest() -> None:
    secret = "provider-secret-that-must-never-reach-disk"
    sanitized = sanitize_host_arguments(
        {
            "provider": {"authorization": secret},
            "deep": {"a": {"b": {"c": {"d": secret}}}},
        }
    )
    assert isinstance(sanitized, SanitizedHostValue)
    summary, omission = bounded_safe_argument_summary(sanitized.value)

    rendered = json.dumps(summary, sort_keys=True)
    assert secret not in rendered
    assert sha256(secret.encode()).hexdigest() not in rendered
    assert omission is not None
    assert secret.encode() not in omission.omitted_sha256.encode()
    assert sha256(secret.encode()).hexdigest() not in omission.omitted_sha256
    assert "REDACTED" in rendered


def test_safe_argument_summary_rejects_cycles_without_leakage() -> None:
    cyclic: dict[str, object] = {"command": "id"}
    cyclic["self"] = cyclic
    with pytest.raises(ValueError, match="unsupported value"):
        bounded_safe_argument_summary(cyclic)
