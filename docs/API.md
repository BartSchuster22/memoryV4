# MemoryV4 Runtime API

The locked target interface is [CONTRACT_V1.md](CONTRACT_V1.md), the boundary is [ADR-0001](ADR-0001-TIER3-BOUNDARY.md), and enforced authorization rules are documented in [GOVERNANCE.md](GOVERNANCE.md). Runtime clients use `GET /capabilities` to distinguish `implemented`, `foundation`, and `planned` operations.

## Authentication

All application routes except `GET /health` require bearer authentication. `MEMORYV4_API_KEYS` maps tokens to structured actor, scope, and permission grants. Legacy token-to-scope values are supported with least-privileged read/search/create-working authority.

An explicitly configured UNIFY grant may delegate the authenticated application actor using `X-MemoryV4-Actor`; ordinary grants reject that header.

Every response carries:

```http
X-Request-ID: req_...
X-MemoryV4-Contract-Version: 1.0.0
```

Handled errors use the contract v1 envelope.

## GET /health

Public SQLite health check. It applies migrations idempotently, runs `PRAGMA quick_check`, and reports `degraded` when integrity is not OK.

```json
{
  "status": "ok",
  "service": "memoryv4-core",
  "version": "0.2.0-governance",
  "storage_backend": "sqlite"
}
```

## GET /capabilities

Requires `memory.read`. Publishes operation support, architecture, governance enums, permissions, autonomous defaults, scope semantics, and mutation preconditions.

## GET /schema

Requires `memory.read`. Publishes the object contract for entities, records, relations, artifacts, findings, errors, and cross-object invariants without exposing SQLite details.

## POST /records

Governed creation with required `Idempotency-Key`.

- `memory.create-working` may create only `active/working` candidates.
- `memory.create` may create non-canonical live/working records.
- Canonical/live creation additionally requires `memory.promote`.
- Actor is derived from authentication, never from the body.
- Default write policy is `author_only`.

## GET /records

Requires `memory.read`. Optional `scope_path` selects an effective descendant scope; `include_public` explicitly includes public records. Results use ancestor-or-equal visibility. Cursor pagination/filter completion remains a later core-object task, so capability discovery still marks this operation `foundation`.

## GET /records/{record_id}

Requires `memory.read` and uses the same effective-scope visibility decision as list/search. Outside or sibling objects return `404`. Full response filtering remains represented by the current capability status.

## PATCH /records/{record_id}

Requires `memory.edit`, `Idempotency-Key`, and integer `If-Match`. Enforces `team_editable`, `author_only`, `admin_only`, and `immutable`. Canonical records reject direct patch and must later use supersession. Successful updates increment `version` and update FTS content atomically.

## POST /records/{record_id}/promote

Requires `memory.promote`, `Idempotency-Key`, integer `If-Match`, and a trimmed `X-MemoryV4-Reason`. Only `active/working` candidates can be promoted. The operation atomically produces `canonical/live`, increments version, and audits its reason.

## GET /search

Requires `memory.search`. Uses the same effective scope as list/direct GET and emits a retrieval event. Advanced filters and pagination remain a later retrieval task, so capability discovery reports the current foundation status truthfully.

## Idempotent responses

Exact mutation replay returns the original object with:

```http
Idempotency-Replayed: true
```

Key reuse for a different actor request returns `409 idempotency_conflict`; stale versions return `412 version_conflict`; missing required mutation headers return `428 precondition_required`.

## Error envelope

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

Denied mutations are durably audited without request content, tokens, or secrets.
