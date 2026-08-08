"""Behavioral tests for the disposable SQLite retrieval projection."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import stat
import threading
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

import pytest

from sedna.knowledge.retrieval import (
    AuthorizationScope,
    AuthorizationState,
    CurrentSituation,
    EpistemicLane,
    RetrievalIndex,
    RetrievalQuery,
    SituationFacet,
    ValidatedTarget,
)
from sedna.knowledge.retrieval import sqlite as retrieval_sqlite_module
from sedna.knowledge.retrieval.sqlite import SQLiteRetrievalIndex
from sedna.knowledge.schema import SemanticKnowledgeBundle
from tests.knowledge.test_retrieval_projection import _bundle


def _query(
    *terms: str,
    facets: tuple[SituationFacet, ...] = (),
    situation_terms: tuple[str, ...] = (),
    max_candidates: int = 32,
) -> RetrievalQuery:
    target = ValidatedTarget.parse("10.10.10.10")
    return RetrievalQuery(
        situation=CurrentSituation(
            target=target,
            authorization=AuthorizationScope(
                state=AuthorizationState.AUTHORIZED,
                exact_targets=(target,),
            ),
            terms=situation_terms,
        ),
        terms=terms,
        facets=facets,
        max_candidates=max_candidates,
    )


def _renamed_bundle(source_id: str, suffix: str) -> SemanticKnowledgeBundle:
    payload = _bundle().model_dump(mode="json")

    def replace_source(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "source_id" and item == "source-retrieval":
                    value[key] = source_id
                else:
                    replace_source(item)
        elif isinstance(value, list):
            for item in value:
                replace_source(item)

    replace_source(payload)
    manifest = payload["compilation_manifest"]
    id_fields = {
        "case-negative": f"case-negative-{suffix}",
        "case-negative-step": f"case-negative-step-{suffix}",
        "case-positive": f"case-positive-{suffix}",
        "case-positive-step": f"case-positive-step-{suffix}",
        "reference-http": f"reference-http-{suffix}",
        "rule-http": f"rule-http-{suffix}",
    }
    manifest["emitted_artifact_ids"] = [
        id_fields[identifier] for identifier in manifest["emitted_artifact_ids"]
    ]
    payload["references"][0]["artifact_id"] = id_fields["reference-http"]
    for case in payload["cases"]:
        old_case_id = case["case_id"]
        case["case_id"] = id_fields[old_case_id]
        for step in case["steps"]:
            step["step_id"] = id_fields[step["step_id"]]
    payload["guidance"][0]["rule_id"] = id_fields["rule-http"]
    return SemanticKnowledgeBundle.model_validate(payload)


def _table_rows(path: Path, table: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(path) as connection:
        return connection.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()


def test_schema_uses_fts5_foreign_keys_indexes_and_a_version(tmp_path: Path) -> None:
    path = tmp_path / "indexes" / "sedna.sqlite"

    with SQLiteRetrievalIndex(path) as index:
        assert isinstance(index, RetrievalIndex)
        index.upsert_bundle(_bundle())

        connection = index._connection
        assert connection is not None
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        tables = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        assert {
            "artifacts",
            "facet_values",
            "artifact_links",
            "artifact_sources",
            "indexed_sources",
        } <= tables.keys()
        assert "USING fts5" in tables["artifact_fts"]
        indexes = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
        assert {
            "artifacts_owner_source_idx",
            "artifacts_lane_idx",
            "facet_values_lookup_idx",
            "artifact_links_target_idx",
            "artifact_sources_source_idx",
        } <= indexes
        assert [
            row[0] for row in connection.execute("SELECT DISTINCT canonical_path FROM artifacts")
        ] == ["semantic_bundles/source-retrieval.json"]

    assert [row[0] for row in _table_rows(path, "artifacts")] == [
        "case-negative",
        "case-negative-step",
        "case-positive",
        "case-positive-step",
        "reference-http",
        "rule-http",
    ]
    assert len(_table_rows(path, "facet_values")) == 24
    assert len(_table_rows(path, "artifact_links")) == 2
    assert len(_table_rows(path, "artifact_sources")) >= 6
    assert len(_table_rows(path, "artifact_fts")) == 6


def test_source_states_bind_hash_artifact_count_digest_and_support_bounded_paging(
    tmp_path: Path,
) -> None:
    first = _renamed_bundle("source-first", "a")
    second = _renamed_bundle("source-second", "b")

    with SQLiteRetrievalIndex(tmp_path / "sedna.sqlite") as index:
        index.rebuild((second, first))
        first_page = index.list_source_states(after_source_id=None, limit=1)
        second_page = index.list_source_states(
            after_source_id=first_page[-1].source_id,
            limit=1,
        )

        assert [state.source_id for state in first_page + second_page] == [
            "source-first",
            "source-second",
        ]
        assert all(state.artifact_count == 6 for state in first_page + second_page)
        assert all(len(state.projection_digest) == 64 for state in first_page + second_page)
        assert [state.source_sha256 for state in first_page + second_page] == [
            first.source_sha256,
            second.source_sha256,
        ]
        assert index.list_source_states(after_source_id="source-second", limit=1) == ()
        with pytest.raises(ValueError, match="limit"):
            index.list_source_states(after_source_id=None, limit=0)


def test_hash_only_bundle_change_is_visible_in_source_state(tmp_path: Path) -> None:
    original = _bundle()
    payload = original.model_dump(mode="json")
    payload["source_sha256"] = "b" * 64
    payload["compilation_manifest"]["source_sha256"] = "b" * 64
    changed = SemanticKnowledgeBundle.model_validate(payload)

    with SQLiteRetrievalIndex(tmp_path / "sedna.sqlite") as index:
        index.upsert_bundle(original)
        before = index.list_source_states(after_source_id=None, limit=10)[0]
        index.upsert_bundle(changed)
        after = index.list_source_states(after_source_id=None, limit=10)[0]

        assert index.get_artifact("reference-http") == original.references[0]
        assert after.source_sha256 == "b" * 64
        assert after.projection_digest != before.projection_digest


def test_source_state_corruption_requires_rebuild_and_rebuild_replaces_old_schema(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sedna.sqlite"
    index = SQLiteRetrievalIndex(path)
    index.upsert_bundle(_bundle())
    index.close()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE indexed_sources SET source_sha256 = ? WHERE source_id = ?",
            ("b" * 64, "source-retrieval"),
        )

    with SQLiteRetrievalIndex(path) as reopened:
        audit = reopened.audit()
        assert audit.rebuild_required
        assert "source_projection_mismatch" in audit.issues
        reopened.rebuild((_bundle(),))
        assert reopened.audit().rebuild_required is False

    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE indexed_sources")
        connection.execute("PRAGMA user_version = 2")

    with SQLiteRetrievalIndex(path) as old_schema:
        assert old_schema.audit().rebuild_required
        old_schema.rebuild((_bundle(),))
        assert old_schema.audit().rebuild_required is False


def test_existing_empty_regular_target_is_initialized_as_a_new_index(tmp_path: Path) -> None:
    path = tmp_path / "sedna.sqlite"
    path.touch()

    with SQLiteRetrievalIndex(path) as index:
        audit = index.audit()
        assert audit.rebuild_required is False
        assert audit.issues == ()


def test_failed_initialization_restores_an_existing_empty_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sedna.sqlite"
    path.touch()
    real_fsync = os.fsync
    failed = False

    def fail_first_directory_sync(descriptor: int) -> None:
        nonlocal failed
        if stat.S_ISDIR(os.fstat(descriptor).st_mode) and not failed:
            failed = True
            raise OSError("injected initialization sync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_first_directory_sync)
    with pytest.raises(OSError, match="initialization sync failure"):
        SQLiteRetrievalIndex(path)

    assert failed
    assert path.read_bytes() == b""
    assert not list(tmp_path.glob(".*sedna.sqlite*.backup*"))


def test_source_upsert_is_complete_and_rolls_back_after_injected_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sedna.sqlite"
    original = _bundle()
    updated_payload = original.model_dump(mode="json")
    updated_payload["references"][0]["statement"] = "replacement-only-token"
    updated = SemanticKnowledgeBundle.model_validate(updated_payload)

    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(original)
        before = path.read_bytes()
        original_insert = index._insert_projection_rows

        def fail_after_source_delete(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise RuntimeError("injected insertion failure")

        monkeypatch.setattr(index, "_insert_projection_rows", fail_after_source_delete)
        with pytest.raises(RuntimeError, match="injected"):
            index.upsert_bundle(updated)
        monkeypatch.setattr(index, "_insert_projection_rows", original_insert)

        assert index.get_artifact("reference-http") == original.references[0]
        assert index.audit().rebuild_required is False
        index.upsert_bundle(updated)
        assert index.get_artifact("reference-http") == updated.references[0]

    assert path.read_bytes() != before


def test_delete_source_removes_only_its_complete_projection(tmp_path: Path) -> None:
    first = _renamed_bundle("source-first", "a")
    second = _renamed_bundle("source-second", "b")

    with SQLiteRetrievalIndex(tmp_path / "sedna.sqlite") as index:
        index.upsert_bundle(first)
        index.upsert_bundle(second)
        index.delete_source("source-first")

        assert index.get_artifact("reference-http-a") is None
        assert index.get_artifact("reference-http-b") == second.references[0]
        audit = index.audit()
        assert audit.artifact_count == 6
        assert audit.source_count == 1
        assert audit.facet_count == 24
        assert audit.fts_count == 6
        assert audit.rebuild_required is False


def test_get_artifact_revalidates_canonical_json_and_identity(tmp_path: Path) -> None:
    path = tmp_path / "sedna.sqlite"
    index = SQLiteRetrievalIndex(path)
    index.upsert_bundle(_bundle())
    assert index.get_artifact("reference-http") == _bundle().references[0]
    index.close()

    with sqlite3.connect(path) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT canonical_json FROM artifacts WHERE artifact_id = 'reference-http'"
            ).fetchone()[0]
        )
        payload["statement"] = "HTB{unsafe_reconstruction}"
        connection.execute(
            "UPDATE artifacts SET canonical_json = ? WHERE artifact_id = 'reference-http'",
            (json.dumps(payload),),
        )

    with SQLiteRetrievalIndex(path) as reopened:
        with pytest.raises(ValueError, match="canonical artifact"):
            reopened.get_artifact("reference-http")
        audit = reopened.audit()
        assert audit.corruption_count >= 1
        assert audit.rebuild_required


def test_search_is_lane_scoped_explainable_bounded_and_deterministic(tmp_path: Path) -> None:
    first = _renamed_bundle("source-first", "a")
    second = _renamed_bundle("source-second", "b")

    with SQLiteRetrievalIndex(tmp_path / "sedna.sqlite") as index:
        index.upsert_bundle(first)
        index.upsert_bundle(second)

        hits = index.search_candidates(
            _query("reference-statement-token"),
            lane=EpistemicLane.REFERENCE,
            limit=20,
        )
        assert [hit.artifact_id for hit in hits] == ["reference-http-a", "reference-http-b"]
        assert all(math.isfinite(hit.lexical_relevance) for hit in hits)
        assert all(0.0 <= hit.lexical_relevance <= 1.0 for hit in hits)
        assert hits[0].lexical_relevance == hits[1].lexical_relevance
        assert hits[0].matched_terms == ("reference-statement-token",)
        assert "statement" in hits[0].matched_fields
        assert any("reference-statement-token" in item for item in hits[0].matched_evidence)

        assert {
            candidate.artifact_id
            for candidate in index.search_candidates(
                _query("step-action-token"), lane=EpistemicLane.CASE_STEP, limit=20
            )
        } == {"case-positive-step-a", "case-positive-step-b"}
        assert {
            candidate.artifact_id
            for candidate in index.search_candidates(
                _query("step-action-token"), lane=EpistemicLane.NEGATIVE_EVIDENCE, limit=20
            )
        } == {"case-negative-step-a", "case-negative-step-b"}
        assert {
            candidate.artifact_id
            for candidate in index.search_candidates(
                _query("rule-action-token"), lane=EpistemicLane.GUIDANCE, limit=20
            )
        } == {"rule-http-a", "rule-http-b"}
        assert (
            len(
                index.search_candidates(
                    _query("token", max_candidates=1), lane=EpistemicLane.REFERENCE, limit=20
                )
            )
            == 1
        )
        with pytest.raises(ValueError, match="limit"):
            index.search_candidates(_query("token"), lane=EpistemicLane.REFERENCE, limit=0)


def test_search_uses_safe_fts_terms_and_exact_facet_prefilters(tmp_path: Path) -> None:
    with SQLiteRetrievalIndex(tmp_path / "sedna.sqlite") as index:
        index.upsert_bundle(_bundle())

        hostile = index.search_candidates(
            _query('reference OR "unterminated); DROP TABLE artifacts; --'),
            lane=EpistemicLane.REFERENCE,
            limit=10,
        )
        assert isinstance(hostile, tuple)
        assert index.get_artifact("reference-http") is not None

        matching_facet = SituationFacet(
            namespace="typed", key="os_family", value="linux", confidence=1.0
        )
        mismatching_facet = matching_facet.model_copy(update={"value": "windows"})
        facet_hits = index.search_candidates(
            _query(facets=(matching_facet,)), lane=EpistemicLane.REFERENCE, limit=20
        )
        assert [candidate.artifact_id for candidate in facet_hits] == ["reference-http"]
        assert facet_hits[0].lexical_relevance == 0.0
        assert (
            index.search_candidates(
                _query(facets=(mismatching_facet,)), lane=EpistemicLane.REFERENCE, limit=20
            )
            == ()
        )
        assert index.search_candidates(_query(), lane=EpistemicLane.REFERENCE, limit=20) == ()


def test_rebuild_atomically_replaces_only_after_closed_checked_fsynced_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sedna.sqlite"
    first = _renamed_bundle("source-first", "a")
    second = _renamed_bundle("source-second", "b")

    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(first)
        before = path.read_bytes()
        real_replace = os.replace

        def fail_replace(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise OSError("injected rebuild replace failure")

        monkeypatch.setattr(os, "replace", fail_replace)
        with pytest.raises(OSError, match="replace failure"):
            index.rebuild((second,))
        assert index.get_artifact("reference-http-a") == first.references[0]
        assert path.read_bytes() == before

        monkeypatch.setattr(os, "replace", real_replace)
        audit = index.rebuild((second,))
        assert audit.artifact_count == 6
        assert audit.rebuild_required is False
        assert index.get_artifact("reference-http-a") is None
        assert index.get_artifact("reference-http-b") == second.references[0]

    assert not list(tmp_path.glob(".*sedna.sqlite*.tmp*"))
    assert not list(tmp_path.glob("*.sqlite-wal"))
    assert not list(tmp_path.glob("*.sqlite-shm"))


def test_rebuild_validation_failure_preserves_live_database(tmp_path: Path) -> None:
    path = tmp_path / "sedna.sqlite"
    original = _bundle()
    corrupt = original.model_copy(
        update={
            "references": (original.references[0].model_copy(update={"statement": "HTB{unsafe}"}),)
        }
    )

    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(original)
        before = path.read_bytes()
        with pytest.raises(ValueError, match="final flag"):
            index.rebuild((corrupt,))
        assert index.get_artifact("reference-http") == original.references[0]
        assert path.read_bytes() == before


def test_rebuild_restores_live_database_when_post_replace_sync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sedna.sqlite"
    original = _renamed_bundle("source-first", "a")
    replacement = _renamed_bundle("source-second", "b")

    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(original)
        before = path.read_bytes()
        real_fsync = os.fsync
        failed = False

        def fail_first_directory_sync(descriptor: int) -> None:
            nonlocal failed
            if stat.S_ISDIR(os.fstat(descriptor).st_mode) and not failed:
                failed = True
                raise OSError("injected post-replace sync failure")
            real_fsync(descriptor)

        monkeypatch.setattr(os, "fsync", fail_first_directory_sync)
        with pytest.raises(OSError, match="sync failure"):
            index.rebuild((replacement,))

        assert failed
        assert path.read_bytes() == before
        assert index.get_artifact("reference-http-a") == original.references[0]
        assert index.get_artifact("reference-http-b") is None

    assert not list(tmp_path.glob(".*sedna.sqlite*.backup*"))


def test_audit_detects_orphans_fts_parity_source_coverage_and_schema_version(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sedna.sqlite"
    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(_bundle())
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM artifact_fts WHERE artifact_id = 'reference-http'")
        connection.execute(
            "DELETE FROM artifact_sources "
            "WHERE artifact_id = 'case-positive' AND relation = 'artifact'"
        )
        connection.execute(
            "INSERT INTO facet_values "
            "(artifact_id, facet_id, channel, namespace, key, value, relation, origin, confidence) "
            "VALUES ('missing-artifact', 'orphan', 'typed', 'typed', 'os_family', 'linux', "
            "'required', 'explicit', 1.0)"
        )
        connection.execute("PRAGMA user_version = 999")

    with SQLiteRetrievalIndex(path) as index:
        audit = index.audit()

    assert audit.artifact_count == 6
    assert audit.fts_count == 5
    assert audit.orphan_count >= 1
    assert audit.rebuild_required
    assert {
        "fts_count_mismatch",
        "orphan_rows",
        "schema_version_mismatch",
        "source_coverage_mismatch",
    } <= set(audit.issues)


@pytest.mark.parametrize(
    "tamper_sql",
    (
        "UPDATE artifact_fts SET statement = 'tampered fts' WHERE artifact_id = 'reference-http'",
        "UPDATE facet_values SET value = 'tampered-facet' "
        "WHERE rowid = (SELECT rowid FROM facet_values "
        "WHERE artifact_id = 'reference-http' LIMIT 1)",
        "UPDATE artifact_links SET relation = 'tampered-link' "
        "WHERE from_artifact_id = 'case-positive-step'",
        "UPDATE artifact_sources SET relation = 'tampered-provenance' "
        "WHERE rowid = (SELECT rowid FROM artifact_sources "
        "WHERE artifact_id = 'reference-http' AND relation LIKE 'facet:%' LIMIT 1)",
        "UPDATE artifacts SET owner_source_id = 'source-tampered' "
        "WHERE artifact_id = 'reference-http'",
        "UPDATE artifacts SET canonical_json = "
        "replace(canonical_json, 'reference-statement-token', 'tampered-json-token') "
        "WHERE artifact_id = 'reference-http'",
    ),
)
def test_audit_digest_detects_every_normalized_projection_tamper(
    tmp_path: Path,
    tamper_sql: str,
) -> None:
    path = tmp_path / "sedna.sqlite"
    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(_bundle())
    with sqlite3.connect(path) as connection:
        connection.execute(tamper_sql)

    with SQLiteRetrievalIndex(path) as index:
        audit = index.audit()

    assert audit.rebuild_required
    assert "projection_digest_mismatch" in audit.issues


def test_audit_requires_projection_digest_column_even_when_schema_version_matches(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sedna.sqlite"
    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(_bundle())
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE artifacts DROP COLUMN projection_digest")
        connection.execute(
            "UPDATE artifact_fts SET statement = 'poisoned without digest' "
            "WHERE artifact_id = 'reference-http'"
        )

    with SQLiteRetrievalIndex(path) as index:
        audit = index.audit()

    assert audit.rebuild_required
    assert "schema_column_missing" in audit.issues
    assert (audit.artifact_count, audit.source_count, audit.fts_count) == (6, 1, 6)


def test_connect_time_target_swap_cannot_write_through_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sedna.sqlite"
    path.touch()
    outside = tmp_path / "outside.sqlite"
    with sqlite3.connect(outside) as connection:
        connection.execute("CREATE TABLE sentinel(value TEXT)")
        connection.execute("INSERT INTO sentinel VALUES ('unchanged')")
    outside_before = outside.read_bytes()
    real_connect = sqlite3.connect
    swapped = False

    def racing_connect(database: object, *args: object, **kwargs: object) -> sqlite3.Connection:
        nonlocal swapped
        if not swapped:
            path.unlink()
            path.symlink_to(outside)
            swapped = True
        connection = real_connect(database, *args, **kwargs)
        if database != ":memory:":
            connection.execute("PRAGMA user_version = 77")
        return connection

    monkeypatch.setattr(retrieval_sqlite_module.sqlite3, "connect", racing_connect)

    with pytest.raises((ValueError, RuntimeError), match="regular file|changed"):
        SQLiteRetrievalIndex(path)

    assert swapped
    assert outside.read_bytes() == outside_before


def test_replaced_lock_path_cannot_create_independent_writer_domains(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sedna.sqlite"
    first_bundle = _renamed_bundle("source-first", "a")
    old_writer_bundle = _renamed_bundle("source-old-writer", "old")
    new_writer_bundle = _renamed_bundle("source-new-writer", "new")
    first = SQLiteRetrievalIndex(path)
    first.upsert_bundle(first_bundle)

    lock_path = tmp_path / ".sedna.sqlite.lock"
    displaced_lock = tmp_path / ".sedna.sqlite.lock.displaced"
    os.replace(lock_path, displaced_lock)
    second = SQLiteRetrievalIndex(path)
    barrier = threading.Barrier(2)
    first_verify = first._verify_expected_target
    second_verify = second._verify_expected_target

    def synchronized_verify(
        verifier: Callable[[tuple[int, int] | None], None],
        expected_identity: tuple[int, int] | None,
    ) -> None:
        verifier(expected_identity)
        with suppress(threading.BrokenBarrierError):
            barrier.wait(timeout=1)

    monkeypatch.setattr(
        first,
        "_verify_expected_target",
        lambda identity: synchronized_verify(first_verify, identity),
    )
    monkeypatch.setattr(
        second,
        "_verify_expected_target",
        lambda identity: synchronized_verify(second_verify, identity),
    )
    succeeded: list[str] = []
    errors: list[BaseException] = []

    def write(index: SQLiteRetrievalIndex, bundle: SemanticKnowledgeBundle) -> None:
        try:
            index.upsert_bundle(bundle)
            succeeded.append(bundle.source_id)
        except BaseException as error:
            errors.append(error)

    old_worker = threading.Thread(target=write, args=(first, old_writer_bundle))
    new_worker = threading.Thread(target=write, args=(second, new_writer_bundle))
    try:
        old_worker.start()
        new_worker.start()
        old_worker.join(timeout=4)
        new_worker.join(timeout=4)
    finally:
        first.close()
        second.close()

    assert not old_worker.is_alive()
    assert not new_worker.is_alive()
    assert succeeded == ["source-new-writer"]
    assert len(errors) == 1
    assert "lock target changed" in str(errors[0])
    with SQLiteRetrievalIndex(path) as index:
        assert index.get_artifact("reference-http-a") == first_bundle.references[0]
        assert index.get_artifact("reference-http-new") == new_writer_bundle.references[0]
        assert index.get_artifact("reference-http-old") is None


@pytest.mark.parametrize("operation", ("upsert", "delete"))
def test_mutation_failure_after_candidate_commit_preserves_prior_bytes_and_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    path = tmp_path / "sedna.sqlite"
    original = _bundle()
    updated_payload = original.model_dump(mode="json")
    updated_payload["references"][0]["statement"] = "replacement-only-token"
    updated = SemanticKnowledgeBundle.model_validate(updated_payload)

    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(original)
        before = path.read_bytes()
        real_fsync = os.fsync
        failed = False

        def fail_first_directory_sync(descriptor: int) -> None:
            nonlocal failed
            if stat.S_ISDIR(os.fstat(descriptor).st_mode) and not failed:
                failed = True
                raise OSError("injected mutation sync failure")
            real_fsync(descriptor)

        monkeypatch.setattr(os, "fsync", fail_first_directory_sync)
        with pytest.raises(OSError, match="sync failure"):
            if operation == "upsert":
                index.upsert_bundle(updated)
            else:
                index.delete_source(original.source_id)

        assert failed
        assert path.read_bytes() == before
        assert index.get_artifact("reference-http") == original.references[0]


def test_corrupted_serialized_sibling_is_rejected_before_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sedna.sqlite"
    original = _bundle()
    replacement = _renamed_bundle("source-replacement", "replacement")

    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(original)
        before = path.read_bytes()
        real_write = index._write_named_bytes

        def corrupt_temporary(filename: str, payload: bytes) -> tuple[int, int]:
            identity = real_write(filename, payload)
            if filename.endswith(".tmp"):
                assert index._parent_fd is not None
                descriptor = os.open(filename, os.O_WRONLY, dir_fd=index._parent_fd)
                try:
                    os.ftruncate(descriptor, 128)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            return identity

        monkeypatch.setattr(index, "_write_named_bytes", corrupt_temporary)
        with pytest.raises((sqlite3.DatabaseError, ValueError), match="database|integrity|schema"):
            index.rebuild((replacement,))

        assert path.read_bytes() == before
        assert index.get_artifact("reference-http") == original.references[0]
        assert index.get_artifact("reference-http-replacement") is None


def test_failed_temporary_write_does_not_unlink_a_replacement_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sedna.sqlite"
    replacement = _renamed_bundle("source-replacement", "replacement")

    with SQLiteRetrievalIndex(path) as index:
        real_fsync = os.fsync
        attacker_path: Path | None = None

        def replace_temporary_then_fail(descriptor: int) -> None:
            nonlocal attacker_path
            temporary_paths = list(tmp_path.glob(".*sedna.sqlite*.tmp"))
            if attacker_path is None and temporary_paths:
                temporary_path = temporary_paths[0]
                os.replace(temporary_path, tmp_path / "displaced-candidate.sqlite")
                temporary_path.write_bytes(b"unrelated replacement path")
                attacker_path = temporary_path
                raise OSError("injected temporary sync failure")
            real_fsync(descriptor)

        monkeypatch.setattr(os, "fsync", replace_temporary_then_fail)
        with pytest.raises(OSError, match="temporary sync failure"):
            index.rebuild((replacement,))

        assert attacker_path is not None
        assert attacker_path.read_bytes() == b"unrelated replacement path"


def test_semantically_poisoned_sibling_fails_full_projection_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sedna.sqlite"
    original = _bundle()
    replacement = _renamed_bundle("source-replacement", "replacement")

    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(original)
        before = path.read_bytes()
        real_write = index._write_named_bytes

        def poison_fts(filename: str, payload: bytes) -> tuple[int, int]:
            identity = real_write(filename, payload)
            if filename.endswith(".tmp"):
                with sqlite3.connect(tmp_path / filename) as connection:
                    connection.execute(
                        "UPDATE artifact_fts SET statement = 'poisoned after serialization' "
                        "WHERE artifact_id = 'reference-http-replacement'"
                    )
            return identity

        monkeypatch.setattr(index, "_write_named_bytes", poison_fts)
        with pytest.raises(ValueError, match="projection|audit"):
            index.rebuild((replacement,))

        assert path.read_bytes() == before
        assert index.get_artifact("reference-http") == original.references[0]


def test_post_validation_sibling_mutation_rolls_back_before_backup_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sedna.sqlite"
    original = _bundle()
    replacement = _renamed_bundle("source-replacement", "replacement")

    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(original)
        before = path.read_bytes()
        real_validate = index._validate_serialized_sibling

        def mutate_after_validation(
            filename: str,
            expected_identity: tuple[int, int],
            *,
            expected_generation: int,
        ) -> tuple[int, str]:
            validated = real_validate(
                filename,
                expected_identity,
                expected_generation=expected_generation,
            )
            assert index._parent_fd is not None
            descriptor = os.open(filename, os.O_WRONLY, dir_fd=index._parent_fd)
            try:
                os.write(descriptor, b"not a sqlite db!")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return validated

        monkeypatch.setattr(index, "_validate_serialized_sibling", mutate_after_validation)
        with pytest.raises(
            (sqlite3.DatabaseError, ValueError),
            match="database|validation|changed",
        ):
            index.rebuild((replacement,))

        assert path.read_bytes() == before
        assert index.get_artifact("reference-http") == original.references[0]
        assert index.get_artifact("reference-http-replacement") is None


@pytest.mark.parametrize("attack", ("replace_path", "mutate_inode"))
def test_live_database_change_during_final_validation_restores_prior_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    path = tmp_path / "sedna.sqlite"
    original = _bundle()
    replacement = _renamed_bundle("source-replacement", "replacement")

    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(original)
        before = path.read_bytes()
        real_validate = index._validate_database_bytes
        validation_count = 0

        def attack_during_final_validation(
            database_bytes: bytes,
            expected_generation: int,
        ) -> None:
            nonlocal validation_count
            validation_count += 1
            if validation_count == 3:
                if attack == "replace_path":
                    attacker = tmp_path / "attacker.sqlite"
                    attacker.write_bytes(b"attacker-controlled replacement")
                    os.replace(attacker, path)
                else:
                    descriptor = os.open(path, os.O_WRONLY)
                    try:
                        os.write(descriptor, b"attacker-controlled mutation")
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
            real_validate(database_bytes, expected_generation)

        monkeypatch.setattr(index, "_validate_database_bytes", attack_during_final_validation)
        with pytest.raises((OSError, RuntimeError, ValueError), match="changed|rollback"):
            index.rebuild((replacement,))

        assert validation_count >= 3
        assert path.read_bytes() == before
        assert index.get_artifact("reference-http") == original.references[0]
        assert index.get_artifact("reference-http-replacement") is None


def test_final_commit_check_rejects_in_place_write_after_descriptor_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sedna.sqlite"
    original = _bundle()
    replacement = _renamed_bundle("source-replacement", "replacement")

    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(original)
        before = path.read_bytes()
        real_read = index._read_descriptor_bytes
        attacked = False

        def mutate_live_inode_after_final_read(descriptor: int) -> bytes:
            nonlocal attacked
            database_bytes = real_read(descriptor)
            retained = os.fstat(descriptor)
            current = os.stat(path, follow_symlinks=False)
            backups = list(tmp_path.glob(".*sedna.sqlite*.backup"))
            if (
                not attacked
                and (retained.st_dev, retained.st_ino) == (current.st_dev, current.st_ino)
                and not backups
            ):
                writer = os.open(path, os.O_WRONLY)
                try:
                    os.pwrite(writer, b"BROKEN-HEADER!!!", 0)
                    os.fsync(writer)
                finally:
                    os.close(writer)
                attacked = True
            return database_bytes

        monkeypatch.setattr(index, "_read_descriptor_bytes", mutate_live_inode_after_final_read)
        with pytest.raises(ValueError, match="changed"):
            index.rebuild((replacement,))

        assert attacked
        assert path.read_bytes() == before
        assert index.get_artifact("reference-http") == original.references[0]
        assert index.get_artifact("reference-http-replacement") is None


def test_rollback_restores_from_retained_backup_when_backup_path_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sedna.sqlite"
    original = _bundle()
    replacement = _renamed_bundle("source-replacement", "replacement")

    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(original)
        before = path.read_bytes()
        real_verify = index._verify_retained_sibling
        verification_count = 0

        def replace_backup_then_fail(*args: object, **kwargs: object) -> None:
            nonlocal verification_count
            verification_count += 1
            real_verify(*args, **kwargs)
            if verification_count == 2:
                backups = list(tmp_path.glob(".*sedna.sqlite*.backup"))
                assert len(backups) == 1
                attacker = tmp_path / "attacker-backup.sqlite"
                attacker.write_bytes(b"attacker-controlled backup")
                os.replace(attacker, backups[0])
                raise OSError("injected postinstall failure")

        monkeypatch.setattr(index, "_verify_retained_sibling", replace_backup_then_fail)
        with pytest.raises(OSError, match="postinstall failure"):
            index.rebuild((replacement,))

        assert verification_count >= 2
        assert path.read_bytes() == before
        assert index.get_artifact("reference-http") == original.references[0]
        assert index.get_artifact("reference-http-replacement") is None


@pytest.mark.parametrize(
    "attack_point",
    ("after_semantic_validation", "during_recovery_cleanup"),
)
def test_rollback_rechecks_live_path_before_removing_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack_point: str,
) -> None:
    path = tmp_path / "sedna.sqlite"
    original = _bundle()
    replacement = _renamed_bundle("source-replacement", "replacement")

    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(original)
        before = path.read_bytes()
        previous_generation = index._generation
        real_verify = index._verify_retained_sibling
        real_validate = index._validate_database_bytes
        real_write_generation = index._write_generation
        real_unlink = index._unlink_named_identity_if_present
        verification_count = 0
        generation_repaired = False
        attacked = False

        def fail_after_postinstall_verification(*args: object, **kwargs: object) -> None:
            nonlocal verification_count
            verification_count += 1
            real_verify(*args, **kwargs)
            if verification_count == 2:
                raise OSError("injected postinstall failure")

        def replace_after_final_rollback_validation(
            database_bytes: bytes,
            expected_generation: int,
        ) -> None:
            nonlocal attacked
            real_validate(database_bytes, expected_generation)
            if (
                attack_point == "after_semantic_validation"
                and expected_generation == previous_generation
                and generation_repaired
                and not attacked
            ):
                attacker = tmp_path / "attacker-live.sqlite"
                attacker.write_bytes(b"attacker-controlled live replacement")
                os.replace(attacker, path)
                attacked = True

        def track_generation_repair(generation: int) -> None:
            nonlocal generation_repaired
            real_write_generation(generation)
            if generation == previous_generation:
                generation_repaired = True

        def replace_during_recovery_cleanup(
            filename: str,
            identity: tuple[int, int],
        ) -> None:
            nonlocal attacked
            real_unlink(filename, identity)
            if attack_point == "during_recovery_cleanup" and filename.endswith(".backup"):
                attacker = tmp_path / "attacker-live.sqlite"
                attacker.write_bytes(b"attacker-controlled live replacement")
                os.replace(attacker, path)
                attacked = True

        monkeypatch.setattr(index, "_verify_retained_sibling", fail_after_postinstall_verification)
        monkeypatch.setattr(
            index, "_validate_database_bytes", replace_after_final_rollback_validation
        )
        monkeypatch.setattr(index, "_write_generation", track_generation_repair)
        monkeypatch.setattr(
            index, "_unlink_named_identity_if_present", replace_during_recovery_cleanup
        )
        with pytest.raises(OSError, match="postinstall failure") as raised:
            index.rebuild((replacement,))

        assert attacked
        notes = getattr(raised.value, "__notes__", ())
        assert any("rollback failed" in note for note in notes)
        recovery_paths = list(tmp_path.glob(".*sedna.sqlite*.backup*"))
        assert any(recovery.read_bytes() == before for recovery in recovery_paths)


def test_unprovable_rollback_surfaces_failure_and_preserves_exact_recovery_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sedna.sqlite"
    original = _bundle()
    replacement = _renamed_bundle("source-replacement", "replacement")

    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(original)
        before = path.read_bytes()
        real_verify = index._verify_retained_sibling
        real_validate_sibling = index._validate_serialized_sibling
        verification_count = 0

        def replace_backup_then_fail(*args: object, **kwargs: object) -> None:
            nonlocal verification_count
            verification_count += 1
            real_verify(*args, **kwargs)
            if verification_count == 2:
                backup = next(tmp_path.glob(".*sedna.sqlite*.backup"))
                attacker = tmp_path / "attacker-backup.sqlite"
                attacker.write_bytes(b"attacker-controlled backup")
                os.replace(attacker, backup)
                raise OSError("injected postinstall failure")

        def fail_rollback_validation(
            filename: str,
            expected_identity: tuple[int, int],
            *,
            expected_generation: int,
        ) -> tuple[int, str]:
            if filename.endswith(".rollback"):
                raise OSError("injected rollback validation failure")
            return real_validate_sibling(
                filename,
                expected_identity,
                expected_generation=expected_generation,
            )

        monkeypatch.setattr(index, "_verify_retained_sibling", replace_backup_then_fail)
        monkeypatch.setattr(index, "_validate_serialized_sibling", fail_rollback_validation)
        with pytest.raises(OSError, match="postinstall failure") as raised:
            index.rebuild((replacement,))

        notes = getattr(raised.value, "__notes__", ())
        assert any("rollback failed" in note for note in notes)
        recovery_paths = list(tmp_path.glob(".*sedna.sqlite*.backup.*.recovery"))
        assert len(recovery_paths) == 1
        assert recovery_paths[0].read_bytes() == before


@pytest.mark.parametrize("generation_bytes", (b"", b"12", b"\xff\xfe"))
def test_generation_sidecar_is_recovered_from_valid_database(
    tmp_path: Path,
    generation_bytes: bytes,
) -> None:
    path = tmp_path / "sedna.sqlite"
    lock_path = tmp_path / ".sedna.sqlite.lock"
    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(_bundle())
    lock_path.write_bytes(generation_bytes)

    with SQLiteRetrievalIndex(path) as index:
        assert index.audit().rebuild_required is False
        rebuilt = index.rebuild((_renamed_bundle("source-rebuilt", "rebuilt"),))
        assert rebuilt.rebuild_required is False

    repaired = lock_path.read_bytes()
    assert repaired.endswith(b"\n")
    assert repaired[:-1].isdigit()


def test_audit_requires_one_valid_embedded_generation_row(tmp_path: Path) -> None:
    path = tmp_path / "sedna.sqlite"
    with SQLiteRetrievalIndex(path):
        pass
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM index_metadata")

    with SQLiteRetrievalIndex(path) as index:
        audit = index.audit()

    assert audit.rebuild_required
    assert "generation_metadata_invalid" in audit.issues


def test_audit_rejects_ordinary_table_impersonating_fts5(tmp_path: Path) -> None:
    path = tmp_path / "sedna.sqlite"
    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(_bundle())
    with sqlite3.connect(path) as connection:
        fts_rows = connection.execute(
            "SELECT artifact_id, statement, rationale, observations, action_intent, "
            "expected_evidence, exceptions FROM artifact_fts ORDER BY artifact_id"
        ).fetchall()
        connection.execute("DROP TABLE artifact_fts")
        connection.execute(
            "CREATE TABLE artifact_fts(artifact_id, statement, rationale, observations, "
            "action_intent, expected_evidence, exceptions)"
        )
        connection.executemany(
            "INSERT INTO artifact_fts VALUES (?, ?, ?, ?, ?, ?, ?)",
            fts_rows,
        )

    with SQLiteRetrievalIndex(path) as index:
        audit = index.audit()

    assert audit.rebuild_required
    assert "schema_object_mismatch" in audit.issues


@pytest.mark.parametrize(
    "unexpected_schema_sql",
    (
        "CREATE TABLE unexpected_table(value TEXT)",
        "CREATE VIEW unexpected_view AS SELECT artifact_id FROM artifacts",
        "CREATE INDEX unexpected_index ON artifacts(verification_status)",
    ),
)
def test_audit_rejects_every_unexpected_application_schema_object(
    tmp_path: Path,
    unexpected_schema_sql: str,
) -> None:
    path = tmp_path / "sedna.sqlite"
    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(_bundle())
    with sqlite3.connect(path) as connection:
        connection.execute(unexpected_schema_sql)

    with SQLiteRetrievalIndex(path) as index:
        audit = index.audit()

    assert audit.rebuild_required
    assert "schema_object_unexpected" in audit.issues


def test_unexpected_trigger_blocks_upsert_before_it_can_delete_another_source(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sedna.sqlite"
    first = _renamed_bundle("source-first", "first")
    second = _renamed_bundle("source-second", "second")
    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(first)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TRIGGER delete_other_sources AFTER INSERT ON artifacts BEGIN "
            "DELETE FROM artifacts WHERE owner_source_id != NEW.owner_source_id; END"
        )

    with SQLiteRetrievalIndex(path) as index:
        audit = index.audit()
        assert audit.rebuild_required
        assert "schema_object_unexpected" in audit.issues
        with pytest.raises(RuntimeError, match="full rebuild"):
            index.upsert_bundle(second)
        assert index.get_artifact("reference-http-first") == first.references[0]
        assert index.get_artifact("reference-http-second") is None


def test_audit_uses_one_snapshot_while_another_index_deletes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sedna.sqlite"
    with SQLiteRetrievalIndex(path) as index:
        index.upsert_bundle(_bundle())
        before = index.audit()
        attempted = threading.Event()
        committed = threading.Event()
        errors: list[BaseException] = []

        def delete_source() -> None:
            attempted.set()
            try:
                with SQLiteRetrievalIndex(path) as writer:
                    writer.delete_source("source-retrieval")
            except BaseException as error:
                errors.append(error)
            finally:
                committed.set()

        worker = threading.Thread(target=delete_source)
        blocked = False

        def trace(statement: str) -> None:
            nonlocal blocked
            if not blocked and statement.startswith("SELECT COUNT(*) FROM facet_values"):
                blocked = True
                worker.start()
                assert attempted.wait(timeout=2)
                committed.wait(timeout=0.5)

        original_audit = index._audit_connection

        def blocking_audit(connection: sqlite3.Connection):
            connection.set_trace_callback(trace)
            try:
                return original_audit(connection)
            finally:
                connection.set_trace_callback(None)

        monkeypatch.setattr(index, "_audit_connection", blocking_audit)
        observed = index.audit()
        worker.join(timeout=3)

        assert not worker.is_alive()
        assert errors == []
        assert observed == before
        assert observed.rebuild_required is False
        assert index.audit().artifact_count == 0


def test_match_explanations_follow_fts5_diacritic_and_phrase_semantics(tmp_path: Path) -> None:
    payload = _bundle().model_dump(mode="json")
    payload["references"][0]["statement"] = "café alpha gap beta"
    bundle = SemanticKnowledgeBundle.model_validate(payload)

    with SQLiteRetrievalIndex(tmp_path / "sedna.sqlite") as index:
        index.upsert_bundle(bundle)
        candidates = index.search_candidates(
            _query("cafe", "alpha beta", "alpha"),
            lane=EpistemicLane.REFERENCE,
            limit=10,
        )

    assert [candidate.artifact_id for candidate in candidates] == ["reference-http"]
    assert candidates[0].matched_terms == ("alpha", "cafe")
    assert candidates[0].matched_fields == ("statement",)
    assert any("[café]" in evidence for evidence in candidates[0].matched_evidence)
    assert all("alpha beta" not in evidence for evidence in candidates[0].matched_evidence)


def test_cross_thread_failed_close_keeps_resources_and_allows_retry(
    tmp_path: Path,
) -> None:
    index = SQLiteRetrievalIndex(tmp_path / "sedna.sqlite")
    connection = index._connection
    parent_fd = index._parent_fd
    assert connection is not None
    assert parent_fd is not None
    connection_type = type(connection)
    real_close = connection_type.close
    failed = False

    def fail_once(candidate: sqlite3.Connection) -> None:
        nonlocal failed
        if candidate is connection and not failed:
            failed = True
            raise RuntimeError("injected close failure")
        real_close(candidate)

    connection_type.close = fail_once
    errors: list[BaseException] = []
    try:
        worker = threading.Thread(target=lambda: _capture_close_error(index, errors))
        worker.start()
        worker.join(timeout=3)

        assert not worker.is_alive()
        assert len(errors) == 1
        assert "injected close failure" in str(errors[0])
        assert index._connection is connection
        assert index._parent_fd == parent_fd
        assert index.audit().rebuild_required is False

        index.close()
        assert index._connection is None
        assert index._parent_fd is None
    finally:
        connection_type.close = real_close


@pytest.mark.parametrize("target_kind", ("symlink", "fifo", "directory"))
def test_database_target_must_be_a_fixed_regular_file(
    tmp_path: Path,
    target_kind: str,
) -> None:
    path = tmp_path / "sedna.sqlite"
    if target_kind == "symlink":
        outside = tmp_path / "outside.sqlite"
        outside.write_bytes(b"outside")
        path.symlink_to(outside)
    elif target_kind == "fifo":
        os.mkfifo(path)
    else:
        path.mkdir()

    with pytest.raises(ValueError, match="regular file"):
        SQLiteRetrievalIndex(path)


def test_bundle_source_id_must_be_a_safe_canonical_repository_segment(tmp_path: Path) -> None:
    unsafe = _renamed_bundle("../source-escape", "a")

    with (
        SQLiteRetrievalIndex(tmp_path / "sedna.sqlite") as index,
        pytest.raises(ValueError, match="safe path segment"),
    ):
        index.upsert_bundle(unsafe)


def test_parent_symlink_and_target_swap_are_rejected(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(ValueError, match="parent"):
        SQLiteRetrievalIndex(linked_parent / "sedna.sqlite")

    path = tmp_path / "sedna.sqlite"
    index = SQLiteRetrievalIndex(path)
    replacement = tmp_path / "replacement.sqlite"
    replacement.write_bytes(path.read_bytes())
    os.replace(replacement, path)
    with pytest.raises(RuntimeError, match="changed"):
        index.audit()
    index.close()


def test_context_manager_and_close_are_strict_and_idempotent(tmp_path: Path) -> None:
    index = SQLiteRetrievalIndex(tmp_path / "sedna.sqlite")
    with index as entered:
        assert entered is index

    index.close()
    for operation in (
        lambda: index.audit(),
        lambda: index.get_artifact("reference-http"),
        lambda: index.delete_source("source-retrieval"),
        lambda: index.upsert_bundle(_bundle()),
        lambda: index.rebuild((_bundle(),)),
        lambda: index.search_candidates(_query("http"), lane=EpistemicLane.REFERENCE, limit=1),
        lambda: index.__enter__(),
    ):
        with pytest.raises(RuntimeError, match="closed"):
            operation()


def _capture_close_error(index: SQLiteRetrievalIndex, errors: list[BaseException]) -> None:
    try:
        index.close()
    except BaseException as error:
        errors.append(error)
