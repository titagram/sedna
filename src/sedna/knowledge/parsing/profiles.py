"""Deterministic source-family cleanup over parsed Markdown documents."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from html.parser import HTMLParser

from sedna.knowledge.parsing.models import (
    BlockKind,
    ParsedAsset,
    ParsedBlock,
    ParsedDocument,
)

ProfileAdapter = Callable[[ParsedDocument], ParsedDocument]

_HTB_ARTICLE_BOUNDARIES = frozenset(
    {
        "ad blocker detected",
        "cheatsheet",
        "connect to htb",
        "next",
        "previous",
    }
)
_BOUNDARY_BLOCK_KINDS = frozenset(
    {
        BlockKind.HEADING,
        BlockKind.PARAGRAPH,
        BlockKind.ORDERED_LIST_ITEM,
        BlockKind.UNORDERED_LIST_ITEM,
    }
)
_NOTE_METADATA_RE = re.compile(
    r"^(?P<label>tags|related\s+to|see\s+also|previous)\s*:\s*(?P<value>.*)$",
    re.IGNORECASE,
)
_WIKI_LINK_RE = re.compile(r"(?<!!)\[\[(?P<target>[^\]|]+)(?:\|[^\]]*)?\]\]")
_OBSIDIAN_EMBED_RE = re.compile(
    r"!\[\[(?P<target>[^\]|]+)(?:\|(?P<alias>[^\]]*))?\]\]"
)
_CLOSING_PRESENTATION_WRAPPER_RE = re.compile(
    r"\s*</(?:div|p)>\s*", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class _RelationshipEvent:
    line: int
    sequence: int
    target: str


class _PresentationHTMLParser(HTMLParser):
    """Identify centered wrappers and collect their human-visible text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.centered = False
        self.centered_balance = 0
        self.text_parts: list[str] = []
        self.asset_labels: list[str] = []
        self._open_tags: list[tuple[str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        is_centered = normalized_tag in {"div", "p"} and _has_center_alignment(attrs)
        self._open_tags.append((normalized_tag, is_centered))
        attributes = {name.casefold(): (value or "") for name, value in attrs}
        if normalized_tag == "img" and attributes.get("src"):
            self.asset_labels.append(attributes.get("alt") or attributes["src"])
        if is_centered:
            self.centered = True
            self.centered_balance += 1

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        for index in range(len(self._open_tags) - 1, -1, -1):
            open_tag, was_centered = self._open_tags[index]
            if open_tag != normalized_tag:
                continue
            del self._open_tags[index:]
            if was_centered:
                self.centered_balance -= 1
            return

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)

    @property
    def visible_text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self.text_parts)).strip()


def apply_profile(document: ParsedDocument, profile: str) -> ParsedDocument:
    """Apply exactly one supported source profile without mutating its input."""
    adapters: dict[str, ProfileAdapter] = {
        "academy_obsidian": _clean_academy_obsidian,
        "github_walkthrough": _clean_github_walkthrough,
        "htb_scrape": _clean_htb_scrape,
    }
    try:
        adapter = adapters[profile]
    except KeyError as error:
        raise ValueError(f"unsupported parser profile: {profile!r}") from error
    return adapter(document)


def _clean_htb_scrape(document: ParsedDocument) -> ParsedDocument:
    first_article_index = next(
        (
            index
            for index, block in enumerate(document.blocks)
            if block.kind is BlockKind.HEADING and block.level == 1
        ),
        None,
    )
    if first_article_index is None:
        return document

    article_blocks: list[ParsedBlock] = []
    for block in document.blocks[first_article_index:]:
        if article_blocks and _is_htb_article_boundary(block):
            break
        article_blocks.append(block)

    return _rebuild_document(document, tuple(article_blocks))


def _is_htb_article_boundary(block: ParsedBlock) -> bool:
    return (
        block.kind in _BOUNDARY_BLOCK_KINDS
        and _normalize_label(block.text) in _HTB_ARTICLE_BOUNDARIES
    )


def _clean_academy_obsidian(document: ParsedDocument) -> ParsedDocument:
    blocks: list[ParsedBlock] = []
    relationship_events: list[_RelationshipEvent] = []
    new_assets: list[ParsedAsset] = []
    sequence = 0
    changed = False

    for block in document.blocks:
        candidate_blocks = (block,)
        if block.kind is BlockKind.PARAGRAPH:
            candidate_blocks, metadata_events = _remove_note_metadata(block, sequence)
            changed = changed or candidate_blocks != (block,)
            relationship_events.extend(metadata_events)
            sequence += len(metadata_events)

        for candidate in candidate_blocks:
            rewritten, assets = _convert_obsidian_embeds(candidate)
            changed = changed or bool(assets)
            blocks.append(rewritten)
            new_assets.extend(assets)

    if not changed:
        return document

    return _rebuild_document(
        document,
        tuple(blocks),
        extra_relationships=relationship_events,
        extra_assets=new_assets,
    )


