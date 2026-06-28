"""Additive, reversible SQLite migration harness for the MemoryV4 Store adapter."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    version: str
    up_sql: str
    down_sql: str


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
            author_actor TEXT NOT NULL,
            write_policy_json TEXT NOT NULL DEFAULT '{}',
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
        SELECT 1;
        """,
        """
        -- SQLite rollback of governed baseline columns is intentionally explicit;
        -- run_migrations never rolls back baseline migrations in automation.
        SELECT 1;
        """,
    ),
    Migration(
        "0006_record_embeddings",
        """
        CREATE TABLE IF NOT EXISTS record_embeddings (
            record_id TEXT PRIMARY KEY REFERENCES records(id) ON DELETE CASCADE,
            embedding BLOB NOT NULL,
            dimensions INTEGER NOT NULL DEFAULT 1024,
            model TEXT NOT NULL DEFAULT 'reserved',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
        """
        DROP TABLE IF EXISTS record_embeddings;
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
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
        CREATE TABLE IF NOT EXISTS task_canvases (
            record_id TEXT PRIMARY KEY REFERENCES records(id) ON DELETE CASCADE,
            artifact_id TEXT REFERENCES artifacts(id),
            node_map_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS task_canvas_nodes (
            node_id TEXT NOT NULL,
            record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
            ref_uri TEXT NOT NULL,
            attrs_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (record_id, node_id)
        );
        CREATE INDEX IF NOT EXISTS idx_task_canvas_nodes_node ON task_canvas_nodes(node_id);
        """,
        """
        DROP INDEX IF EXISTS idx_task_canvas_nodes_node;
        DROP TABLE IF EXISTS task_canvas_nodes;
        DROP TABLE IF EXISTS task_canvases;
        """,
    ),
    Migration(
        "0009_registry_entity_types",
        """
        CREATE TABLE IF NOT EXISTS entity_type_registry (
            entity_type TEXT PRIMARY KEY,
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT OR IGNORE INTO entity_type_registry(entity_type, description) VALUES
            ('person_or_user_profile', 'Generated Tier-1 persona/profile record'),
            ('project', 'Project memory object'),
            ('decision', 'Decision and rationale memory object'),
            ('runbook', 'Operational runbook memory object'),
            ('incident', 'Incident memory object'),
            ('release', 'Release memory object'),
            ('skill', 'Skill/procedure memory object'),
            ('tool', 'Tool memory object'),
            ('capability', 'Capability memory object'),
            ('workflow', 'Workflow memory object'),
            ('agent_profile', 'Agent profile memory object'),
            ('task', 'Task/canvas memory object');
        """,
        """
        DROP TABLE IF EXISTS entity_type_registry;
        """,
    ),
    Migration(
        "0010_public_scope_policy",
        """
        CREATE TABLE IF NOT EXISTS scope_policies (
            scope_path TEXT PRIMARY KEY,
            read_policy TEXT NOT NULL,
            write_policy TEXT NOT NULL,
            detail_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT OR IGNORE INTO scope_policies(scope_path, read_policy, write_policy, detail_json) VALUES
            ('global', 'ancestor_or_equal', 'scoped_actor', '{}'),
            ('public', 'opt_in', 'curator_admin_only', '{"reserved":true,"auto_promotion":false}');
        """,
        """
        DROP TABLE IF EXISTS scope_policies;
        """,
    ),
    Migration(
        "0011_scope_paths",
        """
        -- Scope columns are added conditionally by _ensure_scope_columns for safe
        -- upgrades from P1 fixtures that may or may not already carry scope_path.
        SELECT 1;
        """,
        """
        DROP INDEX IF EXISTS idx_records_scope;
        DROP INDEX IF EXISTS idx_entities_scope;
        DROP INDEX IF EXISTS idx_relations_scope;
        DROP INDEX IF EXISTS idx_artifacts_scope;
        DROP INDEX IF EXISTS idx_retrieval_events_scope;
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
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(kind, refs_json)
        );
        """,
        """
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
    _ensure_migration_object_table(conn)
    applied = {
        row[0]
        for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    }
    applied_now: list[str] = []
    for migration in CORE_MIGRATIONS:
        if migration.version in applied:
            continue
        _apply_migration(conn, migration)
        conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (migration.version,))
        applied_now.append(migration.version)
    return applied_now


def rollback_migration(conn: sqlite3.Connection, version: str) -> None:
    """Rollback one applied migration by version and remove its schema marker."""
    migration = _migration_by_version(version)
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version = ?", (version,)).fetchone() is None:
        return
    _ensure_migration_object_table(conn)
    if version == "0011_scope_paths":
        _rollback_scope_path_objects(conn, version)
    elif version == "0012_health_findings":
        if _migration_introduced_object(conn, version, "table", "health_findings", "health_findings"):
            conn.executescript(migration.down_sql)
    else:
        conn.executescript(migration.down_sql)
    conn.execute("DELETE FROM schema_migrations WHERE version = ?", (version,))
    conn.execute("DELETE FROM schema_migration_objects WHERE version = ?", (version,))


def _migration_by_version(version: str) -> Migration:
    for migration in CORE_MIGRATIONS:
        if migration.version == version:
            return migration
    raise KeyError(f"unknown migration: {version}")


def _apply_migration(conn: sqlite3.Connection, migration: Migration) -> None:
    introduced_objects: list[tuple[str, str, str]] = []
    if migration.version == "0012_health_findings" and not _table_exists(conn, "health_findings"):
        introduced_objects.append(("table", "health_findings", "health_findings"))
    conn.executescript(migration.up_sql)
    _ensure_legacy_columns(conn)
    _ensure_governance_columns(conn)
    if migration.version == "0011_scope_paths":
        introduced_objects.extend(_ensure_scope_columns(conn))
    _record_migration_objects(conn, migration.version, introduced_objects)


def _ensure_legacy_columns(conn: sqlite3.Connection) -> None:
    """Keep migrations additive if an earlier bootstrap DB already created records."""
    if not _table_exists(conn, "records"):
        return
    if not _column_exists(conn, "records", "entity_id"):
        conn.execute("ALTER TABLE records ADD COLUMN entity_id TEXT REFERENCES entities(id)")
    if not _column_exists(conn, "records", "attrs_json"):
        conn.execute("ALTER TABLE records ADD COLUMN attrs_json TEXT NOT NULL DEFAULT '{}'")


def _ensure_governance_columns(conn: sqlite3.Connection) -> None:
    """Add governed-record actor/policy fields to already-bootstrapped DBs."""
    if not _table_exists(conn, "records"):
        return
    if not _column_exists(conn, "records", "author_actor"):
        conn.execute("ALTER TABLE records ADD COLUMN author_actor TEXT NOT NULL DEFAULT 'unknown:migrated'")
    if not _column_exists(conn, "records", "write_policy_json"):
        conn.execute("ALTER TABLE records ADD COLUMN write_policy_json TEXT NOT NULL DEFAULT '{}'")


def _ensure_scope_columns(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    introduced_objects: list[tuple[str, str, str]] = []
    scoped_tables = ("records", "entities", "relations", "artifacts", "retrieval_events")
    for table in scoped_tables:
        if _table_exists(conn, table) and not _column_exists(conn, table, "scope_path"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN scope_path TEXT NOT NULL DEFAULT 'global'")
            introduced_objects.append(("column", table, "scope_path"))
    scope_indexes = {
        "records": "idx_records_scope",
        "entities": "idx_entities_scope",
        "relations": "idx_relations_scope",
        "artifacts": "idx_artifacts_scope",
        "retrieval_events": "idx_retrieval_events_scope",
    }
    for table, index_name in scope_indexes.items():
        if _table_exists(conn, table):
            index_existed = _index_exists(conn, index_name)
            conn.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table}(scope_path)")
            if not index_existed:
                introduced_objects.append(("index", table, index_name))
    return introduced_objects


def _ensure_migration_object_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migration_objects (
            version TEXT NOT NULL,
            object_type TEXT NOT NULL,
            table_name TEXT NOT NULL,
            object_name TEXT NOT NULL,
            PRIMARY KEY (version, object_type, table_name, object_name)
        )
        """
    )


def _record_migration_objects(
    conn: sqlite3.Connection,
    version: str,
    objects: list[tuple[str, str, str]],
) -> None:
    if not objects:
        return
    conn.executemany(
        """
        INSERT OR IGNORE INTO schema_migration_objects(version, object_type, table_name, object_name)
        VALUES (?, ?, ?, ?)
        """,
        [(version, object_type, table_name, object_name) for object_type, table_name, object_name in objects],
    )


def _migration_introduced_object(
    conn: sqlite3.Connection,
    version: str,
    object_type: str,
    table_name: str,
    object_name: str,
) -> bool:
    return (
        conn.execute(
            """
            SELECT 1 FROM schema_migration_objects
            WHERE version = ? AND object_type = ? AND table_name = ? AND object_name = ?
            """,
            (version, object_type, table_name, object_name),
        ).fetchone()
        is not None
    )


def _rollback_scope_path_objects(conn: sqlite3.Connection, version: str) -> None:
    rows = conn.execute(
        """
        SELECT object_type, table_name, object_name
        FROM schema_migration_objects
        WHERE version = ?
        ORDER BY CASE object_type WHEN 'index' THEN 0 WHEN 'column' THEN 1 ELSE 2 END
        """,
        (version,),
    ).fetchall()
    for object_type, table_name, object_name in rows:
        if object_type == "index":
            conn.execute(f"DROP INDEX IF EXISTS {object_name}")
        elif object_type == "column":
            _drop_column_if_exists(conn, table_name, object_name)


def _index_exists(conn: sqlite3.Connection, index: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?", (index,)).fetchone() is not None


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return column in {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _drop_column_if_exists(conn: sqlite3.Connection, table: str, column: str) -> None:
    if _table_exists(conn, table) and _column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
