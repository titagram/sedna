from __future__ import annotations

import pytest
from markdown_it import MarkdownIt
from pydantic import ValidationError

from sedna.engagement.reporting.markdown import render_operational_report
from sedna.engagement.reporting.models import (
    ReportCapturedOutput,
    ReportObservation,
    ReportOverflowSummary,
    ReportSource,
)
from tests.engagement.test_report_models import _report


def test_renderer_keeps_untrusted_markdown_html_and_fences_inert() -> None:
    private_report = _report()
    hostile = private_report.model_copy(
        update={
            "display_name": "# injected heading <script>alert(1)</script>",
            "objective": "[steal](file:///etc/passwd) ````` break",
            "tool_executions": (
                private_report.tool_executions[0].model_copy(
                    update={
                        "output": ReportCapturedOutput(
                            disposition="inline",
                            inline_text="```\n# active\n<script>x</script>\n````",
                        )
                    }
                ),
            ),
        }
    )

    rendered = render_operational_report(hostile)
    tokens = MarkdownIt("commonmark").parse(rendered)

    assert "&lt;script&gt;alert\\(1\\)&lt;/script&gt;" in rendered
    assert not any(token.type in {"html_block", "html_inline"} for token in tokens)
    assert "file:///etc/passwd" not in [
        token.attrGet("href") for token in tokens if token.type == "link_open"
    ]
    headings = [
        tokens[index + 1].content
        for index, token in enumerate(tokens)
        if token.type == "heading_open"
    ]
    assert headings == [
        "Sedna operational report",
        "Scope and objective",
        "Session timeline",
        "Observations and hypotheses",
        "Decisions and frontier changes",
        "Commands, outputs, and failed attempts",
        "Credentials and flags",
        "Evidence and sources",
        "Completion and unresolved issues",
    ]
    assert "# active" in rendered
    assert render_operational_report(hostile) == rendered


def test_renderer_keeps_backticks_in_evidence_paths_inside_inert_code() -> None:
    private_report = _report()
    execution = private_report.tool_executions[0]
    evidence = execution.output.evidence
    assert evidence is not None
    hostile_path = "evidence/` [steal](https:evil.example) `.txt"
    hostile = private_report.model_copy(
        update={
            "tool_executions": (
                execution.model_copy(
                    update={
                        "output": execution.output.model_copy(
                            update={
                                "evidence": evidence.model_copy(
                                    update={"relative_path": hostile_path}
                                )
                            }
                        )
                    }
                ),
            )
        }
    )

    tokens = MarkdownIt("commonmark").parse(render_operational_report(hostile))

    assert not any(token.type == "link_open" for token in tokens)
    assert hostile_path in [
        child.content
        for token in tokens
        for child in (token.children or ())
        if child.type == "code_inline"
    ]


def test_renderer_rejects_a_model_constructed_control_character_path() -> None:
    private_report = _report()
    execution = private_report.tool_executions[0]
    evidence = execution.output.evidence
    assert evidence is not None
    forged = private_report.model_copy(
        update={
            "tool_executions": (
                execution.model_copy(
                    update={
                        "output": execution.output.model_copy(
                            update={
                                "evidence": evidence.model_copy(
                                    update={"relative_path": "evidence/line\nbreak.txt"}
                                )
                            }
                        )
                    }
                ),
            )
        }
    )

    with pytest.raises(ValidationError, match="confined relative path"):
        render_operational_report(forged)


def test_renderer_preserves_timeline_source_queries_and_overflow_audit_data() -> None:
    private_report = _report()
    event_id = private_report.secrets[0].event_ids[0]
    complete = private_report.model_copy(
        update={
            "timeline": (
                ReportObservation(
                    summary="timeline sentinel",
                    confidence=1.0,
                    event_ids=(event_id,),
                ),
            ),
            "sources": (
                ReportSource(
                    locator="source sentinel",
                    query="query sentinel",
                    assessment="assessment sentinel",
                    event_ids=(event_id,),
                ),
            ),
            "overflow": (
                ReportOverflowSummary(
                    section="timeline",
                    omitted_count=3,
                    first_omitted_sequence=7,
                    last_omitted_sequence=11,
                    omitted_event_digest="c" * 64,
                ),
            ),
        }
    )

    rendered = render_operational_report(complete)

    assert "timeline sentinel" in rendered
    assert "source sentinel" in rendered
    assert "query sentinel" in rendered
    assert "assessment sentinel" in rendered
    assert "timeline: 3 events omitted (sequences 7–11)" in rendered
    assert "c" * 64 in rendered
