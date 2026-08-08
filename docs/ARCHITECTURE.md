# MemoryV4 Architecture

The Tier-3 ownership/deployment boundary is locked by [ADR-0001](ADR-0001-TIER3-BOUNDARY.md); [Contract v1](CONTRACT_V1.md) defines the stable object/API/error surface.

## Core-object slice

MemoryV4 core is a slim FastAPI service around one governed object graph and a SQLite-only Store adapter. The implemented graph contains:

- composite-identity entities;
- governed records linked to entities;
- typed relations between entities, records, and artifacts;
- artifacts linked to records/entities;
- FTS-backed record retrieval;
- entity-context aggregation;
- audit and retrieval events.

UI/explorer surfaces, Kanban/orchestration, distillation and health workers, vector embeddings, Postgres, and MemoryV3 cutover logic remain outside this slice.

## Non-negotiables

1. **One object model.** Fact/Scene/Persona/Decision concepts are records and relations, not parallel stores.
2. **Governance is in core.** Roles, lifecycle, write policy, actor identity, action grants, scope isolation, idempotency, optimistic versions, and mutation audit are enforced below every client.
3. **No autonomous canonical writes.** Autonomous grants create only active/working candidates; canonical creation/promotion requires `memory.promote`.
4. **Slim boundary.** UI and orchestration belong elsewhere.
5. **Additive migrations.** `0001_foundation`, `0002_governance`, and `0003_core_objects` have ordered up/down paths.
6. **Scope isolation is graph-wide.** Direct/list/search/context reads use ancestor-or-equal visibility. New links require every referenced object to exist and be visible from the link scope, preventing sibling edges.
7. **Opaque pagination state is not authority.** Cursors are bound to scope/query/filter/sort context; authorization is reevaluated on every page request.

## Package layout

- `app/main.py`: FastAPI factory, governed object routes, discovery, error middleware, and authorization.
- `app/contracts.py`: machine-readable contract, permissions, operation status, schemas, and invariants.
- `app/schemas.py`: Pydantic object graph, validators, page envelopes, and context response.
- `app/pagination.py`: opaque context-bound cursor codec.
- `app/migrations.py`: additive SQLite migration registry and rollback helper.
- `app/storage.py`: SQLite Store adapter, transactions, FTS5, object integrity, pagination, and aggregation.
- `tests/test_core_objects.py`: composite identity, object graph, pagination, filters, context, integrity, and scope tests.
- `tests/test_governance.py`: permissions, write policy, delegation, idempotency/concurrency, promotion, and denial audit.
- `tests/test_foundation.py`, `tests/test_contract.py`, `tests/test_health.py`: migration/storage, contract, and health coverage.

## Storage and retrieval

SQLite is the only backend. WAL and busy-timeout settings support serialized mutation claims. Mutation objects, audit events, and idempotency responses are committed in the same transaction. Existing FTS triggers continue to synchronize record title/content updates. Search uses FTS5 and `bm25()` when available and falls back to lexical `LIKE` matching with identical scope/object filters.

`0003_core_objects` rebuilds entity identity as `(entity_type,id)`, expands records with entity IDs/tags/confidence/supersession/deletion metadata, and replaces foundation relation/artifact layouts with typed references and versions. Existing rows are preserved with safe defaults.

## Scope model

A scope such as:

```text
org:acme/project:psi/agent:alice/user:u123/session:s001
```

can see `global` and each ancestor through its exact effective scope. It cannot see sibling tenants, projects, agents, users, or sessions. `public` is included only when explicitly requested with `include_public=true`. Requested effective scopes must remain within the bearer grant.
