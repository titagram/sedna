"""Acceptance tests for immutable promotion source rendering."""

import json
from dataclasses import FrozenInstanceError
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from markdown_it import MarkdownIt
from pydantic import ValidationError

import sedna.engagement.promotion.models as promotion_models
from sedna.engagement import (
    JournalRevision,
    PromotionArtifactId,
    PromotionCaseId,
    PromotionSourceId,
)
from sedna.engagement.promotion import (
    MAX_PROMOTION_PROVENANCE_EVENT_IDS,
    MAX_PROMOTION_SPAN_EVENT_IDS,
    PROMOTION_DRAFT_SCHEMA_VERSION,
    CommittedPromotionSource,
    PromotionClaim,
    PromotionDraft,
    PromotionEvidenceItem,
    PromotionInput,
    PromotionProvenanceMap,
    PromotionProvenanceSpan,
    PromotionRenderIdentity,
    PromotionSecretInventory,
    PromotionStepDraft,
    RenderedPromotionSource,
    build_promotion_prepared_source,
    promotion_source_id,
    render_promotion_source,
)

ENGAGEMENT_ID = UUID("11111111-1111-4111-8111-111111111111")
ATTEMPT_ID = UUID("22222222-2222-4222-8222-222222222222")
VERIFICATION_EVENT_ID = UUID("33333333-3333-4333-8333-333333333333")
CONTEXT_EVENT_ID = UUID("44444444-4444-4444-8444-444444444444")
DECISION_EVENT_ID = UUID("55555555-5555-4555-8555-555555555555")
OUTCOME_EVENT_ID = UUID("66666666-6666-4666-8666-666666666666")
EVIDENCE_ID = "evidence-sha256-" + "7" * 64
SOURCE_ID = f"source-{uuid5(NAMESPACE_URL, f'sedna:journal-promotion:{ENGAGEMENT_ID}')}"


def _claim(text: str, event_id: UUID) -> PromotionClaim:
    return PromotionClaim(text=text, event_ids=(event_id,), evidence_ids=(EVIDENCE_ID,))


def _context() -> PromotionInput:
    return PromotionInput(
        engagement_id=ENGAGEMENT_ID,
        verified_revision=JournalRevision(sequence=41, event_hash="a" * 64),
        verification_event_id=VERIFICATION_EVENT_ID,
        display_name="Reusable protocol case",
        objective="Establish verified access with <CREDENTIAL_1>",
        context=(
            PromotionEvidenceItem(
                summary="A Linux service was observable.",
                event_ids=(CONTEXT_EVENT_ID,),
                evidence_ids=(EVIDENCE_ID,),
            ),
        ),
        decisions=(),
        outcomes=(),
        alternatives=(),
    )


def _draft() -> PromotionDraft:
    return PromotionDraft(
        schema_version=PROMOTION_DRAFT_SCHEMA_VERSION,
        title="Protocol inspection after an initial failure",
        starting_access=_claim("A Linux service was observable.", CONTEXT_EVENT_ID),
        applicability=(_claim("Applicable to the observed service.", CONTEXT_EVENT_ID),),
        steps=(
            PromotionStepDraft(
                ordinal=1,
                state_before="The initial path had failed.",
                observations=("The protocol remained observable.",),
                hypotheses=("Inspection could expose a strategic pivot.",),
                selected_strategy="Inspect protocol behavior before selecting a follow-up.",
                command_examples=("inspect --target <TARGET_1>",),
                outcome="Verified access was established with <CREDENTIAL_1>.",
                negative_evidence=("The initial path did not produce access.",),
                retry_conditions=("Retry while the protocol remains applicable.",),
                state_after="Verified access was established.",
                event_ids=(DECISION_EVENT_ID, OUTCOME_EVENT_ID),
                evidence_ids=(EVIDENCE_ID,),
            ),
        ),
        alternate_paths=(),
        transferable_properties=(
            _claim("Use observed behavior to select the next strategy.", DECISION_EVENT_ID),
        ),
        non_transferable_properties=(
            _claim("The exact target identity is case-local.", CONTEXT_EVENT_ID),
        ),
        generalizability="low",
        generalizability_basis=_claim(
            "Evidence supports only the observed service context.", CONTEXT_EVENT_ID
        ),
        verified_outcome=_claim("Verified access was established.", OUTCOME_EVENT_ID),
    )


def _identity() -> PromotionRenderIdentity:
    return PromotionRenderIdentity(
        engagement_id=ENGAGEMENT_ID,
        attempt_id=ATTEMPT_ID,
        verification_event_id=VERIFICATION_EVENT_ID,
        verified_revision=JournalRevision(sequence=41, event_hash="a" * 64),
        source_id=SOURCE_ID,
        promotion_revision=1,
    )


