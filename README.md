# MemoryV4 Core

MemoryV4 is a slim, single-container FastAPI memory core. This D0 scaffold intentionally includes only the core service baseline, health endpoint, docs skeleton, tests, migrations directory, and local build commands.

Hard boundaries for this repository:
- no MemoryV3 cutover or live-memory mutation
- no UI/explorer or Kanban/orchestration code in core
- SQLite-only implementation now; Postgres is a future Store adapter slot only

## Local commands

```bash
make test
make run
make docker-build
make qa10
```
