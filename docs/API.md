# MemoryV4 Runtime API

The locked target interface is [CONTRACT_V1.md](CONTRACT_V1.md), and the architectural boundary is [ADR-0001](ADR-0001-TIER3-BOUNDARY.md). Runtime clients must use authenticated `GET /capabilities` to distinguish `implemented`, `foundation`, and `planned` operations.

## Authentication

All routes except `GET /health`, framework-generated `/docs`, and `/openapi.json` require:

```http
Authorization: Bearer <token>
```

Foundation tokens are configured by `MEMORYV4_API_KEYS` as a JSON object mapping token to a granted `scope_path`. Action-scoped permission enforcement is locked in contract v1 but remains a subsequent governance implementation task. Foundation record routes must therefore not be treated as production-ready.

Every response carries:

```http
X-Request-ID: req_...
X-MemoryV4-Contract-Version: 1.0.0
```

Handled errors use the contract v1 error envelope.

## GET /health

Public SQLite health check. It applies migrations idempotently, runs `PRAGMA quick_check`, and reports `degraded` when integrity is not OK.

```json
{
  "status": "ok",
  "service": "memoryv4-core",
  "version": "0.1.0-t2",
  "storage_backend": "sqlite"
}
```

## GET /capabilities

Authenticated, machine-readable operation inventory. Each operation reports:

- `implemented`: satisfies the locked v1 contract;
- `foundation`: route exists but does not yet satisfy every v1 governance requirement;
- `planned`: target contract is locked but route is not implemented.

It also publishes architecture ownership, governance enums, permissions, autonomous defaults, and scope semantics.

## GET /schema

Authenticated, machine-readable object contract for entities, records, relations, artifacts, findings, errors, and cross-object invariants. It describes the target v1 object model without exposing SQLite details.

## Foundation routes

The following routes predate the locked v1 governance contract and are reported as `foundation`:

- `POST /records`
- `GET /records`
- `GET /records/{record_id}`
- `GET /search`

They provide scoped record creation/list/read and lexical retrieval with audit/retrieval events. They do not yet implement action permissions, write policy, idempotency, optimistic concurrency, canonical promotion protection, or the full unified direct-read decision. Those gaps are explicit rather than hidden by capability discovery.

## Error envelope

```json
{
  "error": {
    "code": "forbidden",
    "message": "scope outside key grant",
    "status": 403,
    "request_id": "req_...",
    "details": {}
  }
}
```

See [CONTRACT_V1.md](CONTRACT_V1.md) for mutation headers, stable error codes, endpoint permissions, lifecycle rules, and the full required endpoint inventory.
