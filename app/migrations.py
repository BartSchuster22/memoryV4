"""Additive migration harness for the SQLite Store adapter."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    """A reversible SQLite schema migration."""

    version: str
    up: str
    down: str


CORE_MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        "0001_core_governed_objects",
        """
        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            name TEXT NOT NULL,
            attrs_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS records (
            id TEXT PRIMARY KEY,
            entity_id TEXT REFERENCES entities(id),
            entity_type TEXT NOT NULL,
            title TEXT NOT NULL,
            topic TEXT NOT NULL,
            content TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('canonical','active','evidence','exhaust')),
            lifecycle TEXT NOT NULL CHECK(lifecycle IN ('live','working','superseded','archived','expired')),
            source_refs_json TEXT NOT NULL DEFAULT '[]',
            attrs_json TEXT NOT NULL DEFAULT '{}',
            superseded_by TEXT REFERENCES records(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_records_topic ON records(topic);
        CREATE INDEX IF NOT EXISTS idx_records_governance ON records(role, lifecycle);

        CREATE TABLE IF NOT EXISTS relations (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('canonical','active','evidence','exhaust')),
            lifecycle TEXT NOT NULL CHECK(lifecycle IN ('live','working','superseded','archived','expired')),
            attrs_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_id);
        CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_id);

        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            artifact_type TEXT NOT NULL,
            uri TEXT NOT NULL,
            media_type TEXT,
            role TEXT NOT NULL CHECK(role IN ('canonical','active','evidence','exhaust')),
            lifecycle TEXT NOT NULL CHECK(lifecycle IN ('live','working','superseded','archived','expired')),
            attrs_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

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
            record_ids_json TEXT NOT NULL DEFAULT '[]',
            detail_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        """,
        """
        DROP TABLE IF EXISTS retrieval_events;
        DROP TABLE IF EXISTS audit_events;
        DROP TABLE IF EXISTS artifacts;
        DROP TABLE IF EXISTS relations;
        DROP TABLE IF EXISTS records;
        DROP TABLE IF EXISTS entities;
        """,
    ),
    Migration(
        "0002_record_author_and_write_policy",
        """
        -- Columns are added by _ensure_governance_columns so this migration remains
        -- idempotent for both fresh and already-bootstrapped SQLite databases.
        """,
        """
        ALTER TABLE records DROP COLUMN write_policy_json;
        ALTER TABLE records DROP COLUMN author_actor;
        """,
    ),
    Migration(
        "0006_record_embeddings",
        """
        CREATE TABLE IF NOT EXISTS record_embedding_features (
            provider TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
            detail_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS record_embedding_slots (
            slot TEXT PRIMARY KEY,
            dimensions INTEGER NOT NULL,
            provider TEXT NOT NULL DEFAULT 'sqlite-vec',
            model TEXT,
            attrs_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS record_embeddings (
            record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
            slot TEXT NOT NULL REFERENCES record_embedding_slots(slot) ON DELETE CASCADE,
            embedding_json TEXT,
            embedding_blob BLOB,
            dimensions INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(record_id, slot)
        );
        CREATE INDEX IF NOT EXISTS idx_record_embeddings_slot ON record_embeddings(slot);
        INSERT OR IGNORE INTO record_embedding_slots(slot, dimensions, provider, model)
        VALUES ('default', 1024, 'sqlite-vec', 'reserved');
        """,
        """
        DROP INDEX IF EXISTS idx_record_embeddings_slot;
        DROP TABLE IF EXISTS record_embeddings;
        DROP TABLE IF EXISTS record_embedding_slots;
        DROP TABLE IF EXISTS record_embedding_features;
        DROP TABLE IF EXISTS record_embeddings_vec;
        """,
    ),
    Migration(
        "0007_promotion_log",
        """
        CREATE TABLE IF NOT EXISTS promotion_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_ref TEXT NOT NULL,
            record_id TEXT REFERENCES records(id),
            decision TEXT NOT NULL CHECK(decision IN ('tier1','tier3','discard','conflict')),
            created_at TEXT NOT NULL,
            UNIQUE(source_ref)
        );
        CREATE INDEX IF NOT EXISTS idx_promotion_log_record ON promotion_log(record_id);
        """,
        """
        DROP INDEX IF EXISTS idx_promotion_log_record;
        DROP TABLE IF EXISTS promotion_log;
        """,
    ),
    Migration(
        "0008_task_canvas",
        """
        CREATE TABLE IF NOT EXISTS task_canvas_nodes (
            task_record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
            node_id TEXT NOT NULL,
            artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
            raw_ref TEXT NOT NULL,
            attrs_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(task_record_id, node_id)
        );
        CREATE INDEX IF NOT EXISTS idx_task_canvas_nodes_artifact ON task_canvas_nodes(artifact_id);
        """,
        """
        DROP INDEX IF EXISTS idx_task_canvas_nodes_artifact;
        DROP TABLE IF EXISTS task_canvas_nodes;
        """,
    ),
    Migration(
        "0009_registry_entity_types",
        """
        CREATE TABLE IF NOT EXISTS registry_entity_types (
            entity_type TEXT PRIMARY KEY,
            description TEXT NOT NULL DEFAULT '',
            reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT OR IGNORE INTO registry_entity_types(entity_type, description, reserved) VALUES
            ('person_or_user_profile', 'Generated Tier-1 persona/user profile records', 1),
            ('project', 'Project or product memory root', 1),
            ('decision', 'Decision memory with rationale and alternatives', 1),
            ('runbook', 'Operational procedure or recovery guide', 1),
            ('incident', 'Incident record and evidence trail', 1),
            ('release', 'Release notes and deployment evidence', 1),
            ('skill', 'Reusable agent skill or playbook', 1),
            ('tool', 'Tooling and integration knowledge', 1),
            ('capability', 'Agent or platform capability', 1),
            ('workflow', 'Workflow or process memory', 1),
            ('agent_profile', 'Agent profile configuration and behavior', 1),
            ('task', 'Task/canvas symbolic offload record', 1);
        """,
        """
        DROP TABLE IF EXISTS registry_entity_types;
        """,
    ),
    Migration(
        "0011_scope",
        """
        CREATE INDEX IF NOT EXISTS idx_records_scope ON records(scope_path);
        CREATE INDEX IF NOT EXISTS idx_entities_scope ON entities(scope_path);
        CREATE INDEX IF NOT EXISTS idx_relations_scope ON relations(scope_path);
        CREATE INDEX IF NOT EXISTS idx_artifacts_scope ON artifacts(scope_path);
        CREATE INDEX IF NOT EXISTS idx_retrieval_events_scope ON retrieval_events(scope_path);
        """,
        """
        DROP INDEX IF EXISTS idx_retrieval_events_scope;
        DROP INDEX IF EXISTS idx_artifacts_scope;
        DROP INDEX IF EXISTS idx_relations_scope;
        DROP INDEX IF EXISTS idx_entities_scope;
        DROP INDEX IF EXISTS idx_records_scope;
        ALTER TABLE retrieval_events DROP COLUMN scope_path;
        ALTER TABLE artifacts DROP COLUMN scope_path;
        ALTER TABLE relations DROP COLUMN scope_path;
        ALTER TABLE entities DROP COLUMN scope_path;
        ALTER TABLE records DROP COLUMN scope_path;
        """,
    ),
    Migration(
        "0012_health_findings",
        """
        CREATE TABLE IF NOT EXISTS health_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL CHECK(kind IN ('contradiction','compaction','decay','orphan')),
            refs_json TEXT NOT NULL,
            detail_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved','dismissed')),
            created_at TEXT NOT NULL,
            UNIQUE(kind, refs_json)
        );
        CREATE INDEX IF NOT EXISTS idx_health_findings_status ON health_findings(status);
        """,
        """
        DROP INDEX IF EXISTS idx_health_findings_status;
        DROP TABLE IF EXISTS health_findings;
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
    _ensure_legacy_columns(conn)
    _ensure_governance_columns(conn)
    applied = {
        row[0]
        for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    }
    applied_now: list[str] = []
    for migration in CORE_MIGRATIONS:
        if migration.version in applied:
            continue
        if migration.version == "0011_scope":
            _ensure_scope_columns(conn)
        conn.executescript(migration.up)
        if migration.version == "0002_record_author_and_write_policy":
            _ensure_governance_columns(conn)
        if migration.version == "0006_record_embeddings":
            _configure_embedding_feature(conn)
        conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (migration.version,))
        applied_now.append(migration.version)
    return applied_now


