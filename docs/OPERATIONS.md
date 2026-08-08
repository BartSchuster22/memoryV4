# MemoryV4 Operations

## Local commands

- `python3 -m pip install -r requirements-dev.txt`: install local test dependencies.
- `make run`: run the FastAPI app locally with Uvicorn.
- `make test`: run the local test suite.
- `make lint`: run Ruff over `app` and `tests`.
- `make qa`: run lint, tests, and the QA10 scorecard placeholder.
- `make docker-build`: build the single core container image.
- `make qa10`: write and print the current QA10 scorecard JSON.

## Single-container Docker operations

MemoryV4 is packaged as one application container. The container runs Uvicorn and
stores runtime state in SQLite at `MEMORYV4_DB_PATH`, which defaults to
`/data/memoryv4.sqlite3` in the image. No database container, queue, vector DB,
worker sidecar, or separate frontend container is part of this slice.

Build from the repository root:

```bash
docker build -t memoryv4-core:dev .
```

Start with a fresh named volume:

```bash
docker volume create memoryv4-data
docker run --rm --name memoryv4-core \
  -p 8000:8000 \
  -e MEMORYV4_DB_PATH=/data/memoryv4.sqlite3 \
  -v memoryv4-data:/data \
  memoryv4-core:dev
```

Verify the running container:

```bash
curl -fsS http://127.0.0.1:8000/health
docker exec memoryv4-core python - <<'PY'
import sqlite3
db = "/data/memoryv4.sqlite3"
with sqlite3.connect(db) as conn:
    print(conn.execute("PRAGMA quick_check").fetchone()[0])
    print(conn.execute("select version from schema_migrations order by version").fetchall())
PY
```

Expected verification output includes health JSON with `"status":"ok"`,
`quick_check` equal to `ok`, and `0001_foundation` present in
`schema_migrations`; a current database includes `0001_foundation` through
`0005_review_audit_operations`.

Optional local Compose convenience:

```bash
docker compose up --build
```

The compose file must remain a single MemoryV4 app service. It may declare a
named volume for `/data`, but must not define Postgres, Redis, vector DBs,
workers, or frontend services.

## Runtime configuration

- `MEMORYV4_DB_PATH`: SQLite database path. Defaults to `/data/memoryv4.sqlite3`.
- `MEMORYV4_API_KEYS`: JSON object mapping bearer token to granted scope, for example `{"tenant-a-key":"org:a"}`.
- `MEMORYV4_API_KEY`: single bearer token fallback.
- `MEMORYV4_API_SCOPE`: single-token grant scope. Defaults to `global` when `MEMORYV4_API_KEY` is set.

## Health and migrations

`GET /health` is public. It initializes the SQLite database, runs pending migrations, enables WAL, and performs `PRAGMA quick_check`. Migrations are idempotent; rerunning on an initialized DB is a no-op.

## Auth and scope operations

All non-health routes require bearer auth. A token grant is a scope subtree. For example, a token granted `org:a/project:p` can read and write `org:a/project:p` and descendants, but cannot request `org:b`, `org:a/project:other`, or `global` directly. Read/search results are filtered to ancestor-or-equal records under the requested scope.

Operational access is split deliberately: `memory.review` reads/closes findings,
`memory.audit.read` queries audit and retrieval events, and `memory.admin` queries
`/usage`. Use timezone-aware `from_time`/`to_time` for incident and usage windows.
Workers write findings through the Store seam; no worker sidecar or public
finding-create endpoint is part of this deployment.

## Backup and artifacts

SQLite database files, WAL files, local caches, `.env` files, and secrets are ignored by git and must not be committed. Back up the DB before manual migration rollback or destructive local testing.

## Live operations boundary

This slice performs no live deployment, no cutover, and no writes to MemoryV3 or Hermes generated `MEMORY.md`/`USER.md` files.
