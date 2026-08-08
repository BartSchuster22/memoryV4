"""Additive SQLite migration registry for MemoryV4 foundation."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.schemas import MigrationResult, utc_now

MigrationFn = Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class Migration:
    version: str
    up: MigrationFn
    down: MigrationFn


def _executescript(conn: sqlite3.Connection, sql: str) -> None:
    conn.executescript(sql)


def _up_0001(conn: sqlite3.Connection) -> None:
    _executescript(
        conn,
        """
        CREATE TABLE IF NOT EXISTS entities (
          id TEXT PRIMARY KEY,
          entity_type TEXT NOT NULL,
          name TEXT NOT NULL,
          scope_path TEXT NOT NULL DEFAULT 'global',
          attrs_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_entities_scope ON entities(scope_path);

        CREATE TABLE IF NOT EXISTS records (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          content TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('canonical','active','evidence','exhaust')),
          lifecycle TEXT NOT NULL CHECK(
            lifecycle IN ('live','working','superseded','archived','expired')
          ),
          scope_path TEXT NOT NULL DEFAULT 'global',
          entity_type TEXT,
          topic TEXT,
          source_refs_json TEXT NOT NULL DEFAULT '[]',
          provenance_json TEXT NOT NULL DEFAULT '{}',
          attrs_json TEXT NOT NULL DEFAULT '{}',
          author_actor TEXT NOT NULL,
          superseded_by TEXT REFERENCES records(id),
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_records_scope ON records(scope_path);
        CREATE INDEX IF NOT EXISTS idx_records_role_lifecycle ON records(role, lifecycle);

        CREATE TABLE IF NOT EXISTS relations (
          id TEXT PRIMARY KEY,
          from_id TEXT NOT NULL,
          to_id TEXT NOT NULL,
          relation_type TEXT NOT NULL,
          scope_path TEXT NOT NULL DEFAULT 'global',
          provenance_json TEXT NOT NULL DEFAULT '{}',
          author_actor TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_relations_scope ON relations(scope_path);

        CREATE TABLE IF NOT EXISTS artifacts (
          id TEXT PRIMARY KEY,
          record_id TEXT REFERENCES records(id),
          artifact_type TEXT NOT NULL,
          uri TEXT NOT NULL,
          checksum TEXT,
          scope_path TEXT NOT NULL DEFAULT 'global',
          provenance_json TEXT NOT NULL DEFAULT '{}',
          author_actor TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_artifacts_scope ON artifacts(scope_path);

        CREATE TABLE IF NOT EXISTS audit_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          action TEXT NOT NULL,
          object_type TEXT NOT NULL,
          object_id TEXT NOT NULL,
          actor TEXT NOT NULL,
          scope_path TEXT NOT NULL,
          detail_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_events_object ON audit_events(object_type, object_id);

        CREATE TABLE IF NOT EXISTS retrieval_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          query TEXT NOT NULL,
          scope_path TEXT NOT NULL,
          actor TEXT NOT NULL,
          result_count INTEGER NOT NULL,
          degraded INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_retrieval_events_scope ON retrieval_events(scope_path);
        """,
    )
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS records_fts "
            "USING fts5(id UNINDEXED, title, content)"
        )
    except sqlite3.OperationalError:
        # Some minimal SQLite builds may omit FTS5; SqliteStore falls back to LIKE search.
        pass


def _down_0001(conn: sqlite3.Connection) -> None:
    _executescript(
        conn,
        """
        DROP TABLE IF EXISTS records_fts;
        DROP TABLE IF EXISTS retrieval_events;
        DROP TABLE IF EXISTS audit_events;
        DROP TABLE IF EXISTS artifacts;
        DROP TABLE IF EXISTS relations;
        DROP TABLE IF EXISTS records;
        DROP TABLE IF EXISTS entities;
        """,
    )


def _up_0002(conn: sqlite3.Connection) -> None:
    _executescript(
        conn,
        """
        ALTER TABLE records ADD COLUMN write_policy TEXT NOT NULL DEFAULT 'author_only'
          CHECK(write_policy IN ('team_editable','author_only','admin_only','immutable'));
        ALTER TABLE records ADD COLUMN version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1);

        CREATE TABLE idempotency_requests (
          actor TEXT NOT NULL,
          operation TEXT NOT NULL,
          idempotency_key TEXT NOT NULL,
          request_hash TEXT NOT NULL,
          response_json TEXT NOT NULL,
          status_code INTEGER NOT NULL,
          object_id TEXT,
          created_at TEXT NOT NULL,
          PRIMARY KEY(actor, idempotency_key)
        );
        CREATE INDEX idx_idempotency_created_at ON idempotency_requests(created_at);
        """,
    )


def _down_0002(conn: sqlite3.Connection) -> None:
    _executescript(
        conn,
        """
        DROP TABLE IF EXISTS idempotency_requests;
        ALTER TABLE records DROP COLUMN version;
        ALTER TABLE records DROP COLUMN write_policy;
        """,
    )


def _up_0003(conn: sqlite3.Connection) -> None:
    _executescript(
        conn,
        """
        ALTER TABLE entities RENAME TO entities_foundation;
        CREATE TABLE entities (
          id TEXT NOT NULL,
          entity_type TEXT NOT NULL,
          name TEXT NOT NULL,
          scope_path TEXT NOT NULL DEFAULT 'global',
          attrs_json TEXT NOT NULL DEFAULT '{}',
          version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(entity_type, id)
        );
        INSERT INTO entities(
          id, entity_type, name, scope_path, attrs_json, version, created_at, updated_at
        )
        SELECT id, entity_type, name, scope_path, attrs_json, 1, created_at, updated_at
        FROM entities_foundation;
        DROP TABLE entities_foundation;
        CREATE INDEX idx_entities_scope ON entities(scope_path);
        CREATE INDEX idx_entities_type ON entities(entity_type);
        CREATE INDEX idx_entities_name ON entities(name);

        ALTER TABLE records ADD COLUMN entity_id TEXT;
        ALTER TABLE records ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE records ADD COLUMN confidence REAL CHECK(
          confidence IS NULL OR (confidence >= 0 AND confidence <= 1)
        );
        ALTER TABLE records ADD COLUMN supersedes TEXT REFERENCES records(id);
        ALTER TABLE records ADD COLUMN deleted_at TEXT;
        CREATE INDEX idx_records_entity ON records(entity_type, entity_id);
        CREATE INDEX idx_records_updated ON records(updated_at, id);

        ALTER TABLE relations RENAME TO relations_foundation;
        CREATE TABLE relations (
          id TEXT PRIMARY KEY,
          from_kind TEXT NOT NULL CHECK(from_kind IN ('entity','record','artifact')),
          from_entity_type TEXT,
          from_id TEXT NOT NULL,
          to_kind TEXT NOT NULL CHECK(to_kind IN ('entity','record','artifact')),
          to_entity_type TEXT,
          to_id TEXT NOT NULL,
          relation_type TEXT NOT NULL,
          scope_path TEXT NOT NULL DEFAULT 'global',
          provenance_json TEXT NOT NULL DEFAULT '{}',
          author_actor TEXT NOT NULL,
          version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        INSERT INTO relations(
          id, from_kind, from_id, to_kind, to_id, relation_type, scope_path,
          provenance_json, author_actor, version, created_at, updated_at
        )
        SELECT id, 'record', from_id, 'record', to_id, relation_type, scope_path,
               provenance_json, author_actor, 1, created_at, created_at
        FROM relations_foundation;
        DROP TABLE relations_foundation;
        CREATE INDEX idx_relations_scope ON relations(scope_path);
        CREATE INDEX idx_relations_from ON relations(from_kind, from_entity_type, from_id);
        CREATE INDEX idx_relations_to ON relations(to_kind, to_entity_type, to_id);
        CREATE INDEX idx_relations_type ON relations(relation_type);

        ALTER TABLE artifacts RENAME TO artifacts_foundation;
        CREATE TABLE artifacts (
          id TEXT PRIMARY KEY,
          record_id TEXT REFERENCES records(id),
          entity_type TEXT,
          entity_id TEXT,
          artifact_type TEXT NOT NULL,
          uri TEXT NOT NULL,
          checksum TEXT,
          scope_path TEXT NOT NULL DEFAULT 'global',
          provenance_json TEXT NOT NULL DEFAULT '{}',
          author_actor TEXT NOT NULL,
          version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        INSERT INTO artifacts(
          id, record_id, artifact_type, uri, checksum, scope_path, provenance_json,
          author_actor, version, created_at, updated_at
        )
        SELECT id, record_id, artifact_type, uri, checksum, scope_path, provenance_json,
               author_actor, 1, created_at, created_at
        FROM artifacts_foundation;
        DROP TABLE artifacts_foundation;
        CREATE INDEX idx_artifacts_scope ON artifacts(scope_path);
        CREATE INDEX idx_artifacts_record ON artifacts(record_id);
        CREATE INDEX idx_artifacts_entity ON artifacts(entity_type, entity_id);
        CREATE INDEX idx_artifacts_type ON artifacts(artifact_type);
        """,
    )


def _down_0003(conn: sqlite3.Connection) -> None:
    _executescript(
        conn,
        """
        ALTER TABLE artifacts RENAME TO artifacts_core;
        CREATE TABLE artifacts (
          id TEXT PRIMARY KEY,
          record_id TEXT REFERENCES records(id),
          artifact_type TEXT NOT NULL,
          uri TEXT NOT NULL,
          checksum TEXT,
          scope_path TEXT NOT NULL DEFAULT 'global',
          provenance_json TEXT NOT NULL DEFAULT '{}',
          author_actor TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        INSERT INTO artifacts
        SELECT id, record_id, artifact_type, uri, checksum, scope_path,
               provenance_json, author_actor, created_at
        FROM artifacts_core;
        DROP TABLE artifacts_core;
        CREATE INDEX idx_artifacts_scope ON artifacts(scope_path);

        ALTER TABLE relations RENAME TO relations_core;
        CREATE TABLE relations (
          id TEXT PRIMARY KEY,
          from_id TEXT NOT NULL,
          to_id TEXT NOT NULL,
          relation_type TEXT NOT NULL,
          scope_path TEXT NOT NULL DEFAULT 'global',
          provenance_json TEXT NOT NULL DEFAULT '{}',
          author_actor TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        INSERT INTO relations
        SELECT id, from_id, to_id, relation_type, scope_path,
               provenance_json, author_actor, created_at
        FROM relations_core;
        DROP TABLE relations_core;
        CREATE INDEX idx_relations_scope ON relations(scope_path);

        DROP INDEX IF EXISTS idx_records_entity;
        DROP INDEX IF EXISTS idx_records_updated;
        ALTER TABLE records DROP COLUMN deleted_at;
        ALTER TABLE records DROP COLUMN supersedes;
        ALTER TABLE records DROP COLUMN confidence;
        ALTER TABLE records DROP COLUMN tags_json;
        ALTER TABLE records DROP COLUMN entity_id;

        ALTER TABLE entities RENAME TO entities_core;
        CREATE TABLE entities (
          id TEXT PRIMARY KEY,
          entity_type TEXT NOT NULL,
          name TEXT NOT NULL,
          scope_path TEXT NOT NULL DEFAULT 'global',
          attrs_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        INSERT INTO entities(id, entity_type, name, scope_path, attrs_json, created_at, updated_at)
        SELECT CASE
                 WHEN count(*) OVER (PARTITION BY id) > 1 THEN entity_type || ':' || id
                 ELSE id
               END,
               entity_type, name, scope_path, attrs_json, created_at, updated_at
        FROM entities_core;
        DROP TABLE entities_core;
        CREATE INDEX idx_entities_scope ON entities(scope_path);
        """,
    )


def _up_0004(conn: sqlite3.Connection) -> None:
    _executescript(
        conn,
        """
        ALTER TABLE records ADD COLUMN previous_lifecycle TEXT
          CHECK(previous_lifecycle IS NULL OR previous_lifecycle IN ('live','working'));
        ALTER TABLE records ADD COLUMN lifecycle_changed_at TEXT;
        CREATE INDEX idx_records_supersedes ON records(supersedes);
        CREATE INDEX idx_records_superseded_by ON records(superseded_by);
        CREATE TRIGGER records_lifecycle_insert_guard
        BEFORE INSERT ON records
        WHEN (NEW.lifecycle IN ('archived','expired','superseded')
              AND NEW.previous_lifecycle IS NULL)
          OR (NEW.lifecycle IN ('live','working') AND NEW.previous_lifecycle IS NOT NULL)
          OR (NEW.lifecycle = 'superseded' AND NEW.superseded_by IS NULL)
          OR (NEW.lifecycle <> 'superseded' AND NEW.superseded_by IS NOT NULL)
          OR (NEW.lifecycle = 'archived' AND NEW.deleted_at IS NULL)
          OR (NEW.lifecycle <> 'archived' AND NEW.deleted_at IS NOT NULL)
        BEGIN
          SELECT RAISE(ABORT, 'invalid record lifecycle state');
        END;
        CREATE TRIGGER records_lifecycle_update_guard
        BEFORE UPDATE OF lifecycle, previous_lifecycle, superseded_by, deleted_at ON records
        WHEN (NEW.lifecycle IN ('archived','expired','superseded')
              AND NEW.previous_lifecycle IS NULL)
          OR (NEW.lifecycle IN ('live','working') AND NEW.previous_lifecycle IS NOT NULL)
          OR (NEW.lifecycle = 'superseded' AND NEW.superseded_by IS NULL)
          OR (NEW.lifecycle <> 'superseded' AND NEW.superseded_by IS NOT NULL)
          OR (NEW.lifecycle = 'archived' AND NEW.deleted_at IS NULL)
          OR (NEW.lifecycle <> 'archived' AND NEW.deleted_at IS NOT NULL)
        BEGIN
          SELECT RAISE(ABORT, 'invalid record lifecycle state');
        END;
        """,
    )


def _down_0004(conn: sqlite3.Connection) -> None:
    _executescript(
        conn,
        """
        DROP TRIGGER IF EXISTS records_lifecycle_update_guard;
        DROP TRIGGER IF EXISTS records_lifecycle_insert_guard;
        DROP INDEX IF EXISTS idx_records_superseded_by;
        DROP INDEX IF EXISTS idx_records_supersedes;
        ALTER TABLE records DROP COLUMN lifecycle_changed_at;
        ALTER TABLE records DROP COLUMN previous_lifecycle;
        """,
    )


def _up_0005(conn: sqlite3.Connection) -> None:
    _executescript(
        conn,
        """
        CREATE TABLE review_findings (
          id TEXT PRIMARY KEY,
          finding_type TEXT NOT NULL
            CHECK(finding_type IN ('candidate','contradiction','stale','health')),
          status TEXT NOT NULL DEFAULT 'open'
            CHECK(status IN ('open','resolved','dismissed')),
          subject_kind TEXT NOT NULL CHECK(subject_kind IN ('entity','record','artifact')),
          subject_entity_type TEXT,
          subject_id TEXT NOT NULL,
          detail_json TEXT NOT NULL DEFAULT '{}',
          resolution_json TEXT,
          scope_path TEXT NOT NULL,
          created_by_actor TEXT NOT NULL,
          resolved_by_actor TEXT,
          resolved_at TEXT,
          version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          CHECK(
            (subject_kind = 'entity' AND subject_entity_type IS NOT NULL) OR
            (subject_kind <> 'entity' AND subject_entity_type IS NULL)
          ),
          CHECK(
            (status = 'open' AND resolution_json IS NULL AND resolved_by_actor IS NULL
              AND resolved_at IS NULL) OR
            (status IN ('resolved','dismissed') AND resolution_json IS NOT NULL
              AND resolved_by_actor IS NOT NULL AND resolved_at IS NOT NULL)
          )
        );
        CREATE INDEX idx_review_findings_scope ON review_findings(scope_path);
        CREATE INDEX idx_review_findings_status ON review_findings(status, updated_at, id);
        CREATE INDEX idx_review_findings_type ON review_findings(finding_type, updated_at, id);
        CREATE INDEX idx_review_findings_subject ON review_findings(
          subject_kind, subject_entity_type, subject_id
        );
        CREATE INDEX idx_audit_events_scope_created
          ON audit_events(scope_path, created_at, id);
        CREATE INDEX idx_audit_events_action_created
          ON audit_events(action, created_at, id);
        CREATE INDEX idx_audit_events_actor_created
          ON audit_events(actor, created_at, id);
        CREATE INDEX idx_retrieval_events_scope_created
          ON retrieval_events(scope_path, created_at, id);
        CREATE INDEX idx_retrieval_events_actor_created
          ON retrieval_events(actor, created_at, id);
        """,
    )


def _down_0005(conn: sqlite3.Connection) -> None:
    _executescript(
        conn,
        """
        DROP INDEX IF EXISTS idx_retrieval_events_actor_created;
        DROP INDEX IF EXISTS idx_retrieval_events_scope_created;
        DROP INDEX IF EXISTS idx_audit_events_actor_created;
        DROP INDEX IF EXISTS idx_audit_events_action_created;
        DROP INDEX IF EXISTS idx_audit_events_scope_created;
        DROP TABLE IF EXISTS review_findings;
        """,
    )


MIGRATIONS = [
    Migration("0001_foundation", _up_0001, _down_0001),
    Migration("0002_governance", _up_0002, _down_0002),
    Migration("0003_core_objects", _up_0003, _down_0003),
    Migration("0004_record_lifecycle", _up_0004, _down_0004),
    Migration("0005_review_audit_operations", _up_0005, _down_0005),
]


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_registry(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version TEXT PRIMARY KEY,
          applied_at TEXT NOT NULL
        )
        """
    )


def migrate(database_path: Path) -> MigrationResult:
    applied: list[str] = []
    with _connect(database_path) as conn:
        _ensure_registry(conn)
        existing = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        for migration in MIGRATIONS:
            if migration.version in existing:
                continue
            migration.up(conn)
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (migration.version, utc_now()),
            )
            applied.append(migration.version)
    return MigrationResult(applied=applied)


def rollback_all(database_path: Path) -> None:
    with _connect(database_path) as conn:
        _ensure_registry(conn)
        existing = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        for migration in reversed(MIGRATIONS):
            if migration.version not in existing:
                continue
            migration.down(conn)
            conn.execute("DELETE FROM schema_migrations WHERE version = ?", (migration.version,))
