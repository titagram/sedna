"""Tests for immutable structural parsing contracts."""

import json

import pytest
from pydantic import ValidationError

import sedna.knowledge.parsing.models as parsing_models
from sedna.knowledge.parsing import (
    BlockKind,
    LogicalSegment,
    ParsedAsset,
    ParsedBlock,
    ParsedDocument,
    PreparedSource,
    SegmentAsset,
    parse_markdown,
)
from sedna.knowledge.schema import (
    DocumentManifest,
    DocumentType,
    ExtractionMetadata,
    IngestionStatus,
    KnowledgeRole,
    SourceQuality,
)


def manifest() -> DocumentManifest:
    return DocumentManifest(
        source_id="source-test",
        path="sample.md",
        sha256="a" * 64,
        title="Sample",
        language="en",
        document_type=DocumentType.LESSON,
        knowledge_role=KnowledgeRole.REFERENCE,
        quality=SourceQuality.COMPLETE,
        parser_profile="academy_obsidian",
        ingestion_status=IngestionStatus.ACCEPTED,
        extraction=ExtractionMetadata(
            schema_version="1.0.0",
            parser_id="markdown-it-commonmark",
            parser_version="1",
            extractor_id="deterministic-foundation",
            extractor_version="1",
        ),
    )


def test_parsed_block_requires_source_line_range():
    with pytest.raises(ValidationError):
        ParsedBlock(kind=BlockKind.PARAGRAPH, text="Observe HTTP.")


@pytest.mark.parametrize("level", [0, 7])
def test_parsed_block_rejects_invalid_heading_level(level):
    with pytest.raises(ValidationError):
        ParsedBlock(
            kind=BlockKind.HEADING,
            text="Enumeration",
            start_line=1,
            end_line=1,
            level=level,
        )


def test_parsed_heading_requires_a_level():
    with pytest.raises(ValidationError):
        ParsedBlock(kind=BlockKind.HEADING, text="Enumeration", start_line=1, end_line=1)


def test_code_block_language_is_optional():
    block = ParsedBlock(
        kind=BlockKind.CODE,
        text="nmap -sV TARGET_IP",
        start_line=3,
        end_line=5,
    )

    assert block.language is None


def test_non_code_block_rejects_language():
    with pytest.raises(ValidationError):
        ParsedBlock(
            kind=BlockKind.PARAGRAPH,
            text="Observe HTTP.",
            start_line=1,
            end_line=1,
            language="text",
        )


@pytest.mark.parametrize(
    "parsed",
    [
        ParsedBlock(
            kind=BlockKind.PARAGRAPH,
            text="Read the reference.",
            start_line=1,
            end_line=1,
            metadata={"url": "https://example.test/reference"},
        ),
        ParsedAsset(
            target="screenshot.png",
            start_line=2,
            end_line=2,
            metadata={"source": "markdown_image"},
        ),
    ],
)
def test_metadata_is_deeply_immutable_and_json_round_trips(parsed):
    key = next(iter(parsed.metadata))

    with pytest.raises(TypeError):
        parsed.metadata[key] = "changed"

    restored = type(parsed).model_validate_json(parsed.model_dump_json())
    assert restored == parsed
    assert restored.model_dump()["metadata"] == dict(parsed.metadata)


def test_parsed_models_reject_reversed_line_ranges():
    with pytest.raises(ValidationError):
        ParsedBlock(
            kind=BlockKind.PARAGRAPH,
            text="Observe HTTP.",
            start_line=3,
            end_line=2,
        )
    with pytest.raises(ValidationError):
        ParsedAsset(target="screenshot.png", start_line=3, end_line=2)


def test_logical_segment_tracks_blocks_heading_path_and_assets():
    asset = SegmentAsset(
        asset_index=0,
        target="screenshot.png",
        start_line=4,
        end_line=4,
    )
    segment = LogicalSegment(
        block_indices=(0, 1),
        text="Enumeration\nObserve HTTP.",
        start_line=1,
        end_line=4,
        heading_path=("Enumeration",),
        assets=(asset,),
    )

    assert segment.block_indices == (0, 1)
    assert segment.assets == (asset,)


