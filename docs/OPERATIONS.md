# MemoryV4 Operations

## Local commands

- `python3 -m pip install -r requirements-dev.txt`: install local test dependencies.
- `make run`: run the FastAPI app locally with Uvicorn.
- `make test`: run the local test suite.
- `make lint`: run Ruff over `app` and `tests`.
- `make qa`: run lint, tests, and the QA10 scorecard placeholder.
- `make docker-build`: build the single core container image.
- `make qa10`: write and print the current QA10 scorecard JSON.

## Runtime configuration

- `MEMORYV4_DB_PATH`: SQLite database path. Defaults to `/data/memoryv4.sqlite3`.
- `MEMORYV4_API_KEYS`: JSON object mapping bearer token to granted scope, for example `{"tenant-a-key":"org:a"}`.
- `MEMORYV4_API_KEY`: single bearer token fallback.
- `MEMORYV4_API_SCOPE`: single-token grant scope. Defaults to `global` when `MEMORYV4_API_KEY` is set.

## Health and migrations

`GET /health` is public. It initializes the SQLite database, runs pending migrations, enables WAL, and performs `PRAGMA quick_check`. Migrations are idempotent; rerunning on an initialized DB is a no-op.

## Auth and scope operations

All non-health routes require bearer auth. A token grant is a scope subtree. For example, a token granted `org:a/project:p` can read and write `org:a/project:p` and descendants, but cannot request `org:b`, `org:a/project:other`, or `global` directly. Read/search results are filtered to ancestor-or-equal records under the requested scope.

## Backup and artifacts

SQLite database files, WAL files, local caches, `.env` files, and secrets are ignored by git and must not be committed. Back up the DB before manual migration rollback or destructive local testing.

## Live operations boundary

This slice performs no live deployment, no cutover, and no writes to MemoryV3 or Hermes generated `MEMORY.md`/`USER.md` files.
