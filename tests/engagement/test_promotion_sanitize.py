from __future__ import annotations

import html
import json
from urllib.parse import quote
from uuid import UUID

import pytest
from pydantic import ValidationError

from sedna.engagement.models import JournalRevision
from sedna.engagement.promotion.models import (
    MAX_PROMOTION_DRAFT_BYTES,
    MAX_PROMOTION_INPUT_BYTES,
    PROMOTION_DRAFT_SCHEMA_VERSION,
    PromotionClaim,
    PromotionCompilationResult,
    PromotionCriticFinding,
    PromotionCriticVerdict,
    PromotionDraft,
    PromotionEvidenceItem,
    PromotionInput,
    PromotionSecretInventory,
    PromotionStepDraft,
)
from sedna.engagement.promotion.sanitize import (
    assert_promotion_safe,
    assert_semantic_promotion_safe,
    symbolize_evidence,
    symbolize_text,
)

EVENT_ID = UUID("55555555-5555-4555-8555-555555555555")
EVIDENCE_ID = "evidence-sha256-" + "a" * 64


def _inventory() -> PromotionSecretInventory:
    return PromotionSecretInventory(
        flags=("HTB{root-proof}", "0123456789abcdef0123456789abcdef"),
        credentials=("OrionAdm!n:Summer2026",),
        target_identifiers=("10.10.11.42", "orion.htb"),
        challenge_identifiers=("HTB-Orion", "Orion"),
    )


@pytest.mark.parametrize(
    "text",
    (
        "HTB{root-proof}",
        quote("HTB{root-proof}"),
        quote(quote("HTB{root-proof}")),
        html.escape("HTB{root-proof}"),
        "0123456789abcdef0123456789abcdef",
        "OrionAdm!n:Summer2026",
        quote("OrionAdm!n:Summer2026"),
        "http://10.10.11.42/ and orion.htb",
        "HTB-Orion challenge",
    ),
)
def test_symbolization_removes_plain_and_encoded_private_values(text: str) -> None:
    item = PromotionEvidenceItem(
        summary=f"Observed {text}", event_ids=(EVENT_ID,), evidence_ids=(EVIDENCE_ID,)
    )

    safe = symbolize_evidence(item, _inventory())

    rendered = safe.model_dump_json()
    assert "root-proof" not in rendered
    assert "0123456789abcdef0123456789abcdef" not in rendered
    assert "OrionAdm" not in rendered
    assert "10.10.11.42" not in rendered
    assert "orion.htb" not in rendered
    assert "HTB-Orion" not in rendered
    assert any(token in rendered for token in ("<FLAG_", "<CREDENTIAL_", "<TARGET_", "<CHALLENGE_"))
    assert safe.event_ids == (EVENT_ID,)
    assert safe.evidence_ids == (EVIDENCE_ID,)


def test_symbolization_is_stable_and_preserves_non_secret_hashes() -> None:
    inventory = PromotionSecretInventory(
        credentials=("beta-secret", "alpha-secret"),
        target_identifiers=("192.0.2.20", "192.0.2.10"),
    )
    ordinary_digest = "f" * 64
    text = (
        "Reuse beta-secret against 192.0.2.20 after alpha-secret on 192.0.2.10; "
        f"artifact digest {ordinary_digest}"
    )

    first = symbolize_text(text, inventory)
    second = symbolize_text(text, inventory)

    assert first == second
    assert ordinary_digest in first
    assert "alpha-secret" not in first
    assert "beta-secret" not in first
    assert "192.0.2.10" not in first
    assert "192.0.2.20" not in first
    assert {"<CREDENTIAL_1>", "<CREDENTIAL_2>", "<TARGET_1>", "<TARGET_2>"} <= set(
        first.replace(";", "").split()
    )


def test_final_leak_scan_rejects_reintroduced_or_encoded_values() -> None:
    with pytest.raises(ValueError, match="promotion material contains private value"):
        assert_promotion_safe("Recovered OrionAdm%21n%3ASummer2026", _inventory())
    with pytest.raises(ValueError, match="promotion material contains raw flag"):
        assert_promotion_safe("nested HTB%257Buncatalogued%257D", PromotionSecretInventory())


