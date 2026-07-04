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


MIGRATIONS = [Migration("0001_foundation", _up_0001, _down_0001)]


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
