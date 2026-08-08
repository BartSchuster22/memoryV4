from pathlib import Path

import pytest

from app.contracts import Permission
from app.settings import load_settings

AUTH_ENV = (
    "MEMORYV4_API_KEYS",
    "MEMORYV4_API_KEY",
    "MEMORYV4_API_KEY_FILE",
    "MEMORYV4_API_SCOPE",
    "MEMORYV4_API_ACTOR",
    "MEMORYV4_API_PERMISSIONS",
    "MEMORYV4_API_ALLOW_ACTOR_DELEGATION",
)


def clear_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in AUTH_ENV:
        monkeypatch.delenv(name, raising=False)


def test_file_backed_single_key_supports_explicit_delegation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_auth_env(monkeypatch)
    secret = tmp_path / "api-key"
    secret.write_text("production-token\n", encoding="utf-8")
    monkeypatch.setenv("MEMORYV4_API_KEY_FILE", str(secret))
    monkeypatch.setenv("MEMORYV4_API_SCOPE", "org:aquiero")
    monkeypatch.setenv("MEMORYV4_API_ACTOR", "unify:gateway")
    monkeypatch.setenv("MEMORYV4_API_PERMISSIONS", "memory.read,memory.search")
    monkeypatch.setenv("MEMORYV4_API_ALLOW_ACTOR_DELEGATION", "true")

    grant = load_settings().api_keys["production-token"]

    assert grant.actor == "unify:gateway"
    assert grant.scope_path == "org:aquiero"
    assert grant.permissions == frozenset({Permission.read, Permission.search})
    assert grant.allow_actor_delegation is True


def test_auth_key_sources_are_mutually_exclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_auth_env(monkeypatch)
    secret = tmp_path / "api-key"
    secret.write_text("file-token", encoding="utf-8")
    monkeypatch.setenv("MEMORYV4_API_KEY", "direct-token")
    monkeypatch.setenv("MEMORYV4_API_KEY_FILE", str(secret))

    with pytest.raises(ValueError, match="set only one"):
        load_settings()


def test_file_backed_key_fails_closed_when_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_auth_env(monkeypatch)
    secret = tmp_path / "api-key"
    secret.write_text("\n", encoding="utf-8")
    monkeypatch.setenv("MEMORYV4_API_KEY_FILE", str(secret))

    with pytest.raises(ValueError, match="must not be empty"):
        load_settings()


def test_delegation_setting_is_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_auth_env(monkeypatch)
    monkeypatch.setenv("MEMORYV4_API_KEY", "direct-token")
    monkeypatch.setenv("MEMORYV4_API_ALLOW_ACTOR_DELEGATION", "yes")

    with pytest.raises(ValueError, match="must be true or false"):
        load_settings()
