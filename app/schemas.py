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


class ObjectKind(StrEnum):
    entity = "entity"
    record = "record"
    artifact = "artifact"


class SortOrder(StrEnum):
    asc = "asc"
    desc = "desc"


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


def _trimmed_identifier(value: str) -> str:
    if value != value.strip() or not value or len(value) > 200:
        raise ValueError("identifier must be trimmed and contain 1 to 200 characters")
    if any(ord(char) < 32 for char in value):
        raise ValueError("identifier cannot contain control characters")
    return value


class EntityRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: str = Field(min_length=1, max_length=100)
    id: str

    @field_validator("entity_type", "id")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return _trimmed_identifier(value)


class EntityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"ent_{uuid4().hex}")
    entity_type: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=240)
    scope_path: str = "global"
    attrs: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "entity_type")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return _trimmed_identifier(value)

    @field_validator("scope_path")
    @classmethod
    def validate_scope_path(cls, value: str) -> str:
        return ScopePath.validate(value)


class Entity(EntityCreate):
    version: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class EntityPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=240)
    attrs: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_change(self) -> EntityPatch:
        if not self.model_fields_set:
            raise ValueError("at least one mutable field is required")
        return self


class RecordCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1)
    role: Role
    lifecycle: Lifecycle
    write_policy: WritePolicy = WritePolicy.author_only
    scope_path: str = "global"
    entity: EntityRef | None = None
    topic: str | None = Field(default=None, max_length=240)
    tags: list[str] = Field(default_factory=list, max_length=50)
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_refs: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    attrs: dict[str, Any] = Field(default_factory=dict)

    @field_validator("scope_path")
    @classmethod
    def validate_scope_path(cls, value: str) -> str:
        return ScopePath.validate(value)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, values: list[str]) -> list[str]:
        if any(not value or value != value.strip() or len(value) > 100 for value in values):
            raise ValueError("tags must be unique trimmed strings of at most 100 characters")
        if len(set(values)) != len(values):
            raise ValueError("tags must be unique")
        return values


class Record(RecordCreate):
    id: str = Field(default_factory=lambda: f"rec_{uuid4().hex}")
    author_actor: str
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    supersedes: str | None = None
    superseded_by: str | None = None
    deleted_at: str | None = None
    version: int = Field(default=1, ge=1)


class RecordPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=240)
    content: str | None = Field(default=None, min_length=1)
    entity: EntityRef | None = None
    topic: str | None = Field(default=None, max_length=240)
    tags: list[str] | None = Field(default=None, max_length=50)
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_refs: list[str] | None = None
    provenance: dict[str, Any] | None = None
    attrs: dict[str, Any] | None = None
    write_policy: WritePolicy | None = None

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        if any(not value or value != value.strip() or len(value) > 100 for value in values):
            raise ValueError("tags must be unique trimmed strings of at most 100 characters")
        if len(set(values)) != len(values):
            raise ValueError("tags must be unique")
        return values

    @model_validator(mode="after")
    def require_change(self) -> RecordPatch:
        if not self.model_fields_set:
            raise ValueError("at least one mutable field is required")
        return self


class ObjectRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ObjectKind
    id: str
    entity_type: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _trimmed_identifier(value)

    @model_validator(mode="after")
    def validate_entity_type(self) -> ObjectRef:
        if self.kind == ObjectKind.entity and self.entity_type is None:
            raise ValueError("entity references require entity_type")
        if self.kind != ObjectKind.entity and self.entity_type is not None:
            raise ValueError("entity_type is valid only for entity references")
        return self


class RelationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_ref: ObjectRef = Field(validation_alias="from", serialization_alias="from")
    to_ref: ObjectRef = Field(validation_alias="to", serialization_alias="to")
    relation_type: str = Field(min_length=1, max_length=100)
    scope_path: str = "global"
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("relation_type")
    @classmethod
    def validate_relation_type(cls, value: str) -> str:
        return _trimmed_identifier(value)

    @field_validator("scope_path")
    @classmethod
    def validate_scope_path(cls, value: str) -> str:
        return ScopePath.validate(value)


class Relation(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(default_factory=lambda: f"rel_{uuid4().hex}")
    from_ref: ObjectRef = Field(validation_alias="from", serialization_alias="from")
    to_ref: ObjectRef = Field(validation_alias="to", serialization_alias="to")
    relation_type: str = Field(min_length=1, max_length=100)
    scope_path: str = "global"
    provenance: dict[str, Any] = Field(default_factory=dict)
    author_actor: str
    version: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    @field_validator("relation_type")
    @classmethod
    def validate_relation_type(cls, value: str) -> str:
        return _trimmed_identifier(value)

    @field_validator("scope_path")
    @classmethod
    def validate_scope_path(cls, value: str) -> str:
        return ScopePath.validate(value)


class ArtifactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str | None = None
    entity: EntityRef | None = None
    artifact_type: str = Field(min_length=1, max_length=100)
    uri: str = Field(min_length=1, max_length=2048)
    checksum: str | None = Field(default=None, max_length=512)
    scope_path: str = "global"
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("artifact_type")
    @classmethod
    def validate_artifact_type(cls, value: str) -> str:
        return _trimmed_identifier(value)

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        if value != value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("uri must be trimmed and contain no control characters")
        return value

    @field_validator("checksum")
    @classmethod
    def validate_checksum(cls, value: str | None) -> str | None:
        if value is not None and (
            value != value.strip() or ":" not in value or any(char.isspace() for char in value)
        ):
            raise ValueError("checksum must use algorithm:digest form")
        return value

    @field_validator("scope_path")
    @classmethod
    def validate_scope_path(cls, value: str) -> str:
        return ScopePath.validate(value)

    @model_validator(mode="after")
    def require_target(self) -> ArtifactCreate:
        if self.record_id is None and self.entity is None:
            raise ValueError("artifact must link to a record or entity")
        return self


class Artifact(ArtifactCreate):
    id: str = Field(default_factory=lambda: f"art_{uuid4().hex}")
    author_actor: str
    version: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class SearchResult(BaseModel):
    record: Record
    score: float


class EntityPage(BaseModel):
    entities: list[Entity]
    next_cursor: str | None = None


class RecordPage(BaseModel):
    records: list[Record]
    next_cursor: str | None = None


class RelationPage(BaseModel):
    relations: list[Relation]
    next_cursor: str | None = None


class ArtifactPage(BaseModel):
    artifacts: list[Artifact]
    next_cursor: str | None = None


class SearchPage(BaseModel):
    results: list[SearchResult]
    next_cursor: str | None = None


class EntityContext(BaseModel):
    entity: Entity
    records: list[Record]
    relations: list[Relation]
    artifacts: list[Artifact]
    truncated: bool = False


class MigrationResult(BaseModel):
    applied: list[str]
