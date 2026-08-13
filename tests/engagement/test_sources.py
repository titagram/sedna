from __future__ import annotations

import inspect

import pytest

import sedna.engagement.sources as sources_module
from sedna.engagement.repository import EngagementJournalRepository
from sedna.engagement.sources import (
    SharedSourceEntry,
    SharedSourceRegistry,
    SourceRegistryLimitError,
)


def source_entry(
    locator: str, *, topics: tuple[str, ...] = ("web",), name: str | None = None
) -> SharedSourceEntry:
    display = name or locator.split("//")[-1].split("/")[0] or "source"
    return SharedSourceEntry.suggested(
        name=display,
        locator=locator,
        topics=topics,
        notes="Useful orientation; validate claims against current evidence.",
    )


def test_registry_adds_readable_machine_block_and_preserves_manual_bytes(tmp_path) -> None:
    root = tmp_path / "knowledge"
    root.mkdir(mode=0o700)
    manual = "# My sources\n\nThis paragraph is maintained by the user.\n"
    (root / "sources.md").write_text(manual, encoding="utf-8")

    with EngagementJournalRepository(root) as repository:
        result = SharedSourceRegistry(repository).add_or_update(
            SharedSourceEntry.suggested(
                name="HackTricks",
                locator="https://book.hacktricks.wiki/",
                topics=("web", "linux", "active directory"),
                notes="Useful orientation; validate claims against current evidence.",
            )
        )

    rendered = (root / "sources.md").read_text(encoding="utf-8")
    assert rendered.startswith(manual)
    assert f"<!-- sedna-source:v1 begin {result.entry.source_id} -->" in rendered
    assert "### Source" in rendered
    assert "HackTricks" in rendered
    assert "https://book.hacktricks.wiki/" in rendered
    assert f"<!-- sedna-source:v1 end {result.entry.source_id} -->" in rendered


