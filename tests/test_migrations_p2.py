from __future__ import annotations

import sqlite3
from pathlib import Path

from app.migrations import CORE_MIGRATIONS, rollback_migration, run_migrations


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def _indexes(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_p2_migrations_fresh_db_are_idempotent_and_have_down_paths(tmp_path: Path) -> None:
    db = tmp_path / "fresh.sqlite3"
    with sqlite3.connect(db) as conn:
        applied = run_migrations(conn)
        rerun = run_migrations(conn)

        versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        assert applied == [migration.version for migration in CORE_MIGRATIONS]
        assert rerun == []
        assert versions == [
            "0001_core_governed_objects",
            "0002_record_author_and_write_policy",
            "0006_record_embeddings",
            "0007_promotion_log",
            "0008_task_canvas",
            "0009_registry_entity_types",
            "0011_scope",
            "0012_health_findings",
        ]

        tables = _tables(conn)
        assert {
            "record_embedding_features",
            "record_embedding_slots",
            "record_embeddings",
            "promotion_log",
            "task_canvas_nodes",
            "registry_entity_types",
            "health_findings",
        }.issubset(tables)
        assert "scope_path" in _columns(conn, "records")
        assert "scope_path" in _columns(conn, "entities")
        assert {"idx_records_scope", "idx_entities_scope", "idx_health_findings_status"}.issubset(_indexes(conn))

        # Down paths remove only the objects/columns introduced by the P2 migrations.
        for version in [
            "0012_health_findings",
            "0011_scope",
            "0009_registry_entity_types",
            "0008_task_canvas",
            "0007_promotion_log",
            "0006_record_embeddings",
        ]:
            rollback_migration(conn, version)

        remaining_tables = _tables(conn)
        assert "records" in remaining_tables
        assert "entities" in remaining_tables
        assert "promotion_log" not in remaining_tables
        assert "record_embeddings" not in remaining_tables
        assert "scope_path" not in _columns(conn, "records")
        assert "scope_path" not in _columns(conn, "entities")


def test_p2_migrations_upgrade_seeded_v3_like_fixture_without_core_data_loss(tmp_path: Path) -> None:
    db = tmp_path / "upgrade.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            INSERT INTO schema_migrations(version) VALUES ('0001_core_governed_objects'), ('0002_record_author_and_write_policy');
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
                role TEXT NOT NULL CHECK(role IN ('canonical','active','evidence','exhaust')),
                lifecycle TEXT NOT NULL CHECK(lifecycle IN ('live','working','superseded','archived','expired')),
                author_actor TEXT NOT NULL DEFAULT 'unknown:migrated',
                write_policy_json TEXT NOT NULL DEFAULT '{}',
                source_refs_json TEXT NOT NULL DEFAULT '[]',
                attrs_json TEXT NOT NULL DEFAULT '{}',
                superseded_by TEXT REFERENCES records(id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE relations (
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
            CREATE TABLE artifacts (
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
            CREATE TABLE audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, actor TEXT NOT NULL, record_id TEXT, detail_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
            CREATE TABLE retrieval_events (id INTEGER PRIMARY KEY AUTOINCREMENT, query TEXT NOT NULL, actor TEXT NOT NULL, record_ids_json TEXT NOT NULL DEFAULT '[]', detail_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
            INSERT INTO entities(id, entity_type, name, created_at, updated_at) VALUES ('ent_1', 'person', 'Alice', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
            INSERT INTO records(id, entity_id, entity_type, title, topic, content, role, lifecycle, author_actor, created_at, updated_at) VALUES ('rec_1', 'ent_1', 'decision', 'Keep governance', 'architecture', 'Governed records survive migration.', 'canonical', 'live', 'human:architect', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
            """
        )

        applied = run_migrations(conn)
        rerun = run_migrations(conn)

        assert applied == [
            "0006_record_embeddings",
            "0007_promotion_log",
            "0008_task_canvas",
            "0009_registry_entity_types",
            "0011_scope",
            "0012_health_findings",
        ]
        assert rerun == []
        assert conn.execute("SELECT title, scope_path FROM records WHERE id='rec_1'").fetchone() == (
            "Keep governance",
            "global",
        )
        assert conn.execute("SELECT name, scope_path FROM entities WHERE id='ent_1'").fetchone() == (
            "Alice",
            "global",
        )
        conn.execute(
            "INSERT INTO promotion_log(source_ref, record_id, decision, created_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            ("session:1", "rec_1", "tier3"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO promotion_log(source_ref, record_id, decision, created_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            ("session:1", "rec_1", "tier3"),
        )
        assert conn.execute("SELECT COUNT(*) FROM promotion_log WHERE source_ref='session:1'").fetchone()[0] == 1


def test_embedding_feature_flag_falls_back_when_sqlite_vec_is_unavailable(tmp_path: Path) -> None:
    with sqlite3.connect(tmp_path / "vectors.sqlite3") as conn:
        run_migrations(conn)
        feature = conn.execute(
            "SELECT provider, enabled, detail_json FROM record_embedding_features WHERE provider='sqlite-vec'"
        ).fetchone()
        assert feature is not None
        assert feature[1] in (0, 1)
        assert _columns(conn, "record_embeddings") >= {"record_id", "slot", "embedding_json", "embedding_blob"}
