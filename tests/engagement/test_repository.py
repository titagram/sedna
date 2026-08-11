from __future__ import annotations

import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import pytest

import sedna.engagement.repository as repository_module
from sedna.engagement import JournalRevision
from sedna.engagement.reducer import EngagementReplayError
from sedna.engagement.repository import (
    EngagementJournalRepository,
    JournalUnavailableError,
    ProjectionOwnershipError,
    RevisionConflictError,
)


def _repository(root: Path, fixed_clock, fixed_uuid_factory):
    return EngagementJournalRepository(
        root,
        clock=fixed_clock,
        uuid_factory=fixed_uuid_factory,
    )


def test_create_commits_manifest_open_and_lane_binding_atomically(
    tmp_path, manifest, lane, initial_drafts, fixed_clock, fixed_uuid_factory
) -> None:
    with _repository(tmp_path / "knowledge", fixed_clock, fixed_uuid_factory) as repository:
        snapshot = repository.create(manifest, initial_drafts(manifest, lane))

    root = tmp_path / "knowledge" / "engagements" / str(manifest.engagement_id)
    assert snapshot.revision.sequence == 2
    assert (root / "engagement.json").is_file()
    assert (root / "events.jsonl").read_text(encoding="utf-8").count("\n") == 2
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "engagement.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((root / "events.jsonl").stat().st_mode) == 0o600
    assert stat.S_IMODE((root / "journal-head.json").stat().st_mode) == 0o600
    assert (root / "engagement-state.json").is_file()
    assert not (root / "state.json").exists()


def test_create_retry_finishes_exact_pending_intent_once(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch,
) -> None:
    root = tmp_path / "knowledge"
    failed = False

    def fail_after_head(point: str) -> None:
        nonlocal failed
        if point == "create_after_head" and not failed:
            failed = True
            raise OSError("injected create crash")

    repository = _repository(root, fixed_clock, fixed_uuid_factory)
    monkeypatch.setattr(repository, "_fault", fail_after_head)
    with pytest.raises(OSError, match="injected"):
        repository.create(manifest, initial_drafts(manifest, lane))
    repository.close()

    with _repository(root, fixed_clock, fixed_uuid_factory) as recovered:
        snapshot = recovered.create(manifest, initial_drafts(manifest, lane))

    engagement = root / "engagements" / str(manifest.engagement_id)
    assert snapshot.revision.sequence == 2
    assert not (root / "engagements" / f".pending-create-{manifest.engagement_id}").exists()
    assert (engagement / "events.jsonl").read_bytes().count(b"\n") == 2