@pytest.mark.parametrize("private", ("OrionAdm!n:Summer2026", "OrionAdm%21n%3ASummer2026"))
def test_semantic_promotion_scan_rejects_private_material_at_nested_boundary(
    private: str,
) -> None:
    from tests.knowledge.test_semantic_repository import _verified_result

    safe = _verified_result()
    assert safe.bundle is not None
    manifest = safe.bundle.compilation_manifest.model_copy(update={"extractor_model_id": private})
    bundle = safe.bundle.model_copy(update={"compilation_manifest": manifest})
    hostile = safe.model_copy(update={"bundle": bundle})

    with pytest.raises(ValueError, match="promotion material contains private value"):
        assert_semantic_promotion_safe(hostile, _inventory())


def test_recursive_decode_accepts_eight_rounds_and_rejects_budget_exhaustion() -> None:
    private = "credential:value"
    inventory = PromotionSecretInventory(credentials=(private,))
    encoded = private
    for _ in range(8):
        encoded = quote(encoded, safe="")
    assert symbolize_text(encoded, inventory) == "<CREDENTIAL_1>"

    encoded = quote(encoded, safe="")
    with pytest.raises(ValueError, match="decode budget"):
        symbolize_text(encoded, inventory)
    with pytest.raises(ValueError, match="decode budget"):
        assert_promotion_safe("x" * 65_537, PromotionSecretInventory())


def test_secret_inventory_is_bounded_non_serializable_and_redacted_in_repr() -> None:
    inventory = _inventory()

    rendered = repr(inventory)
    assert "root-proof" not in rendered
    assert "OrionAdm" not in rendered
    assert not hasattr(inventory, "model_dump")
    assert not hasattr(inventory, "model_dump_json")
    with pytest.raises(ValueError, match="private value") as caught:
        PromotionSecretInventory(credentials=("PRIVATE_SENTINEL",) * 513)
    assert "PRIVATE_SENTINEL" not in str(caught.value)
    with pytest.raises(ValueError, match="private value"):
        PromotionSecretInventory(credentials=("x" * (512 * 1024 + 1),))


def test_secret_inventory_invalid_utf8_leaves_no_raw_exception_chain() -> None:
    private = "PRIVATE-SENTINEL-" + chr(0xD800)

    with pytest.raises(ValueError, match="valid UTF-8") as caught:
        PromotionSecretInventory(credentials=(private,))

    error = caught.value
    assert private not in str(error)
    assert private not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None

    reachable = [error]
    for current in reachable:
        for linked in (current.__cause__, current.__context__):
            if linked is not None and linked not in reachable:
                reachable.append(linked)
        traceback = current.__traceback__
        while traceback is not None:
            for value in traceback.tb_frame.f_locals.values():
                if isinstance(value, BaseException) and value not in reachable:
                    reachable.append(value)
            traceback = traceback.tb_next

    for current in reachable:
        assert private not in str(current)
        assert private not in repr(current)
        if isinstance(current, UnicodeEncodeError):
            assert current.object != private


def _claim(text: str = "Grounded claim") -> PromotionClaim:
    return PromotionClaim(text=text, event_ids=(EVENT_ID,), evidence_ids=(EVIDENCE_ID,))


def _draft() -> PromotionDraft:
    return PromotionDraft(
        schema_version=PROMOTION_DRAFT_SCHEMA_VERSION,
        title="Reusable case",
        starting_access=_claim("Initial access"),
        applicability=(_claim("Linux target"),),
        steps=(
            PromotionStepDraft(
                ordinal=1,
                state_before="No access",
                observations=("Service exposed",),
                hypotheses=("Credential may be reusable",),
                selected_strategy="Validate the credential",
                command_examples=("tool {target} {credential_ref}",),
                outcome="Authenticated access",
                negative_evidence=("Anonymous access failed",),
                retry_conditions=("A credential becomes available",),
                state_after="Authenticated session",
                event_ids=(EVENT_ID,),
                evidence_ids=(EVIDENCE_ID,),
            ),
        ),
        transferable_properties=(_claim("Credential reuse pattern"),),
        non_transferable_properties=(_claim("Source challenge identity"),),
        generalizability="medium",
        generalizability_basis=_claim("One verified environment"),
        verified_outcome=_claim("Objective completed"),
    )


