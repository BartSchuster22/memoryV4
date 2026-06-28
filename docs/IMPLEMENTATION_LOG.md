# MemoryV4 Implementation Log

## P0 bootstrap

Scope: bootstrap a fresh MemoryV4 core repository without cutting over from any current memory system.

Baseline present at this run's start:

- `app/main.py` exposes `GET /health`.
- `Dockerfile` builds a single FastAPI core container.
- `docker-compose.yml`, `Makefile`, `pyproject.toml`, `requirements.txt`, and `tests/test_health.py` exist.
- Docs skeleton exists under `docs/`.

## P1 Store seam

This run added the first bounded Store seam while keeping SQLite as the only implemented backend:

- Added `app/models.py` for governed `Record`, `Filter`, `AuditEvent`, `Role`, and `Lifecycle` types.
- Added `app/ports.py` with the narrow `Store` Protocol.
- Replaced the D0 SQLite probe with `SqliteStore` in `app/storage.py`.
- Kept all SQL, WAL, schema initialization, lexical matching, audit writes, and health findings inside the SQLite adapter.
- Kept `vector_rank` as a no-op reserved lane until the later embeddings phase.
- Removed board-local watchdog/orchestration files from the core repo and added a regression test for the slim-core boundary.
- Updated docs for architecture, operations, API, QA/phase status, and this step log.

## Evidence

Local validation during this run:

- `python3 -m pytest tests/test_store_seam.py -q` went RED first on the missing Store seam and then GREEN after implementation.
- `python3 -m pytest -q` passed after implementation.
- `make lint` passed.
- `make qa10` emitted the P1 pending scorecard.
- `docker build -t memoryv4-core:p0-p1 .` passed.
- Container smoke passed: `GET /health` returned `{"service":"memoryv4-core","status":"ok","storage_backend":"sqlite","version":"0.1.0-d0"}`.

## Commit discipline

Use `bin/git-memoryV4` for pushes with the deployed MemoryV4 key. Commit and push after each coherent step.

## Live impact

- No live deploy.
- No MemoryV3 mutation.
- No cutover.
- Rollback: revert this branch/commit; no production data migration or live state change was performed.
