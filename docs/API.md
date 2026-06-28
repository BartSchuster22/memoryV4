# MemoryV4 API

MemoryV4 exposes a small core API for governed memory records. This file defines the target contract for implementation phases. P0 may only implement `/health`; later phases add the governed routes below without changing the authorization and scoping rules.

## Contract principles

- `GET /health` is the only unauthenticated route.
- Every other route requires a scoped API key.
- Every request is evaluated against the key's allowed scope subtree.
- Public scope is reserved and unavailable to normal write paths.
- Autonomous workers may propose `working` records or findings only; they may not create or promote `canonical` records.
- Mutating routes write audit events in the same transaction as the mutation.
- Retrieval routes write retrieval events without exposing other tenants' records.

## Auth model

Clients authenticate with a bearer token:

```http
Authorization: Bearer <api_key>
```

Each key is configured with:

- `actor_id`: stable actor name for audit events.
- `allowed_scope_path`: the root scope subtree the key may access.
- `capabilities`: allowed operations such as `read`, `write_working`, `promote`, `supersede`, `admin_public`, `backup`, or `restore`.
- `expires_at` or rotation metadata where supported.

Token comparison must be constant-time. On startup, a non-loopback listener without configured keys must emit a warning or fail closed according to deployment policy.

## Scope authorization

Every non-health request includes or derives a requested scope:

```http
X-MemoryV4-Scope: org:acme/project:psi/agent:alice/user:u123
```

The service rejects the request unless the requested scope is equal to or below the API key's `allowed_scope_path`.

Retrieval returns only records in the requested scope prefix subtree, plus curated `public` records only if tenant policy explicitly enables public reads.

Examples:

- A key scoped to `org:acme/project:psi/agent:alice` may query `.../user:u123` under Alice.
- The same key may not query `org:acme/project:psi/agent:bob`.
- A default tenant query does not read `public` unless public reads are explicitly enabled.
- No autonomous actor may write to `public`.

## Record shape

Target JSON representation:

```json
{
  "id": "rec_...",
  "entity_id": "ent_...",
  "entity_type": "decision",
  "title": "Credits do not expire",
  "topic": "billing-policy",
  "content": "Decision, rationale, alternatives, and current policy.",
  "attrs": {},
  "role": "canonical",
  "lifecycle": "live",
  "scope_path": "org:acme/project:psi",
  "write_policy": {
    "promote_requires": ["promote"],
    "supersede_requires": ["supersede"]
  },
  "author_actor": "human:operator",
  "source_refs": ["session:...", "artifact:..."],
  "supersedes": [],
  "superseded_by": null,
  "created_at": "2026-06-28T00:00:00Z",
  "updated_at": "2026-06-28T00:00:00Z"
}
```

Allowed roles: `canonical`, `active`, `evidence`, `exhaust`.
Allowed lifecycles: `live`, `working`, `superseded`, `archived`, `expired`.

## Public endpoints

### GET /health

Unauthenticated readiness check.

Response:

```json
{
  "status": "ok",
  "service": "memoryv4-core",
  "version": "0.1.0",
  "storage_backend": "sqlite",
  "degraded": false
}
```

The health check may open the configured SQLite file and run a lightweight integrity check. It must not leak secrets, tenant names, record counts by tenant, or private paths.

## Governed record endpoints

### POST /v1/records

Create a governed record.

Required capability:

- `write_working` for autonomous actors creating `working` candidates.
- `write` or stronger for verified human/service writes.
- `promote` is required if role/lifecycle is `canonical/live`.

Request body:

```json
{
  "entity_id": "ent_...",
  "entity_type": "fact",
  "title": "Short title",
  "topic": "topic-key",
  "content": "Record body",
  "attrs": {},
  "role": "active",
  "lifecycle": "working",
  "scope_path": "org:acme/project:psi/agent:alice",
  "source_refs": ["session:abc#turn-12"]
}
```

Rules:

