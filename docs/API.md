# MemoryV4 Runtime API

The locked interface is [CONTRACT_V1.md](CONTRACT_V1.md), the service boundary is [ADR-0001](ADR-0001-TIER3-BOUNDARY.md), and authorization rules are in [GOVERNANCE.md](GOVERNANCE.md). `GET /capabilities` is the runtime source for implemented versus planned operations.

## Common behavior

All routes except `GET /health` require bearer authentication. Every response includes `X-Request-ID` and `X-MemoryV4-Contract-Version: 1.0.0`. Handled failures use the v1 error envelope.

Mutation routes require `Idempotency-Key`. PATCH, promotion, supersession, lifecycle
transition, and finding resolution additionally require integer `If-Match`.
Promotion, supersession, transition, and finding resolution require a trimmed
`X-MemoryV4-Reason` of at most 500 characters. Exact replay returns the stored
response with `Idempotency-Replayed: true`.

List APIs use opaque, filter-bound `cursor` pagination. Cursors cannot be reused with a different scope, filter, sort, or query. `limit` is 1–100. Default visibility is ancestor-or-equal within the authenticated grant; siblings are excluded and `include_public=true` is explicit.

## Discovery and health

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/health` | public | Initialized SQLite integrity/readiness health |
| GET | `/capabilities` | `memory.read` | Runtime operation/governance support |
| GET | `/schema` | `memory.read` | Machine-readable object contract |

Healthy runtime response version is `0.6.0-persistence-recovery`.

Production lifespan startup acquires the database lease, completes any interrupted
restore, applies atomically claimed migrations, validates migration history and
SQLite integrity, and repairs derived FTS state. Startup fails closed on corruption
or incompatible migration history. A database failure after startup returns a
redacted `503 storage_unavailable`; health reports `degraded` without leaking paths
or SQLite diagnostics.

## Entities

Entity identity is the composite `(entity_type, id)`, so different types may use the same `id`.

| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `/entities` | `memory.create` | Idempotent create; identity conflicts return `409` |
| GET | `/entities` | `memory.read` | Filters: `entity_type`, `name_contains`; sorting: `name`, `created_at`, `updated_at`, `id` |
| GET | `/entities/{entity_type}/{id}` | `memory.read` | Governed direct read; hidden objects return `404` |
| PATCH | `/entities/{entity_type}/{id}` | `memory.edit` | Mutable `name` and `attrs`; versioned/idempotent |

## Records

`POST /records` enforces action grants and canonical restrictions. An optional `entity: {entity_type,id}` must exist and be visible from the record scope. Records support tags and confidence in addition to provenance/source metadata.

`GET /records` supports filters for role, lifecycle, entity, topic, tag, and minimum confidence, plus sorting by title, timestamps, confidence, or ID. Soft-deleted records are excluded by default; `include_deleted=true` requires `memory.archive`.

`GET /records/{id}` uses the same visibility decision as list/search. `PATCH /records/{id}` enforces write policy, optimistic versioning, linked-entity integrity, and FTS synchronization. `POST /records/{id}/promote` atomically converts an active/working candidate to canonical/live and audits the reason.

### Governed record lifecycle

| Method | Path | Permission | Body | Result |
|---|---|---|---|---|
| POST | `/records/{id}/supersede` | `memory.edit` | Replacement snapshot fields | Atomically closes the source as `superseded` and returns the linked replacement |
| POST | `/records/{id}/transition` | `memory.archive` | `{"lifecycle":"..."}` | Archives, expires, restores, or changes a non-canonical live/working state |

Both operations require `Idempotency-Key`, `If-Match`, and `X-MemoryV4-Reason`.

Supersession accepts at least one replacement field from `title`, `content`, `entity`, `topic`, `tags`, `confidence`, `source_refs`, `provenance`, `attrs`, or `write_policy`. Omitted fields are copied from the source. Role, lifecycle, scope, and authenticated replacement author are governed by core and cannot be supplied. The source receives `superseded_by`; the replacement receives `supersedes`. The source version increments, and the linked replacement starts at version 1. A superseded record is immutable and cannot be transitioned or superseded again.

| Current | Allowed target | Additional rule |
|---|---|---|
| `working` | `live`, `archived`, `expired` | — |
| `live` | `working`, `archived`, `expired` | Canonical records cannot become `working` |
| `archived` | Stored `previous_lifecycle` only | Clears `deleted_at` on restoration |
| `expired` | Stored `previous_lifecycle` only | Freshness restoration must be explicit |
| `superseded` | None | Terminal history state |

Archival is soft deletion: it sets `deleted_at`, remains available through `GET /records?include_deleted=true` to callers with `memory.archive`, and is excluded from ordinary list/get/search/context reads. Expiry preserves ordinary visibility but records the prior lifecycle. Restoring either state returns exactly to the stored prior `live` or `working` state. No-op transitions and direct transition to `superseded` return `409`.

## Relations

| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `/relations` | `memory.create` | Idempotent create using typed `from` and `to` object references |
| GET | `/relations` | `memory.read` | Filters by relation type and endpoint kind/ID; cursor paginated |

Reference kinds are `entity`, `record`, and `artifact`. Entity references require `entity_type`; other references reject it. Both endpoints must exist and be visible from the relation scope, preventing cross-sibling edges.

## Artifacts

| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `/artifacts` | `memory.create` | Idempotent create linked to a record, entity, or both |
| GET | `/artifacts` | `memory.read` | Filters by type, record, or composite entity identity |

Artifact targets must exist and be visible from the artifact scope. Checksums use `algorithm:digest` form.

## Search and entity context

`GET /search` requires `memory.search`, emits a retrieval event, and supports role, lifecycle, entity, and tag filters plus filter-bound cursor pagination. SQLite FTS5/BM25 is used when available; lexical fallback keeps the same governance filters.

`GET /context/{entity_type}/{id}` requires `memory.read` and aggregates the visible entity with linked records, direct relations, and artifacts. `limit` applies per collection and `truncated` reports whether any collection exceeded it.

## Review findings

Review/health workers persist typed findings through the Store seam; there is no
public finding-creation endpoint.

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/review/findings` | `memory.review` | Scope-governed, cursor-paginated queue |
| POST | `/review/findings/{id}/resolve` | `memory.review` | Idempotent optimistic close as `resolved` or `dismissed` |

