"""Regression tests for the bounded, planner-only situation view."""

from __future__ import annotations

from uuid import uuid4

from sedna.planning.models import EvidenceInterpretationState, InterpretationSubject
from sedna.planning.situation import planner_situation_view
from tests.planning.test_llm import _situation


def test_planner_situation_view_caps_interpretations_without_mutating_canonical_state() -> None:
    canonical = _situation().model_copy(
        update={
            "interpretations": tuple(
                EvidenceInterpretationState(
                    event_ids=(uuid4(),),
                    subject=InterpretationSubject(
                        attachment_event_id=uuid4(),
                        terminal_tool_event_id=None,
                        evidence_id=f"evidence-sha256-{index:064x}",
                    ),
                    status="completed",
                )
                for index in range(128)
            )
        }
    )

    planner_view = planner_situation_view(canonical)

    assert len(canonical.interpretations) == 128
    assert len(planner_view.interpretations) == 16
    # Completed audit records use the deterministic tail of canonical order.
    assert planner_view.interpretations == canonical.interpretations[-16:]
    assert planner_view.state_digest == canonical.state_digest
    assert planner_view.authoritative_journal_revision == canonical.authoritative_journal_revision
