"""Deterministic and explainable classification of inventoried source documents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from sedna.knowledge.inventory import SourceCandidate
from sedna.knowledge.schema import (
    DocumentType,
    IngestionStatus,
    KnowledgeRole,
    SourceQuality,
)

_ATX_HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_SETEXT_HEADING_RE = re.compile(r"(?m)^([^\n]+)\n\s*(?:={4,}|-{4,})\s*$")
_FENCED_CODE_RE = re.compile(r"(?m)^\s{0,3}(?:```|~~~)")
_INDENTED_CODE_RE = re.compile(r"(?m)^(?: {4}|\t)\S")
_URL_RE = re.compile(r"https?://[^\s<>\])]+", re.IGNORECASE)
_HTB_FLAG_RE = re.compile(r"HTB\{[^}\r\n]+\}", re.IGNORECASE)
_FLAG_HEADING_RE = re.compile(
    r"(?ims)^\s{0,3}#{1,6}[^\n]*\bflag\b[^\n]*\n(?P<body>.{0,500})"
)
_HEX_32_RE = re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", re.IGNORECASE)
_USER_ROOT_FLAG_RE = re.compile(
    r"(?ims)^\s*(?:user|root)(?:\s+flag)?\s*:?\s*$\n(?P<body>.{0,160})"
)
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_ACTION_LANGUAGE_RE = re.compile(
    r"\b(?:we|i)\s+(?:ran|run|used|started|added|enumerated|scanned|inspected|"
    r"tested|tried|executed|uploaded|connected|requested|checked|decoded|"
    r"extracted|opened|visited|submitted)\b|"
    r"\blet(?:'s| us)\b|"
    r"\bafter\s+(?:running|adding|enumerating|scanning|checking|uploading|"
    r"connecting|requesting|decoding|extracting)\b",
    re.IGNORECASE,
)
_RESULT_LANGUAGE_RE = re.compile(
    r"\b(?:we|i)\s+(?:found|discovered|observed|identified|obtained|received|"
    r"saw|got|confirmed)\b|"
    r"\b(?:the|our)\s+(?:scan|command|request|exploit|test|enumeration)\s+"
    r"(?:showed|revealed|returned|found|failed|succeeded|confirmed)\b|"
    r"\baccording to (?:the )?(?:scan )?results\b|"
    r"\bresults?\s+(?:showed|revealed|indicated|returned|confirmed|led)\b",
    re.IGNORECASE,
)
_WALKTHROUGH_LANGUAGE_RE = re.compile(
    r"\b(?:walkthrough|write[ -]?up|step[ -]?by[ -]?step|full guide)\b",
    re.IGNORECASE,
)
_TECHNICAL_PDF_RE = re.compile(
    r"\b(?:cheat[ -]?sheet|reference|handbook|manual)\b", re.IGNORECASE
)
_NON_PROCEDURAL_HEADING_RE = re.compile(
    r"\b(?:flag|challenge overview|overview|objective|metadata|table of contents|"
    r"contents|difficulty|category)\b",
    re.IGNORECASE,
)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


class ClassificationResult(BaseModel):
    """Immutable disposition and routing decision for one source document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_type: DocumentType
    knowledge_role: KnowledgeRole
    quality: SourceQuality
    parser_profile: str = Field(min_length=1)
    ingestion_status: IngestionStatus
    reasons: tuple[str, ...] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class _DocumentSignals:
    headings: tuple[str, ...]
    substantive_heading_count: int
    code_block_count: int
    word_count: int
    table_line_count: int
    narrative_line_count: int
    has_flag: bool
    procedural: bool
    walkthrough_urls: tuple[str, ...]


