# MemoryV4 Governance

Governance enforcement is implemented in the core service for record creation, read/list/search, patch, promotion, supersession, and lifecycle transition. It is not delegated to UNIUI or framework clients.

## Authenticated grants

`MEMORYV4_API_KEYS` accepts structured grants:

```json
{
  "replace-with-secret-token": {
    "actor": "service:unify",
    "scope_path": "org:aquiero",
    "permissions": ["memory.read", "memory.search", "memory.create-working"],
    "allow_actor_delegation": false
  }
}
```

Scope-only legacy values remain parseable but are deliberately least-privileged: `memory.read`, `memory.search`, and `memory.create-working`. They cannot create canonical content or perform general edits.

`memory.admin` implies action permissions but does not bypass scope checks, idempotency, optimistic concurrency, reasons, immutable/canonical edit rules, or audit.

### UNIFY actor delegation

A grant explicitly configured with `allow_actor_delegation=true` must send a valid `X-MemoryV4-Actor` on every authenticated request. MemoryV4 records that delegated identity as the author/audit actor. Grants without this flag reject the header. This lets a private UNIFY credential assert the already-authenticated application actor without allowing ordinary framework keys to spoof authors.

## Scope decisions

- A grant owns a root `scope_path`.
- Mutation targets must be equal to or descendants of that root.
- A read has an effective scope, defaulting to the grant root.
- List, direct GET, and search use the same ancestor-or-equal visible scope set.
- Reading a descendant object requires selecting that descendant as the effective `scope_path`.
- Sibling/outside direct objects return `404` where object existence would leak.
- `public` is visible only with explicit `include_public=true`.

## Creation authority

| Grant | Allowed creation |
|---|---|
| `memory.create-working` | Exactly `role=active,lifecycle=working` |
| `memory.create` | Non-canonical live/working records |
| `memory.create` + `memory.promote` | May also create canonical/live records |
| `memory.admin` | Same governed rules with all action permissions |

New records cannot start `superseded`, `archived`, or `expired`; lifecycle actions own those states. Client-provided `author_actor` and unknown request fields are rejected.

## Write policies

| Policy | Direct patch rule |
|---|---|
| `team_editable` | Any actor with `memory.edit`; only author/admin may change policy |
| `author_only` | Author with `memory.edit`, or admin |
| `admin_only` | Admin only |
| `immutable` | No direct patch, including admin; governed supersession is the revision path |

Canonical records cannot be patched under any policy. Their revision path is supersession.

## Promotion

`POST /records/{id}/promote` requires:

- `memory.promote`;
- target inside the grant scope;
- an `active/working` candidate;
- `Idempotency-Key`;
- `If-Match` version;
- trimmed `X-MemoryV4-Reason`.

Promotion atomically changes the candidate to `canonical/live`, increments its version, and records the reason in durable audit.

## Lifecycle and supersession

`POST /records/{id}/transition` requires `memory.archive`, `If-Match`, idempotency, and a reason. `live` and `working` may transition to each other or to `archived`/`expired`; canonical records cannot become `working`. Archived and expired records store the exact prior nonterminal lifecycle and restore only to it. Archival sets soft-deletion metadata, while expiry remains ordinarily visible.

`superseded` is terminal and can be produced only by `POST /records/{id}/supersede`. Supersession requires `memory.edit`, write-policy authority, `If-Match`, idempotency, and a reason. It atomically creates a replacement snapshot, closes the source, and links `supersedes`/`superseded_by`. Replacement role, lifecycle, scope, and actor attribution are core-owned. Database guards reject inconsistent lifecycle, deletion, and supersession state.

## Idempotency and concurrency

- Record create, patch, promotion, supersession, and transition require an idempotency key.
- Keys are unique per authenticated/delegated actor across mutation operations.
- Exact replay returns the stored result with `Idempotency-Replayed: true`.
- Reuse for a different canonical request returns `409 idempotency_conflict`.
- SQLite `BEGIN IMMEDIATE` serializes idempotent mutation claims.
- Patch, promotion, supersession, and transition require a strong integer `If-Match` value.
- Stale versions return `412 version_conflict`.

## Audit

Successful creates, patches, promotions, supersessions, transitions, and searches emit durable audit/retrieval records. Failed mutation requests emit `request.denied` with actor, grant scope, method/path, response status, request ID, and bounded lifecycle reason when supplied.

Audit records never include bearer tokens, submitted content, authorization headers, or tracebacks. Actor identity always comes from the authenticated grant or an explicitly authorized UNIFY delegation header—not a request body.
