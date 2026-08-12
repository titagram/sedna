from __future__ import annotations

import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from sedna.engagement import (
    CaptureLimitation,
    SanitizedHostValue,
    normalize_host_payload,
    sanitize_host_arguments,
)
from sedna.engagement.evidence import (
    DEFAULT_EVIDENCE_QUOTA,
    EvidenceCaptureError,
    EvidenceQuota,
    EvidenceStore,
    OrphanEvidencePage,
)
from sedna.engagement.logbook import (
    LogbookProjectionConflict,
    rebuild_session_logbooks,
    render_session_logbook,
    repository_evidence_reader,
)
from sedna.engagement.repository import EngagementJournalRepository

LOGBOOK_FIXED_TIME = datetime(2026, 8, 11, 12, 30, tzinfo=UTC)


def _engagement_path(root: Path, engagement_id) -> Path:
    return root / "engagements" / str(engagement_id)


def _create_engagement(root: Path, manifest, lane, initial_drafts, fixed_clock) -> None:
    with EngagementJournalRepository(root, clock=fixed_clock) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))


def _all_engagement_bytes(root: Path, engagement_id) -> bytes:
    engagement = _engagement_path(root, engagement_id)
    chunks: list[bytes] = []
    for path in sorted(engagement.rglob("*")):
        if path.is_file():
            chunks.append(path.read_bytes())
    return b"\n".join(chunks)


class _RecordedSession(NamedTuple):
    repository: EngagementJournalRepository
    engagement_id: object
    events: tuple[Any, ...]


@pytest.fixture
def recorded_session_snapshot(
    initial_drafts, tool_started, tool_completed, evidence_attached_draft
):
    def factory(root: Path, manifest, lane, hostile: str) -> _RecordedSession:
        """Create an engagement with one completed tool call carrying hostile output."""
        repository = EngagementJournalRepository(root, clock=lambda: LOGBOOK_FIXED_TIME)
        repository.create(manifest, initial_drafts(manifest, lane))
        normalized = normalize_host_payload(hostile)
        assert isinstance(normalized, SanitizedHostValue)
        assert normalized.canonical_bytes is not None
        reference = repository.write_evidence(
            manifest.engagement_id,
            normalized.canonical_bytes,
            media_type="text/plain",
            representation=normalized.representation,
        )
        repository.append_batch(
            manifest.engagement_id,
            (
                tool_started(lane, call_id="call-1"),
                evidence_attached_draft(lane, reference),
                tool_completed(lane, call_id="call-1"),
            ),
        )
        events = repository.load_events(manifest.engagement_id)
        return _RecordedSession(repository, manifest.engagement_id, events)

    return factory


class _CapturedArguments(NamedTuple):
    reference: Any
    persisted_bytes: bytes


@pytest.fixture
def capture_tool_arguments(initial_drafts):
    def factory(
        root: Path, manifest, lane, arguments: dict[str, Any]
    ) -> _CapturedArguments:
        """Sanitize and persist tool arguments as a private evidence sidecar."""
        repository = EngagementJournalRepository(root, clock=lambda: LOGBOOK_FIXED_TIME)
        repository.create(manifest, initial_drafts(manifest, lane))
        sanitized = sanitize_host_arguments(arguments)
        assert isinstance(sanitized, SanitizedHostValue)
        assert sanitized.canonical_bytes is not None
        reference = repository.write_evidence(
            manifest.engagement_id,
            sanitized.canonical_bytes,
            media_type="application/json",
            representation="sanitized_host_json",
            capture_limitations=(
                (CaptureLimitation.PROVIDER_OR_HOST_SECRET_REDACTED,)
                if sanitized.provider_or_host_secret_redacted
                else ()
            ),
        )
        return _CapturedArguments(reference, sanitized.canonical_bytes)

    return factory


def test_evidence_store_preserves_exact_bytes_and_deduplicates_by_digest(
    tmp_path, manifest, lane, initial_drafts, evidence_attached_draft, fixed_clock
) -> None:
    payload = b"user flag: HTB{private-proof}\npassword=p@ssw0rd\xff"
    root = tmp_path / "knowledge"
    with EngagementJournalRepository(root, clock=fixed_clock) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        first = repository.write_evidence(
            manifest.engagement_id,
            payload,
            media_type="application/octet-stream",
            representation="host_bytes",
        )
        second = repository.write_evidence(
            manifest.engagement_id,
            payload,
            media_type="application/octet-stream",
            representation="host_bytes",
        )
        repository.append_batch(
            manifest.engagement_id,
            (evidence_attached_draft(lane, first),),
        )
        restored = repository.read_evidence_slice(
            manifest.engagement_id,
            first.evidence_id,
            offset=0,
            limit=len(payload),
        )

    assert first == second
    assert first.evidence_id == f"evidence-sha256-{first.sha256}"
    assert restored.data == payload
    assert restored.complete is True
    blobs = list(_engagement_path(root, manifest.engagement_id).glob("evidence/blob-*.bin"))
    assert len(blobs) == 1
    assert blobs[0].read_bytes() == payload


