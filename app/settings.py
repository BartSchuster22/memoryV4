"""Runtime settings for the MemoryV4 core service."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Runtime settings.

    SQLite is the only storage target in this repository. Postgres is reserved
    for a future Store adapter and is intentionally not configurable here.
    """

    service_name: str = "memoryv4-core"
    version: str = "0.1.0-t2"
    storage_backend: str = "sqlite"
    database_path: Path = Path("/data/memoryv4.sqlite3")
    api_keys: dict[str, str] = field(default_factory=dict)


def _load_api_keys() -> dict[str, str]:
    raw_json = os.environ.get("MEMORYV4_API_KEYS")
    if raw_json:
        parsed = json.loads(raw_json)
        if not isinstance(parsed, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
        ):
            raise ValueError("MEMORYV4_API_KEYS must be a JSON object mapping token to scope_path")
        return parsed
    token = os.environ.get("MEMORYV4_API_KEY")
    if token:
        return {token: os.environ.get("MEMORYV4_API_SCOPE", "global")}
    return {}


def load_settings() -> Settings:
    db_path = Path(os.environ.get("MEMORYV4_DB_PATH", "/data/memoryv4.sqlite3"))
    return Settings(database_path=db_path, api_keys=_load_api_keys())