def classify_document(candidate: SourceCandidate, text: str | None) -> ClassificationResult:
    """Classify a source using ordered structural and path-based rules.

    Reason codes intentionally expose the rule that won so a manifest or review
    report can explain every deterministic decision.
    """
    relative_path = PurePosixPath(candidate.relative_path).as_posix()
    normalized_path = relative_path.casefold()

    if candidate.suffix.casefold() == ".pdf":
        return _classify_pdf(candidate)

    if text is None or not text.strip():
        return _result(
            DocumentType.EXCLUDED,
            KnowledgeRole.REFERENCE,
            SourceQuality.UNUSABLE,
            "none",
            IngestionStatus.EXCLUDED,
            "empty",
        )

    signals = _collect_signals(text)

    # A real external walkthrough link is substance enough to distinguish a
    # stub from a generic flag dump, but the flag-only rule remains first for
    # documents that contain only official challenge metadata and a flag.
    if signals.has_flag and not signals.procedural and not signals.walkthrough_urls:
        return _result(
            DocumentType.EXCLUDED,
            KnowledgeRole.REFERENCE,
            SourceQuality.UNUSABLE,
            "none",
            IngestionStatus.EXCLUDED,
            "flag_only",
        )

    if (
        len(signals.walkthrough_urls) == 1
        and not signals.procedural
        and (signals.has_flag or signals.word_count < 100)
    ):
        return _result(
            DocumentType.EXTERNAL_STUB,
            KnowledgeRole.REFERENCE,
            SourceQuality.MINIMAL,
            "none",
            IngestionStatus.EXCLUDED,
            "external_walkthrough_only",
        )

    if _is_machine_path(normalized_path) and signals.procedural:
        quality = _walkthrough_quality(signals)
        return _result(
            DocumentType.MACHINE_WALKTHROUGH,
            KnowledgeRole.CASE_STUDY,
            quality,
            "github_walkthrough",
            IngestionStatus.ACCEPTED,
            "machine_path",
            "procedural_signals",
            f"quality_{quality.value}",
        )

    if _is_challenge_path(normalized_path) and signals.procedural:
        quality = _walkthrough_quality(signals)
        return _result(
            DocumentType.CHALLENGE_WALKTHROUGH,
            KnowledgeRole.CASE_STUDY,
            quality,
            "github_walkthrough",
            IngestionStatus.ACCEPTED,
            "challenge_path",
            "procedural_signals",
            f"quality_{quality.value}",
        )

    if _is_reference_family_path(normalized_path):
        parser_profile = (
            "htb_scrape"
            if normalized_path.startswith("01_information-gathering/")
            else "academy_obsidian"
        )
        if _is_table_dominant(signals):
            return _result(
                DocumentType.CHEATSHEET_REFERENCE,
                KnowledgeRole.REFERENCE,
                SourceQuality.PARTIAL,
                parser_profile,
                IngestionStatus.ACCEPTED,
                "reference_family_path",
                "table_dominant",
            )

        quality = _lesson_quality(signals)
        return _result(
            DocumentType.LESSON,
            KnowledgeRole.REFERENCE,
            quality,
            parser_profile,
            IngestionStatus.ACCEPTED,
            "reference_family_path",
            "narrative_dominant",
            f"quality_{quality.value}",
        )

    return _result(
        DocumentType.EXCLUDED,
        KnowledgeRole.REFERENCE,
        SourceQuality.MINIMAL,
        "none",
        IngestionStatus.QUARANTINED,
        "ambiguous",
        "no_deterministic_rule_matched",
    )


def _classify_pdf(candidate: SourceCandidate) -> ClassificationResult:
    filename = PurePosixPath(candidate.relative_path).name.replace("_", " ")
    if _TECHNICAL_PDF_RE.search(filename):
        return _result(
            DocumentType.CHEATSHEET_REFERENCE,
            KnowledgeRole.REFERENCE,
            SourceQuality.PARTIAL,
            "none",
            IngestionStatus.QUARANTINED,
            "technical_reference_pdf",
            "pdf_parser_unavailable",
        )
    return _result(
        DocumentType.EXCLUDED,
        KnowledgeRole.REFERENCE,
        SourceQuality.MINIMAL,
        "none",
        IngestionStatus.QUARANTINED,
        "ambiguous",
        "unsupported_pdf",
    )


