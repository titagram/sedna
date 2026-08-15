"""Crash recovery and confinement for immutable promotion source commits."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sedna.engagement.events import PromotionCandidateReadyPayload, PromotionRequestedPayload
from sedna.engagement.promotion.adapter import PromotionCommitCapability
from sedna.engagement.promotion.models import PromotionClaimOwnership
from sedna.engagement.promotion.render import render_promotion_source
from sedna.engagement.repository import EngagementJournalRepository, JournalUnavailableError

from .test_promotion_render import _context, _draft, _identity


def _rendered():
    from sedna.engagement.promotion.models import PromotionSecretInventory

    return render_promotion_source(
        _draft(),
        context=_context(),
        inventory=PromotionSecretInventory(),
        identity=_identity(),
    )


def _created_repository(root, manifest, lane, initial_drafts, fixed_clock):
    repository = EngagementJournalRepository(root, clock=fixed_clock)
    snapshot = repository.create(manifest, initial_drafts(manifest, lane))
    capability = PromotionCommitCapability(repository._issue_promotion_journal_writer())
    identity = _identity()
    requested = repository._append_promotion_event(
        manifest.engagement_id,
        PromotionRequestedPayload(
            attempt_id=identity.attempt_id,
            attempt_ordinal=1,
            promotion_revision=1,
            idempotency_key="1" * 64,
            verified_revision=identity.verified_revision,
            verification_event_id=identity.verification_event_id,
            compiler_version="1",
            extractor_prompt_version="1",
            critic_prompt_version="1",
            repair_prompt_version="1",
            renderer_version="1",
            semantic_compiler_version="1",
            semantic_prompt_versions=("1",),
            claim_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        ),
        expected_revision=snapshot.revision,
    )
    candidate = repository._append_promotion_event(
        manifest.engagement_id,
        PromotionCandidateReadyPayload(
            attempt_id=identity.attempt_id,
            promotion_revision=1,
            candidate_relative_path=(
                f"engagements/{manifest.engagement_id}/promotion/candidate-case-v1.json"
            ),
            candidate_sha256="2" * 64,
            repair_count=0,
        ),
        expected_revision=requested.revision,
    )
    snapshot = repository.load_snapshot(manifest.engagement_id)
    assert snapshot.revision == candidate.revision
    ownership = PromotionClaimOwnership(
        attempt_id=identity.attempt_id,
        claim_event_id=requested.events[0].event_id,
        _issuer_token=capability._issuer_token,
    )
    return repository, snapshot, capability, ownership


def test_source_commit_is_descriptor_confined_and_idempotent(
    tmp_path, manifest, lane, initial_drafts, fixed_clock
) -> None:
    root = tmp_path / "knowledge"
    rendered = _rendered()
    repository, snapshot, capability, ownership = _created_repository(
        root, manifest, lane, initial_drafts, fixed_clock
    )
    with repository:
        first = capability.commit_source(
            manifest.engagement_id,
            rendered,
            ownership=ownership,
            expected_revision=snapshot.revision,
        )
        retried = capability.commit_source(
            manifest.engagement_id,
            rendered,
            ownership=ownership,
            expected_revision=first.source.committed_revision,
        )

        assert first.created is True
        assert retried.created is False
        assert retried.event_id == first.event_id
        assert (root / rendered.source_relative_path).read_bytes() == rendered.markdown.encode()
        assert (root / rendered.provenance_relative_path).read_bytes()
        assert not (
            root
            / "engagements"
            / str(manifest.engagement_id)
            / "promotion"
            / "sources"
            / ".source-transaction.json"
        ).exists()


@pytest.mark.parametrize(
    "fault_point",
    [
        "promotion_source_after_intent",
        "promotion_source_after_source",
        "promotion_source_after_provenance",
        "promotion_source_before_event_append",
        "promotion_source_after_event_append",
    ],
)
def test_source_commit_recovers_every_declared_crash_boundary(
    tmp_path, manifest, lane, initial_drafts, fixed_clock, monkeypatch, fault_point
) -> None:
    root = tmp_path / "knowledge"
    rendered = _rendered()
    repository, snapshot, capability, ownership = _created_repository(
        root, manifest, lane, initial_drafts, fixed_clock
    )
    with repository:
        fired = False

        def crash(point: str) -> None:
            nonlocal fired
            if point == fault_point and not fired:
                fired = True
                raise RuntimeError("simulated crash")

        monkeypatch.setattr(repository, "_fault", crash)
        with pytest.raises(RuntimeError, match="simulated crash"):
            capability.commit_source(
                manifest.engagement_id,
                rendered,
                ownership=ownership,
                expected_revision=snapshot.revision,
            )
        monkeypatch.setattr(repository, "_fault", lambda _point: None)

        recovered = capability.commit_source(
            manifest.engagement_id,
            rendered,
            ownership=ownership,
            expected_revision=snapshot.revision,
        )
        loaded = repository.load_snapshot(manifest.engagement_id)

        assert loaded.revision == recovered.source.committed_revision
        assert sum(event.type == "promotion_source_committed" for event in loaded.events) == 1
        assert (root / rendered.source_relative_path).read_bytes() == rendered.markdown.encode()


def test_source_commit_rejects_symlinked_storage_directory_before_mutation(
    tmp_path, manifest, lane, initial_drafts, fixed_clock
) -> None:
    root = tmp_path / "knowledge"
    outside = tmp_path / "outside"
    outside.mkdir()
    repository, snapshot, capability, ownership = _created_repository(
        root, manifest, lane, initial_drafts, fixed_clock
    )
    engagement = root / "engagements" / str(manifest.engagement_id)
    (engagement / "promotion").mkdir(mode=0o700)
    (engagement / "promotion" / "sources").symlink_to(outside, target_is_directory=True)

    with repository, pytest.raises(OSError):
        capability.commit_source(
            manifest.engagement_id,
            _rendered(),
            ownership=ownership,
            expected_revision=snapshot.revision,
        )
    assert list(outside.iterdir()) == []
    with EngagementJournalRepository(root) as reopened:
        assert reopened.load_snapshot(manifest.engagement_id).revision == snapshot.revision


def test_source_retry_rejects_tampered_event_bound_artifact(
    tmp_path, manifest, lane, initial_drafts, fixed_clock
) -> None:
    root = tmp_path / "knowledge"
    rendered = _rendered()
    repository, snapshot, capability, ownership = _created_repository(
        root, manifest, lane, initial_drafts, fixed_clock
    )
    with repository:
        committed = capability.commit_source(
            manifest.engagement_id,
            rendered,
            ownership=ownership,
            expected_revision=snapshot.revision,
        )
        Path(root / rendered.source_relative_path).write_bytes(b"tampered")
        with pytest.raises(JournalUnavailableError, match="artifact conflicts"):
            capability.commit_source(
                manifest.engagement_id,
                rendered,
                ownership=ownership,
                expected_revision=committed.source.committed_revision,
            )
