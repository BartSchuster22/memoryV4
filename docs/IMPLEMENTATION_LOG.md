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

## P2 additive migrations

This run added the P2 additive migration slice from the rollback baseline:

- Reworked the migration harness around explicit `Migration` objects with non-empty `up_sql` and `down_sql`.
- Added `0006_record_embeddings` through `0012_health_findings`, intentionally leaving `0003`-`0005` absent.
- Kept migrations SQLite-only, additive, rerunnable, and reversible for the objects/columns they introduce.
- Added a seeded P1 upgrade fixture that verifies existing governed rows survive and receive `scope_path='global'` defaults.
- Added `make qa10-gate-1` and a partial P2 QA10 scorecard writer.

## Evidence

Local validation during P1:

- `python3 -m pytest tests/test_store_seam.py -q` went RED first on the missing Store seam and then GREEN after implementation.
- `python3 -m pytest -q` passed after implementation.
- `make lint` passed.
- `make qa10` emitted the P1 pending scorecard.
- `docker build -t memoryv4-core:p0-p1 .` passed.
- Container smoke passed: `GET /health` returned `{"service":"memoryv4-core","status":"ok","storage_backend":"sqlite","version":"0.1.0-d0"}`.
- `docker run --rm memoryv4-core:dev python -m pytest -q` went RED first because pytest/tests were not present in the image.
- Added `make docker-test` and updated the Docker image/CI/docs so the same single image can run local pytest and boot as the runtime service.
- `make docker-test` passed after the image update.

Local validation during P2:

- `python3 -m pytest tests/test_migrations_p2.py -q` went RED first on the missing P2 migration/rollback harness and then GREEN after implementation.
- `python3 -m pytest -q` passed after updating Store seam expectations for P2 migrations.
- `make qa10-gate-1` passed fresh DB, seeded upgrade fixture, rerun/no-op, and down-path checks.
- `make qa10` emitted `build/qa10-scorecard.json` with Gate 1 passing and later gates pending.

## Commit discipline

Use `bin/git-memoryV4` for pushes with the deployed MemoryV4 key. Commit and push after each coherent step.

## Live impact

- No live deploy.
- No MemoryV3 mutation.
- No cutover.
- Rollback: revert this branch/commit; no production data migration or live state change was performed.
