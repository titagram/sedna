from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid5

import pytest
from pydantic import ValidationError

from sedna.engagement import CaptureLimitation, EvidenceId, JournalRevision
from sedna.engagement.reporting.models import (
    REPORT_RENDERER_VERSION,
    REPORT_SCHEMA_VERSION,
    OperationalReport,
    ReportCapturedOutput,
    ReportCompletion,
    ReportEvidenceRef,
    ReportObservation,
    ReportRef,
    ReportSecret,
    ReportSession,
    ReportToolExecution,
)

ENGAGEMENT_ID = UUID("11111111-1111-4111-8111-111111111111")
EVENT_ID = UUID("22222222-2222-4222-8222-222222222222")
EVIDENCE_ID: EvidenceId = "evidence-sha256-" + "3" * 64


def _report() -> OperationalReport:
    evidence = ReportEvidenceRef(
        attachment_event_id=EVENT_ID,
        event_sequence=21,
        evidence_id=EVIDENCE_ID,
        relative_path="evidence/nmap-event-21.txt",
        sha256="a" * 64,
        media_type="text/plain",
        representation="host_text",
        capture_limitations=(CaptureLimitation.PROVIDER_OR_HOST_SECRET_REDACTED,),
        size_bytes=42,
    )
    return OperationalReport(
        schema_version=REPORT_SCHEMA_VERSION,
        report_id=uuid5(ENGAGEMENT_ID, "report:1"),
        report_revision=1,
        engagement_id=ENGAGEMENT_ID,
        display_name="HTB-Orion",
        journal_revision=JournalRevision(sequence=41, event_hash="b" * 64),
        generated_at=datetime(2026, 8, 11, 14, 30, tzinfo=UTC),
        lifecycle_status="closed_unverified",
        objective="Obtain the user and root flags",
        scope=("exact_target:10.10.11.42",),
        tool_executions=(
            ReportToolExecution(
                call_id="call-21",
                tool_name="shell",
                suggested_commands=("nmap -sV <TARGET>",),
                executed_command="nmap -sV 10.10.11.42",
                outcome="progress",
                output=ReportCapturedOutput(disposition="evidence", evidence=evidence),
                event_ids=(EVENT_ID,),
            ),
        ),
        secrets=(
            ReportSecret(
                kind="flag",
                label="user flag",
                value="HTB{private-proof}",
                event_ids=(EVENT_ID,),
            ),
        ),
        completion=ReportCompletion(
            objective_satisfied=True,
            final_access=("user shell",),
            unresolved_issues=(),
        ),
    )


def test_private_report_accepts_proof_values_and_round_trips_strictly() -> None:
    report = _report()
    encoded = report.model_dump_json()

    assert "HTB{private-proof}" in encoded
    assert OperationalReport.model_validate_json(encoded) == report
    assert report.schema_version == "1.0.0"
    evidence = report.tool_executions[0].output.evidence
    assert evidence is not None
    assert evidence.attachment_event_id == EVENT_ID
    assert evidence.event_sequence == 21
    assert evidence.representation == "host_text"
    assert evidence.capture_limitations == (CaptureLimitation.PROVIDER_OR_HOST_SECRET_REDACTED,)


def test_report_rejects_unknown_fields_invalid_identity_and_unbound_output() -> None:
    payload = _report().model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        OperationalReport.model_validate(payload)

    with pytest.raises(ValidationError, match="report_id"):
        OperationalReport.model_validate(
            _report().model_copy(update={"report_id": UUID(int=1)}).model_dump(mode="python")
        )

    with pytest.raises(ValidationError):
        ReportCapturedOutput(disposition="inline", inline_text=None, evidence=None)


def test_report_models_cover_absent_outputs_and_derived_references() -> None:
    absent = ReportCapturedOutput(disposition="absent", absence_reason="timed_out")
    assert absent.inline_text is None
    session = ReportSession(
        session_id="session-1",
        started_at=datetime(2026, 8, 11, 14, 0, tzinfo=UTC),
        ended_at=datetime(2026, 8, 11, 14, 30, tzinfo=UTC),
        event_ids=(EVENT_ID,),
    )
    observation = ReportObservation(summary="bounded fact", confidence=0.8, event_ids=(EVENT_ID,))
    assert session.ended_at >= session.started_at
    assert observation.confidence == 0.8
    ref = ReportRef(
        report_id=uuid5(ENGAGEMENT_ID, "report:1"),
        report_revision=1,
        json_relative_path=f"engagements/{ENGAGEMENT_ID}/reports/report-v1.json",
        json_sha256="a" * 64,
        markdown_relative_path=f"engagements/{ENGAGEMENT_ID}/reports/report-v1.md",
        markdown_sha256="b" * 64,
        renderer_version=REPORT_RENDERER_VERSION,
        journal_revision=JournalRevision(sequence=41, event_hash="b" * 64),
    )
    assert ref.renderer_version == "1"


def test_report_revision_accepts_the_last_slot_and_rejects_the_next() -> None:
    report = _report().model_copy(
        update={
            "report_id": uuid5(ENGAGEMENT_ID, "report:1024"),
            "report_revision": 1024,
        }
    )

    assert OperationalReport.model_validate(report.model_dump()).report_revision == 1024
    with pytest.raises(ValidationError):
        OperationalReport.model_validate(
            report.model_copy(
                update={
                    "report_id": uuid5(ENGAGEMENT_ID, "report:1025"),
                    "report_revision": 1025,
                }
            ).model_dump()
        )


def test_report_models_reject_duplicate_event_and_evidence_ids() -> None:
    with pytest.raises(ValidationError, match="event_ids"):
        ReportObservation(
            summary="duplicated event",
            confidence=0.5,
            event_ids=(EVENT_ID, EVENT_ID),
        )

    with pytest.raises(ValidationError, match="evidence_ids"):
        ReportObservation(
            summary="duplicated evidence",
            confidence=0.5,
            event_ids=(EVENT_ID,),
            evidence_ids=(EVIDENCE_ID, EVIDENCE_ID),
        )
