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
class Record:
    id: str
    entity_type: str
    title: str
    topic: str
    content: str
    role: Role
    lifecycle: Lifecycle
    scope_path: str = "global"
    source_refs: list[str] = field(default_factory=list)
    superseded_by: str | None = None


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
