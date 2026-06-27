"""SQLite migration runner for additive MemoryV4 core migrations."""

from __future__ import annotations

from pathlib import Path
import sqlite3

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def migrate_up(conn: sqlite3.Connection, *, target_version: str | None = None) -> None:
    """Apply pending migrations through target_version, or all available migrations."""

    _ensure_schema_migrations(conn)
    applied = _applied_versions(conn)
    for version, path in _migration_files("up"):
        if target_version is not None and version > target_version:
            break
        if version in applied:
            continue
        with conn:
            conn.executescript(path.read_text())
            conn.execute("insert into schema_migrations(version) values (?)", (version,))


def migrate_down(conn: sqlite3.Connection, *, target_version: str) -> None:
    """Roll back applied migrations until target_version remains applied.

    Down files remove only the objects introduced by their paired up migration.
    """

    _ensure_schema_migrations(conn)
    applied = _applied_versions(conn)
    for version, path in sorted(_migration_files("down"), reverse=True):
        if version <= target_version or version not in applied:
            continue
        with conn:
            conn.executescript(path.read_text())
            conn.execute("delete from schema_migrations where version = ?", (version,))


def _ensure_schema_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        "create table if not exists schema_migrations ("
        "version text primary key, "
        "applied_at text not null default current_timestamp)"
    )


def _applied_versions(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("select version from schema_migrations")}


def _migration_files(direction: str) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for path in MIGRATIONS_DIR.glob(f"*.{direction}.sql"):
        version = path.name.split("_", 1)[0].split(".", 1)[0]
        files.append((version, path))
    return sorted(files)
