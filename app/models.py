"""Governed MemoryV4 core models."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(str, Enum):
    CANONICAL = "canonical"
    ACTIVE = "active"
    EVIDENCE = "evidence"
    EXHAUST = "exhaust"


class Lifecycle(str, Enum):
    LIVE = "live"
    WORKING = "working"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    EXPIRED = "expired"


@dataclass(frozen=True)
class Entity:
    id: str
    entity_type: str
    name: str
    scope_path: str = "global"
    attrs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("id", self.id)
        _require_non_empty("entity_type", self.entity_type)
        _require_non_empty("name", self.name)
        _require_non_empty("scope_path", self.scope_path)


@dataclass(frozen=True)
class Record:
    id: str
    entity_type: str
    title: str
    topic: str
    content: str
    role: Role | str
    lifecycle: Lifecycle | str
    scope_path: str = "global"
    entity_id: str | None = None
    source_refs: list[str] = field(default_factory=list)
    superseded_by: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("id", self.id)
        _require_non_empty("entity_type", self.entity_type)
        _require_non_empty("title", self.title)
        _require_non_empty("topic", self.topic)
        _require_non_empty("scope_path", self.scope_path)
        object.__setattr__(self, "role", _coerce_role(self.role))
        object.__setattr__(self, "lifecycle", _coerce_lifecycle(self.lifecycle))


@dataclass(frozen=True)
class Relation:
    id: str
    source_id: str
    target_id: str
    relation_type: str
    role: Role | str
    lifecycle: Lifecycle | str
    scope_path: str = "global"
    attrs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("id", self.id)
        _require_non_empty("source_id", self.source_id)
        _require_non_empty("target_id", self.target_id)
        _require_non_empty("relation_type", self.relation_type)
        _require_non_empty("scope_path", self.scope_path)
        object.__setattr__(self, "role", _coerce_role(self.role))
        object.__setattr__(self, "lifecycle", _coerce_lifecycle(self.lifecycle))


@dataclass(frozen=True)
class Artifact:
    id: str
    artifact_type: str
    uri: str
    role: Role | str
    lifecycle: Lifecycle | str
    scope_path: str = "global"
    media_type: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("id", self.id)
        _require_non_empty("artifact_type", self.artifact_type)
        _require_non_empty("uri", self.uri)
        _require_non_empty("scope_path", self.scope_path)
        object.__setattr__(self, "role", _coerce_role(self.role))
        object.__setattr__(self, "lifecycle", _coerce_lifecycle(self.lifecycle))


@dataclass(frozen=True)
class Filter:
    entity_type: str | None = None
    role: str | None = None
    lifecycle: str | None = None
    topic: str | None = None
    scope_prefixes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AuditEvent:
    action: str
    actor: str
    record_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("action", self.action)
        _require_non_empty("actor", self.actor)


@dataclass(frozen=True)
class RetrievalEvent:
    query: str
    actor: str
    record_ids: list[str]
    scope_path: str = "global"
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("query", self.query)
        _require_non_empty("actor", self.actor)
        _require_non_empty("scope_path", self.scope_path)


def _coerce_role(value: Role | str) -> Role:
    if isinstance(value, Role):
        return value
    try:
        return Role(value)
    except ValueError as exc:
        allowed = ", ".join(role.value for role in Role)
        raise ValueError(f"invalid role {value!r}; expected one of: {allowed}") from exc


def _coerce_lifecycle(value: Lifecycle | str) -> Lifecycle:
    if isinstance(value, Lifecycle):
        return value
    try:
        return Lifecycle(value)
    except ValueError as exc:
        allowed = ", ".join(lifecycle.value for lifecycle in Lifecycle)
        raise ValueError(f"invalid lifecycle {value!r}; expected one of: {allowed}") from exc


def _require_non_empty(name: str, value: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} must not be empty")