def _canonical_size(value: object) -> int:
    return len(
        json.dumps(
            value,
            allow_nan=False,
            default=str,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def _sized_input_payload(size: int) -> dict[str, object]:
    item = {
        "summary": "x",
        "event_ids": (EVENT_ID,),
        "evidence_ids": (EVIDENCE_ID,),
    }
    context = [dict(item) for _ in range(40)]
    payload: dict[str, object] = {
        "engagement_id": EVENT_ID,
        "verified_revision": {"sequence": 1, "event_hash": "a" * 64},
        "verification_event_id": EVENT_ID,
        "display_name": "Case",
        "objective": "Objective",
        "context": context,
        "decisions": (),
        "outcomes": (),
        "alternatives": (),
    }
    remaining = size - _canonical_size(payload)
    assert remaining >= 0
    for evidence in context:
        growth = min(remaining, 16_383)
        evidence["summary"] += "x" * growth
        remaining -= growth
    assert remaining == 0
    assert _canonical_size(payload) == size
    payload["context"] = tuple(context)
    return payload


def _sized_draft_payload(size: int) -> dict[str, object]:
    payload = _draft().model_dump(mode="python")
    step = payload["steps"][0]
    steps = [dict(step, ordinal=index) for index in range(1, 41)]
    payload["steps"] = steps
    remaining = size - _canonical_size(payload)
    assert remaining >= 0
    for draft_step in steps:
        growth = min(remaining, 16_384 - len(draft_step["selected_strategy"]))
        draft_step["selected_strategy"] += "x" * growth
        remaining -= growth
    assert remaining == 0
    assert _canonical_size(payload) == size
    payload["steps"] = tuple(steps)
    return payload


def test_promotion_input_and_draft_enforce_exact_canonical_byte_limits() -> None:
    assert PromotionInput.model_validate(_sized_input_payload(MAX_PROMOTION_INPUT_BYTES - 1))
    assert PromotionInput.model_validate(_sized_input_payload(MAX_PROMOTION_INPUT_BYTES))
    host_calls = []

    def call_host(payload: object) -> None:
        PromotionInput.model_validate(payload)
        host_calls.append(payload)

    with pytest.raises(ValidationError, match="promotion input exceeds"):
        call_host(_sized_input_payload(MAX_PROMOTION_INPUT_BYTES + 1))
    assert host_calls == []

    assert PromotionDraft.model_validate(_sized_draft_payload(MAX_PROMOTION_DRAFT_BYTES - 1))
    assert PromotionDraft.model_validate(_sized_draft_payload(MAX_PROMOTION_DRAFT_BYTES))
    with pytest.raises(ValidationError, match="promotion draft exceeds"):
        PromotionDraft.model_validate(_sized_draft_payload(MAX_PROMOTION_DRAFT_BYTES + 1))


def test_promotion_models_reject_incoherent_critic_and_result_shapes() -> None:
    finding = PromotionCriticFinding(
        code="unsupported_claim", message="Missing citation", step_ordinals=(1,)
    )
    with pytest.raises(ValidationError, match="accepted"):
        PromotionCriticVerdict(accepted=True, findings=(finding,))
    with pytest.raises(ValidationError, match="accepted"):
        PromotionCriticVerdict(accepted=False, findings=())

    accepted = PromotionCriticVerdict(accepted=True, findings=())
    with pytest.raises(ValidationError, match="disposition"):
        PromotionCompilationResult(
            disposition="verified",
            draft=None,
            critic=accepted,
            repair_count=0,
            failure_code=None,
        )
    with pytest.raises(ValidationError, match="disposition"):
        PromotionCompilationResult(
            disposition="failed",
            draft=_draft(),
            critic=accepted,
            repair_count=0,
            failure_code="transport_failure",
        )


def test_promotion_models_require_sorted_unique_provenance_and_consecutive_steps() -> None:
    later = UUID("66666666-6666-4666-8666-666666666666")
    with pytest.raises(ValidationError, match="sorted and unique"):
        PromotionEvidenceItem(summary="Fact", event_ids=(later, EVENT_ID))
    with pytest.raises(ValidationError, match="sorted and unique"):
        PromotionEvidenceItem(summary="Fact", event_ids=(EVENT_ID, EVENT_ID))

    second = _draft().steps[0].model_copy(update={"ordinal": 3})
    with pytest.raises(ValidationError, match="consecutive"):
        PromotionDraft.model_validate(
            _draft().model_dump() | {"steps": (_draft().steps[0], second)}
        )


def test_promotion_input_revision_contract_remains_strict() -> None:
    with pytest.raises(ValidationError):
        JournalRevision(sequence=True, event_hash="a" * 64)
