# MemoryV4 Core

MemoryV4 is a slim FastAPI memory core. This slice implements a governed SQLite-only
object graph, scoped retrieval/context, record lifecycle, typed review findings,
queryable audit/retrieval events, usage reporting, and additive migrations.

The architecture and v1 contract are now locked:

- [Tier-3 architecture ADR](docs/ADR-0001-TIER3-BOUNDARY.md)
- [API/object/error contract v1](docs/CONTRACT_V1.md)
- [Enforced governance model](docs/GOVERNANCE.md)
- Authenticated `GET /capabilities` for truthful runtime support discovery
- Authenticated `GET /schema` for the machine-readable object contract

Hard boundaries for this repository:
- no MemoryV3 cutover or live-memory mutation
- no UI/explorer or Kanban/orchestration code in core
- SQLite-only implementation now; Postgres is a future Store adapter slot only
- no autonomous process writes canonical memory; v1 action permissions enforce this boundary in core

## Implemented core-object slice

- Public `GET /health`.
- Authenticated `GET /capabilities` and `GET /schema` contract discovery.
- Stable v1 request/contract headers and handled error envelope.
- Action-scoped grants, least-privileged legacy keys, and explicit UNIFY actor delegation.
- Enforced write policies, protected canonical creation/promotion, and unified read visibility.
- Idempotent record create/patch/promotion with optimistic concurrency and denial audit.
- Authenticated governed `POST /records`, `GET /records`, `GET /records/{id}`,
  `PATCH /records/{id}`, `POST /records/{id}/promote`,
  `POST /records/{id}/supersede`, `POST /records/{id}/transition`, and `GET /search`.
- Composite-identity entity create/list/get/patch APIs.
- Typed relation and artifact create/list APIs with scoped reference integrity.
- Entity-linked records with tags/confidence, list/search filters, opaque cursor pagination,
  deterministic sorting, and `GET /context/{entity_type}/{id}` aggregation.
- Governed record model with roles `canonical`, `active`, `evidence`, `exhaust` and lifecycles `live`, `working`, `superseded`, `archived`, `expired`.
- `scope_path` tenant isolation using ancestor-or-equal visibility. Sibling tenant/user/agent/project branches are not returned.
- Atomic canonical/immutable supersession plus governed archive, expiry, and exact restoration.
- Typed finding list/resolve APIs with scope, optimistic concurrency, idempotency,
  reviewer attribution, and audit.
- Governed cursor APIs for audit/retrieval events and admin-only usage aggregation.
- SQLite migration registry with reversible foundation, governance, core-object,
  lifecycle, and review/audit/operations migrations.
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

## Single-container Docker runtime

The supported packaged runtime is one MemoryV4 application container. It runs the
FastAPI service and uses a SQLite database file at `MEMORYV4_DB_PATH`; do not add
Postgres, Redis, a vector database, worker sidecars, or a separate frontend
container to this deployment slice.

Build the image:

```bash
docker build -t memoryv4-core:dev .
```

Run with a fresh named volume for runtime SQLite state:

```bash
docker volume create memoryv4-data
docker run --rm --name memoryv4-core \
  -p 8000:8000 \
  -e MEMORYV4_DB_PATH=/data/memoryv4.sqlite3 \
  -v memoryv4-data:/data \
  memoryv4-core:dev
```

Verify health and migration/schema initialization from another shell:

```bash
curl -fsS http://127.0.0.1:8000/health
docker exec memoryv4-core python - <<'PY'
import sqlite3
db = "/data/memoryv4.sqlite3"
with sqlite3.connect(db) as conn:
    print(conn.execute("select version from schema_migrations order by version").fetchall())
PY
```

`docker-compose.yml` is optional local convenience only and defines exactly one
application service plus its named volume:

```bash
docker compose up --build
```

## Runtime configuration

- `MEMORYV4_DB_PATH`: SQLite database path. Defaults to `/data/memoryv4.sqlite3`.
- `MEMORYV4_API_KEYS`: JSON object mapping bearer token to a structured actor,
  `scope_path`, permissions, and optional `allow_actor_delegation` grant. Legacy
  token-to-scope strings are read/search/create-working only.
- `MEMORYV4_API_KEY` and `MEMORYV4_API_SCOPE`: single-key fallback when `MEMORYV4_API_KEYS` is not set.
- `MEMORYV4_API_ACTOR` and `MEMORYV4_API_PERMISSIONS`: actor and comma-separated
  permissions for the single-key fallback.

Only `GET /health` is public. All non-health routes require bearer authorization and reject requested scopes outside the token grant.
