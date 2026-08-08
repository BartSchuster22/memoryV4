# MemoryV4 API Contract v1

**Status:** Locked architecture and interface contract. Runtime support is discovered truthfully through `GET /capabilities`: `implemented` is v1-compliant, `foundation` exists but does not yet satisfy the full v1 governance contract, and `planned` is not implemented.

## Versioning and transport

- Contract version: `1.0.0`
- Internal service paths are unversioned for v1.
- Every response includes `X-MemoryV4-Contract-Version: 1.0.0` and `X-Request-ID`.
- Breaking changes require a new major API boundary; additive optional fields/endpoints are allowed.
- All routes except `GET /health` require bearer authentication.
- MemoryV4 is private and is called by UNIFY, not browsers or framework containers directly.

## Object contracts

The machine-readable definitions are returned by authenticated `GET /schema` and are sourced from `app/contracts.py`.

| Object | Identity | Purpose |
|---|---|---|
| Entity | `{entity_type, id}` | Typed durable subject such as project, person, service, agent, decision, collection, or document |
| Record | `rec_*` | Governed content with authority, lifecycle, provenance, ownership, and history |
| Relation | `rel_*` | Typed edge between entities, records, or artifacts |
| Artifact | `art_*` | External/binary source linked to an entity or record |
| Finding | `fnd_*` | Candidate, contradiction, staleness, or health review item |

Entity types and relation types are extensible strings. Their identity and semantics are not encoded as separate physical stores.

## Permissions

| Permission | Meaning |
|---|---|
| `memory.read` | Read visible objects and context |
| `memory.search` | Search visible records and emit retrieval evidence |
| `memory.create-working` | Create only `active/working` candidates |
| `memory.create` | Create governed objects subject to policy |
| `memory.edit` | Edit mutable objects subject to policy/version |
| `memory.promote` | Promote verified content to canonical authority |
| `memory.archive` | Transition lifecycle, soft-delete, or restore |
| `memory.review` | Read and resolve review findings |
| `memory.audit.read` | Read audit and retrieval events |
| `memory.admin` | Restricted schema/usage/maintenance operations |

`memory.admin` does not erase scope, lifecycle, audit, idempotency, reason, or version requirements.

## Scope decision

A key/actor grant identifies a root scope. For an effective request scope such as:

```text
org:aquiero/project:alica-v1/agent:alica/user:u1
```

reads may return `global`, `org:aquiero`, each ancestor through the exact effective scope, and `public` only when explicitly requested. Sibling branches are never visible. Mutations may target only the grant scope or its descendants. Direct GET, list, search, and context use the same visibility decision.

Unauthorized direct-object access must return `404 not_found` where revealing existence would leak a sibling object.

## Mutation protocol

- `Idempotency-Key` is required for v1 mutations.
- `If-Match: "<version>"` is also required for update, supersede, transition, promote, and finding resolution.
- Lifecycle, supersession, promotion, deletion, restoration, and finding-resolution requests require a non-empty reason.
- Authenticated identity is the actor; clients cannot choose `author_actor`.
- A private gateway grant explicitly configured for actor delegation must send
  `X-MemoryV4-Actor`; grants without that capability reject the header.
- Reuse of an idempotency key with a different canonical request returns `409 idempotency_conflict`.
- Stale versions return `412 version_conflict`; absent preconditions return `428 precondition_required`.
- Successful and denied mutations emit durable audit events without secrets.

## Required endpoint inventory

| Method | Path | Permission | Mutation requirements |
|---|---|---|---|
| GET | `/capabilities` | `memory.read` | — |
| GET | `/schema` | `memory.read` | — |
| GET/POST | `/entities` | read/create | POST: idempotency |
| GET/PATCH | `/entities/{type}/{id}` | read/edit | PATCH: idempotency + version |
| GET/POST | `/records` | read/create | POST: idempotency |
| GET/PATCH | `/records/{id}` | read/edit | PATCH: idempotency + version |
| POST | `/records/{id}/supersede` | edit | idempotency + version + reason |
| POST | `/records/{id}/transition` | archive | idempotency + version + reason |
| POST | `/records/{id}/promote` | promote | idempotency + version + reason |
| GET/POST | `/relations` | read/create | POST: idempotency |
| GET/POST | `/artifacts` | read/create | POST: idempotency |
| GET | `/search` | search | retrieval event |
| GET | `/context/{entity_type}/{entity_id}` | read | — |
| GET | `/review/findings` | review | — |
| POST | `/review/findings/{id}/resolve` | review | idempotency + version + reason |
| GET | `/audit/events` | audit.read | — |
| GET | `/retrieval-events` | audit.read | — |
| GET | `/usage` | admin | — |

List endpoints use cursor pagination and default to excluding soft-deleted objects. Filters are additive query parameters; cursors are opaque.

## Record invariants

- Autonomous creation defaults to `role=active,lifecycle=working`.
- `canonical` creation or promotion requires `memory.promote`.
- Canonical revision creates a replacement and links `supersedes`/`superseded_by` atomically.
- `immutable` records cannot be edited; a permitted supersession is the only revision path.
- Soft deletion is lifecycle metadata, never a hard-delete mutation through the application API.
- Provenance and source references remain attached through lifecycle changes.

## Error envelope

All handled API errors use:

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

Stable v1 codes include:

- `unauthorized`
- `forbidden`
- `not_found`
- `invalid_request`
- `conflict`
- `idempotency_conflict`
- `version_conflict`
- `precondition_required`
- `unsupported_operation`
- `rate_limited`
- `internal_error`

Validation detail may grow additively, but secrets, bearer tokens, internal tracebacks, and database paths must never be returned.

## Discovery truth

`GET /capabilities` lists every required operation with `implemented` or `planned` status. Clients must feature-detect rather than infer support from this document. `GET /schema` publishes object fields, enums, and invariant statements without exposing storage internals.