def test_rendered_source_has_exact_secret_free_line_provenance() -> None:
    rendered = render_promotion_source(
        _draft(),
        context=_context(),
        inventory=PromotionSecretInventory(
            flags=("HTB{private-proof}",),
            credentials=("root:private-password",),
            target_identifiers=("10.10.11.42",),
        ),
        identity=_identity(),
    )

    assert rendered.source_sha256 == sha256(rendered.markdown.encode()).hexdigest()
    assert rendered.provenance.source_sha256 == rendered.source_sha256
    assert rendered.source_id == SOURCE_ID
    assert rendered.source_relative_path == (
        f"engagements/{ENGAGEMENT_ID}/promotion/sources/promotion-v1.md"
    )
    assert rendered.provenance_relative_path.endswith("promotion-v1.provenance.json")
    assert rendered.provenance.spans
    assert all(span.start_line <= span.end_line for span in rendered.provenance.spans)
    assert "HTB{" not in rendered.markdown
    assert "10.10.11.42" not in rendered.markdown
    assert "<CREDENTIAL_1>" in rendered.markdown
    lines = rendered.markdown.splitlines()
    for span in rendered.provenance.spans:
        assert lines[span.start_line - 1 : span.end_line]


def test_hostile_markdown_is_inert_and_keeps_exact_multiline_spans() -> None:
    hostile_claim = PromotionClaim(
        text="---\n# forged heading\n<script>alert(1)</script> [jump](https://invalid)",
        event_ids=(CONTEXT_EVENT_ID,),
        evidence_ids=(EVIDENCE_ID,),
    )
    hostile_step = PromotionStepDraft(
        ordinal=1,
        state_before="before\n## forged state heading",
        observations=("<b>observation</b>",),
        hypotheses=("[hypothesis](https://invalid)",),
        selected_strategy="strategy `inline`",
        command_examples=("printf '```'\ninspect --target <TARGET_1>",),
        outcome="outcome",
        negative_evidence=("negative",),
        retry_conditions=("retry",),
        state_after="after",
        event_ids=(DECISION_EVENT_ID, OUTCOME_EVENT_ID),
        evidence_ids=(EVIDENCE_ID,),
    )
    draft = _draft().model_copy(
        update={
            "title": "--- # [hostile](https://invalid) <i>title</i>",
            "starting_access": hostile_claim,
            "steps": (hostile_step,),
        }
    )

    rendered = render_promotion_source(
        draft,
        context=_context(),
        inventory=PromotionSecretInventory(),
        identity=_identity(),
    )

    tokens = MarkdownIt("commonmark").parse(rendered.markdown)
    headings = [
        (
            int(token.tag[1:]),
            "".join(child.content for child in (tokens[index + 1].children or ())),
        )
        for index, token in enumerate(tokens)
        if token.type == "heading_open"
    ]
    assert headings == [
        (1, "--- # [hostile](https://invalid) <i>title</i>"),
        (2, "Starting Access"),
        (2, "Applicability"),
        (2, "Steps"),
        (3, "Step 1"),
        (2, "Alternate Paths"),
        (2, "Transferable Properties"),
        (2, "Non-transferable Properties"),
        (2, "Generalizability Basis"),
        (2, "Verified Outcome"),
    ]
    assert not any(
        token.type in {"html_block", "html_inline", "link_open", "image"} for token in tokens
    )
    code = next(token for token in tokens if token.type == "fence")
    assert code.content == "printf '```'\ninspect --target <TARGET_1>\n"
    lines = rendered.markdown.splitlines()
    hostile_span = next(
        span
        for span in rendered.provenance.spans
        if span.event_ids == (CONTEXT_EVENT_ID,) and span.end_line - span.start_line == 2
    )
    assert hostile_span.evidence_ids == (EVIDENCE_ID,)
    assert lines[hostile_span.start_line - 1 : hostile_span.end_line] == [
        r"\-\-\-",
        r"\# forged heading",
        "&lt;script&gt;alert\\(1\\)&lt;/script&gt; \\[jump\\]\\(https://invalid\\)",
    ]


def test_lone_cr_prose_keeps_exact_physical_emitted_line_span() -> None:
    draft = _draft().model_copy(
        update={
            "starting_access": _claim("first physical line\rsecond physical line", CONTEXT_EVENT_ID)
        }
    )

    rendered = render_promotion_source(
        draft,
        context=_context(),
        inventory=PromotionSecretInventory(),
        identity=_identity(),
    )

    lines = rendered.markdown.splitlines()
    matching_spans = [
        span
        for span in rendered.provenance.spans
        if lines[span.start_line - 1 : span.end_line]
        == ["first physical line", "second physical line"]
    ]
    assert len(matching_spans) == 1
    assert matching_spans[0].end_line - matching_spans[0].start_line == 1


