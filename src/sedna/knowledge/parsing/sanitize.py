"""Remove final flags from searchable copies of parsed source text."""

from __future__ import annotations

import re

EXCLUDED_FLAG = "<EXCLUDED_FLAG>"

_HTB_FLAG_RE = re.compile(r"\bHTB\{[^}\r\n]*\}", re.IGNORECASE)
_STANDALONE_32_HEX_RE = re.compile(
    r"(?<![A-Za-z0-9])[0-9A-Fa-f]{32}(?![A-Za-z0-9])"
)
_HEX_FLAG_HEADINGS = frozenset(
    {
        "final flag",
        "flag",
        "root",
        "root flag",
        "user",
        "user flag",
    }
)


def sanitize_searchable_text(text: str, heading_path: tuple[str, ...]) -> str:
    """Return a searchable copy with deterministic final-flag redaction.

    HTB flag tokens are excluded in every context. Bare 32-character hexadecimal
    values are ambiguous, so they are removed only under an explicit flag or a
    conventional machine ``User``/``Root`` heading. The input string and parsed
    source document remain untouched.
    """
    return _sanitize_searchable_text(text, heading_path, frozenset())


def _contextual_hex_flag_values(
    text: str,
    heading_path: tuple[str, ...],
) -> frozenset[str]:
    if not _is_hex_flag_context(heading_path):
        return frozenset()
    return frozenset(match.group().casefold() for match in _STANDALONE_32_HEX_RE.finditer(text))


def _sanitize_searchable_text(
    text: str,
    heading_path: tuple[str, ...],
    known_hex_flags: frozenset[str],
) -> str:
    sanitized = _HTB_FLAG_RE.sub(EXCLUDED_FLAG, text)
    if _is_hex_flag_context(heading_path):
        sanitized = _STANDALONE_32_HEX_RE.sub(EXCLUDED_FLAG, sanitized)
    elif known_hex_flags:
        sanitized = _STANDALONE_32_HEX_RE.sub(
            lambda match: (
                EXCLUDED_FLAG
                if match.group().casefold() in known_hex_flags
                else match.group()
            ),
            sanitized,
        )
    return sanitized


def _is_hex_flag_context(heading_path: tuple[str, ...]) -> bool:
    return any(_normalize_heading(part) in _HEX_FLAG_HEADINGS for part in heading_path)


def _normalize_heading(heading: str) -> str:
    return re.sub(r"[\W_]+", " ", heading.casefold()).strip()
