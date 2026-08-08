"""SQLite durability primitives shared by runtime, migrations, and recovery tooling."""

from __future__ import annotations

import fcntl
import sqlite3
from pathlib import Path
from types import TracebackType


class PersistenceError(RuntimeError):
    """Base class for persistence startup and recovery failures."""


class IntegrityCheckError(PersistenceError):
    pass


class DatabaseInUseError(PersistenceError):
    pass


class SchemaCompatibilityError(PersistenceError):
    pass


class DatabaseLock:
    """Advisory process lock; runtimes share it while offline restore is exclusive."""

    def __init__(self, database_path: Path, *, exclusive: bool, blocking: bool = True):
        self.path = database_path.with_name(f"{database_path.name}.lock")
        self.exclusive = exclusive
        self.blocking = blocking
        self._handle = None

    def acquire(self) -> DatabaseLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        self.path.chmod(0o600)
        operation = fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH
        if not self.blocking:
            operation |= fcntl.LOCK_NB
        try:
            fcntl.flock(handle.fileno(), operation)
        except BlockingIOError as exc:
            handle.close()
            mode = "exclusive" if self.exclusive else "shared"
            raise DatabaseInUseError(
                f"could not acquire {mode} database lock: {self.path}"
            ) from exc
        self._handle = handle
        return self

    def close(self) -> None:
        if self._handle is None:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None

    def __enter__(self) -> DatabaseLock:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def connect_sqlite(
    database_path: Path,
    *,
    busy_timeout_ms: int = 5000,
    readonly: bool = False,
) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    if readonly:
        uri = f"{database_path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=busy_timeout_ms / 1000)
    else:
        conn = sqlite3.connect(database_path, timeout=busy_timeout_ms / 1000)
        database_path.chmod(0o600)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    if not readonly:
        mode = str(conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
        if mode != "wal":
            conn.close()
            raise PersistenceError(f"SQLite refused WAL mode and returned {mode!r}")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA wal_autocheckpoint=1000")
    conn.execute("PRAGMA trusted_schema=OFF")
    return conn


def assert_integrity(conn: sqlite3.Connection, *, full: bool) -> None:
    pragma = "integrity_check" if full else "quick_check"
    rows = [str(row[0]) for row in conn.execute(f"PRAGMA {pragma}").fetchall()]
    if rows != ["ok"]:
        raise IntegrityCheckError(f"SQLite {pragma} failed: {'; '.join(rows[:10])}")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        samples = [tuple(row) for row in violations[:10]]
        raise IntegrityCheckError(f"SQLite foreign_key_check failed: {samples}")


def checkpoint(conn: sqlite3.Connection, *, truncate: bool = False) -> tuple[int, int, int]:
    mode = "TRUNCATE" if truncate else "PASSIVE"
    row = conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
    if row is None:
        raise PersistenceError("SQLite did not return a WAL checkpoint result")
    result = (int(row[0]), int(row[1]), int(row[2]))
    if result[0] != 0:
        raise PersistenceError(f"SQLite WAL checkpoint was busy: {result}")
    return result
