from __future__ import annotations

import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Barrier, Event
from uuid import UUID

import pytest

import sedna.engagement.repository as repository_module
from sedna.engagement import (
    EngagementAbandonedPayload,
    JournalEventDraft,
    JournalRevision,
    SystemCorrelation,
)
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


def _engagement_path(root: Path, engagement_id: UUID) -> Path:
    return root / "engagements" / str(engagement_id)


def _forbid_repository_writes(monkeypatch):
    calls: list[str] = []

    def forbidden(_parent_fd: int, name: str, _data: bytes) -> None:
        calls.append(name)
        raise AssertionError(f"unexpected repository write: {name}")

    monkeypatch.setattr(repository_module, "_atomic_write", forbidden)
    return calls


def _second_manifest_and_lane(manifest, new_lane):
    return (
        manifest.model_copy(
            update={
                "engagement_id": UUID("22222222-2222-4222-8222-222222222222"),
                "display_name": "HTB-Orion-2",
            }
        ),
        new_lane(session_id="session-pegasus", task_id="task-second"),
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
    root.chmod(0o700)
    engagements.chmod(0o700)
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
        engagement = _engagement_path(root, manifest.engagement_id)
    engagement = root / "engagements" / str(manifest.engagement_id)
    journal = (engagement / "events.jsonl").read_bytes()
    head = json.loads((engagement / "journal-head.json").read_bytes())

    assert head["revision"] == snapshot.revision.model_dump(mode="json")
    assert head["event_count"] == 2
    assert head["journal_bytes"] == len(journal)


def test_manifest_exact_byte_limit_is_accepted_and_one_over_is_prewrite(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch,
) -> None:
    manifest_size = len(repository_module._model_bytes(manifest))
    monkeypatch.setattr(repository_module, "MAX_MANIFEST_BYTES", manifest_size)
    with _repository(tmp_path / "exact", fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))

    repository = _repository(tmp_path / "over", fixed_clock, fixed_uuid_factory)
    calls = _forbid_repository_writes(monkeypatch)
    monkeypatch.setattr(repository_module, "MAX_MANIFEST_BYTES", manifest_size - 1)
    with pytest.raises(ValueError, match="manifest exceeds"):
        repository.create(manifest, initial_drafts(manifest, lane))
    repository.close()
    assert calls == []


def test_materialized_event_exact_byte_limit_is_rechecked_on_idempotent_replay(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    user_note_draft,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch,
) -> None:
    draft = user_note_draft("event-boundary").model_copy(
        update={
            "event_id": UUID("33333333-3333-4333-8333-333333333333"),
            "idempotency_key": "event-boundary",
        }
    )
    repository = _repository(tmp_path / "knowledge", fixed_clock, fixed_uuid_factory)
    repository.create(manifest, initial_drafts(manifest, lane))
    existing = repository.load_events(manifest.engagement_id)
    event = repository._materialize(manifest.engagement_id, existing, (draft,))[0]
    event_size = max(
        len(repository_module._event_line(item)) for item in (*existing, event)
    )
    monkeypatch.setattr(repository_module, "MAX_JOURNAL_EVENT_BYTES", event_size)
    repository.append_batch(manifest.engagement_id, (draft,))

    calls = _forbid_repository_writes(monkeypatch)
    monkeypatch.setattr(repository_module, "MAX_JOURNAL_EVENT_BYTES", event_size - 1)
    with pytest.raises(ValueError, match="journal event exceeds"):
        repository.append_batch(manifest.engagement_id, (draft,))
    repository.close()
    assert calls == []


def test_batch_exact_count_limit_is_accepted_and_one_over_is_prewrite(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    user_note_draft,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch,
) -> None:
    repository = _repository(tmp_path / "knowledge", fixed_clock, fixed_uuid_factory)
    repository.create(manifest, initial_drafts(manifest, lane))
    monkeypatch.setattr(repository_module, "MAX_JOURNAL_BATCH_EVENTS", 2)
    repository.append_batch(
        manifest.engagement_id,
        (user_note_draft("exact-1"), user_note_draft("exact-2")),
    )

    calls = _forbid_repository_writes(monkeypatch)
    with pytest.raises(ValueError, match="journal batch exceeds"):
        repository.append_batch(
            manifest.engagement_id,
            (
                user_note_draft("over-1"),
                user_note_draft("over-2"),
                user_note_draft("over-3"),
            ),
        )
    repository.close()
    assert calls == []


def test_prospective_event_count_exact_limit_is_accepted_and_one_over_is_prewrite(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    user_note_draft,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch,
) -> None:
    repository = _repository(tmp_path / "knowledge", fixed_clock, fixed_uuid_factory)
    repository.create(manifest, initial_drafts(manifest, lane))
    monkeypatch.setattr(repository_module, "MAX_JOURNAL_EVENTS", 3)
    repository.append_batch(manifest.engagement_id, (user_note_draft("exact"),))

    calls = _forbid_repository_writes(monkeypatch)
    with pytest.raises(ValueError, match="event count exceeds"):
        repository.append_batch(manifest.engagement_id, (user_note_draft("over"),))
    repository.close()
    assert calls == []


