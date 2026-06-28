from __future__ import annotations

import sqlite3
from pathlib import Path

from app.migrations import CORE_MIGRATIONS, rollback_migration, run_migrations
from app.storage import SqliteStore

P2_VERSIONS = [
    "0006_record_embeddings",
    "0007_promotion_log",
    "0008_task_canvas",
    "0009_registry_entity_types",
    "0010_public_scope_policy",
    "0011_scope_paths",
    "0012_health_findings",
]


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    }


def _indexes(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_p2_migration_versions_are_additive_and_ordered() -> None:
    versions = [migration.version for migration in CORE_MIGRATIONS]

    assert versions == ["0001_core_governed_objects", "0002_record_author_and_write_policy", *P2_VERSIONS]
    assert len(versions) == len(set(versions))
    for migration in CORE_MIGRATIONS:
        assert migration.up_sql.strip(), migration.version
        assert migration.down_sql.strip(), migration.version
    assert not any(version.startswith(("0003", "0004", "0005")) for version in versions)


def test_p2_fresh_database_rerun_noop_and_down_paths(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.sqlite3"
    with sqlite3.connect(db_path) as conn:
        applied = run_migrations(conn)
        rerun = run_migrations(conn)
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]

        assert applied == ["0001_core_governed_objects", "0002_record_author_and_write_policy", *P2_VERSIONS]
        assert rerun == []
        assert versions == applied
        assert {
            "record_embeddings",
            "promotion_log",
            "task_canvases",
            "task_canvas_nodes",
            "entity_type_registry",
            "scope_policies",
            "health_findings",
        }.issubset(_tables(conn))
        assert {"idx_records_scope", "idx_entities_scope", "idx_promotion_log_record"}.issubset(_indexes(conn))
        assert "scope_path" in _columns(conn, "records")
        assert "scope_path" in _columns(conn, "entities")
        assert conn.execute("SELECT COUNT(*) FROM entity_type_registry").fetchone()[0] >= 12
        assert conn.execute("SELECT read_policy, write_policy FROM scope_policies WHERE scope_path='public'").fetchone() == (
            "opt_in",
            "curator_admin_only",
        )

        for version in reversed(P2_VERSIONS):
            rollback_migration(conn, version)

        remaining = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        assert remaining == ["0001_core_governed_objects", "0002_record_author_and_write_policy"]
        assert not {
            "record_embeddings",
            "promotion_log",
            "task_canvases",
            "task_canvas_nodes",
            "entity_type_registry",
            "scope_policies",
            "health_findings",
        }.intersection(_tables(conn))


def test_p2_upgrade_fixture_from_p1_baseline_preserves_rows_and_applies_scope_defaults(tmp_path: Path) -> None:
    db_path = tmp_path / "upgrade.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            INSERT INTO schema_migrations(version) VALUES
                ('0001_core_governed_objects'),
                ('0002_record_author_and_write_policy');
            CREATE TABLE entities (
                id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                name TEXT NOT NULL,
                attrs_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE records (
                id TEXT PRIMARY KEY,
                entity_id TEXT REFERENCES entities(id),
                entity_type TEXT NOT NULL,
                title TEXT NOT NULL,
                topic TEXT NOT NULL,
                content TEXT NOT NULL,
                role TEXT NOT NULL,
                lifecycle TEXT NOT NULL,
                author_actor TEXT NOT NULL DEFAULT 'unknown:migrated',
                write_policy_json TEXT NOT NULL DEFAULT '{}',
                source_refs_json TEXT NOT NULL DEFAULT '[]',
                attrs_json TEXT NOT NULL DEFAULT '{}',
                superseded_by TEXT REFERENCES records(id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO entities(id, entity_type, name, created_at, updated_at)
            VALUES ('ent_1', 'decision', 'Storage', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
            INSERT INTO records(
                id, entity_id, entity_type, title, topic, content, role, lifecycle,
                author_actor, write_policy_json, source_refs_json, attrs_json, created_at, updated_at
            ) VALUES (
                'rec_1', 'ent_1', 'decision', 'Storage', 'storage', 'SQLite only', 'canonical', 'live',
                'human:operator', '{}', '[]', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
            );
            """
        )

        applied = run_migrations(conn)

        assert applied == P2_VERSIONS
        assert conn.execute("SELECT COUNT(*) FROM records WHERE id='rec_1'").fetchone()[0] == 1
        assert conn.execute("SELECT scope_path FROM records WHERE id='rec_1'").fetchone()[0] == "global"
        assert conn.execute("SELECT scope_path FROM entities WHERE id='ent_1'").fetchone()[0] == "global"
        assert conn.execute("SELECT COUNT(*) FROM promotion_log").fetchone()[0] == 0


def test_sqlite_store_initializes_with_p2_schema_and_idempotent_findings(tmp_path: Path) -> None:
    db_path = tmp_path / "store.sqlite3"
    store = SqliteStore(db_path)
    store.write_finding({"kind": "orphan", "refs": ["missing"], "detail": {"table": "relations"}})
    store.write_finding({"kind": "orphan", "refs": ["missing"], "detail": {"table": "relations"}})

    with sqlite3.connect(db_path) as conn:
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        finding_count = conn.execute("SELECT COUNT(*) FROM health_findings").fetchone()[0]

    assert versions == ["0001_core_governed_objects", "0002_record_author_and_write_policy", *P2_VERSIONS]
    assert finding_count == 1
