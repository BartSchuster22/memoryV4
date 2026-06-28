"""Minimal additive migration harness for the SQLite Store adapter."""
from __future__ import annotations

import sqlite3

CORE_MIGRATIONS: tuple[tuple[str, str], ...] = (
    (
        "0001_core_governed_objects",
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
            entity_id TEXT REFERENCES entities(id),
            entity_type TEXT NOT NULL,
            title TEXT NOT NULL,
            topic TEXT NOT NULL,
            content TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('canonical','active','evidence','exhaust')),
            lifecycle TEXT NOT NULL CHECK(lifecycle IN ('live','working','superseded','archived','expired')),
            author_actor TEXT NOT NULL,
            write_policy_json TEXT NOT NULL DEFAULT '{}',
            scope_path TEXT NOT NULL DEFAULT 'global',
            source_refs_json TEXT NOT NULL DEFAULT '[]',
            attrs_json TEXT NOT NULL DEFAULT '{}',
            superseded_by TEXT REFERENCES records(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_records_topic ON records(topic);
        CREATE INDEX IF NOT EXISTS idx_records_scope ON records(scope_path);
        CREATE INDEX IF NOT EXISTS idx_records_governance ON records(role, lifecycle);

        CREATE TABLE IF NOT EXISTS relations (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('canonical','active','evidence','exhaust')),
            lifecycle TEXT NOT NULL CHECK(lifecycle IN ('live','working','superseded','archived','expired')),
            scope_path TEXT NOT NULL DEFAULT 'global',
            attrs_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_id);
        CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_id);
        CREATE INDEX IF NOT EXISTS idx_relations_scope ON relations(scope_path);

        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            artifact_type TEXT NOT NULL,
            uri TEXT NOT NULL,
            media_type TEXT,
            role TEXT NOT NULL CHECK(role IN ('canonical','active','evidence','exhaust')),
            lifecycle TEXT NOT NULL CHECK(lifecycle IN ('live','working','superseded','archived','expired')),
            scope_path TEXT NOT NULL DEFAULT 'global',
            attrs_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_artifacts_scope ON artifacts(scope_path);

        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            actor TEXT NOT NULL,
            record_id TEXT,
            detail_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS retrieval_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            actor TEXT NOT NULL,
            scope_path TEXT NOT NULL DEFAULT 'global',
            record_ids_json TEXT NOT NULL DEFAULT '[]',
            detail_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_retrieval_events_scope ON retrieval_events(scope_path);

        CREATE TABLE IF NOT EXISTS health_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL CHECK(kind IN ('contradiction','compaction','decay','orphan')),
            refs_json TEXT NOT NULL,
            detail_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved','dismissed')),
            created_at TEXT NOT NULL,
            UNIQUE(kind, refs_json)
        );
        """,
    ),
    (
        "0002_record_author_and_write_policy",
        """
        -- Columns are added by _ensure_governance_columns so this migration remains
        -- idempotent for both fresh and already-bootstrapped SQLite databases.
        """,
    ),
)


def run_migrations(conn: sqlite3.Connection) -> list[str]:
    """Apply unapplied core migrations and return versions applied in this call."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    applied = {
        row[0]
        for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    }
    applied_now: list[str] = []
    for version, sql in CORE_MIGRATIONS:
        if version in applied:
            continue
        conn.executescript(sql)
        _ensure_legacy_columns(conn)
        _ensure_governance_columns(conn)
        conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
        applied_now.append(version)
    return applied_now


def _ensure_legacy_columns(conn: sqlite3.Connection) -> None:
    """Keep P1 additive if an earlier bootstrap DB already created records."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(records)").fetchall()}
    if "entity_id" not in columns:
        conn.execute("ALTER TABLE records ADD COLUMN entity_id TEXT REFERENCES entities(id)")
    if "attrs_json" not in columns:
        conn.execute("ALTER TABLE records ADD COLUMN attrs_json TEXT NOT NULL DEFAULT '{}'")


def _ensure_governance_columns(conn: sqlite3.Connection) -> None:
    """Add governed-record actor/policy fields to already-bootstrapped DBs."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(records)").fetchall()}
    if "author_actor" not in columns:
        conn.execute("ALTER TABLE records ADD COLUMN author_actor TEXT NOT NULL DEFAULT 'unknown:migrated'")
    if "write_policy_json" not in columns:
        conn.execute("ALTER TABLE records ADD COLUMN write_policy_json TEXT NOT NULL DEFAULT '{}'")
