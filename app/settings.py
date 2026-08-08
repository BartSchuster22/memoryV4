"""Runtime settings for the MemoryV4 core service."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.contracts import Permission
from app.schemas import ScopePath

LEGACY_PERMISSIONS = frozenset(
    {Permission.read, Permission.search, Permission.create_working}
)


@dataclass(frozen=True)
class ApiKeyGrant:
    actor: str
    scope_path: str
    permissions: frozenset[Permission]
    allow_actor_delegation: bool = False

    def has(self, permission: Permission) -> bool:
        return Permission.admin in self.permissions or permission in self.permissions


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the private SQLite-only service."""

    service_name: str = "memoryv4-core"
    version: str = "0.3.0-core-objects"
    storage_backend: str = "sqlite"
    database_path: Path = Path("/data/memoryv4.sqlite3")
    api_keys: dict[str, ApiKeyGrant] = field(default_factory=dict)


def _legacy_actor(token: str) -> str:
    fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
    return f"api-key:{fingerprint}"


def _parse_permissions(value: Any) -> frozenset[Permission]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise ValueError("api key permissions must be a non-empty array of permission strings")
    try:
        return frozenset(Permission(item) for item in value)
    except ValueError as exc:
        raise ValueError(f"unknown MemoryV4 permission: {exc}") from exc


def _parse_grant(token: str, value: Any) -> ApiKeyGrant:
    if isinstance(value, str):
        # Backward-compatible parsing is deliberately least-privileged. Legacy
        # scope-only keys can read/search and create working candidates only.
        return ApiKeyGrant(
            actor=_legacy_actor(token),
            scope_path=ScopePath.validate(value),
            permissions=LEGACY_PERMISSIONS,
        )
    if not isinstance(value, dict):
        raise ValueError("api key grant must be a scope string or an object")
    unknown = set(value) - {"actor", "scope_path", "permissions", "allow_actor_delegation"}
    if unknown:
        raise ValueError(f"unknown api key grant fields: {sorted(unknown)}")
    actor = value.get("actor")
    scope_path = value.get("scope_path")
    if (
        not isinstance(actor, str)
        or not actor.strip()
        or actor != actor.strip()
        or len(actor) > 200
        or any(ord(char) < 32 for char in actor)
    ):
        raise ValueError("api key actor must be a non-empty trimmed string")
    if not isinstance(scope_path, str):
        raise ValueError("api key scope_path must be a string")
    allow_actor_delegation = value.get("allow_actor_delegation", False)
    if not isinstance(allow_actor_delegation, bool):
        raise ValueError("allow_actor_delegation must be a boolean")
    return ApiKeyGrant(
        actor=actor,
        scope_path=ScopePath.validate(scope_path),
        permissions=_parse_permissions(value.get("permissions")),
        allow_actor_delegation=allow_actor_delegation,
    )


def _load_api_keys() -> dict[str, ApiKeyGrant]:
    raw_json = os.environ.get("MEMORYV4_API_KEYS")
    if raw_json:
        parsed = json.loads(raw_json)
        if not isinstance(parsed, dict) or not all(
            isinstance(key, str) and bool(key) for key in parsed
        ):
            raise ValueError("MEMORYV4_API_KEYS must be a JSON object mapping token to grant")
        return {token: _parse_grant(token, grant) for token, grant in parsed.items()}

    token = os.environ.get("MEMORYV4_API_KEY")
    if not token:
        return {}
    permissions = [item.strip() for item in os.environ.get(
        "MEMORYV4_API_PERMISSIONS",
        ",".join(permission.value for permission in LEGACY_PERMISSIONS),
    ).split(",")]
    return {
        token: _parse_grant(
            token,
            {
                "actor": os.environ.get("MEMORYV4_API_ACTOR", _legacy_actor(token)),
                "scope_path": os.environ.get("MEMORYV4_API_SCOPE", "global"),
                "permissions": permissions,
            },
        )
    }


def load_settings() -> Settings:
    db_path = Path(os.environ.get("MEMORYV4_DB_PATH", "/data/memoryv4.sqlite3"))
    return Settings(database_path=db_path, api_keys=_load_api_keys())
