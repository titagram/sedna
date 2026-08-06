"""Loss-minimized CommonMark parsing with source-line provenance."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from html.parser import HTMLParser

from markdown_it import MarkdownIt
from markdown_it.token import Token

from sedna.knowledge.parsing.models import (
    BlockKind,
    ParsedAsset,
    ParsedBlock,
    ParsedDocument,
)


@dataclass(frozen=True, slots=True)
class _InlinePayload:
    text: str
    links: tuple[str, ...] = ()
    assets: tuple[ParsedAsset, ...] = ()


@dataclass(frozen=True, slots=True)
class _BlockPayload:
    block: ParsedBlock
    links: tuple[str, ...] = ()
    assets: tuple[ParsedAsset, ...] = ()


class _ImageHTMLParser(HTMLParser):
    """Collect image attributes without interpreting the surrounding HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "img":
            return
        attributes = {name.casefold(): value or "" for name, value in attrs}
        if attributes.get("src"):
            self.images.append(attributes)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def parse_markdown(source_id: str, path: str, markdown: str) -> ParsedDocument:
    """Parse Markdown into ordered structural blocks with exact line spans.

    Markdown-it token maps use zero-based, half-open line ranges. Every emitted
    block and asset converts those maps to one-based, inclusive provenance.
    Cleanup is deliberately absent here: parser profiles own that later step.
    """
    tokens = MarkdownIt("commonmark").enable("table").parse(markdown)
    parsed_blocks: list[_BlockPayload] = []
    index = 0

    while index < len(tokens):
        token = tokens[index]
        if token.level != 0:
            index += 1
            continue

        if token.type == "heading_open":
            close_index = _matching_close(tokens, index)
            parsed_blocks.append(_heading_block(token, tokens[index + 1 : close_index]))
            index = close_index + 1
            continue

        if token.type == "paragraph_open":
            close_index = _matching_close(tokens, index)
            parsed_blocks.append(_paragraph_block(token, tokens[index + 1 : close_index]))
            index = close_index + 1
            continue

        if token.type in {"fence", "code_block"}:
            parsed_blocks.append(_code_block(token))
            index += 1
            continue

        if token.type == "table_open":
            close_index = _matching_close(tokens, index)
            parsed_blocks.append(_table_block(token, tokens[index + 1 : close_index]))
            index = close_index + 1
            continue

        if token.type in {"bullet_list_open", "ordered_list_open"}:
            close_index = _matching_close(tokens, index)
            parsed_blocks.extend(_list_blocks(tokens, index, close_index))
            index = close_index + 1
            continue

        if token.type == "blockquote_open":
            close_index = _matching_close(tokens, index)
            parsed_blocks.append(
                _container_block(
                    BlockKind.BLOCKQUOTE,
                    token,
                    tokens[index + 1 : close_index],
                )
            )
            index = close_index + 1
            continue

        if token.type == "html_block":
            parsed_blocks.append(_html_block(token))
            index += 1
            continue

        if token.type == "hr":
            start_line, end_line = _source_span(token)
            parsed_blocks.append(
                _BlockPayload(
                    ParsedBlock(
                        kind=BlockKind.THEMATIC_BREAK,
                        text=token.markup,
                        start_line=start_line,
                        end_line=end_line,
                    )
                )
            )
            index += 1
            continue

        index += 1

    relationships = _unique_in_order(
        link for parsed in parsed_blocks for link in parsed.links
    )
    return ParsedDocument(
        source_id=source_id,
        path=path,
        blocks=tuple(parsed.block for parsed in parsed_blocks),
        assets=tuple(asset for parsed in parsed_blocks for asset in parsed.assets),
        relationships=relationships,
    )


def _heading_block(open_token: Token, inner_tokens: list[Token]) -> _BlockPayload:
    payload = _tokens_payload(inner_tokens)
    level = int(open_token.tag.removeprefix("h"))
    return _make_block(BlockKind.HEADING, open_token, payload, level=level)