def test_segment_asset_is_a_retrieval_safe_view_with_no_raw_metadata():
    safe = SegmentAsset(
        asset_index=0,
        target="<EXCLUDED_FLAG>.png",
        start_line=4,
        end_line=4,
    )

    assert set(SegmentAsset.model_fields) == {
        "asset_index",
        "target",
        "start_line",
        "end_line",
    }
    assert safe.target == "<EXCLUDED_FLAG>.png"
    with pytest.raises(ValidationError, match="flag marker"):
        SegmentAsset(
            asset_index=0,
            target="HTB{secret}.png",
            start_line=4,
            end_line=4,
        )


@pytest.mark.parametrize("block_indices", [(1, 0), (0, 0), (0, 2)])
def test_logical_segment_requires_unique_increasing_block_indices(block_indices):
    with pytest.raises(ValidationError):
        LogicalSegment(
            block_indices=block_indices,
            text="Observe HTTP.",
            start_line=1,
            end_line=1,
        )


def test_logical_segment_rejects_huge_index_gap_without_materializing_range(monkeypatch):
    range_calls = []

    def observe_range_materialization(*args):
        range_calls.append(args)
        return ()

    monkeypatch.setattr(parsing_models, "range", observe_range_materialization, raising=False)

    with pytest.raises(ValidationError):
        LogicalSegment(
            block_indices=(0, 1_000_000_000),
            text="Observe HTTP.",
            start_line=1,
            end_line=1,
        )
    assert range_calls == []


def test_logical_segment_rejects_reversed_line_range():
    with pytest.raises(ValidationError):
        LogicalSegment(
            block_indices=(0,),
            text="Observe HTTP.",
            start_line=2,
            end_line=1,
        )


def test_prepared_source_contains_only_structural_preparation_fields():
    heading = ParsedBlock(
        kind=BlockKind.HEADING,
        text="Enumeration",
        start_line=1,
        end_line=1,
        level=1,
    )
    document = ParsedDocument(source_id="source-test", path="sample.md", blocks=(heading,))
    segment = LogicalSegment(
        block_indices=(0,),
        text="Enumeration",
        start_line=1,
        end_line=1,
        heading_path=("Enumeration",),
    )

    prepared = PreparedSource(manifest=manifest(), document=document, segments=(segment,))

    assert prepared.document == document
    assert set(PreparedSource.model_fields) == {"manifest", "document", "segments"}
    with pytest.raises(ValidationError):
        PreparedSource(
            manifest=manifest(),
            document=document,
            segments=(segment,),
            hypotheses=("HTTP may expose an admin panel",),
        )


def test_parsing_models_are_frozen_and_forbid_unknown_fields():
    block = ParsedBlock(
        kind=BlockKind.PARAGRAPH,
        text="Observe HTTP.",
        start_line=1,
        end_line=1,
    )

    with pytest.raises(ValidationError):
        block.text = "changed"
    with pytest.raises(ValidationError):
        ParsedDocument(source_id="source-test", path="sample.md", unknown=True)


def test_prepared_source_rejects_document_identity_mismatch():
    document = ParsedDocument(source_id="another-source", path="sample.md")

    with pytest.raises(ValidationError):
        PreparedSource(manifest=manifest(), document=document, segments=())


def test_prepared_source_rejects_out_of_range_segment_block_index():
    block = ParsedBlock(
        kind=BlockKind.PARAGRAPH,
        text="Observe HTTP.",
        start_line=1,
        end_line=1,
    )
    document = ParsedDocument(source_id="source-test", path="sample.md", blocks=(block,))
    segment = LogicalSegment(
        block_indices=(1,),
        text="Observe HTTP.",
        start_line=1,
        end_line=1,
    )

    with pytest.raises(ValidationError):
        PreparedSource(manifest=manifest(), document=document, segments=(segment,))


