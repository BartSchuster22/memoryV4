# MemoryV4 Core

MemoryV4 is a slim, single-container FastAPI memory core. This P0-P2 scaffold intentionally includes only the core service baseline, SQLite `Store` adapter seam, additive schema migrations, health endpoint, docs skeleton, tests, and local build commands.

Hard boundaries for this repository:

- no MemoryV3 cutover or live-memory mutation
- no UI/explorer or Kanban/orchestration code in core
- SQLite-only implementation now; Postgres is a future Store adapter slot only

## Local commands

```bash
python3 -m pip install -r requirements-dev.txt
make lint          # compile app/ and tests/
make test          # pytest unit suite
make qa            # lint + test + P2 migration QA scorecard
make qa10-gate-1  # P2 fresh/upgrade/rerun/down migration gate
make run           # uvicorn app.main:app on 127.0.0.1:8000
make docker-build  # build memoryv4-core:dev
```

## Container boot smoke

```bash
docker compose up --build memoryv4-core
curl -fsS http://127.0.0.1:8000/health
```

Expected health response:

```json
{"status":"ok","service":"memoryv4-core","version":"0.1.0-d0","storage_backend":"sqlite"}
```

## Runtime

The container stores its SQLite file at `/data/memoryv4.sqlite3` by default. Override with `MEMORYV4_DB_PATH` for tests or local runs. SQLite migrations are applied on Store startup and recorded in `schema_migrations`; P2 applies baseline `0001`/`0002` plus additive migrations `0006_record_embeddings` through `0012_health_findings`.