def _collect_signals(text: str) -> _DocumentSignals:
    visible_text = _HTML_COMMENT_RE.sub("", text)
    headings = tuple(
        _clean_heading(heading)
        for heading in (
            *_ATX_HEADING_RE.findall(visible_text),
            *_SETEXT_HEADING_RE.findall(visible_text),
        )
    )
    substantive_headings = tuple(
        heading for heading in headings if not _NON_PROCEDURAL_HEADING_RE.search(heading)
    )
    code_block_count = len(_FENCED_CODE_RE.findall(visible_text)) // 2
    if not code_block_count and _INDENTED_CODE_RE.search(visible_text):
        code_block_count = 1

    action_language = bool(_ACTION_LANGUAGE_RE.search(visible_text))
    result_language = bool(_RESULT_LANGUAGE_RE.search(visible_text))
    procedural = (
        len(substantive_headings) >= 2 and code_block_count >= 1
    ) or (action_language and result_language)

    urls = tuple(url.rstrip(".,;:'\"") for url in _URL_RE.findall(visible_text))
    walkthrough_urls = tuple(
        url for url in urls if _url_is_external_walkthrough(visible_text, url)
    )
    lines = visible_text.splitlines()
    table_line_count = sum(bool(_TABLE_ROW_RE.match(line)) for line in lines)
    narrative_line_count = sum(_is_narrative_line(line) for line in lines)

    return _DocumentSignals(
        headings=headings,
        substantive_heading_count=len(substantive_headings),
        code_block_count=code_block_count,
        word_count=len(re.findall(r"\b[\w'-]+\b", visible_text)),
        table_line_count=table_line_count,
        narrative_line_count=narrative_line_count,
        has_flag=_contains_final_flag(text),
        procedural=procedural,
        walkthrough_urls=walkthrough_urls,
    )


def _contains_final_flag(text: str) -> bool:
    if _HTB_FLAG_RE.search(text):
        return True
    flag_sections = (*_FLAG_HEADING_RE.finditer(text), *_USER_ROOT_FLAG_RE.finditer(text))
    return any(_HEX_32_RE.search(match.group("body")) for match in flag_sections)


def _url_is_external_walkthrough(text: str, url: str) -> bool:
    lowered_url = url.casefold()
    if "app.hackthebox.com/challenges" in lowered_url:
        return False
    start = text.find(url)
    context = text[max(0, start - 100) : start + len(url) + 100]
    return bool(_WALKTHROUGH_LANGUAGE_RE.search(f"{context} {url}"))


def _clean_heading(heading: str) -> str:
    return re.sub(r"<[^>]+>", " ", heading).strip()


def _is_narrative_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or _TABLE_ROW_RE.match(stripped):
        return False
    if stripped.startswith(("#", "```", "~~~", "![", "Tags:", "Related to:", "See also:")):
        return False
    if re.fullmatch(r"[-=* _]{3,}", stripped):
        return False
    return len(re.findall(r"\b[\w'-]+\b", stripped)) >= 5


def _is_table_dominant(signals: _DocumentSignals) -> bool:
    return signals.table_line_count >= 3 and (
        signals.table_line_count >= signals.narrative_line_count
        or any("cheatsheet" in heading.casefold() for heading in signals.headings)
    )


def _walkthrough_quality(signals: _DocumentSignals) -> SourceQuality:
    if signals.substantive_heading_count >= 3 and signals.code_block_count >= 2:
        return SourceQuality.COMPLETE
    if signals.word_count >= 200 and signals.code_block_count >= 1:
        return SourceQuality.COMPLETE
    return SourceQuality.PARTIAL


def _lesson_quality(signals: _DocumentSignals) -> SourceQuality:
    if signals.word_count >= 100 and len(signals.headings) >= 2:
        return SourceQuality.COMPLETE
    return SourceQuality.PARTIAL


def _is_machine_path(path: str) -> bool:
    return path.startswith("write-ups/machines/")


def _is_challenge_path(path: str) -> bool:
    return path.startswith("write-ups/challanges/") or path.startswith("write-ups/challenges/")


def _is_reference_family_path(path: str) -> bool:
    return path.startswith("write-ups/academy/") or path.startswith("01_information-gathering/")


def _result(
    document_type: DocumentType,
    knowledge_role: KnowledgeRole,
    quality: SourceQuality,
    parser_profile: str,
    ingestion_status: IngestionStatus,
    *reasons: str,
) -> ClassificationResult:
    return ClassificationResult(
        document_type=document_type,
        knowledge_role=knowledge_role,
        quality=quality,
        parser_profile=parser_profile,
        ingestion_status=ingestion_status,
        reasons=reasons,
    )
