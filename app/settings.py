"""Runtime settings for the MemoryV4 core shell."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Minimal D0 settings.

    SQLite is the only storage target in this repository. Postgres is reserved
    for a future Store adapter and is intentionally not configurable here.
    """

    service_name: str = "memoryv4-core"
    version: str = "0.1.0-d0"
    storage_backend: str = "sqlite"
    database_path: Path = Path("/data/memoryv4.sqlite3")


def load_settings() -> Settings:
    db_path = Path(os.environ.get("MEMORYV4_DB_PATH", "/data/memoryv4.sqlite3"))
    return Settings(database_path=db_path)
