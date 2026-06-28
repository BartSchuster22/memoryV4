# MemoryV4 Operations

## Commands

- `python3 -m pip install -r requirements-dev.txt`: install local test dependencies.
- `make run`: run the FastAPI app locally with Uvicorn.
- `make test`: run the local test suite.
- `make docker-build`: build the single core container image.
- `make qa10`: phase placeholder that records QA10 as pending until later gates are implemented.

## Runtime configuration

- `MEMORYV4_DB_PATH`: SQLite database path. Defaults to `/data/memoryv4.sqlite3` in the container.
- Docker Compose mounts `memoryv4-data` at `/data`.
- There is no Postgres DSN or backend selector in P1. SQLite is the only implemented backend.

## Health and readiness

`GET /health` is unauthenticated and checks that `SqliteStore` can initialize/open the configured SQLite path and pass `PRAGMA quick_check`. All future non-health endpoints must enforce scoped keys.

## Git push from chatboard

Use `bin/git-memoryV4` for authenticated pushes. The wrapper honors `MEMORYV4_GIT_DEPLOY_KEY` first, then falls back to the chatboard profile key at `/home/herman/.hermes/profiles/chatboard/home/.ssh/memoryV4_deploy_ed25519_20260628` when the `/srv/memoryV4` key is not mounted.

Example:

```bash
bin/git-memoryV4 push -u origin HEAD
```

## Core boundary

This repo should not contain runtime UI, explorer, Kanban, watchdog, or orchestration directories. The regression suite includes a boundary test that fails if forbidden core-adjacent surfaces are reintroduced.

## Live operations boundary

P0-P1 performs no live deployment, no cutover, and no writes to MemoryV3 or live memory databases. Rollback is a branch revert of the bootstrap/refactor commits because no live state is mutated.
