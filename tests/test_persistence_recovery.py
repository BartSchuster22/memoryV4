import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.migrations as migration_module
from app.main import create_app
from app.migrations import Migration, migrate, rollback_all
from app.recovery import (
    backup_database,
    inspect_database,
    recover_interrupted_restore,
    restore_database,
)
from app.schemas import RecordCreate
from app.sqlite_runtime import (
    DatabaseInUseError,
    IntegrityCheckError,
    PersistenceError,
    SchemaCompatibilityError,
    connect_sqlite,
)
from app.storage import SqliteStore


def record(title: str, *, scope: str = "org:a") -> RecordCreate:
    return RecordCreate(
        title=title,
        content=f"durable content for {title}",
        role="active",
        lifecycle="working",
        scope_path=scope,
    )


def test_atomic_migration_claim_rolls_back_failed_schema_change(tmp_path, monkeypatch) -> None:
    database = tmp_path / "atomic.sqlite"
    migrate(database)

    def fail_after_schema_change(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE should_rollback(id INTEGER PRIMARY KEY)")
        raise RuntimeError("injected migration failure")

    injected = Migration("9999_injected_failure", fail_after_schema_change, lambda conn: None)
    monkeypatch.setattr(migration_module, "MIGRATIONS", [*migration_module.MIGRATIONS, injected])

    with pytest.raises(RuntimeError, match="injected migration failure"):
        migrate(database)

    with sqlite3.connect(database) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='should_rollback'"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version='9999_injected_failure'"
        ).fetchone() is None


def test_atomic_migration_rollback_claim_survives_injected_down_failure(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "atomic-down.sqlite"

    def create_source(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE rollback_source(id INTEGER PRIMARY KEY)")

    def fail_after_down_change(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE down_change_should_rollback(id INTEGER PRIMARY KEY)")
        conn.execute("DROP TABLE rollback_source")
        raise RuntimeError("injected rollback failure")

    injected = Migration("9999_injected_down", create_source, fail_after_down_change)
    monkeypatch.setattr(
        migration_module, "MIGRATIONS", [*migration_module.MIGRATIONS, injected]
    )
    migrate(database)

    with pytest.raises(RuntimeError, match="injected rollback failure"):
        rollback_all(database)

    with sqlite3.connect(database) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rollback_source'"
        ).fetchone() is not None
        assert conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='down_change_should_rollback'"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version='9999_injected_down'"
        ).fetchone() is not None


def test_migration_history_checksum_gap_and_future_version_fail_closed(tmp_path) -> None:
    checksum_database = tmp_path / "checksum.sqlite"
    migrate(checksum_database)
    with sqlite3.connect(checksum_database) as conn:
        conn.execute(
            "UPDATE schema_migrations SET checksum='tampered' WHERE version='0003_core_objects'"
        )
    with pytest.raises(SchemaCompatibilityError, match="checksum mismatch"):
        migrate(checksum_database)

    future_database = tmp_path / "future.sqlite"
    migrate(future_database)
    with sqlite3.connect(future_database) as conn:
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at, checksum) VALUES (?, ?, ?)",
            ("9999_future", "2026-08-08T00:00:00+00:00", "x"),
        )
    with pytest.raises(SchemaCompatibilityError, match="unsupported migrations"):
        migrate(future_database)

    gap_database = tmp_path / "gap.sqlite"
    migrate(gap_database)
    with sqlite3.connect(gap_database) as conn:
        conn.execute("DELETE FROM schema_migrations WHERE version='0003_core_objects'")
    with pytest.raises(SchemaCompatibilityError, match="valid prefix"):
        migrate(gap_database)


def test_startup_repairs_derived_fts_without_changing_source_records(tmp_path) -> None:
    database = tmp_path / "fts.sqlite"
    store = SqliteStore(database)
    created = store.create_record(record("recoverable index"), actor="worker:test")
    store.close()
    with sqlite3.connect(database) as conn:
        conn.execute("DELETE FROM records_fts WHERE id = ?", (created.id,))
        assert conn.execute("SELECT count(*) FROM records_fts").fetchone()[0] == 0

    recovered = SqliteStore(database)
    results = recovered.search_records(
        "recoverable", scope_path="org:a", actor="reader:test"
    )
    recovered.close()
    assert [result.record.id for result in results] == [created.id]


