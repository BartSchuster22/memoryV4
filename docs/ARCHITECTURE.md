# MemoryV4 Architecture

## D0 baseline

MemoryV4 core starts as a slim, single-container FastAPI service with a public `/health` endpoint and a SQLite-only storage probe. This repository is for the memory core only; explorer/UI and Kanban/orchestration live outside the core.

## Non-negotiables

1. One object model. Express Tencent-style Fact/Scene/Persona layers as governed V3-style records and relations. No parallel `facts`/`scenes` stores.
2. Governance is the moat. Preserve roles `canonical`, `active`, `evidence`, `exhaust`; lifecycles `live`, `working`, `superseded`, `archived`, `expired`; supersession; `write_policy`; `author_actor`; `audit_events`; `retrieval_events`; and scoped keys.
3. No autonomous process writes canonical. Distillation and health workers may write `working` records or findings only. A verification step promotes to `live`/`canonical`.
4. Slim is a hard boundary. Memory core only. Kanban/orchestration and the web/explorer UI belong in separate repositories. PRs that re-add UI or orchestration to core are rejected.
5. Additive migrations only. V3 data upgrades in place; every `up` has a `down`.
6. Multi-tenant isolation is a safety property. A query at one scope must never surface another tenant's records.

## Current package layout

- `app/main.py`: FastAPI app factory and `/health` route.
- `app/settings.py`: D0 runtime settings. SQLite is the only configured storage backend.
- `app/storage.py`: tiny SQLite probe used by health checks. The full governed Store port comes later.
- `migrations/`: reserved for additive migrations.
- `ops/watchdogs/`: board-local operational aids, not runtime code.
- `artifacts/`: durable evidence/watchdog state root with no secrets or live memory exports.

## Storage direction

SQLite is the only implemented backend now. The D0 container defaults to `/data/memoryv4.sqlite3` and exposes it as a Docker volume. A future Postgres adapter may be added behind a Store port only when real tenant concurrency demands it; D0 does not implement Postgres.

## Deployable

The core deployable is one container built from `Dockerfile`. Persistent storage, migrations, auth, and retrieval features are added by later phases.