def test_prepared_source_rejects_segment_span_inconsistent_with_blocks():
    block = ParsedBlock(
        kind=BlockKind.PARAGRAPH,
        text="Observe HTTP.",
        start_line=2,
        end_line=3,
    )
    document = ParsedDocument(source_id="source-test", path="sample.md", blocks=(block,))
    segment = LogicalSegment(
        block_indices=(0,),
        text="Observe HTTP.",
        start_line=1,
        end_line=3,
    )

    with pytest.raises(ValidationError):
        PreparedSource(manifest=manifest(), document=document, segments=(segment,))


def test_prepared_source_rejects_segment_asset_not_owned_by_document():
    block = ParsedBlock(
        kind=BlockKind.IMAGE,
        text="HTTP response",
        start_line=1,
        end_line=1,
    )
    registered = ParsedAsset(target="registered.png", start_line=1, end_line=1)
    document = ParsedDocument(
        source_id="source-test",
        path="sample.md",
        blocks=(block,),
        assets=(registered,),
    )
    segment = LogicalSegment(
        block_indices=(0,),
        text="HTTP response",
        start_line=1,
        end_line=1,
        assets=(
            SegmentAsset(
                asset_index=0,
                target="foreign.png",
                start_line=1,
                end_line=1,
            ),
        ),
    )

    with pytest.raises(ValidationError):
        PreparedSource(manifest=manifest(), document=document, segments=(segment,))


def test_prepared_source_rejects_owned_asset_outside_segment_span():
    block = ParsedBlock(
        kind=BlockKind.PARAGRAPH,
        text="Observe HTTP.",
        start_line=1,
        end_line=1,
    )
    distant_asset = ParsedAsset(target="later.png", start_line=100, end_line=100)
    document = ParsedDocument(
        source_id="source-test",
        path="sample.md",
        blocks=(block,),
        assets=(distant_asset,),
    )
    segment = LogicalSegment(
        block_indices=(0,),
        text="Observe HTTP.",
        start_line=1,
        end_line=1,
        assets=(
            SegmentAsset(
                asset_index=0,
                target="later.png",
                start_line=100,
                end_line=100,
            ),
        ),
    )

    with pytest.raises(ValidationError):
        PreparedSource(manifest=manifest(), document=document, segments=(segment,))


def test_prepared_source_accepts_owned_asset_overlapping_segment_span():
    block = ParsedBlock(
        kind=BlockKind.IMAGE,
        text="HTTP response",
        start_line=2,
        end_line=3,
    )
    overlapping_asset = ParsedAsset(target="response.png", start_line=3, end_line=4)
    document = ParsedDocument(
        source_id="source-test",
        path="sample.md",
        blocks=(block,),
        assets=(overlapping_asset,),
    )
    segment = LogicalSegment(
        block_indices=(0,),
        text="HTTP response",
        start_line=2,
        end_line=3,
        assets=(
            SegmentAsset(
                asset_index=0,
                target="response.png",
                start_line=3,
                end_line=4,
            ),
        ),
    )

    prepared = PreparedSource(manifest=manifest(), document=document, segments=(segment,))

    assert prepared.segments[0].assets[0].target == overlapping_asset.target


def test_parse_markdown_preserves_structure_and_exact_source_lines():
    parsed = parse_markdown(
        "source-test",
        "sample.md",
        "# Title\n\nObserve HTTP.\n\n```bash session=scan\nnmap -sV TARGET_IP\n```\n",
    )

    assert [block.kind for block in parsed.blocks] == [
        BlockKind.HEADING,
        BlockKind.PARAGRAPH,
        BlockKind.CODE,
    ]
    assert [(block.start_line, block.end_line) for block in parsed.blocks] == [
        (1, 1),
        (3, 3),
        (5, 7),
    ]
    assert parsed.blocks[0].level == 1
    assert parsed.blocks[2].language == "bash"
    assert parsed.blocks[2].metadata["info"] == "bash session=scan"
    assert parsed.blocks[2].text == "nmap -sV TARGET_IP"


