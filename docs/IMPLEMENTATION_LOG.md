# MemoryV4 Implementation Log

## D0 bootstrap

Scope: bootstrap a fresh MemoryV4 core repository without cutting over from any current memory system.

Baseline present at task start:
- `app/main.py` exposes `GET /health`.
- `Dockerfile` builds a single FastAPI core container.
- `docker-compose.yml`, `Makefile`, `pyproject.toml`, `requirements.txt`, and `tests/test_health.py` exist.
- Docs skeleton exists under `docs/`.

This run:
- cloned `git@github.com:BartSchuster22/memoryV4.git` into the project workspace
- verified push access through `/srv/memoryV4/bin/git-memoryV4`
- added board-local durability/autonomy artifacts under `artifacts/`, `docs/AUTONOMY.md`, and `ops/watchdogs/`
- recorded the `stream_events` P0/P2 recommendation in `docs/STREAM_EVENTS_AUDIT.md`
- tightened the D0 shell to be explicitly SQLite-only via `app/settings.py`, `app/storage.py`, `/data` container volume, and a health storage probe

Commit discipline:
- Use `/srv/memoryV4/bin/git-memoryV4` for pushes with the deployed MemoryV4 key.
- Commit and push after each coherent step.

Live impact:
- No live deploy.
- No MemoryV3 mutation.
- No cutover.

## Phase 5 — review, audit, and operational APIs

- Added reversible `0005_review_audit_operations` with typed finding-state integrity
  and audit/retrieval query indexes.
- Added scoped/filterable/cursor-paginated finding review and idempotent optimistic
  closure with reviewer attribution and audit.
- Added governed audit/retrieval event queries and admin-only usage aggregation.
- Added authorization, scope isolation, cursor, time-window, stale-version,
  idempotency, concurrency, capability, audit, and database-integrity coverage.

## Phase 6 — persistence and recovery hardening

- Hardened SQLite connections with WAL, FULL synchronization, foreign keys, bounded
  busy waits, startup integrity checks, and process advisory leases.
- Made each migration plus registry claim atomic and added applied-source checksums,
  strict-prefix validation, and future-version rejection.
- Added startup completion of interrupted restore and safe reconstruction of derived
  FTS state from authoritative records.
- Added manifested online backup, full structural/logical verification, checkpoint,
  exclusive offline restore, pre-restore snapshots, and atomic/fsynced file swaps.
- Added fault-injection, abrupt restart, corruption, tamper, lock, CLI, backup,
  restore, and interrupted-recovery coverage.