def _remove_note_metadata(
    block: ParsedBlock,
    starting_sequence: int,
) -> tuple[tuple[ParsedBlock, ...], tuple[_RelationshipEvent, ...]]:
    lines = block.text.splitlines()
    if not any(_NOTE_METADATA_RE.fullmatch(line.strip()) for line in lines):
        return (block,), ()

    kept_blocks: list[ParsedBlock] = []
    relationship_events: list[_RelationshipEvent] = []
    sequence = starting_sequence

    for offset, line in enumerate(lines):
        source_line = min(block.start_line + offset, block.end_line)
        match = _NOTE_METADATA_RE.fullmatch(line.strip())
        if match is None:
            if line.strip():
                kept_blocks.append(
                    _copy_block(
                        block,
                        text=line,
                        start_line=source_line,
                        end_line=source_line,
                    )
                )
            continue

        label = re.sub(r"\s+", " ", match.group("label")).casefold()
        if label == "tags":
            continue
        for target in _wiki_targets(match.group("value")):
            relationship_events.append(
                _RelationshipEvent(source_line, sequence, target)
            )
            sequence += 1

    return tuple(kept_blocks), tuple(relationship_events)


def _convert_obsidian_embeds(
    block: ParsedBlock,
) -> tuple[ParsedBlock, tuple[ParsedAsset, ...]]:
    assets: list[ParsedAsset] = []

    def replace_embed(match: re.Match[str]) -> str:
        target = match.group("target").strip()
        alias = (match.group("alias") or "").strip() or None
        source_line = block.start_line + block.text[: match.start()].count("\n")
        assets.append(
            ParsedAsset(
                target=target,
                start_line=source_line,
                end_line=source_line,
                alt_text=alias,
                metadata={"source": "obsidian_embed"},
            )
        )
        return alias or target

    rewritten_text = _OBSIDIAN_EMBED_RE.sub(replace_embed, block.text)
    if not assets:
        return block, ()

    embed_only = _OBSIDIAN_EMBED_RE.fullmatch(block.text.strip()) is not None
    kind = BlockKind.IMAGE if embed_only else block.kind
    metadata = _metadata_with_asset_targets(block, assets)
    return (
        _copy_block(block, kind=kind, text=rewritten_text, metadata=metadata),
        tuple(assets),
    )


def _clean_github_walkthrough(document: ParsedDocument) -> ParsedDocument:
    blocks: list[ParsedBlock] = []
    open_centered_wrappers = 0

    for block in document.blocks:
        if block.kind not in {
            BlockKind.HEADING,
            BlockKind.HTML,
            BlockKind.PARAGRAPH,
        }:
            blocks.append(block)
            continue
        parser = _parse_presentation_html(block.text)
        if parser.centered:
            open_centered_wrappers += parser.centered_balance
            rewritten = _unwrap_centered_block(
                block,
                parser.visible_text,
                tuple(parser.asset_labels),
            )
            if rewritten is not None:
                blocks.append(rewritten)
            continue

        if (
            open_centered_wrappers > 0
            and block.kind is BlockKind.HTML
            and _CLOSING_PRESENTATION_WRAPPER_RE.fullmatch(block.text)
        ):
            open_centered_wrappers -= 1
            continue

        blocks.append(block)

    return _rebuild_document(document, tuple(blocks))


def _parse_presentation_html(text: str) -> _PresentationHTMLParser:
    parser = _PresentationHTMLParser()
    parser.feed(text)
    parser.close()
    return parser


def _unwrap_centered_block(
    block: ParsedBlock,
    visible_text: str,
    asset_labels: tuple[str, ...],
) -> ParsedBlock | None:
    assets = _assets_named_by_block(block)
    if not visible_text and not assets:
        return None

    text = visible_text or " ".join(asset_labels) or " ".join(
        asset.target for asset in assets
    )
    kind = block.kind
    if block.kind is BlockKind.HTML:
        kind = BlockKind.IMAGE if assets and not visible_text else BlockKind.PARAGRAPH
    return _copy_block(block, kind=kind, text=text)