def test_parse_markdown_supports_setext_headings_and_thematic_breaks():
    parsed = parse_markdown(
        "source-test",
        "sample.md",
        "Main title\n==========\n\nSub title\n---------\n\n---\n",
    )

    structure = [
        (block.kind, block.level, block.start_line, block.end_line)
        for block in parsed.blocks
    ]
    assert structure == [
        (BlockKind.HEADING, 1, 1, 2),
        (BlockKind.HEADING, 2, 4, 5),
        (BlockKind.THEMATIC_BREAK, None, 7, 7),
    ]


def test_parse_markdown_preserves_tables_and_inline_links():
    parsed = parse_markdown(
        "source-test",
        "sample.md",
        "| Service | Reference |\n| --- | --- |\n| HTTP | [guide](https://example.test/one) |\n"
        "| SSH | [notes](https://example.test/two) |\n",
    )

    assert len(parsed.blocks) == 1
    table = parsed.blocks[0]
    assert table.kind is BlockKind.TABLE
    assert (table.start_line, table.end_line) == (1, 4)
    assert table.text == (
        "Service | Reference\nHTTP | guide\nSSH | notes"
    )
    assert table.metadata["urls"] == (
        '["https://example.test/one","https://example.test/two"]'
    )
    assert parsed.relationships == (
        "https://example.test/one",
        "https://example.test/two",
    )


def test_parse_markdown_anchors_table_assets_to_their_row_token_map():
    parsed = parse_markdown(
        "source-test",
        "sample.md",
        "| Evidence |\n| --- |\n| ![response](response.png) |\n",
    )

    assert len(parsed.assets) == 1
    assert (parsed.assets[0].start_line, parsed.assets[0].end_line) == (3, 3)


def test_parse_markdown_emits_ordered_and_nested_unordered_list_items():
    parsed = parse_markdown(
        "source-test",
        "sample.md",
        "1. Enumerate\n2. Validate\n\n- Parent\n  - Child\n",
    )

    assert [block.kind for block in parsed.blocks] == [
        BlockKind.ORDERED_LIST_ITEM,
        BlockKind.ORDERED_LIST_ITEM,
        BlockKind.UNORDERED_LIST_ITEM,
        BlockKind.UNORDERED_LIST_ITEM,
    ]
    assert [(block.text, block.start_line, block.end_line) for block in parsed.blocks] == [
        ("Enumerate", 1, 1),
        ("Validate", 2, 3),
        ("Parent", 4, 5),
        ("Child", 5, 5),
    ]


def test_parse_markdown_preserves_blockquotes_and_html_blocks():
    parsed = parse_markdown(
        "source-test",
        "sample.md",
        "> Observation\n> continued\n\n<div class=\"note\">Keep this wrapper.</div>\n",
    )

    assert [block.kind for block in parsed.blocks] == [
        BlockKind.BLOCKQUOTE,
        BlockKind.HTML,
    ]
    assert parsed.blocks[0].text == "Observation\ncontinued"
    assert (parsed.blocks[0].start_line, parsed.blocks[0].end_line) == (1, 2)
    assert parsed.blocks[1].text == '<div class="note">Keep this wrapper.</div>'


def test_parse_markdown_keeps_multiple_inline_links_and_images_without_losing_alt_text():
    parsed = parse_markdown(
        "source-test",
        "sample.md",
        "Read [one](https://one.test) and [two](https://two.test), then compare "
        "![first **view**](one.png \"One\") with ![second](two.png).\n",
    )

    paragraph = parsed.blocks[0]
    assert paragraph.kind is BlockKind.PARAGRAPH
    assert paragraph.text == "Read one and two, then compare first view with second."
    assert paragraph.metadata["urls"] == '["https://one.test","https://two.test"]'
    assert paragraph.metadata["asset_targets"] == '["one.png","two.png"]'
    assert [(asset.target, asset.alt_text, asset.title) for asset in parsed.assets] == [
        ("one.png", "first view", "One"),
        ("two.png", "second", None),
    ]
    assert all((asset.start_line, asset.end_line) == (1, 1) for asset in parsed.assets)