def test_prospective_journal_bytes_include_newlines_at_exact_boundary(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    user_note_draft,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch,
) -> None:
    draft = user_note_draft("journal-byte-boundary").model_copy(
        update={"event_id": UUID("44444444-4444-4444-8444-444444444444")}
    )
    exact_root = tmp_path / "exact"
    with _repository(exact_root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        existing_size = len(
            (_engagement_path(exact_root, manifest.engagement_id) / "events.jsonl").read_bytes()
        )
        event = repository._materialize(
            manifest.engagement_id, repository.load_events(manifest.engagement_id), (draft,)
        )[0]
        prospective_size = existing_size + len(repository_module._event_line(event)) + 1
        monkeypatch.setattr(repository_module, "MAX_JOURNAL_BYTES", prospective_size)
        repository.append_batch(manifest.engagement_id, (draft,))

    over_root = tmp_path / "over"
    repository = _repository(over_root, fixed_clock, fixed_uuid_factory)
    repository.create(manifest, initial_drafts(manifest, lane))
    calls = _forbid_repository_writes(monkeypatch)
    monkeypatch.setattr(repository_module, "MAX_JOURNAL_BYTES", prospective_size - 1)
    with pytest.raises(ValueError, match="journal bytes exceed"):
        repository.append_batch(manifest.engagement_id, (draft,))
    repository.close()
    assert calls == []


def test_prospective_projection_exact_byte_limit_is_accepted_and_one_over_is_prewrite(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    user_note_draft,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch,
) -> None:
    draft = user_note_draft("projection-boundary").model_copy(
        update={"event_id": UUID("55555555-5555-4555-8555-555555555555")}
    )
    exact_root = tmp_path / "exact"
    with _repository(exact_root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        repository.append_batch(manifest.engagement_id, (draft,))
    projection_size = len(
        (
            _engagement_path(exact_root, manifest.engagement_id)
            / "engagement-state.json"
        ).read_bytes()
    )

    accepted_root = tmp_path / "accepted"
    with _repository(accepted_root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        monkeypatch.setattr(
            repository_module, "MAX_DERIVED_PROJECTION_BYTES", projection_size
        )
        repository.append_batch(manifest.engagement_id, (draft,))

    over_root = tmp_path / "over"
    repository = _repository(over_root, fixed_clock, fixed_uuid_factory)
    repository.create(manifest, initial_drafts(manifest, lane))
    calls = _forbid_repository_writes(monkeypatch)
    monkeypatch.setattr(
        repository_module, "MAX_DERIVED_PROJECTION_BYTES", projection_size - 1
    )
    with pytest.raises(ValueError, match="projection exceeds"):
        repository.append_batch(manifest.engagement_id, (draft,))
    repository.close()
    assert calls == []


def test_published_engagement_exact_count_is_accepted_and_one_over_is_prewrite(
    tmp_path,
    manifest,
    lane,
    new_lane,
    initial_drafts,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch,
) -> None:
    second_manifest, second_lane = _second_manifest_and_lane(manifest, new_lane)
    repository = _repository(tmp_path / "knowledge", fixed_clock, fixed_uuid_factory)
    monkeypatch.setattr(repository_module, "MAX_ENGAGEMENTS", 1)
    repository.create(manifest, initial_drafts(manifest, lane))

    calls = _forbid_repository_writes(monkeypatch)
    with pytest.raises(ValueError, match="engagement count exceeds"):
        repository.create(
            second_manifest, initial_drafts(second_manifest, second_lane)
        )
    repository.close()
    assert calls == []


def test_mixed_engagement_directory_exact_count_is_accepted_and_one_over_rejected(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "knowledge"
    engagements = root / "engagements"
    engagements.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    (engagements / ".registry.lock").write_bytes(b"")
    (engagements / ".registry.lock").chmod(0o600)
    (engagements / "invalid-entry").write_bytes(b"")
    monkeypatch.setattr(repository_module, "MAX_ENGAGEMENT_DIRECTORY_ENTRIES", 2)
    EngagementJournalRepository(root).close()

    (engagements / "another-invalid-entry").write_bytes(b"")
    before = sorted(item.name for item in engagements.iterdir())
    with pytest.raises(JournalUnavailableError, match="directory entry bound"):
        EngagementJournalRepository(root)
    assert sorted(item.name for item in engagements.iterdir()) == before


def test_constructor_recovers_exact_pending_create_before_reads(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch,
) -> None:
    root = tmp_path / "knowledge"
    repository = _repository(root, fixed_clock, fixed_uuid_factory)

    def fail_after_head(point: str) -> None:
        if point == "create_after_head":
            raise OSError("injected create crash")

    monkeypatch.setattr(repository, "_fault", fail_after_head)
    with pytest.raises(OSError, match="injected"):
        repository.create(manifest, initial_drafts(manifest, lane))
    repository.close()

    with EngagementJournalRepository(root) as recovered:
        events = recovered.load_events(manifest.engagement_id)

    assert [event.sequence for event in events] == [1, 2]
    assert not (
        root / "engagements" / f".pending-create-{manifest.engagement_id}"
    ).exists()


def test_constructor_removes_empty_preintent_directory_and_fsyncs_parent(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "knowledge"
    engagements = root / "engagements"
    engagements.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    pending = engagements / ".pending-create-11111111-1111-4111-8111-111111111111"
    pending.mkdir(mode=0o700)
    fsynced: list[int] = []
    real_fsync = repository_module.os.fsync

    def spy_fsync(fd: int) -> None:
        fsynced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(repository_module.os, "fsync", spy_fsync)
    with EngagementJournalRepository(root):
        pass

    assert not pending.exists()
    assert fsynced


def test_constructor_fails_closed_on_unknown_or_mismatched_pending_create(
    tmp_path,
) -> None:
    root = tmp_path / "knowledge"
    engagements = root / "engagements"
    engagements.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    pending = engagements / ".pending-create-11111111-1111-4111-8111-111111111111"
    pending.mkdir(mode=0o700)
    (pending / "unknown-user-bytes").write_bytes(b"do-not-publish")

    with pytest.raises(JournalUnavailableError, match="pending create"):
        EngagementJournalRepository(root)
    assert pending.is_dir()
    assert not (engagements / "11111111-1111-4111-8111-111111111111").exists()


def test_constructor_rejects_conflicting_published_uuid_and_pending_intent(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    root = tmp_path / "knowledge"
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
    pending = root / "engagements" / f".pending-create-{manifest.engagement_id}"
    pending.mkdir(mode=0o700)
    (pending / ".create-intent.json").write_bytes(b"{}")
    (pending / ".create-intent.json").chmod(0o600)

    with pytest.raises(JournalUnavailableError, match="pending create"):
        EngagementJournalRepository(root)
    assert pending.is_dir()


def test_engagement_inventory_uses_incremental_scan_not_listdir(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "knowledge"
    engagements = root / "engagements"
    engagements.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    (engagements / ".registry.lock").write_bytes(b"")
    (engagements / ".registry.lock").chmod(0o600)

    def forbid_listdir(*_args, **_kwargs):
        raise AssertionError("unbounded listdir used")

    monkeypatch.setattr(repository_module.os, "listdir", forbid_listdir)
    EngagementJournalRepository(root).close()


def test_directory_cap_is_checked_before_empty_pending_recovery(tmp_path, monkeypatch) -> None:
    root = tmp_path / "knowledge"
    engagements = root / "engagements"
    engagements.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    pending = engagements / ".pending-create-11111111-1111-4111-8111-111111111111"
    pending.mkdir(mode=0o700)
    (engagements / "invalid-1").write_bytes(b"")
    (engagements / "invalid-2").write_bytes(b"")
    monkeypatch.setattr(repository_module, "MAX_ENGAGEMENT_DIRECTORY_ENTRIES", 2)

    with pytest.raises(JournalUnavailableError, match="directory entry bound"):
        EngagementJournalRepository(root)
    assert pending.is_dir()


def test_pending_create_recovery_rejects_a_lane_reserved_by_published_history(
    tmp_path,
    manifest,
    lane,
    new_lane,
    initial_drafts,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch,
) -> None:
    root = tmp_path / "knowledge"
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))

    second_manifest, _ = _second_manifest_and_lane(manifest, new_lane)
    staging_root = tmp_path / "staging"
    staging = _repository(staging_root, fixed_clock, fixed_uuid_factory)

    def fail_after_head(point: str) -> None:
        if point == "create_after_head":
            raise OSError("injected create crash")

    monkeypatch.setattr(staging, "_fault", fail_after_head)
    with pytest.raises(OSError, match="injected"):
        staging.create(second_manifest, initial_drafts(second_manifest, lane))
    staging.close()
    pending_name = f".pending-create-{second_manifest.engagement_id}"
    (staging_root / "engagements" / pending_name).rename(
        root / "engagements" / pending_name
    )

    with pytest.raises(
        (ValueError, JournalUnavailableError), match="lane is already bound|pending create"
    ):
        EngagementJournalRepository(root)
    assert not _engagement_path(root, second_manifest.engagement_id).exists()


def test_abandoned_history_retains_lane_until_exact_unbind_then_releases_it(
    tmp_path,
    manifest,
    lane,
    new_lane,
    initial_drafts,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    root = tmp_path / "knowledge"
    second_manifest, second_lane = _second_manifest_and_lane(manifest, new_lane)
    abandoned = JournalEventDraft(
        actor="system",
        type="engagement_abandoned",
        payload=EngagementAbandonedPayload(reason="host stopped"),
        system_correlation=SystemCorrelation(
            source="lifecycle",
            operation_id=UUID("66666666-6666-4666-8666-666666666666"),
        ),
    )
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        repository.append_batch(manifest.engagement_id, (abandoned,))
        with pytest.raises(ValueError, match="lane is already bound"):
            repository.create(second_manifest, initial_drafts(second_manifest, lane))
        repository.unbind_lane(
            manifest.engagement_id, lane, reason="handoff after abandonment"
        )
        created = repository.create(
            second_manifest, initial_drafts(second_manifest, lane)
        )

    assert created.engagement_id == second_manifest.engagement_id
    assert second_lane != lane


def test_bind_conflict_is_rejected_before_target_lifecycle_mutation(
    tmp_path,
    manifest,
    lane,
    new_lane,
    initial_drafts,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    root = tmp_path / "knowledge"
    second_manifest, second_lane = _second_manifest_and_lane(manifest, new_lane)
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        second = repository.create(
            second_manifest, initial_drafts(second_manifest, second_lane)
        )
        with pytest.raises(ValueError, match="lane is already bound"):
            repository.bind_lane(
                second_manifest.engagement_id,
                lane,
                reason="conflicting bind",
                expected_revision=second.revision,
            )
        assert repository.load_snapshot(second_manifest.engagement_id) == second

    with EngagementJournalRepository(root) as reopened:
        with pytest.raises(ValueError, match="lane is already bound"):
            reopened.bind_lane(
                second_manifest.engagement_id,
                lane,
                reason="still conflicting after reopen",
            )
        assert len(reopened.load_events(second_manifest.engagement_id)) == 2


@pytest.mark.parametrize(
    "relative_name",
    [
        "engagement.json",
        "events.jsonl",
        "journal-head.json",
        "engagement-state.json",
        ".journal.lock",
    ],
)
def test_existing_authoritative_control_and_projection_mode_drift_is_rejected(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    fixed_clock,
    fixed_uuid_factory,
    relative_name,
) -> None:
    root = tmp_path / "knowledge"
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        repository.load_events(manifest.engagement_id)
    target = _engagement_path(root, manifest.engagement_id) / relative_name
    target.chmod(0o644)

    with (
        EngagementJournalRepository(root) as repository,
        pytest.raises(JournalUnavailableError, match="unsafe mode"),
    ):
        repository.load_snapshot(manifest.engagement_id)


@pytest.mark.parametrize("directory", ["root", "engagements", "engagement"])
def test_existing_repository_directory_mode_drift_is_rejected(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    fixed_clock,
    fixed_uuid_factory,
    directory,
) -> None:
    root = tmp_path / "knowledge"
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
    target = {
        "root": root,
        "engagements": root / "engagements",
        "engagement": _engagement_path(root, manifest.engagement_id),
    }[directory]
    target.chmod(0o755)

    if directory in {"root", "engagements"}:
        with pytest.raises(JournalUnavailableError, match="unsafe mode"):
            EngagementJournalRepository(root)
    else:
        with (
            EngagementJournalRepository(root) as repository,
            pytest.raises(JournalUnavailableError, match="unsafe mode"),
        ):
            repository.load_events(manifest.engagement_id)


def test_registry_lock_mode_drift_is_rejected_not_repaired(tmp_path) -> None:
    root = tmp_path / "knowledge"
    repository = EngagementJournalRepository(root)
    repository.close()
    lock = root / "engagements" / ".registry.lock"
    lock.chmod(0o644)

    with pytest.raises(JournalUnavailableError, match="unsafe mode"):
        EngagementJournalRepository(root)
    assert stat.S_IMODE(lock.stat().st_mode) == 0o644


def test_projection_symlink_is_rejected_instead_of_treated_as_missing(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    root = tmp_path / "knowledge"
    outside = tmp_path / "outside.json"
    outside.write_text("untouched", encoding="utf-8")
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
    projection = _engagement_path(root, manifest.engagement_id) / "engagement-state.json"
    projection.unlink()
    projection.symlink_to(outside)

    with (
        EngagementJournalRepository(root) as repository,
        pytest.raises(JournalUnavailableError),
    ):
        repository.load_snapshot(manifest.engagement_id)
    assert outside.read_text(encoding="utf-8") == "untouched"


def test_pending_create_intent_mode_drift_fails_before_recovery(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    fixed_clock,
    fixed_uuid_factory,
    monkeypatch,
) -> None:
    root = tmp_path / "knowledge"
    repository = _repository(root, fixed_clock, fixed_uuid_factory)

    def fail_after_intent(point: str) -> None:
        if point == "create_after_intent":
            raise OSError("injected create crash")

    monkeypatch.setattr(repository, "_fault", fail_after_intent)
    with pytest.raises(OSError, match="injected"):
        repository.create(manifest, initial_drafts(manifest, lane))
    repository.close()
    intent = (
        root
        / "engagements"
        / f".pending-create-{manifest.engagement_id}"
        / ".create-intent.json"
    )
    intent.chmod(0o644)

    with pytest.raises(JournalUnavailableError, match="unsafe mode"):
        EngagementJournalRepository(root)
    assert not _engagement_path(root, manifest.engagement_id).exists()


@pytest.mark.parametrize("intent_name", [".pending-append.json", ".tail-recovery.json"])
def test_pending_and_tail_intent_mode_drift_is_rejected_before_parsing(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    fixed_clock,
    fixed_uuid_factory,
    intent_name,
) -> None:
    root = tmp_path / "knowledge"
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
    intent = _engagement_path(root, manifest.engagement_id) / intent_name
    intent.write_bytes(b"{}")
    intent.chmod(0o644)

    with (
        EngagementJournalRepository(root) as repository,
        pytest.raises(JournalUnavailableError, match="unsafe mode"),
    ):
        repository.load_events(manifest.engagement_id)


@pytest.mark.parametrize("drift", ["directory", "object"])
def test_evidence_directory_and_object_mode_drift_is_rejected_before_capture(
    tmp_path,
    manifest,
    lane,
    initial_drafts,
    fixed_clock,
    fixed_uuid_factory,
    drift,
) -> None:
    root = tmp_path / "knowledge"
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
    engagement = _engagement_path(root, manifest.engagement_id)
    tail = b'{"interrupted":true'
    with (engagement / "events.jsonl").open("ab") as stream:
        stream.write(tail)
        stream.flush()
        os.fsync(stream.fileno())
    evidence = engagement / "evidence"
    evidence.mkdir(mode=0o700)
    if drift == "directory":
        evidence.chmod(0o755)
    else:
        blob = evidence / f"blob-{sha256(tail).hexdigest()}.bin"
        blob.write_bytes(tail)
        blob.chmod(0o644)

    with (
        EngagementJournalRepository(root) as repository,
        pytest.raises(JournalUnavailableError, match="unsafe mode"),
    ):
        repository.load_events(manifest.engagement_id)


def test_absolute_root_with_parent_traversal_component_is_rejected(tmp_path) -> None:
    unsafe = f"{tmp_path}/knowledge/../escaped"
    with pytest.raises(ValueError, match="unsafe component"):
        EngagementJournalRepository(unsafe)
    assert not (tmp_path / "escaped").exists()


def test_competing_creates_for_one_lane_publish_exactly_one_engagement(
    tmp_path,
    manifest,
    lane,
    new_lane,
    initial_drafts,
    fixed_clock,
) -> None:
    root = tmp_path / "knowledge"
    second_manifest, _ = _second_manifest_and_lane(manifest, new_lane)

    def create(candidate) -> tuple[UUID, bool]:
        try:
            with EngagementJournalRepository(root, clock=fixed_clock) as repository:
                repository.create(candidate, initial_drafts(candidate, lane))
        except ValueError:
            return candidate.engagement_id, False
        return candidate.engagement_id, True

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(create, (manifest, second_manifest)))

    assert sum(created for _, created in outcomes) == 1
    winner = next(identifier for identifier, created in outcomes if created)
    loser = next(identifier for identifier, created in outcomes if not created)
    with EngagementJournalRepository(root) as repository:
        assert len(repository.load_events(winner)) == 2
        with pytest.raises(JournalUnavailableError, match="does not exist"):
            repository.load_events(loser)


def test_competing_binds_for_one_lane_append_to_exactly_one_target(
    tmp_path,
    manifest,
    lane,
    new_lane,
    initial_drafts,
    fixed_clock,
    fixed_uuid_factory,
) -> None:
    root = tmp_path / "knowledge"
    second_manifest, second_lane = _second_manifest_and_lane(manifest, new_lane)
    reserved = new_lane(session_id="session-shared", task_id="task-shared")
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        repository.create(
            second_manifest, initial_drafts(second_manifest, second_lane)
        )

    def bind(identifier: UUID) -> tuple[UUID, bool]:
        try:
            with EngagementJournalRepository(root, clock=fixed_clock) as repository:
                repository.bind_lane(identifier, reserved, reason="concurrent reservation")
        except ValueError:
            return identifier, False
        return identifier, True

    identifiers = (manifest.engagement_id, second_manifest.engagement_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(bind, identifiers))

    assert sum(created for _, created in outcomes) == 1
    with EngagementJournalRepository(root) as repository:
        lengths = {
            identifier: len(repository.load_events(identifier))
            for identifier in identifiers
        }
    assert sorted(lengths.values()) == [2, 3]


@pytest.mark.parametrize(
    "fault_point",
    [
        "create_after_directory",
        "create_after_intent",
        "create_after_manifest",
        "create_after_journal",
        "create_after_head",
        "create_after_directory_fsync",
        "create_after_rename_before_parent_fsync",
        "create_after_parent_fsync",
        "create_after_projection",
        "create_before_response",
    ],
)
def test_every_create_crash_window_converges_to_identical_publication(
    tmp_path, manifest, lane, initial_drafts, fixed_clock, fixed_uuid_factory,
    monkeypatch, fault_point,
) -> None:
    names = ("engagement.json", "events.jsonl", "journal-head.json", "engagement-state.json")
    root = tmp_path / fault_point
    repository = _repository(root, fixed_clock, fixed_uuid_factory)
    fired = False

    def crash_once(point: str) -> None:
        nonlocal fired
        if point == fault_point and not fired:
            fired = True
            raise OSError(f"crash at {fault_point}")

    monkeypatch.setattr(repository, "_fault", crash_once)
    with pytest.raises(OSError, match="crash at"):
        repository.create(manifest, initial_drafts(manifest, lane))
    repository.close()
    with _repository(root, fixed_clock, fixed_uuid_factory) as recovered:
        snapshot = recovered.create(manifest, initial_drafts(manifest, lane))
    engagement = _engagement_path(root, manifest.engagement_id)
    journal = (engagement / "events.jsonl").read_bytes()
    head = json.loads((engagement / "journal-head.json").read_bytes())
    projection = json.loads((engagement / "engagement-state.json").read_bytes())
    assert fired and snapshot.revision.sequence == 2
    assert all((engagement / name).is_file() for name in names)
    assert head["journal_sha256"] == sha256(journal).hexdigest()
    assert projection["authoritative_revision"] == head["revision"]
    assert not (root / "engagements" / f".pending-create-{manifest.engagement_id}").exists()
    assert not (engagement / ".create-intent.json").exists()


@pytest.mark.parametrize(
    "fault_point",
    [
        "append_before_intent",
        "append_after_intent",
        "append_before_journal_write",
        "append_after_partial_journal_write",
        "append_after_complete_journal_write",
        "append_after_journal_fsync",
        "append_after_head_replace",
        "append_after_intent_clear",
        "append_after_projection",
        "append_before_response",
    ],
)
def test_every_append_crash_window_converges_to_exact_batch_and_projection(
    tmp_path, manifest, lane, initial_drafts, user_note_draft, fixed_clock,
    fixed_uuid_factory, monkeypatch, fault_point,
) -> None:
    root = tmp_path / fault_point
    draft = user_note_draft(fault_point).model_copy(
        update={
            "event_id": UUID("77777777-7777-4777-8777-777777777777"),
            "idempotency_key": f"append-window:{fault_point}",
        }
    )
    repository = _repository(root, fixed_clock, fixed_uuid_factory)
    opening = repository.create(manifest, initial_drafts(manifest, lane))
    fired = False

    def crash_once(point: str) -> None:
        nonlocal fired
        if point == fault_point and not fired:
            fired = True
            raise OSError(f"crash at {fault_point}")

    monkeypatch.setattr(repository, "_fault", crash_once)
    with pytest.raises(OSError, match="crash at"):
        repository.append_batch(
            manifest.engagement_id, (draft,), expected_revision=opening.revision
        )
    repository.close()
    with EngagementJournalRepository(root, clock=fixed_clock) as recovered:
        result = recovered.append_batch(
            manifest.engagement_id, (draft,), expected_revision=opening.revision
        )
        snapshot = recovered.load_snapshot(manifest.engagement_id)
    engagement = _engagement_path(root, manifest.engagement_id)
    projection = json.loads((engagement / "engagement-state.json").read_bytes())
    assert fired
    if fault_point == "append_before_intent":
        assert result.created_event_ids == (draft.event_id,)
    else:
        assert result.existing_event_ids == (draft.event_id,)
    assert [event.event_id for event in snapshot.events].count(draft.event_id) == 1
    assert projection["authoritative_revision"] == result.revision.model_dump(mode="json")
    assert not (engagement / ".pending-append.json").exists()


@pytest.mark.parametrize("projection_state", ["present", "missing", "stale"])
def test_hash_valid_journal_behind_head_is_corrupt_regardless_of_projection(
    tmp_path, manifest, lane, initial_drafts, user_note_draft, fixed_clock,
    fixed_uuid_factory, projection_state,
) -> None:
    root = tmp_path / projection_state
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        engagement = _engagement_path(root, manifest.engagement_id)
        prefix = (engagement / "events.jsonl").read_bytes()
        repository.append_batch(manifest.engagement_id, (user_note_draft("later"),))
    projection = engagement / "engagement-state.json"
    if projection_state == "missing":
        projection.unlink()
    elif projection_state == "stale":
        projection.write_bytes(b'{"owner":"engagement"}')
    (engagement / "events.jsonl").write_bytes(prefix)
    with EngagementJournalRepository(root) as repository, pytest.raises(
        JournalUnavailableError, match="journal_corrupt"
    ):
        repository.load_snapshot(manifest.engagement_id)


@pytest.mark.parametrize("head_bytes", [None, b"{}"])
def test_missing_or_malformed_published_head_fails_closed(
    tmp_path, manifest, lane, initial_drafts, fixed_clock, fixed_uuid_factory, head_bytes,
) -> None:
    root = tmp_path / "knowledge"
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
    head = _engagement_path(root, manifest.engagement_id) / "journal-head.json"
    head.unlink() if head_bytes is None else head.write_bytes(head_bytes)
    with EngagementJournalRepository(root) as repository, pytest.raises(
        JournalUnavailableError, match="journal_corrupt|journal head"
    ):
        repository.load_events(manifest.engagement_id)


def test_valid_newline_extension_without_matching_intent_is_corrupt(
    tmp_path, manifest, lane, initial_drafts, user_note_draft, fixed_clock,
    fixed_uuid_factory,
) -> None:
    root = tmp_path / "knowledge"
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        engagement = _engagement_path(root, manifest.engagement_id)
        base = (engagement / "events.jsonl").read_bytes()
        base_events = repository.load_events(manifest.engagement_id)
        repository.append_batch(manifest.engagement_id, (user_note_draft("valid"),))
    opening_head = repository_module._head(manifest.engagement_id, base_events, base)
    (engagement / "journal-head.json").write_bytes(repository_module._model_bytes(opening_head))
    with EngagementJournalRepository(root) as repository, pytest.raises(
        JournalUnavailableError, match="journal_corrupt"
    ):
        repository.load_events(manifest.engagement_id)


def test_tail_intent_seals_descriptor_prefix_tail_and_exact_recovery_drafts(
    tmp_path, manifest, lane, initial_drafts, fixed_clock, fixed_uuid_factory,
    monkeypatch,
) -> None:
    root = tmp_path / "knowledge"
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
    engagement = _engagement_path(root, manifest.engagement_id)
    journal = engagement / "events.jsonl"
    tail = b'{"partial":"tail"'
    with journal.open("ab") as stream:
        stream.write(tail)
        stream.flush()
        os.fsync(stream.fileno())
    with EngagementJournalRepository(root, clock=fixed_clock) as repository:
        def crash(point: str) -> None:
            if point == "tail_after_intent":
                raise OSError("sealed")

        monkeypatch.setattr(repository, "_fault", crash)
        with pytest.raises(OSError, match="sealed"):
            repository.load_events(manifest.engagement_id)
    intent = json.loads((engagement / ".tail-recovery.json").read_bytes())
    journal_stat = journal.stat()
    assert intent["engagement_id"] == str(manifest.engagement_id)
    assert intent["journal_identity"] == [journal_stat.st_dev, journal_stat.st_ino]
    assert intent["full_file_size"] == journal_stat.st_size
    assert intent["last_valid_offset"] == journal_stat.st_size - len(tail)
    assert intent["valid_prefix_revision"]["sequence"] == 2
    assert intent["tail_sha256"] == sha256(tail).hexdigest()
    assert [draft["type"] for draft in intent["drafts"]] == [
        "evidence_attached", "recovery_warning"
    ]


def test_tail_second_lock_rejects_byte_identical_journal_descriptor_replacement(
    tmp_path, manifest, lane, initial_drafts, fixed_clock, fixed_uuid_factory,
    monkeypatch,
) -> None:
    root = tmp_path / "knowledge"
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
    engagement = _engagement_path(root, manifest.engagement_id)
    journal = engagement / "events.jsonl"
    with journal.open("ab") as stream:
        stream.write(b'{"partial"')
        stream.flush()
        os.fsync(stream.fileno())
    original = journal.read_bytes()
    replaced = False
    with EngagementJournalRepository(root, clock=fixed_clock) as repository:
        def replace(point: str) -> None:
            nonlocal replaced
            if point == "tail_before_second_lock" and not replaced:
                replaced = True
                journal.rename(engagement / "old-events.jsonl")
                journal.write_bytes(original)
                journal.chmod(0o600)

        monkeypatch.setattr(repository, "_fault", replace)
        with pytest.raises(JournalUnavailableError, match="journal_corrupt"):
            repository.load_events(manifest.engagement_id)
    assert replaced and journal.read_bytes() == original


@pytest.mark.parametrize(
    "fault_point",
    [
        "tail_before_intent",
        "tail_after_intent",
        "evidence_before_temp_write",
        "evidence_after_partial_temp_write",
        "evidence_after_complete_temp_write",
        "evidence_after_file_fsync",
        "evidence_after_publication",
        "evidence_after_directory_fsync",
        "tail_before_second_lock",
        "tail_after_truncate",
        "append_after_head_replace",
        "append_after_projection",
        "tail_after_intent_clear",
    ],
)
def test_every_tail_evidence_crash_window_recovers_one_exact_pair(
    tmp_path, manifest, lane, initial_drafts, fixed_clock, fixed_uuid_factory,
    monkeypatch, fault_point,
) -> None:
    root = tmp_path / fault_point
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
    engagement = _engagement_path(root, manifest.engagement_id)
    journal = engagement / "events.jsonl"
    tail = f'{{"partial":"{fault_point}"'.encode()
    with journal.open("ab") as stream:
        stream.write(tail)
        stream.flush()
        os.fsync(stream.fileno())
    fired = False
    with EngagementJournalRepository(root, clock=fixed_clock) as repository:
        def crash_once(point: str) -> None:
            nonlocal fired
            if point == fault_point and not fired:
                fired = True
                raise OSError(f"crash at {fault_point}")

        monkeypatch.setattr(repository, "_fault", crash_once)
        with pytest.raises(OSError, match="crash at"):
            repository.load_events(manifest.engagement_id)
    with EngagementJournalRepository(root, clock=fixed_clock) as recovered:
        events = recovered.load_events(manifest.engagement_id)
        snapshot = recovered.load_snapshot(manifest.engagement_id)
    assert fired
    assert [event.type.value for event in events[-2:]] == [
        "evidence_attached", "recovery_warning"
    ]
    assert len([event for event in events if event.type.value == "evidence_attached"]) == 1
    blobs = list((engagement / "evidence").glob("blob-*.bin"))
    assert [blob.read_bytes() for blob in blobs] == [tail]
    assert not list((engagement / "evidence").glob(".pending-blob-*.bin"))
    projection = json.loads((engagement / "engagement-state.json").read_bytes())
    assert projection["authoritative_revision"] == snapshot.revision.model_dump(mode="json")


def test_tail_recovery_wins_deterministic_private_capture_quota_race(
    tmp_path, manifest, lane, initial_drafts, fixed_clock, fixed_uuid_factory,
    monkeypatch,
) -> None:
    root = tmp_path / "knowledge"
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
    engagement = _engagement_path(root, manifest.engagement_id)
    journal = engagement / "events.jsonl"
    tail = b'{"quota-race"'
    with journal.open("ab") as stream:
        stream.write(tail)
        stream.flush()
        os.fsync(stream.fileno())
    monkeypatch.setattr(repository_module, "MAX_EVIDENCE_OBJECTS", 1)
    monkeypatch.setattr(repository_module, "MAX_EVIDENCE_ENGAGEMENT_BYTES", len(tail))
    rendezvous = Barrier(2)
    tail_done = Event()
    real_capture = repository_module._EvidenceObjectStore.capture

    def ordered_capture(store, data):
        rendezvous.wait(timeout=2)
        if data != tail:
            assert tail_done.wait(timeout=2)
            return real_capture(store, data)
        try:
            return real_capture(store, data)
        finally:
            tail_done.set()

    monkeypatch.setattr(repository_module._EvidenceObjectStore, "capture", ordered_capture)

    def recover_tail():
        with EngagementJournalRepository(root, clock=fixed_clock) as repository:
            return repository.load_events(manifest.engagement_id)

    def capture_ordinary():
        with EngagementJournalRepository(root) as repository:
            engagement_fd = repository._engagement_fd(manifest.engagement_id)
            try:
                return repository_module._EvidenceObjectStore(engagement_fd).capture(
                    b"ordinary-private"
                )
            finally:
                os.close(engagement_fd)

    with ThreadPoolExecutor(max_workers=2) as pool:
        tail_future = pool.submit(recover_tail)
        ordinary_future = pool.submit(capture_ordinary)
        events = tail_future.result(timeout=5)
        with pytest.raises(ValueError, match="quota"):
            ordinary_future.result(timeout=5)
    assert [event.type.value for event in events[-2:]] == [
        "evidence_attached", "recovery_warning"
    ]
    assert [item.read_bytes() for item in (engagement / "evidence").glob("blob-*.bin")] == [tail]
    assert not list((engagement / "evidence").glob(".pending-blob-*.bin"))


def test_projection_envelopes_have_digest_cas_and_exact_bounds_for_sealed_names(
    tmp_path, manifest, lane, initial_drafts, fixed_clock, fixed_uuid_factory,
    monkeypatch,
) -> None:
    root = tmp_path / "knowledge"
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        snapshot = repository.create(manifest, initial_drafts(manifest, lane))
        engagement = _engagement_path(root, manifest.engagement_id)
        engagement_projection = repository.load_projection(
            manifest.engagement_id, name="engagement-state", owner="engagement"
        )
        assert engagement_projection is not None
        for name in ("state", "frontier", "strategy-ledger"):
            repository.write_projection(
                manifest.engagement_id,
                name=name,
                owner="planning",
                envelope={"payload": {"name": name}},
                expected_revision=snapshot.revision,
            )
            loaded = repository.load_projection(
                manifest.engagement_id, name=name, owner="planning"
            )
            assert loaded is not None
            digest = loaded.pop("projection_digest")
            assert digest == sha256(repository_module._canonical_json(loaded)).hexdigest()
        with pytest.raises(RevisionConflictError):
            repository.write_projection(
                manifest.engagement_id,
                name="state",
                owner="planning",
                envelope={"payload": {}},
                expected_revision=JournalRevision(sequence=0, event_hash="0" * 64),
            )
        value = {
            "payload": {"large": "x" * 2_000},
            "owner": "planning",
            "authoritative_revision": snapshot.revision.model_dump(mode="json"),
            "name": "state",
        }
        exact = repository_module._canonical_projection_envelope(value)
        monkeypatch.setattr(repository_module, "MAX_DERIVED_PROJECTION_BYTES", len(exact))
        repository.write_projection(
            manifest.engagement_id,
            name="state",
            owner="planning",
            envelope={"payload": {"large": "x" * 2_000}},
        )
        target = engagement / "state.json"
        before = target.read_bytes()
        monkeypatch.setattr(repository_module, "MAX_DERIVED_PROJECTION_BYTES", len(exact) - 1)
        with pytest.raises(ValueError, match="projection exceeds"):
            repository.write_projection(
                manifest.engagement_id,
                name="state",
                owner="planning",
                envelope={"payload": {"large": "x" * 2_000}},
            )
        assert target.read_bytes() == before


def test_pending_append_revalidates_recovered_totals_before_any_mutation(
    tmp_path, manifest, lane, initial_drafts, user_note_draft, fixed_clock,
    fixed_uuid_factory, monkeypatch,
) -> None:
    root = tmp_path / "knowledge"
    repository = _repository(root, fixed_clock, fixed_uuid_factory)
    repository.create(manifest, initial_drafts(manifest, lane))
    engagement = _engagement_path(root, manifest.engagement_id)
    before = (engagement / "events.jsonl").read_bytes()

    def crash(point: str) -> None:
        if point == "append_after_intent":
            raise OSError("intent durable")

    monkeypatch.setattr(repository, "_fault", crash)
    with pytest.raises(OSError, match="intent durable"):
        repository.append_batch(manifest.engagement_id, (user_note_draft("over-cap"),))
    repository.close()
    monkeypatch.setattr(repository_module, "MAX_JOURNAL_EVENTS", 2)
    with EngagementJournalRepository(root) as recovered, pytest.raises(
        JournalUnavailableError, match="exceeds limits"
    ):
        recovered.load_events(manifest.engagement_id)
    assert (engagement / "events.jsonl").read_bytes() == before
    assert (engagement / ".pending-append.json").is_file()


def test_oversized_engagement_projection_is_rebuilt_from_authoritative_journal(
    tmp_path, manifest, lane, initial_drafts, fixed_clock, fixed_uuid_factory,
    monkeypatch,
) -> None:
    root = tmp_path / "knowledge"
    with _repository(root, fixed_clock, fixed_uuid_factory) as repository:
        expected = repository.create(manifest, initial_drafts(manifest, lane))
    projection = _engagement_path(root, manifest.engagement_id) / "engagement-state.json"
    canonical = projection.read_bytes()
    projection.write_bytes(canonical + b"x")
    monkeypatch.setattr(
        repository_module, "MAX_DERIVED_PROJECTION_BYTES", len(canonical)
    )
    with EngagementJournalRepository(root) as recovered:
        snapshot = recovered.load_snapshot(manifest.engagement_id)
    assert snapshot == expected
    assert projection.read_bytes() == canonical


@pytest.mark.parametrize("head_bytes", [None, b"not-json"])
def test_exact_pending_append_recovers_missing_or_malformed_head(
    tmp_path, manifest, lane, initial_drafts, user_note_draft, fixed_clock,
    fixed_uuid_factory, monkeypatch, head_bytes,
) -> None:
    root = tmp_path / "knowledge"
    repository = _repository(root, fixed_clock, fixed_uuid_factory)
    repository.create(manifest, initial_drafts(manifest, lane))

    def crash(point: str) -> None:
        if point == "append_after_complete_journal_write":
            raise OSError("journal complete")

    monkeypatch.setattr(repository, "_fault", crash)
    with pytest.raises(OSError, match="journal complete"):
        repository.append_batch(manifest.engagement_id, (user_note_draft("recover-head"),))
    repository.close()
    head = _engagement_path(root, manifest.engagement_id) / "journal-head.json"
    if head_bytes is None:
        head.unlink()
    else:
        head.write_bytes(head_bytes)
    with EngagementJournalRepository(root) as recovered:
        events = recovered.load_events(manifest.engagement_id)
    assert len(events) == 3
    assert events[-1].payload.note == "recover-head"
