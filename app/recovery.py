"""Offline-safe SQLite backup, verification, restore, and crash recovery CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.migrations import _validate_migration_history, migrate
from app.sqlite_runtime import (
    DatabaseLock,
    IntegrityCheckError,
    PersistenceError,
    assert_integrity,
    checkpoint,
    connect_sqlite,
)

JSON_COLUMNS = {
    "entities": ("attrs_json",),
    "records": ("tags_json", "source_refs_json", "provenance_json", "attrs_json"),
    "relations": ("provenance_json",),
    "artifacts": ("provenance_json",),
    "audit_events": ("detail_json",),
    "idempotency_requests": ("response_json",),
    "review_findings": ("detail_json", "resolution_json"),
}


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.chmod(temporary, 0o600)
    _fsync_file(temporary)
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _assert_json_columns(conn: sqlite3.Connection) -> None:
    tables = _table_names(conn)
    for table, columns in JSON_COLUMNS.items():
        if table not in tables:
            continue
        selected = ", ".join(columns)
        for row in conn.execute(f"SELECT rowid, {selected} FROM {table}"):
            for index, column in enumerate(columns, start=1):
                value = row[index]
                if value is None:
                    continue
                try:
                    json.loads(str(value))
                except (TypeError, json.JSONDecodeError) as exc:
                    raise IntegrityCheckError(
                        f"invalid JSON in {table}.{column} at rowid {row[0]}"
                    ) from exc


def _fts_consistent(conn: sqlite3.Connection) -> bool | None:
    if "records_fts" not in _table_names(conn):
        return None
    row = conn.execute(
        """
        SELECT EXISTS(
          SELECT id, title, content FROM records
          EXCEPT SELECT id, title, content FROM records_fts
        ) OR EXISTS(
          SELECT id, title, content FROM records_fts
          EXCEPT SELECT id, title, content FROM records
        )
        """
    ).fetchone()
    return row is not None and not bool(row[0])


def inspect_database(
    database_path: Path,
    *,
    manifest_path: Path | None = None,
    require_manifest: bool = False,
) -> dict[str, object]:
    if not database_path.is_file():
        raise PersistenceError(f"database does not exist: {database_path}")
    with connect_sqlite(database_path, readonly=True) as conn:
        assert_integrity(conn, full=True)
        if "schema_migrations" not in _table_names(conn):
            raise IntegrityCheckError("schema_migrations table is missing")
        versions = _validate_migration_history(conn)
        _assert_json_columns(conn)
        fts_consistent = _fts_consistent(conn)
        if fts_consistent is False:
            raise IntegrityCheckError("records_fts does not match records")
    checksum = _sha256(database_path)
    size = database_path.stat().st_size
    candidate_manifest = manifest_path or database_path.with_name(
        f"{database_path.name}.manifest.json"
    )
    manifest_verified = False
    if candidate_manifest.exists():
        manifest = json.loads(candidate_manifest.read_text())
        if manifest.get("format") != "memoryv4-sqlite-backup-v1":
            raise IntegrityCheckError("backup manifest format is unsupported")
        if manifest.get("sha256") != checksum or manifest.get("size_bytes") != size:
            raise IntegrityCheckError("backup manifest checksum or size mismatch")
        if manifest.get("migrations") != versions:
            raise IntegrityCheckError("backup manifest migration history mismatch")
        sidecars = [Path(f"{database_path}{suffix}") for suffix in ("-wal", "-shm")]
        if any(sidecar.exists() for sidecar in sidecars):
            raise IntegrityCheckError("backup artifact must not have WAL or SHM sidecars")
        manifest_verified = True
    elif require_manifest:
        raise IntegrityCheckError(f"backup manifest is missing: {candidate_manifest}")
    return {
        "status": "ok",
        "path": str(database_path),
        "sha256": checksum,
        "size_bytes": size,
        "migrations": versions,
        "fts": "absent" if fts_consistent is None else "consistent",
        "manifest_verified": manifest_verified,
    }


def backup_database(database_path: Path, output_path: Path) -> dict[str, object]:
    database_path = database_path.resolve()
    output_path = output_path.resolve()
    if database_path == output_path:
        raise PersistenceError("backup output must differ from the live database")
    if not database_path.is_file():
        raise PersistenceError(f"database does not exist: {database_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")
    try:
        with DatabaseLock(database_path, exclusive=False):
            migrate(database_path, integrity_check="full")
            with connect_sqlite(database_path, readonly=True) as source:
                with sqlite3.connect(temporary) as destination:
                    source.backup(destination)
                    destination.execute("PRAGMA journal_mode=DELETE")
                    assert_integrity(destination, full=True)
        report = inspect_database(
            temporary,
            manifest_path=temporary.with_name(f".{temporary.name}.manifest-not-written"),
        )
        os.chmod(temporary, 0o600)
        _fsync_file(temporary)
        for suffix in ("-wal", "-shm"):
            Path(f"{output_path}{suffix}").unlink(missing_ok=True)
        os.replace(temporary, output_path)
        _fsync_directory(output_path.parent)
    except Exception:
        temporary.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(f"{temporary}{suffix}").unlink(missing_ok=True)
        raise
    report["path"] = str(output_path)
    manifest_path = output_path.with_name(f"{output_path.name}.manifest.json")
    manifest = {
        "format": "memoryv4-sqlite-backup-v1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": report["sha256"],
        "size_bytes": report["size_bytes"],
        "migrations": report["migrations"],
    }
    _write_json_atomic(manifest_path, manifest)
    report["manifest"] = str(manifest_path)
    report["manifest_verified"] = True
    return report


def _restore_marker(database_path: Path) -> Path:
    return database_path.with_name(f".{database_path.name}.restore-pending.json")


def recover_interrupted_restore(database_path: Path, *, _locked: bool = False) -> bool:
    marker_path = _restore_marker(database_path)
    if not marker_path.exists():
        return False
    if not _locked:
        with DatabaseLock(database_path, exclusive=True, blocking=False):
            return recover_interrupted_restore(database_path, _locked=True)
    marker = json.loads(marker_path.read_text())
    if marker.get("format") != "memoryv4-restore-v1":
        raise PersistenceError("interrupted restore marker format is unsupported")
    temporary = Path(str(marker["temporary"]))
    previous = Path(str(marker["previous"])) if marker.get("previous") else None
    if (
        temporary.parent != database_path.parent
        or not temporary.name.startswith(f".{database_path.name}.")
        or (
            previous is not None
            and (
                previous.parent != database_path.parent
                or not previous.name.startswith(f"{database_path.name}.pre-restore-")
            )
        )
    ):
        raise PersistenceError("interrupted restore marker contains unsafe paths")
    if temporary.exists():
        if _sha256(temporary) != marker.get("temporary_sha256"):
            raise IntegrityCheckError("interrupted restore staging checksum mismatch")
        inspect_database(
            temporary,
            manifest_path=temporary.with_name(
                f".{temporary.name}.manifest-not-required"
            ),
        )
        if database_path.exists() and previous is not None and not previous.exists():
            os.replace(database_path, previous)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{database_path}{suffix}")
            if sidecar.exists():
                quarantine = Path(f"{previous}{suffix}") if previous else sidecar.with_suffix(
                    f"{sidecar.suffix}.recovery"
                )
                os.replace(sidecar, quarantine)
        os.replace(temporary, database_path)
        _fsync_directory(database_path.parent)
    elif database_path.exists():
        with connect_sqlite(database_path, readonly=True) as conn:
            assert_integrity(conn, full=False)
    elif previous is not None and previous.exists():
        os.replace(previous, database_path)
        for suffix in ("-wal", "-shm"):
            previous_sidecar = Path(f"{previous}{suffix}")
            if previous_sidecar.exists():
                os.replace(previous_sidecar, Path(f"{database_path}{suffix}"))
        _fsync_directory(database_path.parent)
    else:
        raise PersistenceError(
            "interrupted restore has no usable target, temporary, or previous DB"
        )
    marker_path.unlink()
    _fsync_directory(database_path.parent)
    return True


def restore_database(database_path: Path, backup_path: Path) -> dict[str, object]:
    database_path = database_path.resolve()
    backup_path = backup_path.resolve()
    if database_path == backup_path:
        raise PersistenceError("restore source must differ from the live database")
    backup_report = inspect_database(backup_path, require_manifest=True)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    operation_id = f"{_utc_stamp()}-{uuid4().hex[:8]}"
    temporary = database_path.with_name(f".{database_path.name}.{uuid4().hex}.restore")
    with connect_sqlite(backup_path, readonly=True) as source:
        with sqlite3.connect(temporary) as destination:
            source.backup(destination)
            destination.execute("PRAGMA journal_mode=DELETE")
            assert_integrity(destination, full=True)
    os.chmod(temporary, 0o600)
    _fsync_file(temporary)

    rollback_snapshot: Path | None = None
    if database_path.exists():
        candidate = database_path.with_name(
            f"{database_path.name}.pre-restore-{operation_id}.sqlite3"
        )
        try:
            backup_database(database_path, candidate)
            rollback_snapshot = candidate
        except (OSError, sqlite3.DatabaseError, PersistenceError):
            candidate.unlink(missing_ok=True)
            candidate.with_name(f"{candidate.name}.manifest.json").unlink(missing_ok=True)

    previous_raw = (
        database_path.with_name(f"{database_path.name}.pre-restore-raw-{operation_id}")
        if database_path.exists()
        else None
    )
    marker_written = False
    try:
        with DatabaseLock(database_path, exclusive=True, blocking=False):
            if _restore_marker(database_path).exists():
                recover_interrupted_restore(database_path, _locked=True)
            marker = {
                "format": "memoryv4-restore-v1",
                "temporary": str(temporary),
                "temporary_sha256": _sha256(temporary),
                "previous": str(previous_raw) if previous_raw else None,
                "rollback_snapshot": (
                    str(rollback_snapshot) if rollback_snapshot else None
                ),
                "backup_sha256": backup_report["sha256"],
            }
            _write_json_atomic(_restore_marker(database_path), marker)
            marker_written = True
            recover_interrupted_restore(database_path, _locked=True)
    except Exception:
        if not marker_written:
            temporary.unlink(missing_ok=True)
            if rollback_snapshot is not None:
                rollback_snapshot.unlink(missing_ok=True)
                rollback_snapshot.with_name(
                    f"{rollback_snapshot.name}.manifest.json"
                ).unlink(missing_ok=True)
        raise

    if rollback_snapshot is not None and previous_raw is not None:
        previous_raw.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(f"{previous_raw}{suffix}").unlink(missing_ok=True)
    previous = rollback_snapshot or previous_raw
    restored = inspect_database(database_path)
    restored["previous"] = str(previous) if previous else None
    restored["previous_verified"] = rollback_snapshot is not None
    restored["restored_from_sha256"] = backup_report["sha256"]
    return restored


def checkpoint_database(database_path: Path) -> dict[str, object]:
    with DatabaseLock(database_path, exclusive=False):
        with connect_sqlite(database_path) as conn:
            result = checkpoint(conn, truncate=True)
    return {"status": "ok", "checkpoint": list(result), "path": str(database_path)}


def _database_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(os.environ.get("MEMORYV4_DB_PATH", "/data/memoryv4.sqlite3")),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MemoryV4 SQLite recovery tooling")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("backup")
    _database_argument(backup_parser)
    backup_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--backup", type=Path, required=True)
    verify_parser.add_argument("--manifest", type=Path)
    restore_parser = subparsers.add_parser("restore")
    _database_argument(restore_parser)
    restore_parser.add_argument("--backup", type=Path, required=True)
    checkpoint_parser = subparsers.add_parser("checkpoint")
    _database_argument(checkpoint_parser)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "backup":
            result = backup_database(arguments.database, arguments.output)
        elif arguments.command == "verify":
            result = inspect_database(
                arguments.backup,
                manifest_path=arguments.manifest,
                require_manifest=True,
            )
        elif arguments.command == "restore":
            result = restore_database(arguments.database, arguments.backup)
        else:
            result = checkpoint_database(arguments.database)
    except (OSError, sqlite3.DatabaseError, PersistenceError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
