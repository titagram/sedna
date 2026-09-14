"""Canonical event hashing must be byte-identical everywhere.

Defect 9: the repository computes an event's hash with `_canonical_json`
(ensure_ascii=False) while the reducer revalidates it with an inline
json.dumps (ensure_ascii=True by default). For any event whose canonical form
contains a non-ASCII character the two digests differ, so `validate_event_chain`
raises "event hash does not match its canonical envelope" and every append to
that engagement is refused.

Reproduced on a live engagement journal: settlement failed on 12 consecutive
batches with `invalid_extractor_output` because the extracted text contained
non-ASCII characters, then succeeded on batches whose text was pure ASCII — a
non-deterministic stall driven purely by the payload's character set.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from sedna.engagement.events import JournalEvent
from sedna.engagement.reducer import _canonical_event_hash, validate_event_chain
from sedna.engagement.repository import _canonical_json

_LIVE = Path(
    "/home/titagram/.hermes/knowledge/sedna/engagements/"
    "adfbdd61-2f2d-4d19-b5b3-cbeb7c4429f0/events.jsonl"
)


def _real_event() -> JournalEvent:
    """Read one real, valid event from an existing engagement journal."""
    if not _LIVE.exists():
        pytest.skip("reference engagement journal not present on this host")
    for line in _LIVE.read_text().splitlines():
        if not line.strip():
            continue
        return JournalEvent.model_validate(json.loads(line))
    pytest.skip("reference engagement journal is empty")


@pytest.mark.parametrize(
    "actor_id",
    ["ascii-only", "è-accented", "\u2192-arrow", "\u65e5\u672c\u8a9e", "\U0001f6a9-emoji"],
)
def test_event_hash_creation_and_validation_agree(actor_id: str) -> None:
    """The hash written at creation must equal the hash recomputed at validation.

    Regression: any event whose canonical form contains a non-ASCII character
    produced two different digests, so appending it raised
    EngagementReplayError("event hash does not match its canonical envelope").
    """
    event = _real_event().model_copy(update={"actor_id": actor_id})

    as_json = event.model_dump(mode="json", exclude={"event_hash"})
    creating = sha256(_canonical_json(as_json)).hexdigest()
    validating = _canonical_event_hash(event)

    assert creating == validating, (
        f"canonical hashing diverges for {actor_id!r}: creation={creating} validation={validating}"
    )


def test_validate_event_chain_accepts_a_non_ascii_event() -> None:
    """A correctly hashed non-ASCII event must pass chain validation."""
    event = _real_event().model_copy(update={"actor_id": "è-verificato"})
    as_json = event.model_dump(mode="json", exclude={"event_hash"})
    hashed = event.model_copy(update={"event_hash": sha256(_canonical_json(as_json)).hexdigest()})

    validate_event_chain(hashed.engagement_id, [hashed])