def test_mutation_and_audit_roll_back_together_on_injected_failure(tmp_path) -> None:
    database = tmp_path / "transaction.sqlite"
    store = SqliteStore(database)
    with sqlite3.connect(database) as conn:
        conn.execute(
            """
            CREATE TRIGGER fail_record_create_audit BEFORE INSERT ON audit_events
            WHEN NEW.action = 'record.create'
            BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END
            """
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected audit failure"):
        store.create_record(record("must roll back"), actor="worker:test")
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT count(*) FROM records").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 0
    store.close()


def test_online_backup_manifest_integrity_and_json_corruption_detection(tmp_path) -> None:
    database = tmp_path / "live.sqlite"
    backup = tmp_path / "backups" / "snapshot.sqlite"
    store = SqliteStore(database)
    store.create_record(record("backup source"), actor="worker:test")

    report = backup_database(database, backup)
    assert report["status"] == "ok"
    assert report["manifest_verified"] is True
    assert report["migrations"] == [migration.version for migration in migration_module.MIGRATIONS]
    assert inspect_database(backup)["manifest_verified"] is True
    assert database.stat().st_mode & 0o777 == 0o600
    assert backup.stat().st_mode & 0o777 == 0o600
    assert backup.with_name(f"{backup.name}.manifest.json").stat().st_mode & 0o777 == 0o600

    backup.write_bytes(backup.read_bytes() + b"tamper")
    with pytest.raises(IntegrityCheckError, match="manifest checksum"):
        inspect_database(backup)

    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE records SET attrs_json='not-json'")
    with pytest.raises(IntegrityCheckError, match="invalid JSON"):
        inspect_database(database)
    store.close()


def test_restore_is_offline_only_and_preserves_verified_snapshot(tmp_path) -> None:
    database = tmp_path / "live.sqlite"
    backup = tmp_path / "snapshot.sqlite"
    store = SqliteStore(database)
    before, replayed = store.create_record_idempotent(
        record("before backup"),
        actor="worker:test",
        idempotency_key="restore-replay-key",
        request_hash="stable-request-hash",
    )
    assert replayed is False
    backup_database(database, backup)

    with pytest.raises(DatabaseInUseError):
        restore_database(database, backup)

    store.create_record(record("after backup"), actor="worker:test")
    store.close()
    restored = restore_database(database, backup)
    assert restored["status"] == "ok"
    assert restored["previous"] is not None

    reopened = SqliteStore(database)
    assert [item.id for item in reopened.list_records(scope_path="org:a")] == [before.id]
    replay, replayed = reopened.create_record_idempotent(
        record("before backup"),
        actor="worker:test",
        idempotency_key="restore-replay-key",
        request_hash="stable-request-hash",
    )
    assert replayed is True
    assert replay.id == before.id
    assert reopened.count_audit_events(action="record.create") == 1
    reopened.close()


def test_interrupted_restore_marker_completes_before_startup(tmp_path) -> None:
    source = tmp_path / "source.sqlite"
    target = tmp_path / "target.sqlite"
    staged = tmp_path / ".target.sqlite.interrupted.restore"
    previous = tmp_path / "target.sqlite.pre-restore-test"
    source_store = SqliteStore(source)
    wanted = source_store.create_record(record("wanted"), actor="worker:test")
    source_store.close()
    backup_database(source, staged)

    target_store = SqliteStore(target)
    target_store.create_record(record("replace me"), actor="worker:test")
    target_store.close()
    marker = tmp_path / ".target.sqlite.restore-pending.json"
    marker.write_text(
        json.dumps(
            {
                "format": "memoryv4-restore-v1",
                "temporary": str(staged),
                "temporary_sha256": hashlib.sha256(staged.read_bytes()).hexdigest(),
                "previous": str(previous),
            }
        )
    )

    assert recover_interrupted_restore(target) is True
    assert marker.exists() is False
    assert previous.exists() is True
    recovered = SqliteStore(target)
    assert [item.id for item in recovered.list_records(scope_path="org:a")] == [wanted.id]
    recovered.close()


def test_wal_commits_survive_abrupt_process_exit_and_restart(tmp_path) -> None:
    database = tmp_path / "wal.sqlite"
    script = """
import os
from pathlib import Path
from app.schemas import RecordCreate
from app.storage import SqliteStore
store = SqliteStore(Path(os.environ['CHILD_DB']))
store.create_record(
    RecordCreate(
        title='crash-safe',
        content='committed',
        role='active',
        lifecycle='working',
        scope_path='org:a',
    ),
    actor='child',
)
os._exit(0)
"""
    environment = dict(os.environ, CHILD_DB=str(database))
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=environment,
        check=False,
    )
    assert completed.returncode == 0
    reopened = SqliteStore(database)
    assert [item.title for item in reopened.list_records(scope_path="org:a")] == ["crash-safe"]
    reopened.close()


