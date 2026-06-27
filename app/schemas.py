"""Domain schemas and governance validation for MemoryV4 core."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MemoryRole(str, Enum):
    """Governance role of a memory object."""

    CANONICAL = "canonical"
    ACTIVE = "active"
    EVIDENCE = "evidence"
    EXHAUST = "exhaust"


class Lifecycle(str, Enum):
    """Lifecycle state for governed memory objects."""

    WORKING = "working"
    LIVE = "live"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    EXPIRED = "expired"


@dataclass(frozen=True)
class Scope:
    """Hierarchical scope key used for tenant/project/session isolation."""

    key: str


@dataclass(frozen=True)
class SourceRef:
    """Traceable source reference for a memory record or artifact."""

    kind: str
    uri: str
    fragment: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuthorActor:
    """Actor credited with creating or changing an object."""

    id: str
    kind: str = "agent"


@dataclass(frozen=True)
class WritePolicy:
    """Write constraints associated with an object."""

    name: str
    requires_verification: bool = True
    canonical_allowed: bool = False


@dataclass(frozen=True)
class Supersession:
    """Old-to-new record relationship with audit-friendly reason."""

    old_record_id: str
    new_record_id: str
    actor: str
    reason: str
    created_at: str | None = None


@dataclass(frozen=True)
class MemoryRecord:
    """Governed memory record."""

    id: str
    role: MemoryRole
    lifecycle: Lifecycle
    scope: str
    content: str
    source_refs: list[SourceRef] = field(default_factory=list)
    author_actor: str = "unknown"
    write_policy: str = "verification_required"
    superseded_by: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Entity:
    id: str
    record_id: str
    kind: str
    name: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Relation:
    id: str
    source_record_id: str
    target_record_id: str
    relation_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Artifact:
    id: str
    record_id: str
    kind: str
    uri: str
    source_refs: list[SourceRef] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuditEvent:
    id: int | None
    action: str
    actor: str
    record_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None


@dataclass(frozen=True)
class RetrievalEvent:
    id: int | None
    query: str
    lane: str
    scope: str | None
    degraded: bool
    details: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None


@dataclass(frozen=True)
class RankedRecord:
    record_id: str
    score: float
    lane: str
    degraded: bool = False


_ALLOWED_TRANSITIONS: dict[Lifecycle, set[Lifecycle]] = {
    Lifecycle.WORKING: {Lifecycle.LIVE, Lifecycle.ARCHIVED, Lifecycle.EXPIRED},
    Lifecycle.LIVE: {Lifecycle.SUPERSEDED, Lifecycle.ARCHIVED, Lifecycle.EXPIRED},
    Lifecycle.SUPERSEDED: {Lifecycle.ARCHIVED, Lifecycle.EXPIRED},
    Lifecycle.ARCHIVED: set(),
    Lifecycle.EXPIRED: set(),
}


def validate_lifecycle_transition(current: Lifecycle, target: Lifecycle) -> None:
    """Raise ValueError when a requested lifecycle transition is not allowed."""

    if current == target:
        return None
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"Invalid lifecycle transition: {current.value} -> {target.value}")
    return None