def test_quota_failure_is_typed_and_does_not_create_partial_sidecar(
    tmp_path, manifest, lane, initial_drafts, fixed_clock
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalRepository(
        root,
        clock=fixed_clock,
        evidence_quota=EvidenceQuota(max_item_bytes=4, max_engagement_bytes=16),
    ) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        with pytest.raises(EvidenceCaptureError) as caught:
            repository.write_evidence(
                manifest.engagement_id,
                b"12345",
                media_type="text/plain",
                representation="host_text",
            )
    assert caught.value.reason_code == "item_quota_exceeded"
    assert caught.value.observed_size == 5
    evidence = _engagement_path(root, manifest.engagement_id) / "evidence"
    assert not list(evidence.glob("blob-*.bin"))
    assert not list(evidence.glob(".pending-blob-*.bin"))


def test_default_evidence_quota_is_exactly_the_m6a_contract() -> None:
    assert EvidenceQuota(
        max_item_bytes=64 * 1024 * 1024,
        max_engagement_bytes=4 * 1024 * 1024 * 1024,
    ) == DEFAULT_EVIDENCE_QUOTA


def test_evidence_slice_rejects_unbounded_or_negative_reads(
    tmp_path, manifest, lane, initial_drafts, fixed_clock
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalRepository(root, clock=fixed_clock) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        reference = repository.write_evidence(
            manifest.engagement_id,
            b"payload",
            media_type="application/octet-stream",
            representation="host_bytes",
        )
        with pytest.raises(ValueError, match="offset"):
            repository.read_evidence_slice(
                manifest.engagement_id, reference.evidence_id, offset=-1, limit=1
            )
        with pytest.raises(ValueError, match="limit"):
            repository.read_evidence_slice(
                manifest.engagement_id, reference.evidence_id, offset=0, limit=0
            )
        with pytest.raises(ValueError, match="limit"):
            repository.read_evidence_slice(
                manifest.engagement_id, reference.evidence_id, offset=0, limit=65_537
            )


def test_evidence_slice_marks_incomplete_when_bytes_remain(
    tmp_path, manifest, lane, initial_drafts, fixed_clock
) -> None:
    payload = b"0123456789"
    root = tmp_path / "knowledge"
    with EngagementJournalRepository(root, clock=fixed_clock) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        reference = repository.write_evidence(
            manifest.engagement_id,
            payload,
            media_type="application/octet-stream",
            representation="host_bytes",
        )
        partial = repository.read_evidence_slice(
            manifest.engagement_id, reference.evidence_id, offset=4, limit=3
        )
    assert partial.data == b"456"
    assert partial.offset == 4
    assert partial.complete is False


def test_evidence_slice_verifies_digest_and_size(
    tmp_path, manifest, lane, initial_drafts, fixed_clock
) -> None:
    payload = b"integrity-checked"
    root = tmp_path / "knowledge"
    with EngagementJournalRepository(root, clock=fixed_clock) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        reference = repository.write_evidence(
            manifest.engagement_id,
            payload,
            media_type="application/octet-stream",
            representation="host_bytes",
        )
        restored = repository.read_evidence_slice(
            manifest.engagement_id, reference.evidence_id, offset=0, limit=len(payload)
        )
    assert restored.data == payload
    assert restored.complete is True


def test_evidence_store_rejects_non_normalized_or_oversized_input(
    tmp_path, manifest, lane, initial_drafts, fixed_clock
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalRepository(root, clock=fixed_clock) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        engagement_fd = repository._engagement_fd(manifest.engagement_id)
        try:
            store = EvidenceStore(engagement_fd)
            with pytest.raises(TypeError, match="normalized bytes"):
                store.capture("not-bytes")
        finally:
            os.close(engagement_fd)


def test_normalize_host_payload_representations_are_bounded_and_typed() -> None:
    text = normalize_host_payload("hello")
    binary = normalize_host_payload(b"\xff\x00")
    structured = normalize_host_payload({"b": 2, "a": 1})
    empty = normalize_host_payload(None)

    assert text.representation == "host_text"
    assert binary.representation == "host_bytes"
    assert structured.representation == "canonical_host_json"
    assert empty.representation == "host_returned_no_result"


def test_orphan_evidence_inventory_is_bounded_and_non_destructive(
    tmp_path, manifest, lane, initial_drafts, evidence_attached_draft, fixed_clock
) -> None:
    root = tmp_path / "knowledge"
    orphan_payload = b"orphan-bytes"
    attached_payload = b"attached-bytes"
    with EngagementJournalRepository(root, clock=fixed_clock) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        orphan = repository.write_evidence(
            manifest.engagement_id,
            orphan_payload,
            media_type="application/octet-stream",
            representation="host_bytes",
        )
        attached = repository.write_evidence(
            manifest.engagement_id,
            attached_payload,
            media_type="application/octet-stream",
            representation="host_bytes",
        )
        repository.append_batch(
            manifest.engagement_id,
            (evidence_attached_draft(lane, attached),),
        )
        page = repository.inventory_orphan_evidence(manifest.engagement_id, limit=1)
        second = repository.inventory_orphan_evidence(
            manifest.engagement_id, after_name=page.names[-1], limit=256
        )

    assert isinstance(page, OrphanEvidencePage)
    assert len(page.names) == 1
    assert page.total_count == 1
    assert f"blob-{orphan.sha256}.bin" in page.names
    assert f"blob-{attached.sha256}.bin" not in page.names
    assert second.names == ()
    assert second.total_count == 1
    assert second.next_after_name is None
    evidence = _engagement_path(root, manifest.engagement_id) / "evidence"
    assert len(list(evidence.glob("blob-*.bin"))) == 2


@pytest.mark.parametrize(
    "fault_point",
    [
        "evidence_before_temp_write",
        "evidence_after_partial_temp_write",
        "evidence_after_complete_temp_write",
        "evidence_after_file_fsync",
        "evidence_after_publication",
        "evidence_after_directory_fsync",
    ],
)
def test_evidence_capture_publication_is_crash_recoverable(
    tmp_path, manifest, lane, initial_drafts, fixed_clock, fault_point, monkeypatch
) -> None:
    root = tmp_path / fault_point
    _create_engagement(root, manifest, lane, initial_drafts, fixed_clock)
    payload = b"crash-recovery-payload"
    fired = False

    def crash_once(point: str) -> None:
        nonlocal fired
        if point == fault_point and not fired:
            fired = True
            raise OSError(f"crash at {fault_point}")

    with EngagementJournalRepository(root, clock=fixed_clock) as repository:
        monkeypatch.setattr(repository, "_fault", crash_once)
        engagement_fd = repository._engagement_fd(manifest.engagement_id)
        try:
            store = EvidenceStore(engagement_fd, fault=repository._fault)
            with pytest.raises(OSError, match="crash at"):
                store.capture(payload)
        finally:
            os.close(engagement_fd)

    with EngagementJournalRepository(root, clock=fixed_clock) as recovered:
        references = recovered.write_evidence(
            manifest.engagement_id,
            payload,
            media_type="application/octet-stream",
            representation="host_bytes",
        )
        page = recovered.inventory_orphan_evidence(manifest.engagement_id)
    assert fired
    assert references.size == len(payload)
    assert page.total_count == 1
    evidence = _engagement_path(root, manifest.engagement_id) / "evidence"
    assert len(list(evidence.glob("blob-*.bin"))) == 1
    assert list(evidence.glob("blob-*.bin"))[0].read_bytes() == payload


def test_logbook_keeps_untrusted_markdown_in_a_dynamic_code_fence(
    tmp_path, manifest, lane, recorded_session_snapshot
) -> None:
    hostile = "```\n</script>\n[click](javascript:alert(1))\nHTB{proof}\n````"
    snapshot = recorded_session_snapshot(tmp_path / "knowledge", manifest, lane, hostile)

    first = rebuild_session_logbooks(snapshot.repository, manifest.engagement_id)
    path = first[0]
    rendered = path.read_text(encoding="utf-8")
    path.unlink()
    second = rebuild_session_logbooks(snapshot.repository, manifest.engagement_id)

    assert second[0].read_text(encoding="utf-8") == rendered
    assert "HTB{proof}" in rendered
    assert "javascript:alert(1)" in rendered
    assert rendered.count("`````") >= 2
    assert rendered.find("javascript:alert(1)") > rendered.find("`````")


def test_provider_credentials_are_removed_before_argument_sidecar_capture(
    tmp_path, manifest, lane, capture_tool_arguments
) -> None:
    secret = "provider-secret-that-must-never-reach-disk"
    target_credential = "Basic dGFyZ2V0LWV4YW1wbGU="
    captured = capture_tool_arguments(
        tmp_path / "knowledge",
        manifest,
        lane,
        {
            "command": "curl https://192.0.2.44/",
            "headers": {"Authorization": target_credential},
            "provider": {"authorization": secret},
            "provider_token": secret,
        },
    )

    assert captured.reference.capture_limitations == (
        CaptureLimitation.PROVIDER_OR_HOST_SECRET_REDACTED,
    )
    assert secret.encode() not in _all_engagement_bytes(
        tmp_path / "knowledge", manifest.engagement_id
    )
    assert sha256(secret.encode()).hexdigest().encode() not in _all_engagement_bytes(
        tmp_path / "knowledge", manifest.engagement_id
    )
    assert b"[REDACTED:provider-or-host-secret]" in captured.persisted_bytes
    assert target_credential.encode() in captured.persisted_bytes


def test_logbook_renders_identity_revision_and_tool_metadata(
    tmp_path, manifest, lane, recorded_session_snapshot
) -> None:
    snapshot = recorded_session_snapshot(
        tmp_path / "knowledge", manifest, lane, "plain output"
    )
    loaded = snapshot.repository.load_snapshot(manifest.engagement_id)
    rendered = render_session_logbook(
        loaded.manifest,
        loaded.state,
        loaded.events,
        repository_evidence_reader(snapshot.repository, manifest.engagement_id),
        session_id=lane.session_id,
    )

    assert "HTB-Orion" in rendered
    assert "Obtain the user and root flags" in rendered
    assert "192.0.2.44" in rendered
    assert "session-orion" in rendered
    assert "call-1" in rendered
    assert "plain output" in rendered


def test_logbook_link_is_validated_confined_relative_path(
    tmp_path, manifest, lane, recorded_session_snapshot, initial_drafts,
    tool_started, tool_completed, evidence_attached_draft,
) -> None:
    root = tmp_path / "knowledge"
    repository = EngagementJournalRepository(root, clock=lambda: LOGBOOK_FIXED_TIME)
    repository.create(manifest, initial_drafts(manifest, lane))
    normalized = normalize_host_payload(b"\x00\xff\x01binary")
    assert isinstance(normalized, SanitizedHostValue)
    assert normalized.canonical_bytes is not None
    reference = repository.write_evidence(
        manifest.engagement_id,
        normalized.canonical_bytes,
        media_type="application/octet-stream",
        representation="host_bytes",
    )
    repository.append_batch(
        manifest.engagement_id,
        (
            tool_started(lane, call_id="call-1"),
            evidence_attached_draft(lane, reference),
            tool_completed(lane, call_id="call-1"),
        ),
    )
    loaded = repository.load_snapshot(manifest.engagement_id)
    rendered = render_session_logbook(
        loaded.manifest,
        loaded.state,
        loaded.events,
        repository_evidence_reader(repository, manifest.engagement_id),
        session_id=lane.session_id,
    )

    assert "blob-" in rendered
    assert ".." not in rendered
    assert "evidence/evidence" not in rendered


def test_logbook_rebuild_retry_exhaustion_raises_typed_conflict(
    tmp_path, manifest, lane, initial_drafts, user_note_draft, recorded_session_snapshot
) -> None:
    snapshot = recorded_session_snapshot(
        tmp_path / "knowledge", manifest, lane, "conflict output"
    )
    repository = snapshot.repository
    original = repository.load_snapshot
    calls = {"count": 0}

    def load_after_render(engagement_id):
        calls["count"] += 1
        if calls["count"] == 1:
            return original(engagement_id)
        repository.append_batch(
            engagement_id,
            (user_note_draft(f"stale-{calls['count']}"),),
        )
        return original(engagement_id)

    repository.load_snapshot = load_after_render  # type: ignore[method-assign]
    with pytest.raises(LogbookProjectionConflict, match="logbook_rebuild_conflict"):
        rebuild_session_logbooks(repository, manifest.engagement_id)
