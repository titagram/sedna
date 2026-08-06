"""Heading-aware logical segmentation over immutable parsed Markdown blocks."""

from __future__ import annotations

from dataclasses import dataclass

from sedna.knowledge.parsing.models import (
    BlockKind,
    LogicalSegment,
    ParsedBlock,
    ParsedDocument,
)
from sedna.knowledge.parsing.sanitize import (
    _contextual_hex_flag_values,
    _sanitize_searchable_text,
)


@dataclass(frozen=True, slots=True)
class _BlockView:
    index: int
    block: ParsedBlock
    heading_path: tuple[str, ...]
    searchable_text: str


def segment_document(
    document: ParsedDocument,
    maximum_segment_chars: int = 12_000,
) -> tuple[LogicalSegment, ...]:
    """Group a parsed document without slicing source blocks or leaking flags.

    A heading owns blocks through the next heading at the same or a higher level.
    Oversized sections split only between atomic block groups. A heading and its
    first block stay together, as do an action immediately before a code block,
    that code block, and its immediate result or explanation. A single atomic
    group may therefore exceed the configured target.
    """
    if maximum_segment_chars < 1:
        raise ValueError("maximum_segment_chars must be positive")
    if not document.blocks:
        return ()

    views = _block_views(document)
    segments: list[LogicalSegment] = []
    for section in _section_ranges(views):
        atomic_groups = _atomic_groups(views[section.start : section.stop])
        for packed_group in _pack_groups(atomic_groups, maximum_segment_chars):
            segments.append(_build_segment(document, packed_group))
    return tuple(segments)


def _block_views(document: ParsedDocument) -> tuple[_BlockView, ...]:
    heading_stack: list[tuple[int, str]] = []
    contextual_blocks: list[tuple[int, ParsedBlock, tuple[str, ...]]] = []

    for index, block in enumerate(document.blocks):
        if block.kind is BlockKind.HEADING:
            assert block.level is not None
            while heading_stack and heading_stack[-1][0] >= block.level:
                heading_stack.pop()
            heading_stack.append((block.level, block.text))

        heading_path = tuple(text for _, text in heading_stack)
        contextual_blocks.append((index, block, heading_path))

    known_hex_flags = frozenset(
        value
        for _, block, heading_path in contextual_blocks
        for value in _contextual_hex_flag_values(block.text, heading_path)
    )
    return tuple(
        _BlockView(
            index=index,
            block=block,
            heading_path=tuple(
                sanitized_component
                for component in heading_path
                if (
                    sanitized_component := _sanitize_searchable_text(
                        component,
                        heading_path,
                        known_hex_flags,
                    ).strip()
                )
            ),
            searchable_text=_sanitize_searchable_text(
                block.text,
                heading_path,
                known_hex_flags,
            ),
        )
        for index, block, heading_path in contextual_blocks
    )


def _section_ranges(views: tuple[_BlockView, ...]) -> tuple[range, ...]:
    sections: list[range] = []
    section_start = 0
    section_level: int | None = None

    for position, view in enumerate(views):
        if view.block.kind is not BlockKind.HEADING:
            continue
        assert view.block.level is not None

        if section_level is None:
            if position > section_start:
                sections.append(range(section_start, position))
            section_start = position
            section_level = view.block.level
        elif view.block.level <= section_level:
            sections.append(range(section_start, position))
            section_start = position
            section_level = view.block.level

    if section_start < len(views):
        sections.append(range(section_start, len(views)))
    return tuple(sections)


def _atomic_groups(views: tuple[_BlockView, ...]) -> tuple[tuple[_BlockView, ...], ...]:
    if not views:
        return ()

    groups: list[tuple[_BlockView, ...]] = []
    position = 0
    while position < len(views):
        group_start = position
        current = views[position].block

        if current.kind is BlockKind.HEADING:
            position += 1
            if position < len(views) and views[position].block.kind is not BlockKind.HEADING:
                if views[position].block.kind is BlockKind.CODE:
                    position += 1
                    position = _consume_immediate_result(views, position)
                else:
                    position += 1
                    if position < len(views) and views[position].block.kind is BlockKind.CODE:
                        position += 1
                        position = _consume_immediate_result(views, position)
        elif current.kind is BlockKind.CODE:
            position += 1
            position = _consume_immediate_result(views, position)
        elif position + 1 < len(views) and views[position + 1].block.kind is BlockKind.CODE:
            position += 2
            position = _consume_immediate_result(views, position)
        else:
            position += 1

        groups.append(views[group_start:position])
    return tuple(groups)


def _consume_immediate_result(views: tuple[_BlockView, ...], position: int) -> int:
    if position >= len(views):
        return position
    if views[position].block.kind in {BlockKind.HEADING, BlockKind.CODE}:
        return position
    return position + 1


def _pack_groups(
    groups: tuple[tuple[_BlockView, ...], ...],
    maximum_segment_chars: int,
) -> tuple[tuple[_BlockView, ...], ...]:
    packed: list[tuple[_BlockView, ...]] = []
    current: tuple[_BlockView, ...] = ()

    for group in groups:
        candidate = (*current, *group)
        if current and _rendered_length(candidate) > maximum_segment_chars:
            packed.append(current)
            current = group
        else:
            current = candidate

    if current:
        packed.append(current)
    return tuple(packed)


def _rendered_length(views: tuple[_BlockView, ...]) -> int:
    if not views:
        return 0
    return sum(len(view.searchable_text) for view in views) + 2 * (len(views) - 1)


def _build_segment(
    document: ParsedDocument,
    views: tuple[_BlockView, ...],
) -> LogicalSegment:
    block_indices = tuple(view.index for view in views)
    start_line = min(view.block.start_line for view in views)
    end_line = max(view.block.end_line for view in views)
    assets = tuple(
        asset
        for asset in document.assets
        if asset.end_line >= start_line and asset.start_line <= end_line
    )
    return LogicalSegment(
        block_indices=block_indices,
        text="\n\n".join(view.searchable_text for view in views),
        start_line=start_line,
        end_line=end_line,
        heading_path=views[0].heading_path,
        assets=assets,
    )
