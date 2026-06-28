# MemoryV4 Core

MemoryV4 is a slim, single-container FastAPI memory core. This D0 scaffold intentionally includes only the core service baseline, SQLite storage probe, health endpoint, docs skeleton, tests, migrations directory, board-local autonomy artifacts, and local build commands.

Hard boundaries for this repository:
- no MemoryV3 cutover or live-memory mutation
- no UI/explorer or Kanban/orchestration code in core
- SQLite-only implementation now; Postgres is a future Store adapter slot only

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

## Runtime

The container stores its D0 SQLite file at `/data/memoryv4.sqlite3` by default. Override with `MEMORYV4_DB_PATH` for tests or local runs.
