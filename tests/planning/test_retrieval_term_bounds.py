"""Retrieval terms must respect the Term contract (max_length=512).

Defect 10: ``_safe_texts`` bounds the NUMBER of terms but not the LENGTH of each
one. A long observed fact (here a 527-character evidence-slice description)
therefore passes through and ``CurrentSituation(terms=...)`` raises
``ValidationError: terms.N Value should have at most 512 items``, so
``plan_next`` crashes before it can ever propose a frontier.

This reproduced on a live engagement: settlement completed (606 interpretations)
and the very next plan_next call died inside build_retrieval_queries.
"""

from __future__ import annotations

from hashlib import sha256
from uuid import UUID, uuid4

from sedna.engagement import JournalRevision, ScopeReference
from sedna.planning import ObjectiveProgress, SituationProjection
from sedna.planning.models import ObservedFacet, ObservedFact
from sedna.planning.retrieval import build_retrieval_queries

# The schema bound the produced terms must satisfy.
MAX_TERM_LENGTH = 512


def _sha(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _situation_with_fact(text: str) -> SituationProjection:
    event_id = UUID("00000000-0000-0000-0000-000000000001")
    return SituationProjection(
        engagement_id=uuid4(),
        authoritative_journal_revision=JournalRevision(sequence=0, event_hash=_sha("head")),
        material_event_revision=0,
        state_digest=_sha("state"),
        objective_progress=ObjectiveProgress(),
        facts=(ObservedFact(event_ids=(event_id,), text=text),),
    )


def _scope_reference() -> ScopeReference:
    return ScopeReference(
        reference_id="scope-" + "1" * 32,
        kind="exact_target",
        value="10.0.0.1",
    )


def _long_fact() -> str:
    """A realistic over-length observation: an evidence-slice description.

    Mirrors the live failure, where the fact text was
    "the evidence slice conte...s present in the slice." and measured 527 chars.
    """
    body = (
        "the evidence slice contains a tool observation whose structured output "
        "records the service banner, the negotiated protocol version and the "
        "authentication mechanisms offered, together with the timestamps of each "
        "completed stage; the record is retained verbatim without truncation so "
        "that downstream consumers can replay the exact sequence of events and "
        "confirm, field by field, that every structured value described above, "
        "including the banner text, the protocol version, the authentication "
        "mechanisms and all recorded timestamps, is present in the slice."
    )
    assert len(body) > MAX_TERM_LENGTH, len(body)
    return body


def test_long_fact_does_not_break_retrieval_query_construction() -> None:
    """A fact longer than the Term bound must not crash query construction.

    Regression: this raised
    ``ValidationError: terms.N Value should have at most 512 items``
    and aborted plan_next.
    """
    queries = build_retrieval_queries(
        _situation_with_fact(_long_fact()),
        (_scope_reference(),),
    )

    # Construction must succeed; every produced term must satisfy the contract.
    for query in queries:
        for term in query.terms:
            assert len(term) <= MAX_TERM_LENGTH, (len(term), term[:80])
        for term in query.situation.terms:
            assert len(term) <= MAX_TERM_LENGTH, (len(term), term[:80])
        for fact in query.situation.facts:
            assert len(fact.value) <= 2048, (len(fact.value), fact.value[:80])


def test_cumulative_situation_text_stays_within_the_contract_bound() -> None:
    """The whole CurrentSituation must fit the cumulative text bound.

    Regression (defect 10b): bounding each term to 512 and the count to 32 is not
    sufficient — 32 terms of 512 chars, or 32 facets of 2048, overflow
    ``_MAX_QUERY_TEXT`` (8192) and ``CurrentSituation`` raises
    "current situation text exceeds the cumulative bound", aborting plan_next.
    A realistic settled journal (3941 observations) hits this immediately.
    """
    from sedna.knowledge.retrieval.models import (
        _MAX_QUERY_TEXT,
        _situation_text_size,
    )

    event_id = UUID("00000000-0000-0000-0000-000000000001")
    long_text = "observation " + "x" * 500
    situation = SituationProjection(
        engagement_id=uuid4(),
        authoritative_journal_revision=JournalRevision(sequence=0, event_hash=_sha("head")),
        material_event_revision=0,
        state_digest=_sha("state"),
        objective_progress=ObjectiveProgress(),
        facts=tuple(
            ObservedFact(event_ids=(event_id,), text=f"{long_text} number {index}")
            for index in range(32)
        ),
        facets=tuple(
            ObservedFacet(event_ids=(event_id,), key=f"key{index}", value="y" * 600)
            for index in range(32)
        ),
    )

    queries = build_retrieval_queries(situation, (_scope_reference(),))

    assert queries, "expected at least one query"
    for query in queries:
        size = _situation_text_size(query.situation)
        assert size <= _MAX_QUERY_TEXT, (
            f"cumulative situation text {size} exceeds the {_MAX_QUERY_TEXT} bound"
        )


def test_cumulative_bound_does_not_drop_short_evidence() -> None:
    """Budgeting must keep normal, short observations intact."""
    from sedna.knowledge.retrieval.models import _MAX_QUERY_TEXT, _situation_text_size

    queries = build_retrieval_queries(
        _situation_with_fact("HTTP service exposed"),
        (_scope_reference(),),
    )

    assert queries, "expected at least one query"
    assert "http service exposed" in queries[0].terms
    assert _situation_text_size(queries[0].situation) <= _MAX_QUERY_TEXT


def test_short_fact_is_preserved_verbatim() -> None:
    """Bounding length must not alter facts that already fit."""
    queries = build_retrieval_queries(
        _situation_with_fact("HTTP service exposed"),
        (_scope_reference(),),
    )

    assert queries, "expected at least one query"
    assert "http service exposed" in queries[0].terms
