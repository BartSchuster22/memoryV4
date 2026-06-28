# MemoryV4 Core

MemoryV4 is a slim, single-container FastAPI memory core. This P0-P1 scaffold intentionally includes only the core service baseline, SQLite `Store` adapter seam, health endpoint, docs skeleton, tests, migrations directory, and local build commands.

Hard boundaries for this repository:

- no MemoryV3 cutover or live-memory mutation
- no UI/explorer or Kanban/orchestration code in core
- SQLite-only implementation now; Postgres is a future Store adapter slot only

## Local commands

```bash
python3 -m pip install -r requirements-dev.txt
make lint
make test
make qa
make run
make docker-build
```

## Runtime

The container stores its SQLite file at `/data/memoryv4.sqlite3` by default. Override with `MEMORYV4_DB_PATH` for tests or local runs.