def test_parse_markdown_emits_image_blocks_and_extracts_html_images_in_order():
    parsed = parse_markdown(
        "source-test",
        "sample.md",
        "![scan](scan.png)\n\n"
        "<figure>\n<img src=\"before.png\" alt=\"Before\" title=\"Initial\">\n"
        "<img alt=\"After\" src=\"after.png\">\n</figure>\n",
    )

    assert [block.kind for block in parsed.blocks] == [BlockKind.IMAGE, BlockKind.HTML]
    assert parsed.blocks[0].metadata["asset_target"] == "scan.png"
    assert parsed.blocks[1].metadata["asset_targets"] == '["before.png","after.png"]'
    asset_details = [
        (asset.target, asset.alt_text, asset.start_line, asset.end_line)
        for asset in parsed.assets
    ]
    assert asset_details == [
        ("scan.png", "scan", 1, 1),
        ("before.png", "Before", 3, 6),
        ("after.png", "After", 3, 6),
    ]


def test_parse_markdown_deduplicates_relationships_but_preserves_asset_occurrences():
    parsed = parse_markdown(
        "source-test",
        "sample.md",
        "[same](https://example.test) ![one](same.png)\n\n"
        "[same again](https://example.test) ![two](same.png)\n",
    )

    assert parsed.relationships == ("https://example.test",)
    assert [asset.target for asset in parsed.assets] == ["same.png", "same.png"]
    assert [(asset.start_line, asset.end_line) for asset in parsed.assets] == [(1, 1), (3, 3)]


def test_parse_markdown_emits_nested_heading_and_code_after_list_item_direct_text():
    parsed = parse_markdown(
        "source-test",
        "sample.md",
        "- Context\n\n  ## Nested heading\n\n      nested command\n",
    )

    assert [block.kind for block in parsed.blocks] == [
        BlockKind.UNORDERED_LIST_ITEM,
        BlockKind.HEADING,
        BlockKind.CODE,
    ]
    assert [(block.text, block.start_line, block.end_line) for block in parsed.blocks] == [
        ("Context", 1, 5),
        ("Nested heading", 3, 3),
        ("nested command", 5, 5),
    ]
    assert parsed.blocks[1].level == 2
    assert parsed.blocks[2].language is None


def test_parse_markdown_emits_nested_blockquote_structures_without_text_duplication():
    parsed = parse_markdown(
        "source-test",
        "sample.md",
        "> Observation\n>\n> ### Evidence\n>\n> ```shell session=proof\n> id\n> ```\n",
    )

    assert [block.kind for block in parsed.blocks] == [
        BlockKind.BLOCKQUOTE,
        BlockKind.HEADING,
        BlockKind.CODE,
    ]
    assert [block.text for block in parsed.blocks] == ["Observation", "Evidence", "id"]
    assert parsed.blocks[1].level == 3
    assert (parsed.blocks[2].start_line, parsed.blocks[2].end_line) == (5, 7)
    assert parsed.blocks[2].language == "shell"
    assert parsed.blocks[2].metadata["info"] == "shell session=proof"


def test_parse_markdown_classifies_linked_and_formatted_image_only_paragraphs_as_images():
    parsed = parse_markdown(
        "source-test",
        "sample.md",
        "[![linked](linked.png)](https://example.test/full)\n\n"
        "**![formatted](formatted.png)**\n",
    )

    assert [block.kind for block in parsed.blocks] == [BlockKind.IMAGE, BlockKind.IMAGE]
    assert [block.text for block in parsed.blocks] == ["linked", "formatted"]
    assert parsed.blocks[0].metadata["url"] == "https://example.test/full"
    assert parsed.blocks[0].metadata["asset_target"] == "linked.png"
    assert parsed.relationships == ("https://example.test/full",)
    assert [asset.target for asset in parsed.assets] == ["linked.png", "formatted.png"]


