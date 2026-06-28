from __future__ import annotations

import sqlite3
from pathlib import Path

from app.models import AuditEvent, Filter, Lifecycle, Record, Role
from app.ports import Store
from app.storage import SqliteStore


def test_sqlite_store_satisfies_port_and_preserves_governed_record_lifecycle(tmp_path: Path) -> None:
    store: Store = SqliteStore(tmp_path / "memoryv4.sqlite3")

    original = store.create_record(
        Record(
            id="rec_original",
            entity_type="decision",
            title="Storage decision",
            topic="storage",
            content="SQLite is the only implemented backend for P1.",
            role=Role.CANONICAL,
            lifecycle=Lifecycle.LIVE,
            scope_path="org:acme/project:psi",
            author_actor="human:operator",
            write_policy={"promote_requires": ["promote"], "supersede_requires": ["supersede"]},
            source_refs=["plan:section-3"],
        ),
        actor="test-agent",
    )

    assert store.get_record("rec_original") == original
    assert store.lexical_rank("SQLite backend", Filter(topic="storage", scope_prefixes=["org:acme/project:psi"]), 5) == [
        "rec_original"
    ]
    assert store.vector_rank(b"future-vector", Filter(scope_prefixes=["org:acme/project:psi"]), 5) == []

    archived = store.transition("rec_original", Lifecycle.ARCHIVED, actor="reviewer")
    assert archived.lifecycle is Lifecycle.ARCHIVED

    replacement = store.supersede(
        "rec_original",
        Record(
            id="rec_replacement",
            entity_type="decision",
            title="Storage decision",
            topic="storage",
            content="SQLite remains the only built backend; Postgres is a future adapter slot.",
            role=Role.CANONICAL,
            lifecycle=Lifecycle.LIVE,
            scope_path="org:acme/project:psi",
            author_actor="human:operator",
            write_policy={"promote_requires": ["promote"], "supersede_requires": ["supersede"]},
            source_refs=["plan:section-3"],
        ),
        actor="reviewer",
    )

    old = store.get_record("rec_original")
    assert old is not None
    assert old.lifecycle is Lifecycle.SUPERSEDED
    assert old.superseded_by == replacement.id
    assert store.get_record("rec_replacement") == replacement

    with sqlite3.connect(tmp_path / "memoryv4.sqlite3") as conn:
        actions = [row[0] for row in conn.execute("SELECT action FROM audit_events ORDER BY id")]
        persisted_governance = conn.execute(
            "SELECT author_actor, write_policy_json FROM records WHERE id = ?", ("rec_replacement",)
        ).fetchone()
        record_columns = {row[1] for row in conn.execute("PRAGMA table_info(records)").fetchall()}
        migrated_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        migrations = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]

    assert actions == ["create_record", "transition", "create_record", "supersede"]
    assert persisted_governance == (
        "human:operator",
        '{"promote_requires": ["promote"], "supersede_requires": ["supersede"]}',
    )
    assert {"author_actor", "write_policy_json"}.issubset(record_columns)
    assert {
        "schema_migrations",
        "entities",
        "records",
        "relations",
        "artifacts",
        "audit_events",
        "retrieval_events",
        "health_findings",
    }.issubset(migrated_tables)
    assert migrations == [
        "0001_core_governed_objects",
        "0002_record_author_and_write_policy",
        "0006_record_embeddings",
        "0007_promotion_log",
        "0008_task_canvas",
        "0009_registry_entity_types",
        "0010_public_scope_policy",
        "0011_scope_paths",
        "0012_health_findings",
    ]


def test_sqlite_store_writes_health_findings_without_canonical_mutation(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "memoryv4.sqlite3")

    store.write_finding({"kind": "decay", "refs": ["rec_1"], "detail": {"staleness": 0.75}})
    store.write_audit(AuditEvent(action="manual_check", actor="qa", record_id="rec_1", detail={"ok": True}))

    with sqlite3.connect(tmp_path / "memoryv4.sqlite3") as conn:
        finding = conn.execute("SELECT kind, refs_json, detail_json, status FROM health_findings").fetchone()
        audit = conn.execute("SELECT action, actor, record_id FROM audit_events WHERE action='manual_check'").fetchone()

    assert finding == ("decay", '["rec_1"]', '{"staleness": 0.75}', "open")
    assert audit == ("manual_check", "qa", "rec_1")


def test_core_repository_boundary_excludes_ui_and_orchestration_dirs() -> None:
    root = Path(__file__).resolve().parents[1]
    forbidden = [
        "web",
        "app/explorer.py",
        "app/web_auth_store.py",
        "operations",
        "ops/watchdogs",
        "kanban",
    ]

    present = [path for path in forbidden if (root / path).exists()]

    assert present == []