def test_same_normalized_locator_is_idempotent_and_changed_machine_block_is_replaced(
    tmp_path,
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalRepository(root) as repository:
        registry = SharedSourceRegistry(repository)
        first = registry.add_or_update(source_entry("https://example.test/docs"))
        same = registry.add_or_update(source_entry("https://example.test/docs"))
        changed = registry.add_or_update(
            source_entry("https://example.test/docs", topics=("windows",))
        )
    rendered = (root / "sources.md").read_text(encoding="utf-8")

    assert first.changed is True
    assert same.changed is False
    assert changed.changed is True
    assert rendered.count("sedna-source:v1 begin") == 1
    assert "windows" in rendered


def test_malformed_machine_block_fails_without_rewriting_manual_content(tmp_path) -> None:
    root = tmp_path / "knowledge"
    root.mkdir(mode=0o700)
    original = "manual\n<!-- sedna-source:v1 begin broken -->\nunterminated\n"
    (root / "sources.md").write_text(original, encoding="utf-8")
    with (
        EngagementJournalRepository(root) as repository,
        pytest.raises(ValueError, match="invalid managed source block"),
    ):
        SharedSourceRegistry(repository).add_or_update(source_entry("https://example.test"))
    assert (root / "sources.md").read_text(encoding="utf-8") == original


def test_snapshot_and_list_entries_are_atomic_and_bounded(tmp_path) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalRepository(root) as repository:
        registry = SharedSourceRegistry(repository)
        registry.add_or_update(source_entry("https://example.test/one"))
        snapshot = registry.snapshot()
        listed = registry.list_entries()

    assert snapshot.entries == listed
    assert snapshot.content_sha256
    assert snapshot.byte_size <= 1024 * 1024


def test_planner_snapshot_is_managed_only_bounded_and_preserves_full_api(tmp_path) -> None:
    root = tmp_path / "knowledge"
    root.mkdir(mode=0o700)
    manual = b"# private manual prose\n"
    (root / "sources.md").write_bytes(manual)
    with EngagementJournalRepository(root) as repository:
        registry = SharedSourceRegistry(repository)
        registry.add_or_update(source_entry("https://example.test/linux", topics=("linux",)))
        planner = registry.planner_snapshot()

    assert tuple(inspect.signature(SharedSourceRegistry.snapshot).parameters) == ("self",)
    assert tuple(inspect.signature(SharedSourceRegistry.list_entries).parameters) == ("self",)
    assert len(planner.entries) == 1
    assert planner.total_count == 1
    assert planner.truncated is False
    assert planner.omitted_entries_sha256 is None
    assert planner.canonical_bytes <= 64 * 1024
    assert b"private manual prose" not in planner.model_dump_json().encode()
    assert (root / "sources.md").read_bytes().startswith(manual)


def test_planner_hints_exclude_known_incompatible_platform(tmp_path) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalRepository(root) as repository:
        registry = SharedSourceRegistry(repository)
        registry.add_or_update(source_entry("https://example.test/linux", topics=("linux", "http")))
        registry.add_or_update(
            source_entry("https://example.test/windows", topics=("windows", "http"))
        )

        page = registry.list_planner_hints(topic_tokens=("linux", "http"))

    assert [entry.locator for entry in page.entries] == ["https://example.test/linux"]
    assert page.total_count == 2
    assert page.truncated is True


def test_oversized_registry_fails_before_unbounded_parse(tmp_path) -> None:
    root = tmp_path / "knowledge"
    root.mkdir(mode=0o700)
    (root / "sources.md").write_bytes(b"x" * (1024 * 1024 + 1))
    with (
        EngagementJournalRepository(root) as repository,
        pytest.raises(SourceRegistryLimitError, match="byte_limit_exceeded"),
    ):
        SharedSourceRegistry(repository).snapshot()


def test_managed_entry_count_limit_is_enforced_before_write(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "knowledge"
    monkeypatch.setattr(sources_module, "MAX_SOURCE_REGISTRY_ENTRIES", 2)
    with EngagementJournalRepository(root) as repository:
        registry = SharedSourceRegistry(repository)
        registry.add_or_update(source_entry("https://example.test/one"))
        registry.add_or_update(source_entry("https://example.test/two"))
        with pytest.raises(SourceRegistryLimitError, match="entry_limit_exceeded"):
            registry.add_or_update(source_entry("https://example.test/three"))
    rendered = (root / "sources.md").read_text(encoding="utf-8")
    assert rendered.count("sedna-source:v1 begin") == 2


def test_symlinked_sources_md_is_rejected(tmp_path) -> None:
    root = tmp_path / "knowledge"
    root.mkdir(mode=0o700)
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (root / "sources.md").symlink_to(outside)
    with EngagementJournalRepository(root) as repository, pytest.raises(
        ValueError, match="unsafe file"
    ):
        SharedSourceRegistry(repository).snapshot()


def test_unsafe_marker_tokens_in_user_fields_are_rejected(tmp_path) -> None:
    root = tmp_path / "knowledge"
    with (
        EngagementJournalRepository(root) as repository,
        pytest.raises(ValueError, match="managed source marker"),
    ):
        SharedSourceRegistry(repository).add_or_update(
            source_entry("https://example.test", name="<!-- sedna-source: evil")
        )


def test_external_manual_edit_between_read_and_replace_is_preserved_or_conflicts(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "knowledge"
    root.mkdir(mode=0o700)
    manual = "# Manual\n"
    (root / "sources.md").write_text(manual, encoding="utf-8")
    with EngagementJournalRepository(root) as repository:
        registry = SharedSourceRegistry(repository)
        original_read = sources_module.SharedSourceRegistry._read_current
        calls = {"count": 0}

        def edit_between_reads(self) -> bytes:
            calls["count"] += 1
            data = original_read(self)
            if calls["count"] == 2:
                data += b"\nUser typed this during the write.\n"
            return data

        monkeypatch.setattr(
            sources_module.SharedSourceRegistry, "_read_current", edit_between_reads
        )
        result = registry.add_or_update(source_entry("https://example.test/edit"))

    rendered = (root / "sources.md").read_text(encoding="utf-8")
    assert result.changed is True
    assert "User typed this during the write." in rendered
    assert "sedna-source:v1 begin" in rendered


def test_duplicate_source_id_collision_fails_closed(tmp_path) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalRepository(root) as repository:
        registry = SharedSourceRegistry(repository)
        first = registry.add_or_update(source_entry("https://example.test/same"))
        duplicate = SharedSourceEntry.suggested(
            name="Other",
            locator="HTTPS://EXAMPLE.TEST/same",
            topics=("other",),
        )
        assert duplicate.source_id == first.entry.source_id
        replaced = registry.add_or_update(duplicate)
    assert replaced.changed is True
    rendered = (root / "sources.md").read_text(encoding="utf-8")
    assert rendered.count("sedna-source:v1 begin") == 1