def _paragraph_block(open_token: Token, inner_tokens: list[Token]) -> _BlockPayload:
    payload = _tokens_payload(inner_tokens)
    children = tuple(
        child
        for token in inner_tokens
        if token.type == "inline"
        for child in (token.children or ())
    )
    image_only = bool(payload.assets) and all(
        child.type == "image"
        or (child.type == "text" and not child.content.strip())
        or child.type in {"softbreak", "hardbreak"}
        for child in children
    )
    kind = BlockKind.IMAGE if image_only else BlockKind.PARAGRAPH
    return _make_block(kind, open_token, payload)


def _code_block(token: Token) -> _BlockPayload:
    start_line, end_line = _source_span(token)
    info = token.info.strip()
    language = info.split(maxsplit=1)[0] if info else None
    metadata: dict[str, str] = {}
    if info:
        metadata["info"] = info
    if token.type == "fence" and token.markup:
        metadata["fence"] = token.markup
    return _BlockPayload(
        ParsedBlock(
            kind=BlockKind.CODE,
            text=token.content.removesuffix("\n"),
            start_line=start_line,
            end_line=end_line,
            language=language,
            metadata=metadata,
        )
    )


def _table_block(open_token: Token, inner_tokens: list[Token]) -> _BlockPayload:
    rows: list[str] = []
    links: list[str] = []
    assets: list[ParsedAsset] = []
    current_cells: list[str] | None = None

    for token in inner_tokens:
        if token.type == "tr_open":
            current_cells = []
        elif token.type == "tr_close" and current_cells is not None:
            rows.append(" | ".join(current_cells))
            current_cells = None
        elif token.type == "inline" and current_cells is not None:
            payload = _inline_payload(token.children or (), _source_span(token))
            current_cells.append(payload.text)
            links.extend(payload.links)
            assets.extend(payload.assets)

    payload = _InlinePayload("\n".join(rows), tuple(links), tuple(assets))
    return _make_block(BlockKind.TABLE, open_token, payload)


def _list_blocks(tokens: list[Token], start: int, end: int) -> list[_BlockPayload]:
    blocks: list[_BlockPayload] = []
    list_kinds: list[BlockKind] = []
    index = start

    while index <= end:
        token = tokens[index]
        if token.type == "bullet_list_open":
            list_kinds.append(BlockKind.UNORDERED_LIST_ITEM)
        elif token.type == "ordered_list_open":
            list_kinds.append(BlockKind.ORDERED_LIST_ITEM)
        elif token.type in {"bullet_list_close", "ordered_list_close"}:
            list_kinds.pop()
        elif token.type == "list_item_open":
            close_index = _matching_close(tokens, index)
            payload = _direct_list_item_payload(tokens, index + 1, close_index)
            blocks.append(_make_block(list_kinds[-1], token, payload))
        index += 1

    return blocks


def _direct_list_item_payload(tokens: list[Token], start: int, end: int) -> _InlinePayload:
    relevant: list[Token] = []
    nested_item_depth = 0
    for token in tokens[start:end]:
        if token.type == "list_item_open":
            nested_item_depth += 1
            continue
        if token.type == "list_item_close":
            nested_item_depth -= 1
            continue
        if nested_item_depth == 0:
            relevant.append(token)

    return _tokens_payload(relevant)


def _container_block(
    kind: BlockKind,
    open_token: Token,
    inner_tokens: list[Token],
) -> _BlockPayload:
    return _make_block(kind, open_token, _tokens_payload(inner_tokens))


def _html_block(token: Token) -> _BlockPayload:
    span = _source_span(token)
    assets = _html_assets(token.content, span)
    payload = _InlinePayload(token.content.removesuffix("\n"), assets=assets)
    return _make_block(BlockKind.HTML, token, payload)


def _make_block(
    kind: BlockKind,
    source_token: Token,
    payload: _InlinePayload,
    *,
    level: int | None = None,
) -> _BlockPayload:
    start_line, end_line = _source_span(source_token)
    metadata = _target_metadata(payload.links, payload.assets)
    return _BlockPayload(
        ParsedBlock(
            kind=kind,
            text=payload.text,
            start_line=start_line,
            end_line=end_line,
            level=level,
            metadata=metadata,
        ),
        links=payload.links,
        assets=payload.assets,
    )


