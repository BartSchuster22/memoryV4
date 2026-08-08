# MemoryV4 Runtime API

The locked interface is [CONTRACT_V1.md](CONTRACT_V1.md), the service boundary is [ADR-0001](ADR-0001-TIER3-BOUNDARY.md), and authorization rules are in [GOVERNANCE.md](GOVERNANCE.md). `GET /capabilities` is the runtime source for implemented versus planned operations.

## Common behavior

All routes except `GET /health` require bearer authentication. Every response includes `X-Request-ID` and `X-MemoryV4-Contract-Version: 1.0.0`. Handled failures use the v1 error envelope.

Mutation routes require `Idempotency-Key`. PATCH and promotion additionally require integer `If-Match`; promotion requires `X-MemoryV4-Reason`. Exact replay returns the stored response with `Idempotency-Replayed: true`.

List APIs use opaque, filter-bound `cursor` pagination. Cursors cannot be reused with a different scope, filter, sort, or query. `limit` is 1–100. Default visibility is ancestor-or-equal within the authenticated grant; siblings are excluded and `include_public=true` is explicit.

## Discovery and health

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/health` | public | SQLite quick-check and migration health |
| GET | `/capabilities` | `memory.read` | Runtime operation/governance support |
| GET | `/schema` | `memory.read` | Machine-readable object contract |

Healthy runtime response version is `0.3.0-core-objects`.

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
