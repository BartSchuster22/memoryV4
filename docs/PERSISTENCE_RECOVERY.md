# MemoryV4 Persistence and Recovery

## Guarantees and boundary

MemoryV4 uses one SQLite database as its authoritative runtime store. The supported
recovery unit is a verified snapshot produced by `python -m app.recovery backup`.
There is no Postgres adapter, replication, point-in-time recovery, or automatic
off-host scheduler in this phase. Operators must copy completed backup **and
manifest** files to independently durable storage.

The service does not cut over or mutate MemoryV3.

## Runtime durability

Every writable connection enforces:

- WAL journaling;
- `synchronous=FULL`;
- foreign keys;
- a bounded busy timeout (`5000` ms by default);
- `wal_autocheckpoint=1000`;
- untrusted schema mode.

The application process keeps a shared advisory lease on
`<database>.lock`. Online backups take another shared lease. Restore requires an
exclusive, non-blocking lease and therefore refuses to run while a service process
owns the database.

A mutation and its audit/idempotency data use the same SQLite transaction. Tests
inject an audit failure and prove that the object write also rolls back. Abrupt
process-exit testing proves committed WAL state is readable after restart.

## Startup sequence

Production lifespan startup performs these steps before Uvicorn reports readiness:

1. Complete a durable interrupted-restore marker, if present.
2. Acquire the shared service lease.
3. Open SQLite with hardened pragmas.
4. Run `quick_check` or configured `integrity_check` plus `foreign_key_check`.
5. Validate that applied migrations are a strict known prefix with matching source
   checksums; reject gaps, changed migration code, and unknown/future versions.
6. Apply each pending schema change and registry claim in one `BEGIN IMMEDIATE`
   transaction.
7. Repeat integrity and foreign-key validation.
8. Compare FTS rows with authoritative records and transactionally rebuild the
   derived index when they differ.

Corruption or incompatible migration history fails startup. MemoryV4 never guesses
how to repair authoritative object rows. At runtime, a SQLite database failure is
returned as a redacted `503 storage_unavailable`; `/health` becomes `degraded` and
does not expose filesystem paths or SQLite diagnostics.

`MEMORYV4_STARTUP_INTEGRITY_CHECK=quick` is the default. Use `full` where the
additional startup scan time is acceptable.

## Backup artifact

Create an online snapshot while the service is running:

```bash
python -m app.recovery backup \
  --database /data/memoryv4.sqlite3 \
  --output /backups/memoryv4-$(date -u +%Y%m%dT%H%M%SZ).sqlite3
```

The command:

1. validates/migrates the source under a shared lease;
2. uses SQLite's online backup API, including committed WAL state;
3. writes a private (`0600`) temporary snapshot;
4. runs full SQLite integrity and foreign-key checks;
5. validates migration history, persisted JSON, and FTS consistency;
6. fsyncs and atomically renames the snapshot;
7. writes and atomically installs `<snapshot>.manifest.json` containing format,
   UTC timestamp, SHA-256, size, and migration versions.

A raw copy of a live `.sqlite3` file is not a supported backup because committed
state may still be in `-wal`.

## Verification

```bash
python -m app.recovery verify \
  --backup /backups/memoryv4-20260808T120000Z.sqlite3
```

Verification requires the adjacent manifest and rejects:

- checksum or size mismatch;
- unsupported manifest format;
- missing, gapped, modified, or future migration history;
- SQLite integrity or foreign-key errors;
- malformed persisted JSON;
- inconsistent FTS state;
- WAL/SHM sidecars next to a backup artifact.

Run verification after copying an artifact off-host and during periodic restore
drills. A successful command prints one JSON object with `"status":"ok"`.

## Offline restore

Restore is intentionally not an HTTP API. Stop every MemoryV4 process that uses the
database, then run:

```bash
python -m app.recovery restore \
  --database /data/memoryv4.sqlite3 \
  --backup /backups/memoryv4-20260808T120000Z.sqlite3
```

Restore first verifies the source manifest. It stages and fsyncs a full copy in the
live database directory. When the existing database is readable, it also creates a
verified `pre-restore` backup and manifest. Under an exclusive lease it writes a
durable restore marker, moves the prior main/WAL/SHM set aside, atomically installs
the staged snapshot, fsyncs the directory, and removes the marker.

If the process or host stops during the swap, the next service startup consumes the
marker and either completes installation from the verified staged file or restores
the prior raw set when the staged file is unavailable. A normal restore reports the
path of the verified pre-restore snapshot. If the old database was too corrupt to
snapshot, its raw main/WAL/SHM set is retained for forensic recovery instead.

Start the service and verify `/health`, migration history, representative governed
reads/searches, and object/audit counts. Retain the pre-restore snapshot until the
recovery is accepted.

## Checkpoint

A manual WAL checkpoint is available but is not a substitute for backup:

```bash
python -m app.recovery checkpoint --database /data/memoryv4.sqlite3
```

The command reports SQLite's `(busy, log, checkpointed)` result as JSON.

## Recovery objectives

- **RPO:** the creation time of the newest verified off-host backup. MemoryV4 does
  not claim point-in-time recovery.
- **RTO:** operator- and dataset-dependent; measure it with scheduled restore drills.
- **Authoritative repair:** restore a verified snapshot or escalate for forensic
  repair. FTS is the only automatically rebuilt derived data in this phase.