- The requested `scope_path` must be within the key's allowed scope.
- Autonomous keys are rejected if they request `canonical` or `public`.
- `source_refs` are required for distilled records.
- Audit event is written in the same transaction.

### GET /v1/records/{record_id}

Fetch one record if the caller is authorized for the record's scope.

Rules:

- Return `404` for missing records.
- Return `404` or `403` for unauthorized scope according to deployment policy, but never leak sibling existence.

### POST /v1/records/{record_id}/transition

Change lifecycle without changing content.

Request body:

```json
{
  "lifecycle": "archived",
  "reason": "Superseded by current policy record"
}
```

Rules:

- Lifecycle must be a valid transition.
- Promotion to `live` or `canonical/live` requires explicit verification capability.
- Audit event is mandatory.

### POST /v1/records/{record_id}/supersede

Create a replacement record and mark the old record superseded while preserving lineage.

Request body:

```json
{
  "replacement": {
    "title": "Updated title",
    "content": "Updated governed content",
    "role": "canonical",
    "lifecycle": "live",
    "source_refs": ["review:...", "session:..."]
  },
  "reason": "Verified correction"
}
```

Rules:

- The old and new records must be scope-compatible.
- The old record receives `lifecycle=superseded` and `superseded_by=<new_id>`.
- The new record references the old record in `supersedes`.
- Contradiction findings are resolved by explicit supersession, never by silent overwrite.

## Retrieval endpoints

### POST /v1/search

Search governed records within the caller's scope.

Request body:

```json
{
  "query": "credits expiration policy",
  "scope_path": "org:acme/project:psi/agent:alice/user:u123",
  "filters": {
    "entity_type": "decision",
    "role": null,
    "lifecycle": "live",
    "topic": null,
    "include_public": false
  },
  "limit": 10
}
```

Response body:

```json
{
  "results": [
    {
      "record": {"id": "rec_..."},
      "score": 0.031,
      "lanes": {"lexical_rank": 1, "vector_rank": 3},
      "degraded": false
    }
  ]
}
```

Rules:

- Lexical and vector lanes apply identical scope filters.
- Fusion happens before final limit using reciprocal-rank fusion with `k=60`.
- Governance prior is bounded and cannot hide strongly relevant evidence.
- If vector search is unavailable or times out, the response may degrade to lexical-only and must report that state.

## Worker endpoints

### POST /v1/distillation/candidates

Internal endpoint or service method for distillation workers to create candidate records.

Rules:

- Always writes `working` records.
- Requires source refs.
- Deduplication must be idempotent through `promotion_log` or equivalent.
- Cannot write `canonical`, `live/canonical`, or `public`.

### POST /v1/health/findings

Internal endpoint or service method for health workers to write findings.

Finding kinds:

- `contradiction`
- `compaction`
- `decay`
- `orphan`

Rules:

- Findings are inert review items.
- Decay findings do not change retrieval order.
- Contradiction findings do not auto-supersede records.
- Duplicate findings are prevented by a uniqueness key over kind and refs.

## Backup and restore endpoints

Backup and restore may be CLI-only in early phases. If exposed by API, they require `backup` or `restore` capabilities and must be disabled for ordinary application keys.

Minimum contract:

- backup emits a checksummed artifact containing SQLite database content and metadata;
- restore refuses to overwrite an existing database unless explicitly forced;
- restore verifies checksum and runs integrity checks before serving traffic.

## Error shape

Errors use a stable JSON envelope:

```json
{
  "error": {
    "code": "scope_forbidden",
    "message": "Requested scope is outside the API key grant.",
    "request_id": "req_..."
  }
}
```

Recommended status codes:

- `400`: invalid request shape or invalid enum.
- `401`: missing/invalid auth.
- `403`: authenticated but capability/scope denied.
- `404`: record not found or intentionally hidden by scope policy.
- `409`: lifecycle/supersession conflict.
- `422`: governance invariant violation.
- `503`: degraded dependency where fallback is unavailable.
