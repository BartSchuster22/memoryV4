# MemoryV4 Architecture

## P0-P1 baseline

MemoryV4 core is a slim, single-container FastAPI service with a public `/health` endpoint and a SQLite-only governed storage adapter. This repository is for the memory core only; explorer/UI and Kanban/orchestration live outside the core.

## Non-negotiables

1. One object model. Express Tencent-style Fact/Scene/Persona layers as governed V3-style records and relations. No parallel `facts`/`scenes` stores.
2. Governance is the moat. Preserve roles `canonical`, `active`, `evidence`, `exhaust`; lifecycles `live`, `working`, `superseded`, `archived`, `expired`; supersession; audit trail; source references; and scoped keys.
3. No autonomous process writes canonical. Distillation and health workers may write `working` records or findings only. A verification step promotes to `live`/`canonical`.
4. Slim is a hard boundary. Memory core only. Kanban/orchestration and the web/explorer UI belong in separate repositories. PRs that re-add UI or orchestration to core are rejected.
5. Additive migrations only. V3 data upgrades in place; every `up` has a `down`.
6. Multi-tenant isolation is a safety property. A query at one scope must never surface another tenant's records.

## Current package layout

- `app/main.py`: FastAPI app factory and `/health` route.
- `app/settings.py`: runtime settings. SQLite is the only configured storage backend.
- `app/models.py`: governed core record, filter, lifecycle, role, and audit dataclasses.
- `app/ports.py`: narrow `Store` Protocol used by core code.
- `app/storage.py`: `SqliteStore`, the only implemented adapter. All SQL and SQLite behavior stays here.
- `migrations/`: reserved for additive migrations.
- `artifacts/`: optional local evidence root with no secrets or live memory exports.

## Store seam

The P1 storage seam is deliberately narrow:

- governance CRUD: `create_record`, `get_record`, `transition`, `supersede`
- retrieval lanes: `lexical_rank` and `vector_rank` return ranked record ids only
- support writes: `write_audit` and `write_finding`

`SqliteStore` initializes the P1 tables (`records`, `audit_events`, `health_findings`) and keeps WAL/SQL details behind the adapter. `vector_rank` is a reserved lane that returns no candidates until the embedding phase; this preserves SQLite-only baseline behavior while keeping the adapter boundary in place.

Postgres is not implemented, configured, or imported in P1. It is a documented future adapter slot only.

## Deployable

The core deployable is one container built from `Dockerfile`. It persists SQLite at `/data/memoryv4.sqlite3`, exposes only the core FastAPI service, and contains no UI, explorer, Kanban, watchdog, or orchestration runtime code.
