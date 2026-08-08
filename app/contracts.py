"""Locked MemoryV4 v1 architecture and API contract metadata.

This module is deliberately independent from storage.  It is the machine-readable
source used by capability and schema discovery while implementation is completed
behind the stable v1 boundary.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

CONTRACT_VERSION = "1.0.0"
API_STYLE = "unversioned-v1"


class Permission(StrEnum):
    read = "memory.read"
    search = "memory.search"
    create_working = "memory.create-working"
    create = "memory.create"
    edit = "memory.edit"
    promote = "memory.promote"
    archive = "memory.archive"
    review = "memory.review"
    audit_read = "memory.audit.read"
    admin = "memory.admin"


class WritePolicy(StrEnum):
    team_editable = "team_editable"
    author_only = "author_only"
    admin_only = "admin_only"
    immutable = "immutable"


class ObjectKind(StrEnum):
    entity = "entity"
    record = "record"
    artifact = "artifact"


class OperationStatus(StrEnum):
    implemented = "implemented"
    foundation = "foundation"
    planned = "planned"


class CapabilityOperation(BaseModel):
    method: str
    path: str
    permission: Permission
    status: OperationStatus
    mutation: bool = False
    idempotency_required: bool = False
    version_precondition_required: bool = False
    reason_required: bool = False


class CapabilitiesResponse(BaseModel):
    service: str
    contract_version: str = CONTRACT_VERSION
    api_style: str = API_STYLE
    architecture: dict[str, str]
    governance: dict[str, Any]
    operations: list[CapabilityOperation]


class SchemaContractResponse(BaseModel):
    contract_version: str = CONTRACT_VERSION
    schemas: dict[str, dict[str, Any]]
    invariants: list[str]


class ErrorBody(BaseModel):
    code: str
    message: str
    status: int
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody


# Truthful implementation status is part of discovery: publishing a planned
# operation never implies that the route exists yet.
OPERATIONS = [
    CapabilityOperation(
        method="GET", path="/capabilities", permission=Permission.read,
        status=OperationStatus.implemented,
    ),
    CapabilityOperation(
        method="GET", path="/schema", permission=Permission.read,
        status=OperationStatus.implemented,
    ),
    CapabilityOperation(
        method="GET", path="/entities", permission=Permission.read,
        status=OperationStatus.implemented,
    ),
    CapabilityOperation(
        method="POST", path="/entities", permission=Permission.create,
        status=OperationStatus.implemented, mutation=True, idempotency_required=True,
    ),
    CapabilityOperation(
        method="GET", path="/entities/{type}/{id}", permission=Permission.read,
        status=OperationStatus.implemented,
    ),
    CapabilityOperation(
        method="PATCH", path="/entities/{type}/{id}", permission=Permission.edit,
        status=OperationStatus.implemented, mutation=True, idempotency_required=True,
        version_precondition_required=True,
    ),
    CapabilityOperation(
        method="GET", path="/records", permission=Permission.read,
        status=OperationStatus.implemented,
    ),
    CapabilityOperation(
        method="POST", path="/records", permission=Permission.create,
        status=OperationStatus.implemented, mutation=True, idempotency_required=True,
    ),
    CapabilityOperation(
        method="GET", path="/records/{id}", permission=Permission.read,
        status=OperationStatus.implemented,
    ),
    CapabilityOperation(
        method="PATCH", path="/records/{id}", permission=Permission.edit,
        status=OperationStatus.implemented, mutation=True, idempotency_required=True,
        version_precondition_required=True,
    ),
    CapabilityOperation(
        method="POST", path="/records/{id}/supersede", permission=Permission.edit,
        status=OperationStatus.implemented, mutation=True, idempotency_required=True,
        version_precondition_required=True, reason_required=True,
    ),
    CapabilityOperation(
        method="POST", path="/records/{id}/transition", permission=Permission.archive,
        status=OperationStatus.implemented, mutation=True, idempotency_required=True,
        version_precondition_required=True, reason_required=True,
    ),
    CapabilityOperation(
        method="POST", path="/records/{id}/promote", permission=Permission.promote,
        status=OperationStatus.implemented, mutation=True, idempotency_required=True,
        version_precondition_required=True, reason_required=True,
    ),
    CapabilityOperation(
        method="GET", path="/relations", permission=Permission.read,
        status=OperationStatus.implemented,
    ),
    CapabilityOperation(
        method="POST", path="/relations", permission=Permission.create,
        status=OperationStatus.implemented, mutation=True, idempotency_required=True,
    ),
    CapabilityOperation(
        method="GET", path="/artifacts", permission=Permission.read,
        status=OperationStatus.implemented,
    ),
    CapabilityOperation(
        method="POST", path="/artifacts", permission=Permission.create,
        status=OperationStatus.implemented, mutation=True, idempotency_required=True,
    ),
    CapabilityOperation(
        method="GET", path="/search", permission=Permission.search,
        status=OperationStatus.implemented,
    ),
    CapabilityOperation(
        method="GET", path="/context/{entity_type}/{entity_id}",
        permission=Permission.read, status=OperationStatus.implemented,
    ),
    CapabilityOperation(
        method="GET", path="/review/findings", permission=Permission.review,
        status=OperationStatus.implemented,
    ),
    CapabilityOperation(
        method="POST", path="/review/findings/{id}/resolve",
        permission=Permission.review, status=OperationStatus.implemented, mutation=True,
        idempotency_required=True, version_precondition_required=True,
        reason_required=True,
    ),
    CapabilityOperation(
        method="GET", path="/audit/events", permission=Permission.audit_read,
        status=OperationStatus.implemented,
    ),
    CapabilityOperation(
        method="GET", path="/retrieval-events", permission=Permission.audit_read,
        status=OperationStatus.implemented,
    ),
    CapabilityOperation(
        method="GET", path="/usage", permission=Permission.admin,
        status=OperationStatus.implemented,
    ),
]


SCHEMAS: dict[str, dict[str, Any]] = {
    "entity": {
        "identity": "composite entity_type + id",
        "required": ["id", "entity_type", "name", "scope_path", "version"],
        "mutable": ["name", "attrs"],
        "fields": {
            "id": "string",
            "entity_type": "string",
            "name": "string(1..240)",
            "scope_path": "scope_path",
            "attrs": "object",
            "version": "integer>=1",
            "created_at": "RFC3339 UTC",
            "updated_at": "RFC3339 UTC",
        },
    },
    "record": {
        "identity": "id prefixed rec_",
        "required": [
            "id", "title", "content", "role", "lifecycle", "scope_path",
            "write_policy", "version", "author_actor",
        ],
        "fields": {
            "id": "string(rec_*)",
            "title": "string(1..240)",
            "content": "string",
            "role": ["canonical", "active", "evidence", "exhaust"],
            "lifecycle": ["live", "working", "superseded", "archived", "expired"],
            "write_policy": [policy.value for policy in WritePolicy],
            "scope_path": "scope_path",
            "entity": "nullable {entity_type,id}",
            "topic": "nullable string",
            "tags": "array<string>",
            "confidence": "nullable number 0..1",
            "source_refs": "array<string>",
            "provenance": "object",
            "attrs": "object",
            "author_actor": "derived authenticated actor",
            "version": "integer>=1",
            "supersedes": "nullable record id",
            "superseded_by": "nullable record id",
            "previous_lifecycle": "nullable live|working; exact restoration target",
            "lifecycle_changed_at": "nullable RFC3339 UTC",
            "created_at": "RFC3339 UTC",
            "updated_at": "RFC3339 UTC",
            "deleted_at": "nullable RFC3339 UTC",
        },
        "lifecycle_transitions": {
            "live": ["working", "archived", "expired"],
            "working": ["live", "archived", "expired"],
            "archived": ["stored previous_lifecycle"],
            "expired": ["stored previous_lifecycle"],
            "superseded": [],
        },
        "lifecycle_rules": [
            "canonical records cannot transition to working",
            "superseded is produced only by atomic replacement supersession",
            "archived records are soft-deleted and excluded by default",
        ],
    },
    "relation": {
        "identity": "id prefixed rel_",
        "required": ["id", "from", "to", "relation_type", "scope_path", "version"],
        "fields": {
            "from": "{kind: entity|record|artifact, entity_type?, id}",
            "to": "{kind: entity|record|artifact, entity_type?, id}",
            "relation_type": "string",
            "scope_path": "scope_path",
            "provenance": "object",
            "author_actor": "derived authenticated actor",
            "version": "integer>=1",
            "created_at": "RFC3339 UTC",
            "updated_at": "RFC3339 UTC",
        },
    },
    "artifact": {
        "identity": "id prefixed art_",
        "required": ["id", "artifact_type", "uri", "scope_path", "version"],
        "fields": {
            "record_id": "nullable record id",
            "entity": "nullable {entity_type,id}",
            "artifact_type": "string",
            "uri": "string",
            "checksum": "nullable algorithm:digest",
            "scope_path": "scope_path",
            "provenance": "object",
            "author_actor": "derived authenticated actor",
            "version": "integer>=1",
            "created_at": "RFC3339 UTC",
            "updated_at": "RFC3339 UTC",
        },
    },
    "finding": {
        "identity": "id prefixed fnd_",
        "required": ["id", "finding_type", "status", "scope_path", "version"],
        "fields": {
            "finding_type": ["candidate", "contradiction", "stale", "health"],
            "status": ["open", "resolved", "dismissed"],
            "subject": "object reference",
            "detail": "object",
            "resolution": "nullable object",
            "created_by_actor": "derived worker actor",
            "resolved_by_actor": "nullable derived reviewer actor",
            "resolved_at": "nullable RFC3339 UTC",
            "scope_path": "scope_path",
            "version": "integer>=1",
            "created_at": "RFC3339 UTC",
            "updated_at": "RFC3339 UTC",
        },
    },
    "error": ErrorResponse.model_json_schema(),
}


INVARIANTS = [
    "MemoryV4 is the authoritative Tier-3 durable knowledge service.",
    "UNIFY is the sole application gateway; MemoryV4 has no public application port.",
    "UNIUI owns the human interface; frameworks own Tier-2 session state.",
    "One object model represents documents, collections, knowledge, and relationships.",
    "SQLite is the only storage backend for v1.",
    "Reads use ancestor-or-equal visibility and exclude siblings by default.",
    "Mutations may target only equal-or-descendant scopes of the actor grant.",
    "Autonomous actors create active/working candidates and cannot promote canonical truth.",
    "Canonical revisions use supersession; canonical content is never silently overwritten.",
    "Supersession atomically creates a replacement and closes the prior record.",
    "Archived and expired records restore only to their stored prior live or working state.",
    "Every mutation is actor-attributed, scope-checked, idempotent, and audited.",
    "Versioned mutations use If-Match; lifecycle actions also require a reason.",
    "MemoryV3 cutover requires a separate explicit approval.",
]


def capabilities(service_name: str) -> CapabilitiesResponse:
    return CapabilitiesResponse(
        service=service_name,
        architecture={
            "tier": "tier-3",
            "gateway": "UNIFY",
            "human_interface": "UNIUI",
            "storage": "sqlite",
            "deployment": "single-private-container",
        },
        governance={
            "roles": ["canonical", "active", "evidence", "exhaust"],
            "lifecycles": ["live", "working", "superseded", "archived", "expired"],
            "write_policies": [policy.value for policy in WritePolicy],
            "permissions": [permission.value for permission in Permission],
            "autonomous_default": {"role": "active", "lifecycle": "working"},
            "canonical_promotion": "privileged-only",
            "scope_visibility": "ancestor-or-equal; siblings denied",
            "actor_identity": "authenticated grant or explicitly delegated UNIFY actor",
            "idempotency_scope": "per actor across mutations",
        },
        operations=OPERATIONS,
    )


def schema_contract() -> SchemaContractResponse:
    return SchemaContractResponse(schemas=SCHEMAS, invariants=INVARIANTS)
