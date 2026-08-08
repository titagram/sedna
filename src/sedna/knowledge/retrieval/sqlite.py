"""Disposable, source-scoped SQLite FTS5 projection of canonical knowledge."""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import sqlite3
import stat
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from types import TracebackType

from pydantic import TypeAdapter, ValidationError

from sedna.knowledge.retrieval.models import (
    EpistemicLane,
    IndexAudit,
    IndexCandidate,
    IndexedArtifact,
    RetrievalQuery,
)
from sedna.knowledge.retrieval.projection import ProjectedArtifact, project_semantic_bundle
from sedna.knowledge.schema import (
    CaseStep,
    KnowledgeCase,
    ReferenceArtifact,
    SemanticKnowledgeBundle,
)

_SCHEMA_VERSION = 1
_MAX_LIMIT = 100
_FTS_FIELDS = (
    "statement",
    "rationale",
    "observations",
    "action_intent",
    "expected_evidence",
    "exceptions",
)
_WORD = re.compile(r"\w+", flags=re.UNICODE)
_ARTIFACT_ADAPTER = TypeAdapter(IndexedArtifact)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    owner_source_id TEXT NOT NULL,
    canonical_path TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    knowledge_role TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    source_reliability REAL NOT NULL CHECK(source_reliability BETWEEN 0.0 AND 1.0),
    extraction_confidence REAL NOT NULL CHECK(extraction_confidence BETWEEN 0.0 AND 1.0),
    generalizability TEXT NOT NULL,
    context_specificity REAL NOT NULL CHECK(context_specificity BETWEEN 0.0 AND 1.0),
    support_count INTEGER NOT NULL CHECK(support_count >= 0),
    contradiction_count INTEGER NOT NULL CHECK(contradiction_count >= 0),
    observed_outcome TEXT NOT NULL,
    observed_at TEXT,
    freshness_observed_at TEXT,
    independence_group TEXT NOT NULL,
    canonical_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facet_values (
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
    facet_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    relation TEXT NOT NULL,
    origin TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0.0 AND 1.0),
    PRIMARY KEY (artifact_id, facet_id)
);

CREATE TABLE IF NOT EXISTS artifact_links (
    from_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
    relation TEXT NOT NULL,
    to_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
    PRIMARY KEY (from_artifact_id, relation, to_artifact_id)
);

CREATE TABLE IF NOT EXISTS artifact_sources (
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    path TEXT NOT NULL,
    location_json TEXT NOT NULL,
    independence_group TEXT NOT NULL,
    relation TEXT NOT NULL,
    PRIMARY KEY (
        artifact_id, source_id, path, location_json, independence_group, relation
    )
);

CREATE VIRTUAL TABLE IF NOT EXISTS artifact_fts USING fts5(
    artifact_id UNINDEXED,
    statement,
    rationale,
    observations,
    action_intent,
    expected_evidence,
    exceptions,
    tokenize = 'unicode61'
);

CREATE INDEX IF NOT EXISTS artifacts_owner_source_idx ON artifacts(owner_source_id);
CREATE INDEX IF NOT EXISTS artifacts_lane_idx
    ON artifacts(artifact_type, knowledge_role, observed_outcome, artifact_id);
CREATE INDEX IF NOT EXISTS facet_values_lookup_idx
    ON facet_values(namespace, key, value, artifact_id);
CREATE INDEX IF NOT EXISTS artifact_links_target_idx
    ON artifact_links(to_artifact_id, relation, from_artifact_id);
CREATE INDEX IF NOT EXISTS artifact_sources_source_idx
    ON artifact_sources(source_id, artifact_id);