def test_multiline_title_is_rendered_as_one_inert_heading() -> None:
    draft = _draft().model_copy(update={"title": "first line\n# forged second line"})

    rendered = render_promotion_source(
        draft,
        context=_context(),
        inventory=PromotionSecretInventory(),
        identity=_identity(),
    )
    repeated = render_promotion_source(
        draft,
        context=_context(),
        inventory=PromotionSecretInventory(),
        identity=_identity(),
    )

    assert rendered == repeated
    tokens = MarkdownIt("commonmark").parse(rendered.markdown)
    headings = [
        "".join(child.content for child in (tokens[index + 1].children or ()))
        for index, token in enumerate(tokens)
        if token.type == "heading_open"
    ]
    assert headings[0] == "first line # forged second line"
    assert "forged second line" not in headings[1:]


@pytest.mark.parametrize(
    ("title", "expected_heading"),
    (
        ("first line\rsecond line", "first line second line"),
        ("first line\r\nsecond line", "first line second line"),
        ("\nfirst line", "first line"),
        ("first line\n", "first line"),
    ),
)
def test_universal_newline_and_boundary_empty_titles_are_one_inert_heading(
    title: str, expected_heading: str
) -> None:
    draft = _draft().model_copy(update={"title": title})

    rendered = render_promotion_source(
        draft,
        context=_context(),
        inventory=PromotionSecretInventory(),
        identity=_identity(),
    )
    repeated = render_promotion_source(
        draft,
        context=_context(),
        inventory=PromotionSecretInventory(),
        identity=_identity(),
    )

    assert rendered == repeated
    assert rendered.title == title
    tokens = MarkdownIt("commonmark").parse(rendered.markdown)
    headings = [
        "".join(child.content for child in (tokens[index + 1].children or ()))
        for index, token in enumerate(tokens)
        if token.type == "heading_open"
    ]
    assert headings[0] == expected_heading
    assert len(headings) == 10