def test_runtime_uses_wal_full_sync_and_corrupt_startup_fails_fast(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "pragmas.sqlite"
    store = SqliteStore(database, integrity_check="full")
    with connect_sqlite(database) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    store.close()

    corrupt = tmp_path / "corrupt-startup.sqlite"
    corrupt.write_bytes(b"not a sqlite database")
    monkeypatch.setenv("MEMORYV4_DB_PATH", str(corrupt))
    application = create_app()
    with pytest.raises((sqlite3.DatabaseError, PersistenceError)):
        with TestClient(application):
            pass


def test_recovery_cli_backup_verify_and_restore_round_trip(tmp_path) -> None:
    database = tmp_path / "cli-live.sqlite"
    backup = tmp_path / "cli-backup.sqlite"
    store = SqliteStore(database)
    wanted = store.create_record(record("CLI snapshot"), actor="worker:test")
    store.close()
    project = str(Path(__file__).resolve().parents[1])

    for command in (
        ["backup", "--database", str(database), "--output", str(backup)],
        ["verify", "--backup", str(backup)],
    ):
        completed = subprocess.run(
            [sys.executable, "-m", "app.recovery", *command],
            cwd=project,
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout)["status"] == "ok"

    changed = SqliteStore(database)
    changed.create_record(record("discarded"), actor="worker:test")
    changed.close()
    restored = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.recovery",
            "restore",
            "--database",
            str(database),
            "--backup",
            str(backup),
        ],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
    )
    assert restored.returncode == 0, restored.stderr
    reopened = SqliteStore(database)
    assert [item.id for item in reopened.list_records(scope_path="org:a")] == [wanted.id]
    reopened.close()


def test_runtime_corruption_degrades_health_and_returns_safe_503(tmp_path, monkeypatch) -> None:
    database = tmp_path / "runtime.sqlite"
    monkeypatch.setenv("MEMORYV4_DB_PATH", str(database))
    monkeypatch.setenv(
        "MEMORYV4_API_KEYS",
        '{"reader":{"actor":"reader","scope_path":"org:a",'
        '"permissions":["memory.read"]}}',
    )
    client = TestClient(create_app(), raise_server_exceptions=False)
    assert client.get("/health").json()["status"] == "ok"
    for suffix in ("-wal", "-shm"):
        try:
            os.remove(f"{database}{suffix}")
        except FileNotFoundError:
            pass
    database.write_bytes(b"not a sqlite database")

    assert client.get("/health").json()["status"] == "degraded"
    unavailable = client.get("/records", headers={"Authorization": "Bearer reader"})
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "storage_unavailable"
    assert "sqlite" not in unavailable.text.lower()


def test_settings_reject_unsafe_recovery_values(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MEMORYV4_DB_PATH", str(tmp_path / "settings.sqlite"))
    monkeypatch.setenv("MEMORYV4_SQLITE_BUSY_TIMEOUT_MS", "1")
    with pytest.raises(ValueError, match="BUSY_TIMEOUT"):
        create_app()
    monkeypatch.setenv("MEMORYV4_SQLITE_BUSY_TIMEOUT_MS", "5000")
    monkeypatch.setenv("MEMORYV4_STARTUP_INTEGRITY_CHECK", "skip")
    with pytest.raises(ValueError, match="STARTUP_INTEGRITY_CHECK"):
        create_app()
