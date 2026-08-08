"""Governed MemoryV4 object model schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.contracts import WritePolicy


class Role(StrEnum):
    canonical = "canonical"
    active = "active"
    evidence = "evidence"
    exhaust = "exhaust"


class Lifecycle(StrEnum):
    live = "live"
    working = "working"
    superseded = "superseded"
    archived = "archived"
    expired = "expired"


class ScopePath:
    """Scope path helpers for ancestor-or-equal tenant isolation."""

    @staticmethod
    def validate(value: str) -> str:
        if not value or value.strip() != value:
            raise ValueError("scope_path must be non-empty and trimmed")
        if value in {"global", "public"}:
            return value
        parts = value.split("/")
        if any(not part or ":" not in part for part in parts):
            raise ValueError(
                "scope_path must be global, public, or slash-separated level:value segments"
            )
        for part in parts:
            level, name = part.split(":", 1)
            if not level or not name or "/" in name:
                raise ValueError("scope_path segments must be shaped level:value")
        return value

    @staticmethod
    def ancestors(value: str, *, include_public: bool = False) -> list[str]:
        value = ScopePath.validate(value)
        if value == "global":
            ancestors = ["global"]
        elif value == "public":
            ancestors = ["public"]
        else:
            parts = value.split("/")
            ancestors = ["global"]
            ancestors.extend("/".join(parts[:idx]) for idx in range(1, len(parts) + 1))
        if include_public and "public" not in ancestors:
            ancestors.append("public")
        return ancestors

    @staticmethod
    def is_descendant_or_equal(child: str, ancestor: str) -> bool:
        child = ScopePath.validate(child)
        ancestor = ScopePath.validate(ancestor)
        if ancestor == "global":
            return True
        if ancestor == "public":
            return child == "public"
        return child == ancestor or child.startswith(f"{ancestor}/")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RecordCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1)
    role: Role
    lifecycle: Lifecycle
    write_policy: WritePolicy = WritePolicy.author_only
    scope_path: str = "global"
    entity_type: str | None = None
    topic: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    attrs: dict[str, Any] = Field(default_factory=dict)

    @field_validator("scope_path")
    @classmethod
    def validate_scope_path(cls, value: str) -> str:
        return ScopePath.validate(value)


class Record(RecordCreate):
    id: str = Field(default_factory=lambda: f"rec_{uuid4().hex}")
    author_actor: str
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    superseded_by: str | None = None
    version: int = Field(default=1, ge=1)


class RecordPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=240)
    content: str | None = Field(default=None, min_length=1)
    topic: str | None = None
    source_refs: list[str] | None = None
    provenance: dict[str, Any] | None = None
    attrs: dict[str, Any] | None = None
    write_policy: WritePolicy | None = None

    @model_validator(mode="after")
    def require_change(self) -> RecordPatch:
        if not self.model_fields_set:
            raise ValueError("at least one mutable field is required")
        return self


class SearchResult(BaseModel):
    record: Record
    score: float


class MigrationResult(BaseModel):
    applied: list[str]
