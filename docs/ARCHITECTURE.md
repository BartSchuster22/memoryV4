# MemoryV4 Architecture

## D0 baseline

MemoryV4 core starts as a slim, single-container FastAPI service with a public `/health` endpoint. This repository is for the memory core only; explorer/UI and Kanban/orchestration live outside the core.

## Non-negotiables

1. One object model. Express Tencent-style Fact/Scene/Persona layers as governed V3-style records and relations. No parallel `facts`/`scenes` stores.
2. Governance is the moat. Preserve roles `canonical`, `active`, `evidence`, `exhaust`; lifecycles `live`, `working`, `superseded`, `archived`, `expired`; supersession; `write_policy`; `author_actor`; `audit_events`; `retrieval_events`; and scoped keys.
3. No autonomous process writes canonical. Distillation and health workers may write `working` records or findings only. A verification step promotes to `live`/`canonical`.
4. Slim is a hard boundary. Memory core only. Kanban/orchestration and the web/explorer UI belong in separate repositories. PRs that re-add UI or orchestration to core are rejected.
5. Additive migrations only. V3 data upgrades in place; every `up` has a `down`.
6. Multi-tenant isolation is a safety property. A query at one scope must never surface another tenant's records.

## C1 governance spine

C1 adds the core domain and persistence seam without adding UI, explorer, orchestration, cutover, or live-memory writes.

- `app/schemas.py` defines the governed domain schemas for records, entities, relations, artifacts, audit events, retrieval events, roles, lifecycles, scopes, source references, author actors, write policies, and supersession.
- `app/ports.py` defines the engine-agnostic `Store` Protocol. App/core code depends on this port.
- `app/core.py` is a thin governance service over the `Store` port and intentionally imports no SQLite adapter.
- `app/sqlite_store.py` is the only concrete storage adapter in C1. It implements audited record CRUD, lifecycle transitions, supersession integrity, a keyword lexical ranking lane, and degraded vector placeholder behavior.
- `migrations/0001` through `migrations/0012` define the additive SQLite schema objects and paired downs for the objects introduced by each migration.

## Storage direction

SQLite is the only implemented backend now. A future Postgres adapter may be added behind a Store port only when real tenant concurrency demands it; C1 does not implement Postgres.

## Deployable

The core deployable is one container built from `Dockerfile`. Persistent storage, migrations, auth, and retrieval features are added by later phases.