def _tokens_payload(tokens: list[Token]) -> _InlinePayload:
    text_parts: list[str] = []
    links: list[str] = []
    assets: list[ParsedAsset] = []
    for token in tokens:
        if token.type == "inline":
            payload = _inline_payload(token.children or (), _source_span(token))
        elif token.type in {"fence", "code_block"}:
            payload = _InlinePayload(token.content.removesuffix("\n"))
        elif token.type == "html_block":
            token_span = _source_span(token)
            payload = _InlinePayload(
                token.content.removesuffix("\n"),
                assets=_html_assets(token.content, token_span),
            )
        else:
            continue
        if payload.text:
            text_parts.append(payload.text)
        links.extend(payload.links)
        assets.extend(payload.assets)
    return _InlinePayload("\n".join(text_parts), tuple(links), tuple(assets))


def _inline_payload(children: list[Token], span: tuple[int, int]) -> _InlinePayload:
    text_parts: list[str] = []
    links: list[str] = []
    assets: list[ParsedAsset] = []

    for child in children:
        if child.type in {"text", "code_inline"}:
            text_parts.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            text_parts.append("\n")
        elif child.type == "link_open":
            href = child.attrGet("href")
            if href:
                links.append(href)
        elif child.type == "image":
            alt_text = _inline_visible_text(child.children or ()) or child.content
            target = child.attrGet("src")
            if target:
                assets.append(
                    ParsedAsset(
                        target=target,
                        alt_text=alt_text or None,
                        title=child.attrGet("title"),
                        start_line=span[0],
                        end_line=span[1],
                        metadata={"source": "markdown_image"},
                    )
                )
            text_parts.append(alt_text)
        elif child.type == "html_inline":
            html_assets = _html_assets(child.content, span)
            assets.extend(html_assets)
            if html_assets:
                text_parts.extend(asset.alt_text or "" for asset in html_assets)
            else:
                text_parts.append(child.content)

    return _InlinePayload("".join(text_parts), tuple(links), tuple(assets))


def _inline_visible_text(children: list[Token]) -> str:
    return "".join(
        "\n" if child.type in {"softbreak", "hardbreak"} else child.content
        for child in children
        if child.type in {"text", "code_inline", "softbreak", "hardbreak"}
    )


def _html_assets(html: str, span: tuple[int, int]) -> tuple[ParsedAsset, ...]:
    parser = _ImageHTMLParser()
    parser.feed(html)
    parser.close()
    return tuple(
        ParsedAsset(
            target=attributes["src"],
            alt_text=attributes.get("alt") or None,
            title=attributes.get("title") or None,
            start_line=span[0],
            end_line=span[1],
            metadata={"source": "html_image"},
        )
        for attributes in parser.images
    )


def _target_metadata(
    links: tuple[str, ...],
    assets: tuple[ParsedAsset, ...],
) -> dict[str, str]:
    metadata: dict[str, str] = {}
    if links:
        metadata["urls"] = _compact_json(links)
        if len(links) == 1:
            metadata["url"] = links[0]
    targets = tuple(asset.target for asset in assets)
    if targets:
        metadata["asset_targets"] = _compact_json(targets)
        if len(targets) == 1:
            metadata["asset_target"] = targets[0]
    return metadata


def _compact_json(values: tuple[str, ...]) -> str:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _source_span(token: Token) -> tuple[int, int]:
    if token.map is None:
        raise ValueError(f"Markdown token {token.type!r} has no source map")
    return token.map[0] + 1, token.map[1]


def _matching_close(tokens: list[Token], open_index: int) -> int:
    open_token = tokens[open_index]
    depth = 1
    for index in range(open_index + 1, len(tokens)):
        token = tokens[index]
        if token.type == open_token.type:
            depth += 1
        elif token.type == open_token.type.replace("_open", "_close"):
            depth -= 1
            if depth == 0:
                return index
    raise ValueError(f"unclosed Markdown token {open_token.type!r}")


def _unique_in_order(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
