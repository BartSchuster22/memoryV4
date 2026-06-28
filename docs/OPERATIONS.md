# MemoryV4 Operations

MemoryV4 operations are intentionally simple: one container, one SQLite database file on a persistent volume, explicit backup/restore, and no live cutover in this project phase.

## Operating principles

- Run the core as a single container.
- Persist SQLite on a mounted volume.
- Keep the core free of UI, explorer, Kanban, watchdog, and orchestration processes.
- Treat SQLite as the only implemented backend now.
- Require scoped API keys for every route except `/health`.
- Do not mutate MemoryV3 or any current production memory system during this build phase.

## Local development commands

```bash
python3 -m pip install -r requirements-dev.txt
make lint
make test
make qa
make qa10
make run
make docker-build
```

`make qa10` should eventually run the ten acceptance gates described in `docs/QA10.md` and emit a JSON scorecard. Until gates are implemented, placeholders must report pending/skipped honestly rather than claiming success.

## Container model

Target runtime:

```text
memoryv4-core container
  /app      application code
  /data     mounted persistent volume
  8000/tcp  FastAPI service
```

Default persisted database path:

```text
/data/memoryv4.sqlite3
```

The container should start the FastAPI app, initialize/open SQLite, enable required pragmas, and serve `/health`. It must not launch background UI, board automation, MemoryV3 cutover jobs, or separate orchestration loops.

## Environment configuration

Required/expected variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MEMORYV4_DB_PATH` | `/data/memoryv4.sqlite3` | SQLite database path. |
| `MEMORYV4_HOST` | `0.0.0.0` in container | Bind host. |
| `MEMORYV4_PORT` | `8000` | Bind port. |
| `MEMORYV4_API_KEYS_FILE` | unset | Path to scoped API key config for non-health routes. |
| `MEMORYV4_LOG_LEVEL` | `INFO` | Runtime log level. |
| `MEMORYV4_BACKUP_DIR` | `/data/backups` | Backup output directory. |
| `MEMORYV4_PUBLIC_READ_DEFAULT` | `false` | Public scope read opt-in default. Must remain false unless deliberately enabled. |
| `MEMORYV4_EMBEDDINGS_ENABLED` | `false` | Enables future vector lane after implementation. |
| `MEMORYV4_EMBEDDING_BASE_URL` | unset | OpenAI-compatible embedding endpoint when vector lane is enabled. |
| `MEMORYV4_EMBEDDING_MODEL` | unset | Embedding model name. |
| `MEMORYV4_EMBEDDING_SEND_DIMENSIONS` | `true` | Set false for providers that reject dimensions. |

No Postgres DSN or backend selector is part of the current operating contract. Postgres is deferred behind the Store port.

## SQLite runtime settings

The SQLite adapter should own database pragmas and transactions. Target settings:

- WAL mode for safer concurrent reads/writes.
- Foreign keys enabled.
- Busy timeout configured for normal concurrent access.
- Transactions wrap records, FTS/vector rows, audit events, and findings that must stay consistent.
- Integrity check available through health/readiness or operator command.

## Health and readiness

`GET /health` is unauthenticated and safe for container health checks.

It may verify:

- process is alive;
- database file can be opened/initialized;
- required base tables exist;
- `PRAGMA quick_check` passes.

It must not expose secrets, API key status, tenant names, sibling scope information, or raw record content.

All non-health routes require scoped keys.

## Backup

Backups are SQLite-first and must be safe while the service is running.

Minimum backup procedure:

1. Resolve `MEMORYV4_DB_PATH`.
2. Use SQLite backup API or equivalent safe copy method, not a naive copy during writes.
3. Include companion metadata:
   - service version;
   - schema migration version;
   - source database path;
   - created timestamp;
   - checksum;
   - whether embeddings/vector tables are present.
4. Store the artifact under `MEMORYV4_BACKUP_DIR` or an operator-supplied path.
5. Verify the backup by opening it read-only and running an integrity check.

Backups must not be written into repo source directories by default.

## Restore

Restore is explicit and overwrite-guarded.

Minimum restore procedure:

1. Stop the service or put it in maintenance mode.
2. Verify the backup checksum and metadata.
3. Open the backup separately and run integrity checks.
4. Refuse to overwrite an existing live database unless `--force` or equivalent operator confirmation is present.
5. Move the old database aside before replacement.
6. Restore the database file and required sidecar metadata.
7. Start the service and run health, migration, retrieval, and scope-isolation smoke checks.

Restore must include embeddings/vector tables once those exist. Gate 10 in `docs/QA10.md` covers backup/restore round-trip durability.

## Migration operations

Migrations are additive and reversible:

- Every migration has `up` and `down` paths.
- Re-running `up` is a no-op.
- Fresh database and upgrade-from-real-V3 fixtures are both tested.
- Destructive schema changes are forbidden in this phase.
- Rollback drops only objects introduced by the migration being rolled back.

## Key management

Operators configure scoped keys outside source control. API key config must not be committed.

Each key should include:

- actor id;
- allowed scope root;
- capabilities;
- creation/rotation metadata;
- optional expiry.

Capabilities should be narrow. Autonomous actors get `write_working` or finding-write capabilities only. They do not get `promote`, `admin_public`, or unrestricted write permissions.

## Logs and audit

Application logs are operational diagnostics. Governance state lives in database audit tables.

Do log:

- startup configuration excluding secrets;
- database path class, not sensitive full paths when inappropriate;
- migration versions;
- degraded retrieval fallback;
- backup/restore start/end and checksum ids;
- request ids and status codes.

Do not log:

- API keys;
- raw private memory content by default;
- sibling tenant existence on denied requests;
- unredacted source material containing PII.

## Git push from chatboard

This workspace uses the repository-local wrapper for authenticated pushes:

```bash
bin/git-memoryV4 push -u origin HEAD
```

The wrapper honors `MEMORYV4_GIT_DEPLOY_KEY` first, then falls back to the chatboard profile deploy key documented in `docs/ops/github-deploy-key.md`.

## Rollback

During this project phase, rollback is a git branch revert because no live cutover or production memory mutation occurs.

Future runtime rollback requires:

- pre-change backup;
- migration down path when applicable;
- operator approval;
- post-rollback health and scope-isolation verification;
- audit note identifying the actor and reason.

## Operational boundary

This repository does not operate MemoryV3, legacy Shared Memory Vault, MemoryV3 cutover jobs, or current Hermes memory routing. Any future cutover must follow `docs/CUTOVER.md` and remain disabled until explicitly approved.
