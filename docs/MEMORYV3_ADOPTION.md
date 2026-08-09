# MemoryV3 data adoption

This runbook adopts governed MemoryV3 objects into MemoryV4 without deleting, modifying,
redirecting, or disabling MemoryV3. Adoption and authority cutover are separate decisions.
A successful import does **not** authorize a cutover.

## Safety invariants

- Open the MemoryV3 database with SQLite `mode=ro` and `PRAGMA query_only=ON`.
- Export with SQLite's online backup API; never copy a live WAL database directly.
- Keep the source snapshot and manifest immutable.
- Map every adopted object deterministically and checksum the canonical object payload.
- Run a full import and reconciliation against an isolated MemoryV4 database first.
- Take and verify a production MemoryV4 backup immediately before the import.
- Stop only MemoryV4 while applying the plan so the migration obtains its exclusive lock.
- Never stop, mutate, redirect, or delete MemoryV3 as part of this procedure.
- Fail closed on malformed JSON, missing content, unsupported lifecycle links, conflicting IDs,
  invalid checksums, or reconciliation differences.

## Mapping

| MemoryV3 | MemoryV4 mapping |
|---|---|
| Entity | Same composite `(entity_type, entity_id)`, name, attributes, timestamps |
| Record | Same record ID, content, role, lifecycle, policy, entity, tags, source references, and timestamps |
| Archived record | Archived with `previous_lifecycle=live` and a deterministic deletion timestamp |
| Relation | Deterministic `rel_m3_*` ID and entity-to-entity relation |
| Missing relation endpoint | Provenance-marked placeholder entity; disclosed in exceptions |
| Explorer folder | `collection` entity and deterministic `contains` relation |
| Explorer file | `document` entity and deterministic `represents` relation to its record |
| Artifact | Same ID, record/entity link, URI, SHA-256, metadata, and timestamps |
| Authentication/session state | Snapshot only; never imported into MemoryV4 authentication |
| Audit/retrieval/import/backup state | Preserved in the immutable source snapshot, not replayed as live operations |

All adopted objects use the approved `org:aquiero` scope. Provenance identifies MemoryV3,
the source object, mapping schema, and logical source digest. Existing unrelated MemoryV4
objects remain untouched.

## Commands

Run from the MemoryV4 repository with its virtual environment:

```bash
python -m app.adoption snapshot \
  --source /path/to/memory-v3.sqlite3 \
  --output /secure/evidence/memory-v3.snapshot.sqlite3

python -m app.adoption plan \
  --snapshot /secure/evidence/memory-v3.snapshot.sqlite3 \
  --output /secure/evidence/adoption-plan.json \
  --scope org:aquiero

python -m app.adoption apply \
  --plan /secure/evidence/adoption-plan.json \
  --target /secure/evidence/isolated-memory-v4.sqlite3 \
  --dry-run

python -m app.adoption apply \
  --plan /secure/evidence/adoption-plan.json \
  --target /secure/evidence/isolated-memory-v4.sqlite3

python -m app.adoption reconcile \
  --plan /secure/evidence/adoption-plan.json \
  --target /secure/evidence/isolated-memory-v4.sqlite3
```

A rerun is idempotent: byte-equivalent existing mapped rows are counted as `existing`.
An existing object ID with different governed content aborts the transaction.

## Production procedure

1. Recheck MemoryV3 source counts and logical digest against the approved plan.
2. Verify MemoryV3 is healthy and remains active.
3. Run the scheduled MemoryV4 backup service and verify its manifest.
4. Retain the pre-import backup name and checksum as rollback evidence.
5. Stop MemoryV4, but leave UNIFY and MemoryV3 untouched.
6. Mount the adoption plan read-only into a one-shot MemoryV4 image.
7. Apply the plan against `/data/memoryv4.sqlite3`.
8. Start MemoryV4 and wait for healthy status.
9. Reconcile every planned entity, record, relation, artifact, and FTS record.
10. Run MemoryV4 and UNIFY production QA.
11. Confirm MemoryV3's database digest, container status, and routing are unchanged.

## Rollback

If apply, startup, reconciliation, or QA fails:

1. Stop MemoryV4.
2. Restore the recorded pre-import MemoryV4 backup into the data volume with
   `python -m app.recovery restore`.
3. Start MemoryV4 and verify health, integrity, migrations, FTS, and UNIFY QA.
4. Keep the failed plan, output, source snapshot, and reconciliation report as evidence.
5. Do not alter MemoryV3.

No authority cutover, MemoryV3 shutdown, route change, or source deletion may occur without a
separate explicit user approval after shadow comparison and reconciliation are complete.

## Approved production outcome

That separate approval was provided for Phase 14 on 2026-08-09 after reconciliation,
QA, backup, and restore acceptance passed. MemoryV4 is now authoritative. MemoryV3 was
stopped with restart disabled to prevent divergent writes, but its source volume and final
verified snapshot were preserved. No source data was modified or deleted. See
[PRODUCTION_ACCEPTANCE.md](PRODUCTION_ACCEPTANCE.md) and [CUTOVER.md](CUTOVER.md).
