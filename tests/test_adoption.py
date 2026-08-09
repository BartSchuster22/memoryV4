from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.adoption import (
    AdoptionError,
    apply_plan,
    create_plan,
    create_snapshot,
    logical_source_digest,
    reconcile,
)
from app.sqlite_runtime import connect_sqlite

SOURCE_DDL = """
CREATE TABLE entities (
  entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, title TEXT, state TEXT NOT NULL,
  attributes_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY(entity_type, entity_id)
);
CREATE TABLE records (
  id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
  schema_version TEXT NOT NULL, role TEXT NOT NULL, lifecycle TEXT NOT NULL,
  topic TEXT NOT NULL, title TEXT NOT NULL, tags_json TEXT NOT NULL, confidence REAL,
  supersedes_json TEXT NOT NULL, superseded_by TEXT, source_refs_json TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, author_actor TEXT NOT NULL,
  write_policy TEXT NOT NULL, locked_by_author INTEGER NOT NULL, deleted_at TEXT,
  deleted_by TEXT, delete_mode TEXT
);
CREATE TABLE record_content (record_id TEXT PRIMARY KEY, content TEXT NOT NULL);
CREATE TABLE relations (
  id INTEGER PRIMARY KEY, from_entity_type TEXT NOT NULL, from_entity_id TEXT NOT NULL,
  relation_type TEXT NOT NULL, to_entity_type TEXT NOT NULL, to_entity_id TEXT NOT NULL,
  attributes_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE artifacts (
  id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
  record_id TEXT, path TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
  mime_type TEXT, sensitive INTEGER NOT NULL, exportable INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE explorer_items (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, parent_id TEXT, parent_key TEXT NOT NULL,
  name TEXT NOT NULL, name_key TEXT NOT NULL, slug TEXT NOT NULL, record_id TEXT,
  artifact_id TEXT, entity_type TEXT, entity_id TEXT, mime_type TEXT, file_type TEXT,
  sort_order INTEGER, favorite INTEGER NOT NULL, last_opened_at TEXT, deleted_at TEXT,
  deleted_by TEXT, delete_mode TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  attributes_json TEXT NOT NULL
);
CREATE TABLE audit_events (id TEXT);
CREATE TABLE retrieval_events (id INTEGER);
CREATE TABLE stream_events (id TEXT);
CREATE TABLE identity_refs (id INTEGER);
CREATE TABLE import_batches (id TEXT);
CREATE TABLE import_items (id INTEGER);
CREATE TABLE backups (id TEXT);
CREATE TABLE web_users (id TEXT);
CREATE TABLE web_sessions (id TEXT);
"""


def make_source(path: Path, *, malformed: bool = False) -> None:
    timestamp = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(path) as connection:
        connection.executescript(SOURCE_DDL)
        connection.execute(
            "INSERT INTO entities VALUES(?,?,?,?,?,?,?)",
            (
                "project",
                "alpha",
                "Alpha",
                "active",
                "{" if malformed else '{"owner":"team"}',
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "rec_source",
                "project",
                "alpha",
                "0.1.0",
                "canonical",
                "archived",
                "handoff",
                "Source record",
                '["migration"]',
                0.9,
                "[]",
                None,
                '["file:alpha.md"]',
                timestamp,
                timestamp,
                "source-agent",
                "team_editable",
                0,
                None,
                None,
                None,
            ),
        )
        connection.execute("INSERT INTO record_content VALUES(?,?)", ("rec_source", "body"))
        connection.execute(
            "INSERT INTO relations VALUES(?,?,?,?,?,?,?,?)",
            (1, "project", "alpha", "uses", "tool", "missing-tool", "{}", timestamp),
        )
        connection.execute(
            "INSERT INTO explorer_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "exp_root",
                "folder",
                None,
                "root",
                "Root",
                "root",
                "root",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                0,
                None,
                None,
                None,
                None,
                timestamp,
                timestamp,
                "{}",
            ),
        )
        connection.execute(
            "INSERT INTO explorer_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "exp_file",
                "file",
                "exp_root",
                "exp_root",
                "Alpha.md",
                "alpha.md",
                "alpha.md",
                "rec_source",
                None,
                "project",
                "alpha",
                "text/markdown",
                "md",
                None,
                0,
                None,
                None,
                None,
                None,
                timestamp,
                timestamp,
                '{"path":"alpha.md"}',
            ),
        )
        connection.commit()


def test_snapshot_plan_apply_rerun_and_reconcile(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    snapshot = tmp_path / "snapshot.sqlite3"
    plan_path = tmp_path / "plan.json"
    second_plan_path = tmp_path / "plan-2.json"
    target = tmp_path / "target.sqlite3"
    make_source(source)
    with connect_sqlite(source, readonly=True) as connection:
        source_digest_before = logical_source_digest(connection)

    manifest = create_snapshot(source, snapshot)
    plan = create_plan(snapshot, plan_path, "org:test")
    second_plan = create_plan(snapshot, second_plan_path, "org:test")

    assert manifest["source_unchanged"] is True
    assert plan["object_sha256"] == second_plan["object_sha256"]
    assert plan["counts"] == {
        "entities": 4,
        "records": 1,
        "relations": 3,
        "artifacts": 0,
    }
    with connect_sqlite(source, readonly=True) as connection:
        assert logical_source_digest(connection) == source_digest_before

    dry_run = apply_plan(plan_path, target, dry_run=True)
    assert dry_run["committed"] is False
    assert dry_run["created"]["records"] == 1

    applied = apply_plan(plan_path, target)
    assert applied["committed"] is True
    assert applied["created"]["entities"] == 4
    report = reconcile(plan_path, target)
    assert report["status"] == "pass"
    assert report["fts_records"] == 1

    rerun = apply_plan(plan_path, target)
    assert sum(rerun["created"].values()) == 0
    with connect_sqlite(target, readonly=True) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='adoption.memoryv3'"
        ).fetchone()[0] == 1
        lifecycle = connection.execute(
            "SELECT lifecycle,previous_lifecycle,deleted_at FROM records WHERE id='rec_source'"
        ).fetchone()
        assert tuple(lifecycle) == ("archived", "live", "2026-01-01T00:00:00+00:00")


def test_malformed_source_json_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    snapshot = tmp_path / "snapshot.sqlite3"
    make_source(source, malformed=True)
    create_snapshot(source, snapshot)
    with pytest.raises(AdoptionError, match="malformed attributes_json"):
        create_plan(snapshot, tmp_path / "plan.json", "org:test")


def test_conflicting_target_id_fails_without_partial_commit(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    snapshot = tmp_path / "snapshot.sqlite3"
    plan_path = tmp_path / "plan.json"
    target = tmp_path / "target.sqlite3"
    make_source(source)
    create_snapshot(source, snapshot)
    create_plan(snapshot, plan_path, "org:test")
    apply_plan(plan_path, target)
    with connect_sqlite(target) as connection:
        connection.execute("UPDATE records SET title='conflict' WHERE id='rec_source'")
        connection.commit()
    with pytest.raises(AdoptionError, match="conflicting object IDs"):
        apply_plan(plan_path, target)
    with connect_sqlite(target, readonly=True) as connection:
        assert connection.execute(
            "SELECT title FROM records WHERE id='rec_source'"
        ).fetchone()[0] == "conflict"
