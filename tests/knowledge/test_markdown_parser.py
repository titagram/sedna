"""Tests for immutable structural parsing contracts."""

import pytest
from pydantic import ValidationError

from sedna.knowledge.parsing import (
    BlockKind,
    LogicalSegment,
    ParsedAsset,
    ParsedBlock,
    ParsedDocument,
    PreparedSource,
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
    asset = ParsedAsset(
        target="screenshot.png",
        alt_text="HTTP response",
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


@pytest.mark.parametrize("block_indices", [(1, 0), (0, 0), (0, 2)])
def test_logical_segment_requires_unique_increasing_block_indices(block_indices):
    with pytest.raises(ValidationError):
        LogicalSegment(
            block_indices=block_indices,
            text="Observe HTTP.",
            start_line=1,
            end_line=1,
        )


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
    foreign = ParsedAsset(target="foreign.png", start_line=1, end_line=1)
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
        assets=(foreign,),
    )

    with pytest.raises(ValidationError):
        PreparedSource(manifest=manifest(), document=document, segments=(segment,))