Finding types are `candidate`, `contradiction`, `stale`, and `health`; statuses are
`open`, `resolved`, and `dismissed`. List filters are `finding_type`, `status`,
`subject_kind`, and `subject_id`. Findings contain a typed subject, structured
detail/resolution, worker/reviewer attribution, timestamps, scope, and version.

Resolution accepts `{"status":"resolved","resolution":{...}}` and requires
`Idempotency-Key`, `If-Match`, and `X-MemoryV4-Reason`. Only an open finding may
close. Closure increments its version and emits exactly one `finding.resolve` or
`finding.dismiss` audit event; closed findings cannot reopen through this API.

## Audit and operational retrieval

| Method | Path | Permission | Filters / result |
|---|---|---|---|
| GET | `/audit/events` | `memory.audit.read` | `action`, object fields, `actor`, time bounds; cursor page |
| GET | `/retrieval-events` | `memory.audit.read` | `actor`, `degraded`, `query_contains`, time bounds; cursor page |
| GET | `/usage` | `memory.admin` | Current object breakdowns and windowed event counts |

All apply effective-scope grant checks and ancestor-or-equal visibility;
`include_public=true` is explicit. Time bounds are timezone-aware RFC3339 values with
`from_time <= to_time`. Usage covers entities, records, relations, artifacts,
findings, audit events, retrieval events, and degraded retrievals.

## Errors

```json
{
  "error": {
    "code": "forbidden",
    "message": "scope outside actor grant",
    "status": 403,
    "request_id": "req_...",
    "details": {}
  }
}
```

Notable statuses: `409 idempotency_conflict`, `412 version_conflict`, `422 invalid_request`, and `428 precondition_required`. Denied mutations are audited without request content, tokens, or authorization headers.
