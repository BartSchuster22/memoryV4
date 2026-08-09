# MemoryV4 Operations

The persistence and disaster-recovery contract is detailed in
[PERSISTENCE_RECOVERY.md](PERSISTENCE_RECOVERY.md).
The dedicated Gateway integration is defined in
[UNIFY_ADAPTER.md](UNIFY_ADAPTER.md).

## Local commands

- `python3 -m pip install -r requirements-dev.txt`: install test dependencies.
- `make run`: run Uvicorn locally.
- `make test`: run tests.
- `make lint`: run Ruff.
- `make qa`: run lint, tests, and the truthful QA10 scorecard.
- `make docker-build`: build the single application image.
- `make qa10`: write and print the current QA10 JSON status.

## Single-container runtime

MemoryV4 is one application container with SQLite at `MEMORYV4_DB_PATH`, defaulting
to `/data/memoryv4.sqlite3`. No database, queue, vector, worker, or UI sidecar is
part of this deployment.

```bash
docker build -t memoryv4-core:dev .
docker volume create memoryv4-data
docker volume create memoryv4-backups
docker run -d --name memoryv4-core \
  -p 8000:8000 \
  -e MEMORYV4_DB_PATH=/data/memoryv4.sqlite3 \
  -v memoryv4-data:/data \
  -v memoryv4-backups:/backups \
  memoryv4-core:dev
```

Verify startup:

```bash
curl -fsS http://127.0.0.1:8000/health
docker exec memoryv4-core python -m app.recovery checkpoint \
  --database /data/memoryv4.sqlite3
```

Healthy output reports service version `0.7.0-production-qa` and status `ok`.
Current migration history is `0001_foundation` through
`0005_review_audit_operations`.

`docker compose up --build` is local convenience. Production uses the immutable
`compose.production.yaml`; the long-running service mounts only `/data`. A hardened
networkless one-shot container mounts `/data` and `/backups` for scheduled backup.

## Runtime configuration

- `MEMORYV4_DB_PATH`: database path; default `/data/memoryv4.sqlite3`.
- `MEMORYV4_SQLITE_BUSY_TIMEOUT_MS`: 100–60000; default `5000`.
- `MEMORYV4_STARTUP_INTEGRITY_CHECK`: `quick` (default) or `full`.
- `MEMORYV4_API_KEYS`: structured bearer grants.
- `MEMORYV4_API_KEY`, `MEMORYV4_API_SCOPE`, `MEMORYV4_API_ACTOR`, and
  `MEMORYV4_API_PERMISSIONS`: single-grant fallback.

Production startup completes interrupted restore, acquires the database lease,
validates integrity/migration history, applies atomic migrations, and repairs only
derived FTS state. Corrupt or incompatible authoritative storage prevents startup.

## Online backup

Use the online backup API; never copy a live main file by itself.

```bash
stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker exec memoryv4-core python -m app.recovery backup \
  --database /data/memoryv4.sqlite3 \
  --output "/backups/memoryv4-$stamp.sqlite3"
docker exec memoryv4-core python -m app.recovery verify \
  --backup "/backups/memoryv4-$stamp.sqlite3"
```

The `.sqlite3` and adjacent `.manifest.json` are one backup unit. Copy both to
independent durable storage and verify them after transfer. Do not keep the only
backup on the same host or volume as live data.

## Offline restore

Restore refuses while the service owns its shared lease. Stop the service and use a
one-shot container with both volumes:

```bash
docker stop memoryv4-core
docker run --rm --entrypoint python \
  -e MEMORYV4_DB_PATH=/data/memoryv4.sqlite3 \
  -v memoryv4-data:/data \
  -v memoryv4-backups:/backups \
  memoryv4-core:dev -m app.recovery restore \
  --database /data/memoryv4.sqlite3 \
  --backup /backups/memoryv4-20260808T120000Z.sqlite3
docker start memoryv4-core
curl -fsS http://127.0.0.1:8000/health
```

Then verify representative governed reads/searches and object/audit counts. Restore
retains a verified pre-restore snapshot when the old database is readable; retain it
until acceptance. Startup safely completes a restore interrupted after its durable
marker was written.

If the app was launched with `--rm`, stopping removes that container; recreate it
with the same data/backup volumes instead of using `docker start`.

## Incident behavior

- `503 storage_unavailable`: stop mutations, check `/health`, preserve the full data
  volume, and inspect service logs. Responses intentionally omit SQLite details.
- Startup migration checksum/gap/future-version rejection: do not edit
  `schema_migrations`; deploy compatible code or restore a verified snapshot.
- Integrity or malformed-JSON failure: do not run speculative SQL repair. Preserve
  the data volume, verify available backups, and restore or escalate for forensics.
- FTS mismatch: startup rebuilds FTS from authoritative records transactionally.
- Restore lock refusal: a service process still owns the DB; stop all users and retry.

## Auth and scope operations

Only `GET /health` is public. All other routes require bearer authorization and
enforce action grants plus scope subtrees. `memory.review` closes findings,
`memory.audit.read` queries audit/retrieval events, and `memory.admin` queries usage.
Workers persist findings through the Store seam; this container has no worker
sidecar or public finding-create route.

For UNIFY, create a dedicated delegated-actor grant with the minimum required
permissions and a non-global tenant scope. Keep the token only in UNIFY's mounted
secret file. Contract negotiation uses `/capabilities` and requires
`1.0.0`; actor-attributed audit must be verified before production use.

## Source-control and live boundary

Database/WAL/SHM files, backups, manifests containing deployment metadata, local
caches, `.env`, and secrets must not be committed. The Container 4 deployment is
documented in [CONTAINER4.md](CONTAINER4.md). It performs no MemoryV3 cutover and
does not write to Hermes-generated memory files.
