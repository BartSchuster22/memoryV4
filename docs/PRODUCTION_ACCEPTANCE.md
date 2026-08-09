# Phase 14 production acceptance

- **Decision:** accepted
- **Authority effective:** 2026-08-09 UTC
- **Core revision:** `3ce1c24623890aba2b67311610d629370439f2be`
- **MemoryV4 image:** `sha256:977cf9b5ddca7b32f55e079eef9d7631b4fc65e9396d11cce245927172c0b4e1`
- **UNIFY release:** `phase-18.3-8367fd0`

Production evidence containing databases or deployment metadata is retained outside Git
under `/srv/memory-v4-phase13-evidence` and `/srv/memory-v4-phase14-evidence`.

## Acceptance matrix

| Gate | Production evidence | Result |
|---|---|---|
| MemoryV4 is authoritative Tier 3 | Legacy MemoryV3 stopped with restart disabled after a final stable source snapshot; source volume retained | PASS |
| UNIFY is the sole gateway | MemoryV4 and `unify-core` are the only members of `unify_memory-private`; MemoryV4 publishes no host port | PASS |
| UNIUI inspection and editing | Headless Chromium authenticated as a named administrator, loaded MemoryV4, opened governed editing and evidence, and observed no console/page/request failures | PASS |
| Framework tools | Alica and Herman searched through their embedded `unify-memory` plugin; neither framework joins the MemoryV4 network | PASS |
| No agent self-promotion | Runtime tool inventory exposes only search, context, get, remember, and update; candidate inputs cannot set role/lifecycle; no promote operation exists | PASS |
| Governance | Scope isolation, provenance, lifecycle, policy, idempotency, concurrency, audit success, and audit denial suites passed | PASS |
| Persistence | Post-import backup completed and an isolated restore passed integrity, FTS, migrations, adopted-record, and count checks | PASS |
| Full QA10 | MemoryV4 QA10 10/10 and UNIFY production QA10 passed | PASS |
| Operations and rollback | Deployment, adoption, backup/restore, authority cutover, and rollback runbooks are current | PASS |

## Data and restore evidence

Final MemoryV3 source snapshot:

```text
quick_check=ok
source_read_only=true
source_unchanged=true
logical_sha256=84aea9e488970575e7575dd7c91bdf762cfa6575a502c441287619f94c4ce960
entities=378
records=5301
relations=445
explorer_items=5889
artifacts=0
```

The logical digest equals the approved adoption-plan digest. The final snapshot is at:

```text
/srv/memory-v4-phase14-evidence/phase14-20260809T075750Z/
```

Post-import production backup:

```text
artifact=/backups/container4-20260809T073124Z.sqlite3
image=sha256:977cf9b5ddca7b32f55e079eef9d7631b4fc65e9396d11cce245927172c0b4e1
service_exit=0/SUCCESS
retention=14
```

Isolated restore verification:

```text
quick_check=ok
entities=6278
records=5302
relations=10736
fts=5302
adopted_record=present
production_backup_isolated_restore=PASS
```

The additional record beyond the 5,301 adopted MemoryV3 records is the governed Phase 10
framework release-evidence record. The restored FTS count exactly equals the record count.

## Application and framework evidence

Live browser result:

```json
{
  "status": "PASS",
  "authenticated": true,
  "source": "memoryv4-core / contract 1.0.0",
  "governedEditing": true,
  "createRecordVisible": true,
  "evidenceVisible": true,
  "consoleErrors": 0,
  "pageErrors": 0,
  "failedResponses": 0
}
```

A production screenshot was retained at
`/tmp/phase14-production-acceptance.png` on the acceptance host.

Both framework runtimes returned successful governed search responses. Their runtime tool
inventories contain exactly:

```text
unify_memory_search
unify_memory_context
unify_memory_get
unify_memory_remember
unify_memory_update
```

Canonical creation and promotion are absent. Framework registrations include exact
`memory:read` and `memory:write` scopes, while MemoryV4 receives delegated actor identities.

## QA evidence

MemoryV4 repository QA:

```text
Ruff: PASS
Pytest: 61 passed
QA10: 10/10 passed
```

The QA10 groups cover authentication/permissions, scope isolation, write policy and
promotion, lifecycle/supersession, pagination/filter/search, concurrency/idempotency,
audit success/denial, content limits, migrations/rollback, and backup/restore/restart.

UNIFY repository QA passed lint, typecheck, tests, builds, reproducible contracts,
standalone boundaries, deployment hardening, backup retention, identity capacity, canary,
and formatting. The live production QA10 registration/read/concurrency/audit/logout flow
also passed.

## Acceptance defect corrected

The production QA10 registration replay was found to replace framework scopes with only
control scopes, temporarily removing `memory:read` and `memory:write`. The acceptance run
failed rather than masking this. The runner now preserves `control:secrets`, `memory:read`,
and `memory:write`, and a static release assertion prevents regression. The corrected
runner was installed, QA10 reran successfully, and live Alica/Herman searches passed.

## Authority and rollback

MemoryV3 was stopped only after the final source snapshot and all shadow/import checks.
Its restart policy is `no`, preventing divergence. Its Docker volume and immutable exports
remain preserved. Authority rollback is documented in [CUTOVER.md](CUTOVER.md); data
rollback is documented in [OPERATIONS.md](OPERATIONS.md) and
[PERSISTENCE_RECOVERY.md](PERSISTENCE_RECOVERY.md).