def test_parse_markdown_extracts_html_anchor_relationships_and_preserves_raw_html():
    markdown = (
        '<div><a href="https://one.test">One</a><img src="proof.png" alt="Proof">'
        '<a href="https://two.test">Two</a></div>\n\n'
        'Inspect <a href="https://three.test">three</a> beside '
        '<img src="inline.png" alt="Inline">.\n'
    )
    parsed = parse_markdown("source-test", "sample.md", markdown)

    assert [block.kind for block in parsed.blocks] == [BlockKind.HTML, BlockKind.PARAGRAPH]
    assert parsed.blocks[0].text == (
        '<div><a href="https://one.test">One</a><img src="proof.png" alt="Proof">'
        '<a href="https://two.test">Two</a></div>'
    )
    assert parsed.blocks[1].text == (
        'Inspect <a href="https://three.test">three</a> beside '
        '<img src="inline.png" alt="Inline">.'
    )
    assert parsed.blocks[0].metadata["urls"] == '["https://one.test","https://two.test"]'
    assert parsed.blocks[1].metadata["url"] == "https://three.test"
    assert parsed.relationships == (
        "https://one.test",
        "https://two.test",
        "https://three.test",
    )
    assert [asset.target for asset in parsed.assets] == ["proof.png", "inline.png"]


def test_parse_markdown_records_each_raw_html_anchor_start_offset_with_entities():
    html = (
        "<div>\n"
        '<a href="https://example.test/search?a=1&amp;b=2">named</a>\n'
        '<a href="https://example.test/&#x64;up">numeric</a>\n'
        '<a href="https://example.test/search?a=1&amp;b=2">duplicate</a>\n'
        "</div>"
    )
    parsed = parse_markdown(
        "source-test",
        "sample.md",
        "[ordinary](https://ordinary.test)\n\n" + html + "\n",
    )
    html_block = parsed.blocks[1]
    offsets = json.loads(html_block.metadata["url_offsets"])

    assert parsed.relationships == (
        "https://ordinary.test",
        "https://example.test/search?a=1&b=2",
        "https://example.test/dup",
    )
    assert json.loads(html_block.metadata["urls"]) == [
        "https://example.test/search?a=1&b=2",
        "https://example.test/dup",
        "https://example.test/search?a=1&b=2",
    ]
    assert offsets == sorted(offsets)
    assert len(set(offsets)) == 3
    assert all(offset >= 0 and html[offset:].startswith("<a ") for offset in offsets)


def test_parse_markdown_preserves_list_item_child_interleaving_and_relationship_order():
    parsed = parse_markdown(
        "source-test",
        "sample.md",
        "- [before](https://before.test)\n\n"
        "      nested command\n\n"
        "  ## [middle](https://middle.test)\n\n"
        "  [after](https://after.test)\n",
    )

    assert [block.kind for block in parsed.blocks] == [
        BlockKind.UNORDERED_LIST_ITEM,
        BlockKind.CODE,
        BlockKind.HEADING,
        BlockKind.PARAGRAPH,
    ]
    assert [block.text for block in parsed.blocks] == [
        "before",
        "nested command",
        "middle",
        "after",
    ]
    assert parsed.blocks[2].level == 2
    assert parsed.relationships == (
        "https://before.test",
        "https://middle.test",
        "https://after.test",
    )


def test_parse_markdown_preserves_blockquote_child_interleaving_and_relationship_order():
    parsed = parse_markdown(
        "source-test",
        "sample.md",
        "> [before](https://before.test)\n>\n"
        "> > [middle](https://middle.test)\n>\n"
        ">     nested command\n>\n"
        "> [after](https://after.test)\n",
    )

    assert [block.kind for block in parsed.blocks] == [
        BlockKind.BLOCKQUOTE,
        BlockKind.BLOCKQUOTE,
        BlockKind.CODE,
        BlockKind.PARAGRAPH,
    ]
    assert [block.text for block in parsed.blocks] == [
        "before",
        "middle",
        "nested command",
        "after",
    ]
    assert parsed.relationships == (
        "https://before.test",
        "https://middle.test",
        "https://after.test",
    )
