# MemoryV4 Core

MemoryV4 is a slim FastAPI memory core foundation. This slice implements a governed SQLite-only service with scoped authenticated record/retrieval routes, additive migrations, audit/retrieval event tables, and tests for the isolation boundary.

Hard boundaries for this repository:
- no MemoryV3 cutover or live-memory mutation
- no UI/explorer or Kanban/orchestration code in core
- SQLite-only implementation now; Postgres is a future Store adapter slot only
- no autonomous process writes canonical memory; canonical/live writes require an authenticated caller and are audited

## Implemented foundation

- Public `GET /health`.
- Authenticated `POST /records`, `GET /records`, `GET /records/{id}`, and `GET /search`.
- Governed record model with roles `canonical`, `active`, `evidence`, `exhaust` and lifecycles `live`, `working`, `superseded`, `archived`, `expired`.
- `scope_path` tenant isolation using ancestor-or-equal visibility. Sibling tenant/user/agent/project branches are not returned.
- SQLite migration registry with reversible `0001_foundation` migration.
- `SqliteStore` adapter below a store protocol; SQL/FTS5 specifics stay in the adapter.
- Audit events for governed writes and retrieval events for search.
- SQLite FTS5 search when available, with safe lexical fallback.

## Local commands

```bash
python3 -m pip install -r requirements-dev.txt
make test
make lint
make qa
make run
make docker-build
make qa10
```

## Runtime configuration

- `MEMORYV4_DB_PATH`: SQLite database path. Defaults to `/data/memoryv4.sqlite3`.
- `MEMORYV4_API_KEYS`: JSON object mapping bearer token to granted `scope_path`, for example `{"dev-token":"org:acme/project:psi"}`.
- `MEMORYV4_API_KEY` and `MEMORYV4_API_SCOPE`: single-key fallback when `MEMORYV4_API_KEYS` is not set.

Only `GET /health` is public. All non-health routes require `Authorization: Bearer <token>` and reject requested scopes outside the token grant.