def _rebuild_document(
    document: ParsedDocument,
    blocks: tuple[ParsedBlock, ...],
    *,
    extra_relationships: Iterable[_RelationshipEvent] = (),
    extra_assets: Iterable[ParsedAsset] = (),
) -> ParsedDocument:
    relationships = _relationships_in_source_order(blocks, extra_relationships)
    assets = _assets_in_source_order(document, blocks, extra_assets)
    return ParsedDocument(
        source_id=document.source_id,
        path=document.path,
        blocks=blocks,
        assets=assets,
        relationships=relationships,
    )


def _relationships_in_source_order(
    blocks: tuple[ParsedBlock, ...],
    extras: Iterable[_RelationshipEvent],
) -> tuple[str, ...]:
    events = list(extras)
    sequence = max((event.sequence for event in events), default=-1) + 1
    for block in blocks:
        for target in _metadata_values(block, "urls", "url"):
            events.append(_RelationshipEvent(block.start_line, sequence, target))
            sequence += 1

    events.sort(key=lambda event: (event.line, event.sequence))
    return _unique_in_order(event.target for event in events)


def _assets_in_source_order(
    document: ParsedDocument,
    blocks: tuple[ParsedBlock, ...],
    extras: Iterable[ParsedAsset],
) -> tuple[ParsedAsset, ...]:
    retained = [
        asset
        for asset in document.assets
        if any(_block_represents_asset(block, asset) for block in blocks)
    ]
    ordered = [*retained, *extras]
    return tuple(
        asset
        for _, asset in sorted(
            enumerate(ordered),
            key=lambda item: (
                item[1].start_line,
                item[1].end_line,
                item[0],
            ),
        )
    )


def _block_represents_asset(block: ParsedBlock, asset: ParsedAsset) -> bool:
    return (
        block.start_line <= asset.end_line
        and asset.start_line <= block.end_line
        and asset.target in _metadata_values(block, "asset_targets", "asset_target")
    )


def _assets_named_by_block(block: ParsedBlock) -> tuple[ParsedAsset, ...]:
    targets = _metadata_values(block, "asset_targets", "asset_target")
    return tuple(
        ParsedAsset(
            target=target,
            start_line=block.start_line,
            end_line=block.end_line,
        )
        for target in targets
    )


def _metadata_values(
    block: ParsedBlock,
    plural_key: str,
    singular_key: str,
) -> tuple[str, ...]:
    serialized = block.metadata.get(plural_key)
    if serialized is not None:
        values = json.loads(serialized)
        if isinstance(values, list) and all(isinstance(value, str) for value in values):
            return tuple(values)
    singular = block.metadata.get(singular_key)
    return (singular,) if singular is not None else ()


def _metadata_with_asset_targets(
    block: ParsedBlock,
    assets: Iterable[ParsedAsset],
) -> dict[str, str]:
    metadata = dict(block.metadata)
    targets = (
        *_metadata_values(block, "asset_targets", "asset_target"),
        *(asset.target for asset in assets),
    )
    metadata["asset_targets"] = json.dumps(
        targets,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(targets) == 1:
        metadata["asset_target"] = targets[0]
    else:
        metadata.pop("asset_target", None)
    return metadata


def _copy_block(
    block: ParsedBlock,
    *,
    kind: BlockKind | None = None,
    text: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    metadata: dict[str, str] | None = None,
) -> ParsedBlock:
    return ParsedBlock(
        kind=kind or block.kind,
        text=block.text if text is None else text,
        start_line=block.start_line if start_line is None else start_line,
        end_line=block.end_line if end_line is None else end_line,
        level=block.level,
        language=block.language,
        metadata=dict(block.metadata) if metadata is None else metadata,
    )


def _wiki_targets(text: str) -> tuple[str, ...]:
    return tuple(
        target
        for match in _WIKI_LINK_RE.finditer(text)
        if (target := match.group("target").strip())
    )


def _has_center_alignment(attrs: list[tuple[str, str | None]]) -> bool:
    attributes = {name.casefold(): (value or "") for name, value in attrs}
    if attributes.get("align", "").strip().casefold() == "center":
        return True
    style = attributes.get("style", "")
    return any(
        name.strip().casefold() == "text-align"
        and value.strip().casefold() == "center"
        for declaration in style.split(";")
        if ":" in declaration
        for name, value in (declaration.split(":", maxsplit=1),)
    )


def _normalize_label(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _unique_in_order(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