def rollback_migration(conn: sqlite3.Connection, version: str) -> None:
    """Run the down path for one applied migration and remove its version marker."""
    migrations = {migration.version: migration for migration in CORE_MIGRATIONS}
    if version not in migrations:
        raise KeyError(f"unknown migration: {version}")
    conn.executescript(migrations[version].down)
    conn.execute("DELETE FROM schema_migrations WHERE version = ?", (version,))


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_legacy_columns(conn: sqlite3.Connection) -> None:
    """Keep migrations additive if an earlier bootstrap DB already created records."""
    columns = _columns(conn, "records")
    if not columns:
        return
    if "entity_id" not in columns:
        conn.execute("ALTER TABLE records ADD COLUMN entity_id TEXT REFERENCES entities(id)")
    if "attrs_json" not in columns:
        conn.execute("ALTER TABLE records ADD COLUMN attrs_json TEXT NOT NULL DEFAULT '{}'")


def _ensure_governance_columns(conn: sqlite3.Connection) -> None:
    """Add governed-record actor/policy fields to already-bootstrapped DBs."""
    columns = _columns(conn, "records")
    if not columns:
        return
    if "author_actor" not in columns:
        conn.execute("ALTER TABLE records ADD COLUMN author_actor TEXT NOT NULL DEFAULT 'unknown:migrated'")
    if "write_policy_json" not in columns:
        conn.execute("ALTER TABLE records ADD COLUMN write_policy_json TEXT NOT NULL DEFAULT '{}'")


def _ensure_scope_columns(conn: sqlite3.Connection) -> None:
    """Add P2 owning-scope columns to core tables without rewriting existing rows."""
    for table in ("records", "entities", "relations", "artifacts", "retrieval_events"):
        columns = _columns(conn, table)
        if columns and "scope_path" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN scope_path TEXT NOT NULL DEFAULT 'global'")


def _configure_embedding_feature(conn: sqlite3.Connection) -> None:
    """Record whether sqlite-vec is usable; fall back to the portable slot table."""
    enabled = 0
    detail: dict[str, str] = {"fallback": "record_embeddings portable table"}
    try:
        conn.enable_load_extension(True)
        conn.execute("SELECT load_extension('sqlite_vec')")
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS record_embeddings_vec USING vec0(record_id TEXT PRIMARY KEY, embedding FLOAT[1024])")
        enabled = 1
        detail = {"virtual_table": "record_embeddings_vec"}
    except sqlite3.Error as exc:
        detail["reason"] = str(exc)
    finally:
        try:
            conn.enable_load_extension(False)
        except sqlite3.Error:
            pass
    conn.execute(
        """
        INSERT INTO record_embedding_features(provider, enabled, detail_json)
        VALUES ('sqlite-vec', ?, ?)
        ON CONFLICT(provider) DO UPDATE SET enabled=excluded.enabled, detail_json=excluded.detail_json
        """,
        (enabled, json.dumps(detail, sort_keys=True)),
    )