def test_rendered_source_rejects_provenance_beyond_physical_markdown_lines() -> None:
    rendered = render_promotion_source(
        _draft(),
        context=_context(),
        inventory=PromotionSecretInventory(),
        identity=_identity(),
    )
    line_count = len(rendered.markdown.splitlines())
    final = rendered.provenance.spans[-1]
    invalid_map = rendered.provenance.model_copy(
        update={
            "spans": rendered.provenance.spans[:-1]
            + (final.model_copy(update={"end_line": line_count + 1}),)
        }
    )
    canonical = json.dumps(
        invalid_map.model_dump(mode="json", warnings="error"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()

    with pytest.raises(ValidationError, match="physical Markdown lines"):
        RenderedPromotionSource.model_validate(
            rendered.model_dump(mode="python", warnings="error")
            | {
                "provenance": invalid_map,
                "provenance_sha256": sha256(canonical).hexdigest(),
            }
        )


def test_rendered_source_rejects_provenance_bound_to_another_source_digest() -> None:
    rendered = render_promotion_source(
        _draft(),
        context=_context(),
        inventory=PromotionSecretInventory(),
        identity=_identity(),
    )
    invalid_map = rendered.provenance.model_copy(update={"source_sha256": "f" * 64})
    canonical = json.dumps(
        invalid_map.model_dump(mode="json", warnings="error"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()

    with pytest.raises(ValidationError, match="source digest does not match provenance"):
        RenderedPromotionSource.model_validate(
            rendered.model_dump(mode="python", warnings="error")
            | {
                "provenance": invalid_map,
                "provenance_sha256": sha256(canonical).hexdigest(),
            }
        )


def test_trailing_prose_newline_does_not_overstate_final_span() -> None:
    draft = _draft().model_copy(
        update={"verified_outcome": _claim("Verified access was established.\n", OUTCOME_EVENT_ID)}
    )

    rendered = render_promotion_source(
        draft,
        context=_context(),
        inventory=PromotionSecretInventory(),
        identity=_identity(),
    )

    line_count = len(rendered.markdown.splitlines())
    assert all(
        1 <= span.start_line <= span.end_line <= line_count for span in rendered.provenance.spans
    )


def test_render_identity_is_stable_closed_and_immutable() -> None:
    assert PromotionCaseId is PromotionArtifactId
    assert PromotionSourceId is promotion_models.PromotionSourceId
    assert promotion_source_id(ENGAGEMENT_ID) == SOURCE_ID
    first = _identity()
    second = PromotionRenderIdentity(
        engagement_id=ENGAGEMENT_ID,
        attempt_id=UUID("77777777-7777-4777-8777-777777777777"),
        verification_event_id=VERIFICATION_EVENT_ID,
        verified_revision=JournalRevision(sequence=42, event_hash="b" * 64),
        source_id=SOURCE_ID,
        promotion_revision=2,
    )
    assert first.source_id == second.source_id
    with pytest.raises(FrozenInstanceError):
        first.promotion_revision = 2  # type: ignore[misc]
    with pytest.raises(ValueError, match="source identity"):
        PromotionRenderIdentity(
            engagement_id=ENGAGEMENT_ID,
            attempt_id=ATTEMPT_ID,
            verification_event_id=VERIFICATION_EVENT_ID,
            verified_revision=first.verified_revision,
            source_id="source-wrong",
            promotion_revision=1,
        )
    with pytest.raises(ValueError, match="positive integer"):
        PromotionRenderIdentity(
            engagement_id=ENGAGEMENT_ID,
            attempt_id=ATTEMPT_ID,
            verification_event_id=VERIFICATION_EVENT_ID,
            verified_revision=first.verified_revision,
            source_id=SOURCE_ID,
            promotion_revision=0,
        )


def test_provenance_tuple_and_canonical_byte_bounds_are_exact(monkeypatch) -> None:
    event_ids = tuple(UUID(int=index + 1) for index in range(MAX_PROMOTION_SPAN_EVENT_IDS))
    spans = tuple(
        PromotionProvenanceSpan(
            start_line=index * 2 + 1,
            end_line=index * 2 + 1,
            event_ids=event_ids,
        )
        for index in range(MAX_PROMOTION_PROVENANCE_EVENT_IDS // len(event_ids))
    )
    payload = {
        "engagement_id": ENGAGEMENT_ID,
        "attempt_id": ATTEMPT_ID,
        "promotion_revision": 1,
        "verified_revision": JournalRevision(sequence=41, event_hash="a" * 64),
        "verification_event_id": VERIFICATION_EVENT_ID,
        "source_id": SOURCE_ID,
        "source_relative_path": (f"engagements/{ENGAGEMENT_ID}/promotion/sources/promotion-v1.md"),
        "source_sha256": "c" * 64,
        "spans": spans,
    }
    exact = PromotionProvenanceMap(**payload)
    canonical_size = len(
        json.dumps(
            exact.model_dump(mode="json", warnings="error"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    monkeypatch.setattr(promotion_models, "MAX_PROMOTION_PROVENANCE_BYTES", canonical_size)
    PromotionProvenanceMap(**payload)
    monkeypatch.setattr(promotion_models, "MAX_PROMOTION_PROVENANCE_BYTES", canonical_size - 1)
    with pytest.raises(ValidationError, match="byte bound"):
        PromotionProvenanceMap(**payload)

    monkeypatch.setattr(promotion_models, "MAX_PROMOTION_PROVENANCE_BYTES", 8 * 1024 * 1024)
    over = spans + (
        PromotionProvenanceSpan(
            start_line=len(spans) * 2 + 1,
            end_line=len(spans) * 2 + 1,
            event_ids=(UUID(int=MAX_PROMOTION_SPAN_EVENT_IDS + 1),),
        ),
    )
    with pytest.raises(ValidationError, match="cumulative bound"):
        PromotionProvenanceMap(**(payload | {"spans": over}))


def test_rendered_source_byte_limit_is_exact(monkeypatch) -> None:
    rendered = render_promotion_source(
        _draft(),
        context=_context(),
        inventory=PromotionSecretInventory(),
        identity=_identity(),
    )
    size = len(rendered.markdown.encode())
    monkeypatch.setattr(promotion_models, "MAX_PROMOTION_SOURCE_BYTES", size)
    RenderedPromotionSource.model_validate_json(rendered.model_dump_json(warnings="error"))
    monkeypatch.setattr(promotion_models, "MAX_PROMOTION_SOURCE_BYTES", size - 1)
    with pytest.raises(ValidationError, match="source exceeds"):
        RenderedPromotionSource.model_validate_json(rendered.model_dump_json(warnings="error"))


def test_committed_render_builds_strict_journal_promotion_prepared_source() -> None:
    rendered = render_promotion_source(
        _draft(),
        context=_context(),
        inventory=PromotionSecretInventory(),
        identity=_identity(),
    )
    committed = CommittedPromotionSource(
        **rendered.model_dump(mode="python", warnings="error"),
        committed_revision=JournalRevision(sequence=42, event_hash="b" * 64),
    )

    prepared = build_promotion_prepared_source(committed)

    assert prepared.manifest.source_id == SOURCE_ID
    assert prepared.manifest.source_namespace == "journal-promotion"
    assert prepared.manifest.path == rendered.source_relative_path
    assert prepared.manifest.sha256 == rendered.source_sha256
    assert prepared.manifest.parser_profile == "journal_promotion"
    assert prepared.manifest.extraction.extractor_id == "journal-promotion-renderer"
    assert prepared.manifest.assets[0].path == rendered.provenance_relative_path
    assert prepared.manifest.assets[0].sha256 == rendered.provenance_sha256
    assert prepared.document.source_id == SOURCE_ID
    assert prepared.segments