"""


class SQLiteRetrievalIndex:
    """A fixed-path, rebuildable SQLite implementation of ``RetrievalIndex``."""

    def __init__(self, path: str | Path) -> None:
        self._connection: sqlite3.Connection | None = None
        self._parent_fd: int | None = None
        self._db_identity: tuple[int, int] | None = None
        self.path, self._filename = self._prepare_target(path)
        try:
            self._open_parent()
            self._open_connection()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> SQLiteRetrievalIndex:
        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()

    def close(self) -> None:
        """Close the database and retained parent descriptor; repeated calls are harmless."""
        connection = self._connection
        if connection is not None:
            self._connection = None
            connection.close()
        parent_fd = self._parent_fd
        if parent_fd is not None:
            self._parent_fd = None
            os.close(parent_fd)

    def upsert_bundle(self, bundle: SemanticKnowledgeBundle) -> None:
        """Replace one source's entire validated projection in a single transaction."""
        connection = self._ensure_open()
        projection = project_semantic_bundle(bundle)
        source_id = _validated_source_id(bundle.source_id)
        with self._transaction(connection):
            self._delete_source_rows(connection, source_id)
            self._insert_projection_rows(connection, source_id, projection)
        self._refresh_identity()

    def delete_source(self, source_id: str) -> None:
        """Remove every derived row owned by one canonical source."""
        connection = self._ensure_open()
        source_id = _validated_source_id(source_id)
        with self._transaction(connection):
            self._delete_source_rows(connection, source_id)
        self._refresh_identity()

    def rebuild(self, bundles: Iterable[SemanticKnowledgeBundle]) -> IndexAudit:
        """Build, verify, sync, and atomically install a complete sibling database."""
        self._ensure_open()
        projected: list[tuple[str, tuple[ProjectedArtifact, ...]]] = []
        source_ids: set[str] = set()
        for bundle in bundles:
            rows = project_semantic_bundle(bundle)
            source_id = _validated_source_id(bundle.source_id)
            if source_id in source_ids:
                raise ValueError("rebuild source IDs must be unique")
            source_ids.add(source_id)
            projected.append((source_id, rows))

        temporary_name = f".{self._filename}.{secrets.token_hex(16)}.tmp"
        backup_name = f".{self._filename}.{secrets.token_hex(16)}.backup"
        temporary_path = self.path.with_name(temporary_name)
        temporary: SQLiteRetrievalIndex | None = None
        audit: IndexAudit | None = None
        installed = False
        backup_created = False
        try:
            temporary = SQLiteRetrievalIndex(temporary_path)
            temporary_connection = temporary._ensure_open()
            with temporary._transaction(temporary_connection):
                for source_id, rows in projected:
                    temporary._insert_projection_rows(temporary_connection, source_id, rows)
            audit = temporary.audit()
            if audit.rebuild_required:
                raise ValueError(f"rebuilt index failed audit: {', '.join(audit.issues)}")
            temporary._checkpoint_for_replace()
            temporary.close()
            temporary = None
            self._fsync_named_file(temporary_name)

            self._ensure_target_identity()
            parent_fd = self._ensure_parent_open()
            os.link(
                self._filename,
                backup_name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
            backup_created = True
            self._close_connection()
            try:
                os.replace(
                    temporary_name,
                    self._filename,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
                installed = True
                os.fsync(parent_fd)
                self._open_connection()
                os.unlink(backup_name, dir_fd=parent_fd)
                backup_created = False
                with suppress(OSError):
                    os.fsync(parent_fd)
            except BaseException as original_error:
                self._close_connection()
                if installed and backup_created:
                    try:
                        os.replace(
                            backup_name,
                            self._filename,
                            src_dir_fd=parent_fd,
                            dst_dir_fd=parent_fd,
                        )
                        backup_created = False
                        installed = False
                        os.fsync(parent_fd)
                    except BaseException as rollback_error:
                        original_error.add_note(
                            "rebuild rollback failed: "
                            f"{type(rollback_error).__name__}: {rollback_error}"
                        )
                try:
                    self._open_connection()
                except BaseException as reopen_error:
                    original_error.add_note(
                        "rebuild database reopen failed: "
                        f"{type(reopen_error).__name__}: {reopen_error}"
                    )
                raise
            return audit
        finally:
            if temporary is not None:
                temporary.close()
            if not installed:
                self._unlink_sidecars(temporary_name)
            if backup_created and not installed:
                self._unlink_sidecars(backup_name)

    def get_artifact(self, artifact_id: str) -> IndexedArtifact | None:
        """Return one deeply reconstructed canonical artifact by exact identity."""
        connection = self._ensure_open()
        artifact_id = _validated_identifier(artifact_id, "artifact_id")
        row = connection.execute(
            "SELECT artifact_id, canonical_json FROM artifacts WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone()
        if row is None:
            return None
        return self._reconstruct_artifact(row["artifact_id"], row["canonical_json"])

    def search_candidates(
        self,
        query: RetrievalQuery,
        *,
        lane: EpistemicLane,
        limit: int,
    ) -> tuple[IndexCandidate, ...]:
        """Return bounded, lane-scoped lexical candidates with exact match evidence."""
        connection = self._ensure_open()
        query = RetrievalQuery.model_validate(query.model_dump(mode="json"))
        try:
            lane = EpistemicLane(lane)
        except ValueError as error:
            raise ValueError("lane must be a supported epistemic lane") from error
        if type(limit) is not int or not 1 <= limit <= _MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {_MAX_LIMIT}")
        effective_limit = min(limit, query.max_candidates)
        terms = tuple(sorted({*query.situation.terms, *query.terms, *query.synonyms}))
        fts_terms = tuple((term, _WORD.findall(term)) for term in terms)
        searchable_terms = tuple((term, tokens) for term, tokens in fts_terms if tokens)
        if not searchable_terms and not query.facets:
            return ()

        clauses = [_lane_clause(lane)]
        parameters: list[object] = []
        for facet in query.facets:
            clauses.append(
                "EXISTS (SELECT 1 FROM facet_values AS fv "
                "WHERE fv.artifact_id = a.artifact_id "
                "AND fv.namespace = ? AND fv.key = ? AND fv.value = ?)"
            )
            parameters.extend((facet.namespace, facet.key, facet.value))

        if searchable_terms:
            match = " OR ".join(
                f'"{" ".join(token.replace(chr(34), chr(34) * 2) for token in tokens)}"'
                for _, tokens in searchable_terms
            )
            clauses.insert(0, "artifact_fts MATCH ?")
            parameters.insert(0, match)
            rank = "bm25(artifact_fts)"
            table = "artifact_fts JOIN artifacts AS a USING (artifact_id)"
        else:
            rank = "0.0"
            table = "artifacts AS a JOIN artifact_fts USING (artifact_id)"

        parameters.append(effective_limit)
        fields = ", ".join(f"artifact_fts.{field} AS {field}" for field in _FTS_FIELDS)
        rows = connection.execute(
            f"""
            SELECT a.artifact_id, a.canonical_json, {fields}, {rank} AS lexical_rank
            FROM {table}
            WHERE {" AND ".join(clauses)}
            ORDER BY lexical_rank ASC, a.artifact_id ASC
            LIMIT ?
            """,
            parameters,
        ).fetchall()

        max_quality = max((_rank_quality(row["lexical_rank"]) for row in rows), default=0.0)
        candidates: list[IndexCandidate] = []
        for row in rows:
            artifact = self._reconstruct_artifact(row["artifact_id"], row["canonical_json"])
            matched_terms, matched_fields, evidence = _match_evidence(row, searchable_terms)
            candidates.append(
                IndexCandidate(
                    artifact_id=row["artifact_id"],
                    artifact=artifact,
                    lexical_relevance=_normalise_rank(row["lexical_rank"], max_quality),
                    matched_terms=matched_terms,
                    matched_fields=matched_fields,
                    matched_evidence=evidence,
                )
            )
        return tuple(candidates)

    def audit(self) -> IndexAudit:
        """Audit structural integrity, canonical reconstruction, provenance, and FTS parity."""
        connection = self._ensure_open()
        issues: set[str] = set()
        corruption_count = 0

        if connection.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA_VERSION:
            issues.add("schema_version_mismatch")
        integrity = tuple(row[0] for row in connection.execute("PRAGMA integrity_check"))
        bad_integrity = tuple(message for message in integrity if message != "ok")
        if bad_integrity:
            issues.add("integrity_check_failed")
            corruption_count += len(bad_integrity)

        artifact_count = connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
        source_count = connection.execute(
            "SELECT COUNT(DISTINCT owner_source_id) FROM artifacts"
        ).fetchone()[0]
        facet_count = connection.execute("SELECT COUNT(*) FROM facet_values").fetchone()[0]
        fts_count = connection.execute("SELECT COUNT(*) FROM artifact_fts").fetchone()[0]

        foreign_key_rows = tuple(connection.execute("PRAGMA foreign_key_check"))
        explicit_orphans = sum(
            connection.execute(statement).fetchone()[0]
            for statement in (
                "SELECT COUNT(*) FROM facet_values AS child "
                "LEFT JOIN artifacts AS parent USING (artifact_id) "
                "WHERE parent.artifact_id IS NULL",
                "SELECT COUNT(*) FROM artifact_sources AS child "
                "LEFT JOIN artifacts AS parent USING (artifact_id) "
                "WHERE parent.artifact_id IS NULL",
                "SELECT COUNT(*) FROM artifact_links AS child "
                "LEFT JOIN artifacts AS parent ON parent.artifact_id = child.from_artifact_id "
                "WHERE parent.artifact_id IS NULL",
                "SELECT COUNT(*) FROM artifact_links AS child "
                "LEFT JOIN artifacts AS parent ON parent.artifact_id = child.to_artifact_id "
                "WHERE parent.artifact_id IS NULL",
                "SELECT COUNT(*) FROM artifact_fts AS child "
                "LEFT JOIN artifacts AS parent USING (artifact_id) "
                "WHERE parent.artifact_id IS NULL",
            )
        )
        orphan_count = max(len(foreign_key_rows), explicit_orphans)

        duplicate_id_count = connection.execute(
            "SELECT COALESCE(SUM(count - 1), 0) FROM "
            "(SELECT COUNT(*) AS count FROM artifacts GROUP BY artifact_id HAVING count > 1)"
        ).fetchone()[0]
        duplicate_id_count += connection.execute(
            "SELECT COALESCE(SUM(count - 1), 0) FROM "
            "(SELECT COUNT(*) AS count FROM artifact_fts GROUP BY artifact_id HAVING count > 1)"
        ).fetchone()[0]

        artifact_ids = {row[0] for row in connection.execute("SELECT artifact_id FROM artifacts")}
        fts_ids = {row[0] for row in connection.execute("SELECT artifact_id FROM artifact_fts")}
        if artifact_ids != fts_ids:
            issues.add("fts_artifact_mismatch")

        for row in connection.execute("SELECT * FROM artifacts ORDER BY artifact_id"):
            try:
                artifact = self._reconstruct_artifact(row["artifact_id"], row["canonical_json"])
                if not _stored_metadata_matches(row, artifact):
                    raise ValueError("stored projection metadata does not match canonical artifact")
                if row["canonical_json"] != _canonical_json(artifact):
                    raise ValueError("canonical artifact JSON is not canonical")
            except (TypeError, ValueError, ValidationError):
                corruption_count += 1

        uncovered = 0
        for row in connection.execute(
            "SELECT artifact_id, owner_source_id, canonical_json "
            "FROM artifacts ORDER BY artifact_id"
        ):
            try:
                artifact = self._reconstruct_artifact(row["artifact_id"], row["canonical_json"])
            except ValueError:
                continue
            expected_sources = {
                (
                    source.source_id,
                    source.path,
                    json.dumps(
                        source.location.model_dump(mode="json"),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                    artifact.assessment.independence_group,
                )
                for source in artifact.source_refs
            }
            stored_sources = {
                tuple(source_row)
                for source_row in connection.execute(
                    """
                    SELECT source_id, path, location_json, independence_group
                    FROM artifact_sources
                    WHERE artifact_id = ? AND relation = 'artifact'
                    """,
                    (row["artifact_id"],),
                )
            }
            if stored_sources != expected_sources or row["owner_source_id"] not in {
                source[0] for source in expected_sources
            }:
                uncovered += 1
        if uncovered:
            issues.add("source_coverage_mismatch")
            corruption_count += uncovered

        return IndexAudit(
            artifact_count=artifact_count,
            source_count=source_count,
            facet_count=facet_count,
            fts_count=fts_count,
            orphan_count=orphan_count,
            duplicate_id_count=duplicate_id_count,
            corruption_count=corruption_count,
            issues=tuple(sorted(issues)),
        )

    @contextmanager
    def _transaction(self, connection: sqlite3.Connection) -> Iterator[None]:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()

    def _delete_source_rows(self, connection: sqlite3.Connection, source_id: str) -> None:
        connection.execute(
            "DELETE FROM artifact_fts WHERE artifact_id IN "
            "(SELECT artifact_id FROM artifacts WHERE owner_source_id = ?)",
            (source_id,),
        )
        connection.execute("DELETE FROM artifacts WHERE owner_source_id = ?", (source_id,))

    def _insert_projection_rows(
        self,
        connection: sqlite3.Connection,
        source_id: str,
        projection: tuple[ProjectedArtifact, ...],
    ) -> None:
        for artifact in projection:
            connection.execute(
                """
                INSERT INTO artifacts(
                    artifact_id, owner_source_id, canonical_path, artifact_type, knowledge_role,
                    verification_status, source_reliability, extraction_confidence,
                    generalizability, context_specificity, support_count,
                    contradiction_count, observed_outcome, observed_at,
                    freshness_observed_at, independence_group, canonical_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.artifact_id,
                    source_id,
                    f"semantic_bundles/{source_id}.json",
                    artifact.artifact_type,
                    artifact.knowledge_role,
                    artifact.verification_status,
                    artifact.source_reliability,
                    artifact.extraction_confidence,
                    artifact.generalizability,
                    artifact.context_specificity,
                    artifact.support_count,
                    artifact.contradiction_count,
                    artifact.observed_outcome,
                    artifact.observed_at,
                    artifact.freshness_observed_at,
                    artifact.independence_group,
                    artifact.canonical_json,
                ),
            )
        for artifact in projection:
            connection.executemany(
                """
                INSERT INTO facet_values(
                    artifact_id, facet_id, channel, namespace, key, value,
                    relation, origin, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        facet.artifact_id,
                        facet.facet_id,
                        facet.channel,
                        facet.namespace,
                        facet.key,
                        facet.value,
                        facet.relation,
                        facet.origin,
                        facet.confidence,
                    )
                    for facet in artifact.facets
                ),
            )
            connection.executemany(
                """
                INSERT INTO artifact_sources(
                    artifact_id, source_id, path, location_json,
                    independence_group, relation
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        source.artifact_id,
                        source.source_id,
                        source.path,
                        json.dumps(
                            source.location.model_dump(mode="json"),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                        source.independence_group,
                        source.relation,
                    )
                    for source in artifact.sources
                ),
            )
            connection.execute(
                """
                INSERT INTO artifact_fts(
                    artifact_id, statement, rationale, observations,
                    action_intent, expected_evidence, exceptions
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.artifact_id,
                    artifact.statement,
                    artifact.rationale,
                    artifact.observations,
                    artifact.action_intent,
                    artifact.expected_evidence,
                    artifact.exceptions,
                ),
            )
        for artifact in projection:
            connection.executemany(
                """
                INSERT INTO artifact_links(from_artifact_id, relation, to_artifact_id)
                VALUES (?, ?, ?)
                """,
                (
                    (link.from_artifact_id, link.relation, link.to_artifact_id)
                    for link in artifact.links
                ),
            )

    def _reconstruct_artifact(self, artifact_id: str, canonical_json: str) -> IndexedArtifact:
        try:
            payload = json.loads(canonical_json)
            artifact = _ARTIFACT_ADAPTER.validate_python(payload)
        except (TypeError, json.JSONDecodeError, ValidationError) as error:
            raise ValueError(f"invalid canonical artifact for {artifact_id!r}") from error
        if _artifact_id(artifact) != artifact_id:
            raise ValueError(f"invalid canonical artifact identity for {artifact_id!r}")
        return artifact

    def _prepare_target(self, path: str | Path) -> tuple[Path, str]:
        if not isinstance(path, (str, os.PathLike)):
            raise TypeError("database path must be a filesystem path")
        raw = os.fspath(path)
        if not raw or "\x00" in raw:
            raise ValueError("database path must be a fixed filesystem target")
        requested = Path(path).expanduser()
        if requested.name in {"", ".", ".."}:
            raise ValueError("database path must name a fixed file target")
        absolute = requested if requested.is_absolute() else Path.cwd() / requested
        if any(part in {"", ".", ".."} for part in absolute.parts[1:]):
            raise ValueError("database parent must not contain symlink or traversal components")
        return absolute, absolute.name

    def _open_parent(self) -> None:
        required = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")
        if any(not hasattr(os, name) for name in required) or any(
            function not in os.supports_dir_fd for function in (os.open, os.mkdir, os.unlink)
        ):
            raise RuntimeError("platform lacks safe local database path primitives")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        directory_fd = os.open(Path(self.path.anchor), flags)
        try:
            for component in self.path.parent.parts[1:]:
                with suppress(FileExistsError):
                    os.mkdir(component, mode=0o755, dir_fd=directory_fd)
                try:
                    child_fd = os.open(component, flags, dir_fd=directory_fd)
                except OSError as error:
                    raise ValueError("database parent must not contain symlinks") from error
                child_status = os.fstat(child_fd)
                if not stat.S_ISDIR(child_status.st_mode):
                    os.close(child_fd)
                    raise ValueError("database parent must contain only directories")
                os.close(directory_fd)
                directory_fd = child_fd
            self._parent_fd = directory_fd
            directory_fd = -1
        finally:
            if directory_fd >= 0:
                os.close(directory_fd)

    def _open_connection(self) -> None:
        self._ensure_parent_identity()
        before = self._target_status()
        if before is not None and not stat.S_ISREG(before.st_mode):
            raise ValueError("database target must be a regular file")
        initialize_version = before is None or before.st_size == 0
        connection = sqlite3.connect(self.path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            after = self._target_status()
            if after is None or not stat.S_ISREG(after.st_mode):
                raise ValueError("database target must be a regular file")
            if before is not None and (before.st_dev, before.st_ino) != (
                after.st_dev,
                after.st_ino,
            ):
                raise ValueError("database target changed while being opened")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(_SCHEMA)
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if initialize_version and version == 0:
                connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            self._connection = connection
            self._db_identity = (after.st_dev, after.st_ino)
        except BaseException:
            connection.close()
            raise

    def _close_connection(self) -> None:
        connection = self._connection
        if connection is not None:
            self._connection = None
            connection.close()

    def _checkpoint_for_replace(self) -> None:
        connection = self._ensure_open()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        connection.execute("PRAGMA journal_mode = DELETE").fetchone()
        connection.commit()

    def _fsync_named_file(self, filename: str) -> None:
        parent_fd = self._ensure_parent_open()
        descriptor = os.open(
            filename,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("rebuilt database target must be a regular file")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _unlink_sidecars(self, filename: str) -> None:
        parent_fd = self._parent_fd
        if parent_fd is None:
            return
        for suffix in ("", "-journal", "-wal", "-shm"):
            with suppress(FileNotFoundError):
                os.unlink(f"{filename}{suffix}", dir_fd=parent_fd)

    def _ensure_open(self) -> sqlite3.Connection:
        connection = self._connection
        if connection is None:
            raise RuntimeError("retrieval index is closed")
        self._ensure_target_identity()
        return connection

    def _ensure_parent_open(self) -> int:
        parent_fd = self._parent_fd
        if parent_fd is None:
            raise RuntimeError("retrieval index is closed")
        return parent_fd

    def _ensure_parent_identity(self) -> None:
        parent_fd = self._ensure_parent_open()
        expected = os.fstat(parent_fd)
        try:
            actual = os.stat(self.path.parent, follow_symlinks=False)
        except FileNotFoundError as error:
            raise RuntimeError("database parent changed after index open") from error
        if not stat.S_ISDIR(actual.st_mode) or (actual.st_dev, actual.st_ino) != (
            expected.st_dev,
            expected.st_ino,
        ):
            raise RuntimeError("database parent changed after index open")

    def _ensure_target_identity(self) -> None:
        self._ensure_parent_identity()
        status = self._target_status()
        if status is None or not stat.S_ISREG(status.st_mode):
            raise RuntimeError("database target changed after index open")
        if self._db_identity != (status.st_dev, status.st_ino):
            raise RuntimeError("database target changed after index open")

    def _refresh_identity(self) -> None:
        status = self._target_status()
        if status is None or not stat.S_ISREG(status.st_mode):
            raise RuntimeError("database target changed after index write")
        self._db_identity = (status.st_dev, status.st_ino)

    def _target_status(self) -> os.stat_result | None:
        parent_fd = self._ensure_parent_open()
        try:
            return os.stat(self._filename, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None


def _validated_identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048 or "\x00" in value:
        raise ValueError(f"{field} must be bounded non-empty text")
    return value


def _validated_source_id(value: str) -> str:
    value = _validated_identifier(value, "source_id")
    if value in {".", ".."} or "/" in value or "\\" in value or Path(value).is_absolute():
        raise ValueError("source_id must be a safe path segment")
    return value


def _artifact_id(artifact: IndexedArtifact) -> str:
    if isinstance(artifact, ReferenceArtifact):
        return artifact.artifact_id
    if isinstance(artifact, KnowledgeCase):
        return artifact.case_id
    if isinstance(artifact, CaseStep):
        return artifact.step_id
    return artifact.rule_id


def _canonical_json(artifact: IndexedArtifact) -> str:
    return json.dumps(
        artifact.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _stored_metadata_matches(row: sqlite3.Row, artifact: IndexedArtifact) -> bool:
    try:
        owner_source_id = _validated_source_id(row["owner_source_id"])
    except (TypeError, ValueError):
        return False
    assessment = artifact.assessment
    observed_at = artifact.observed_at if isinstance(artifact, ReferenceArtifact) else None
    return (
        row["canonical_path"] == f"semantic_bundles/{owner_source_id}.json"
        and row["artifact_type"] == artifact.artifact_type
        and row["knowledge_role"] == artifact.knowledge_role
        and row["verification_status"] == assessment.verification_status
        and row["source_reliability"] == assessment.source_reliability
        and row["extraction_confidence"] == assessment.extraction_confidence
        and row["generalizability"] == assessment.generalizability
        and row["context_specificity"] == assessment.context_specificity
        and row["support_count"] == assessment.support_count
        and row["contradiction_count"] == assessment.contradiction_count
        and row["observed_outcome"] == assessment.observed_outcome
        and row["observed_at"] == observed_at
        and row["freshness_observed_at"] == assessment.freshness_observed_at
        and row["independence_group"] == assessment.independence_group
    )


def _lane_clause(lane: EpistemicLane) -> str:
    if lane is EpistemicLane.GUIDANCE:
        return "a.artifact_type = 'decision_rule'"
    if lane is EpistemicLane.CASE_STEP:
        return (
            "a.artifact_type = 'case_step' AND a.knowledge_role != 'negative_case' "
            "AND a.observed_outcome != 'failure'"
        )
    if lane is EpistemicLane.NEGATIVE_EVIDENCE:
        return (
            "((a.artifact_type = 'case_step' AND "
            "(a.knowledge_role = 'negative_case' OR a.observed_outcome = 'failure')) "
            "OR a.artifact_type IN ('negative_evidence', 'anti_pattern', 'exception'))"
        )
    return (
        "a.artifact_type NOT IN "
        "('case', 'case_step', 'decision_rule', 'negative_evidence', 'anti_pattern', 'exception')"
    )


def _rank_quality(value: object) -> float:
    try:
        rank = -float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(rank) or rank <= 0.0:
        return 0.0
    return rank


def _normalise_rank(value: object, max_quality: float) -> float:
    quality = _rank_quality(value)
    if max_quality <= 0.0:
        return 0.0
    return min(1.0, quality / max_quality)


def _match_evidence(
    row: sqlite3.Row,
    terms: tuple[tuple[str, list[str]], ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    matched_terms: set[str] = set()
    matched_fields: set[str] = set()
    evidence: set[str] = set()
    for field in _FTS_FIELDS:
        field_tokens = {token.casefold() for token in _WORD.findall(row[field])}
        for term, tokens in terms:
            normalized_tokens = {token.casefold() for token in tokens}
            if normalized_tokens and normalized_tokens <= field_tokens:
                matched_terms.add(term)
                matched_fields.add(field)
                evidence.add(f"{field}: {term}"[:2048])
    return tuple(sorted(matched_terms)), tuple(sorted(matched_fields)), tuple(sorted(evidence))


__all__ = ["SQLiteRetrievalIndex"]