def test_append_assigns_sequence_and_hash_from_current_tail(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    user_note_draft,
    decision_draft,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    with _repository(tmp_path / "knowledge", fixed_clock, fixed_uuid_factory) as repository:
        opened = repository.create(manifest, initial_drafts(manifest, lane))
        result = repository.append_batch(
            manifest.engagement_id,
            (user_note_draft("ready"), decision_draft(lane)),
            expected_revision=opened.revision,
        )
        events = repository.load_events(manifest.engagement_id)

    assert [item.sequence for item in events] == [1, 2, 3, 4]
    assert events[3].previous_hash == events[2].event_hash
    assert result.revision == JournalRevision(sequence=4, event_hash=events[3].event_hash)


def test_same_idempotency_key_returns_existing_event_but_collision_fails(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    user_note_draft,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    draft = user_note_draft("same").model_copy(update={"idempotency_key": "same-note"})
    with _repository(tmp_path / "knowledge", fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        first = repository.append_batch(manifest.engagement_id, (draft,))
        second = repository.append_batch(manifest.engagement_id, (draft,))
        with pytest.raises(ValueError, match="idempotency key collision"):
            repository.append_batch(
                manifest.engagement_id,
                (draft.model_copy(update={"actor_id": "different"}),),
            )

    assert first.created_event_ids == second.existing_event_ids


def test_expected_revision_rejects_stale_batch_without_partial_append(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    user_note_draft,
    decision_draft,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    with _repository(tmp_path / "knowledge", fixed_clock, fixed_uuid_factory) as repository:
        opening = repository.create(manifest, initial_drafts(manifest, lane))
        repository.append_batch(manifest.engagement_id, (user_note_draft("new"),))
        with pytest.raises(RevisionConflictError):
            repository.append_batch(
                manifest.engagement_id,
                (decision_draft(lane),),
                expected_revision=opening.revision,
            )
        assert len(repository.load_events(manifest.engagement_id)) == 3


def test_append_retry_rolls_forward_once_and_repairs_projection(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    user_note_draft,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch,
) -> None:
    root = tmp_path / "knowledge"
    draft = user_note_draft("durable").model_copy(
        update={"idempotency_key": "durable-note"}
    )
    repository = _repository(root, fixed_clock, fixed_uuid_factory)
    opened = repository.create(manifest, initial_drafts(manifest, lane))
    failed = False

    def fail_after_journal(point: str) -> None:
        nonlocal failed
        if point == "append_after_journal_fsync" and not failed:
            failed = True
            raise OSError("injected append crash")

    monkeypatch.setattr(repository, "_fault", fail_after_journal)
    with pytest.raises(OSError, match="injected"):
        repository.append_batch(
            manifest.engagement_id, (draft,), expected_revision=opened.revision
        )
    repository.close()

    with EngagementJournalRepository(root, clock=fixed_clock) as recovered:
        result = recovered.append_batch(
            manifest.engagement_id, (draft,), expected_revision=opened.revision
        )

    projection = json.loads(
        (
            root
            / "engagements"
            / str(manifest.engagement_id)
            / "engagement-state.json"
        ).read_bytes()
    )
    assert result.created_event_ids == ()
    assert len(result.existing_event_ids) == 1
    assert projection["authoritative_revision"]["sequence"] == 3


def test_lifecycle_invalid_batch_is_rejected_before_pending_io(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    tool_completed,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    with _repository(tmp_path / "knowledge", fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        with pytest.raises(EngagementReplayError, match="no matching started call"):
            repository.append_batch(manifest.engagement_id, (tool_completed(lane),))
        engagement = tmp_path / "knowledge" / "engagements" / str(manifest.engagement_id)
        assert not (engagement / ".pending-append.json").exists()


def test_projection_writer_rejects_cross_milestone_ownership_before_target_open(
    tmp_path, manifest, lane, initial_drafts, fixed_clock, fixed_uuid_factory
) -> None:
    with _repository(tmp_path / "knowledge", fixed_clock, fixed_uuid_factory) as repository:
        snapshot = repository.create(manifest, initial_drafts(manifest, lane))
        with pytest.raises(ProjectionOwnershipError):
            repository.write_projection(
                manifest.engagement_id,
                name="engagement-state",
                owner="planning",
                envelope={"authoritative_revision": snapshot.revision.model_dump(mode="json")},
            )


def test_concurrent_repository_instances_produce_one_monotonic_chain(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    user_note_draft,
    fixed_clock,
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalRepository(root, clock=fixed_clock) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))

    def append(index: int) -> None:
        draft = user_note_draft(f"note-{index}").model_copy(
            update={"idempotency_key": f"note-key-{index}"}
        )
        with EngagementJournalRepository(root, clock=fixed_clock) as repository:
            repository.append_batch(manifest.engagement_id, (draft,))

    with ThreadPoolExecutor(max_workers=8) as pool:
        tuple(pool.map(append, range(32)))

    with EngagementJournalRepository(root, clock=fixed_clock) as repository:
        events = repository.load_events(manifest.engagement_id)
    assert [event.sequence for event in events] == list(range(1, 35))
    assert len({event.event_hash for event in events}) == 34


def test_symlinked_engagement_directory_cannot_escape(tmp_path, manifest) -> None:
    root = (tmp_path / "knowledge").resolve()
    outside = tmp_path / "outside"
    outside.mkdir()
    engagements = root / "engagements"
    engagements.mkdir(parents=True)
    (engagements / str(manifest.engagement_id)).symlink_to(outside, target_is_directory=True)

    with EngagementJournalRepository(root) as repository, pytest.raises(ValueError):
        repository.load_events(manifest.engagement_id)
    assert not list(outside.iterdir())


def test_fifo_journal_is_rejected_without_blocking(
    tmp_path, manifest, lane, initial_drafts, fixed_clock, fixed_uuid_factory
) -> None:
    root = tmp_path / "knowledge"
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
    journal = root / "engagements" / str(manifest.engagement_id) / "events.jsonl"
    journal.unlink()
    os.mkfifo(journal, mode=0o600)

    with (
        EngagementJournalRepository(root) as repository,
        pytest.raises(JournalUnavailableError, match="regular file"),
    ):
        repository.load_events(manifest.engagement_id)


def test_target_file_symlink_replacement_is_rejected(
    tmp_path, manifest, lane, initial_drafts, fixed_clock, fixed_uuid_factory
) -> None:
    root = tmp_path / "knowledge"
    outside = tmp_path / "outside.jsonl"
    outside.write_text("untouched", encoding="utf-8")
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
    journal = root / "engagements" / str(manifest.engagement_id) / "events.jsonl"
    journal.unlink()
    journal.symlink_to(outside)

    with (
        EngagementJournalRepository(root) as repository,
        pytest.raises(JournalUnavailableError),
    ):
        repository.load_events(manifest.engagement_id)
    assert outside.read_text(encoding="utf-8") == "untouched"


def test_retained_root_descriptor_survives_pathname_replacement(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    user_note_draft,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    root = tmp_path / "knowledge"
    repository = _repository(root, fixed_clock, fixed_uuid_factory)
    repository.create(manifest, initial_drafts(manifest, lane))
    moved = tmp_path / "retained-root"
    root.rename(moved)
    root.mkdir()
    repository.append_batch(manifest.engagement_id, (user_note_draft("retained"),))
    repository.close()

    assert (moved / "engagements" / str(manifest.engagement_id) / "events.jsonl").read_text(
        encoding="utf-8"
    ).count("\n") == 3
    assert not list(root.iterdir())


def test_partial_final_jsonl_record_is_isolated_and_valid_prefix_replayed(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    user_note_draft,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    root = tmp_path / "knowledge"
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        repository.append_batch(manifest.engagement_id, (user_note_draft("before-tail"),))
    journal = root / "engagements" / str(manifest.engagement_id) / "events.jsonl"
    with journal.open("ab") as stream:
        stream.write(b'{"sequence":4,"broken"')
        stream.flush()
        os.fsync(stream.fileno())

    with EngagementJournalRepository(root, clock=fixed_clock) as recovered:
        events = recovered.load_events(manifest.engagement_id)

    assert [event.sequence for event in events[:3]] == [1, 2, 3]
    assert events[-2].type == "evidence_attached"
    assert events[-1].type == "recovery_warning"
    recovery_files = list((journal.parent / "evidence").glob("blob-*.bin"))
    assert len(recovery_files) == 1
    assert recovery_files[0].read_bytes() == b'{"sequence":4,"broken"'


def test_tail_recovery_resumes_after_truncate_fault_without_losing_evidence(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    user_note_draft,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch,
) -> None:
    root = tmp_path / "knowledge"
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        repository.append_batch(manifest.engagement_id, (user_note_draft("before-tail"),))
    journal = root / "engagements" / str(manifest.engagement_id) / "events.jsonl"
    tail = b'{"sequence":4,"interrupted"'
    with journal.open("ab") as stream:
        stream.write(tail)
        stream.flush()
        os.fsync(stream.fileno())

    failed = False

    def fail_after_truncate(point: str) -> None:
        nonlocal failed
        if point == "tail_after_truncate" and not failed:
            failed = True
            raise OSError("injected tail recovery crash")

    with EngagementJournalRepository(root, clock=fixed_clock) as repository:
        monkeypatch.setattr(repository, "_fault", fail_after_truncate)
        with pytest.raises(OSError, match="injected"):
            repository.load_events(manifest.engagement_id)

    with EngagementJournalRepository(root, clock=fixed_clock) as repository:
        events = repository.load_events(manifest.engagement_id)

    assert [event.type.value for event in events[-2:]] == [
        "evidence_attached",
        "recovery_warning",
    ]
    evidence = root / "engagements" / str(manifest.engagement_id) / "evidence"
    assert [item.read_bytes() for item in evidence.glob("blob-*.bin")] == [tail]


def test_newline_terminated_extension_ahead_of_head_is_corruption(
    tmp_path, manifest, lane, initial_drafts, fixed_clock, fixed_uuid_factory
) -> None:
    root = tmp_path / "knowledge"
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
    journal = root / "engagements" / str(manifest.engagement_id) / "events.jsonl"
    with journal.open("ab") as stream:
        stream.write(b"{}\n")

    with (
        EngagementJournalRepository(root) as repository,
        pytest.raises(JournalUnavailableError, match="journal_corrupt"),
    ):
        repository.load_events(manifest.engagement_id)


def test_missing_or_stale_projection_is_rebuilt_byte_identically(
    tmp_path, manifest, lane, initial_drafts, fixed_clock, fixed_uuid_factory
) -> None:
    root = tmp_path / "knowledge"
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        original = repository.create(manifest, initial_drafts(manifest, lane))
    projection = root / "engagements" / str(manifest.engagement_id) / "engagement-state.json"
    expected = projection.read_bytes()
    projection.write_bytes(b'{"owner":"planning"}')

    with EngagementJournalRepository(root) as repository:
        rebuilt = repository.load_snapshot(manifest.engagement_id)

    assert rebuilt == original
    assert projection.read_bytes() == expected


def test_close_is_idempotent_and_closed_repository_rejects_calls(tmp_path) -> None:
    repository = EngagementJournalRepository((tmp_path / "knowledge").resolve())
    repository.close()
    repository.close()
    with pytest.raises(JournalUnavailableError, match="closed"):
        repository.load_events(UUID("11111111-1111-4111-8111-111111111111"))


def test_constructor_rejects_relative_and_missing_posix_descriptor_primitives(
    tmp_path, monkeypatch
) -> None:
    with pytest.raises(ValueError, match="absolute"):
        EngagementJournalRepository(Path("relative"))
    monkeypatch.delattr(repository_module.os, "O_NOFOLLOW")
    with pytest.raises(JournalUnavailableError, match="POSIX descriptor"):
        EngagementJournalRepository((tmp_path / "knowledge").resolve())


def test_head_contains_exact_authoritative_journal_measurements(
    tmp_path, manifest, lane, initial_drafts, fixed_clock, fixed_uuid_factory
) -> None:
    root = tmp_path / "knowledge"
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        snapshot = repository.create(manifest, initial_drafts(manifest, lane))
    engagement = root / "engagements" / str(manifest.engagement_id)
    journal = (engagement / "events.jsonl").read_bytes()
    head = json.loads((engagement / "journal-head.json").read_bytes())

    assert head["revision"] == snapshot.revision.model_dump(mode="json")
    assert head["event_count"] == 2
    assert head["journal_bytes"] == len(journal)
