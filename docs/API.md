# MemoryV4 API

## Authentication

All routes except `GET /health` require:

```http
Authorization: Bearer <token>
```

Tokens are configured by `MEMORYV4_API_KEYS` as a JSON object mapping token to granted `scope_path`. The service uses constant-time token comparison and rejects reads/writes outside the token's granted scope subtree.

## GET /health

Public health check.

Response:

```json
{
  "status": "ok",
  "service": "memoryv4-core",
  "version": "0.1.0-t2",
  "storage_backend": "sqlite"
}
```

Behavior: initializes/opens the configured SQLite database, runs migrations idempotently, executes `PRAGMA quick_check`, and reports `degraded` if SQLite integrity is not ok.

## POST /records

Create a governed record. Requires the record `scope_path` to be equal to or below the bearer key grant. The write emits an audit event.

Request:

```json
{
  "title": "Decision memory",
  "content": "Credits never expire",
  "role": "canonical",
  "lifecycle": "live",
  "scope_path": "org:acme/project:psi",
  "entity_type": "decision",
  "topic": "credits",
  "source_refs": ["session:abc"],
  "provenance": {"source": "manual-verification"},
  "attrs": {}
}
```

Response: `201` with the stored record including `id`, `author_actor`, timestamps, and `superseded_by`.

## GET /records

List records visible at a requested scope.

Query parameters:
- `scope_path`: optional; defaults to the bearer key's grant scope.
- `include_public`: optional boolean; default `false`.

Visibility is ancestor-or-equal only. Sibling tenant/project/agent/user records are excluded.

Response:

```json
{"records": [{"id": "rec_...", "title": "..."}]}
```

## GET /records/{record_id}

Fetch a single record by id. The record is returned only if its owning `scope_path` is inside the bearer key's granted scope; otherwise the request is rejected.

## GET /search

Search visible records.

Query parameters:
- `q`: required search text.
- `scope_path`: optional; defaults to the bearer key's grant scope.
- `include_public`: optional boolean; default `false`.
- `limit`: optional integer, 1-100; default `25`.

The route records a retrieval event. SQLite FTS5 is used when available; otherwise a lexical fallback is used. Scope filters are applied in both paths.
