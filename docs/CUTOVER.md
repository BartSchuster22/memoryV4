# MemoryV4 authority cutover and rollback

## Current state

MemoryV4 became the authoritative Tier-3 organizational memory service on
**2026-08-09 UTC** after explicit Phase 14 production-acceptance approval.

- UNIFY is the only governed application and framework gateway to MemoryV4.
- MemoryV4 has no published host port and is reachable only from `unify-core` on the
  internal `unify_memory-private` Docker network.
- UNIUI and the Alica/Herman framework tools use UNIFY, never MemoryV4 directly.
- The legacy MemoryV3 container is stopped with restart policy `no` so it cannot accept
  divergent writes.
- The MemoryV3 source volume, final online snapshot, adoption plan, and earlier exports
  are retained unchanged. Nothing was deleted.

The authority decision does not turn imported MemoryV3 records into canonical truth.
Their original role, lifecycle, policy, source references, and provenance remain intact.

## Acceptance prerequisites satisfied

1. Explicit user approval to continue and complete Phase 14.
2. Deterministic MemoryV3 adoption and zero-difference reconciliation.
3. Stable source logical digest before retirement.
4. Verified MemoryV4 pre-import and post-import backups.
5. Successful isolated restore of the post-import production backup.
6. Green MemoryV4 QA10 and green UNIFY QA10.
7. Live UNIUI inspection, governed-editor, and evidence-view verification.
8. Live Alica and Herman governed search through UNIFY.
9. Proof that framework runtimes have neither direct MemoryV4 network access nor a
   canonical-promotion tool.

The detailed evidence and immutable artifact identifiers are in
[PRODUCTION_ACCEPTANCE.md](PRODUCTION_ACCEPTANCE.md).

## Authority rollback

Use rollback only after an acceptance regression or declared MemoryV4 incident. Preserve
both systems before acting.

1. Record the incident and stop MemoryV4 mutations at UNIFY.
2. Verify the retained MemoryV3 source volume or final snapshot before restart.
3. Restore the legacy restart policy and start the retained container:

   ```bash
   docker update --restart=unless-stopped memory-v3
   docker start memory-v3
   docker inspect memory-v3 --format '{{.State.Status}}/{{.State.Health.Status}}'
   ```

4. Declare the temporary authority reversal and direct only approved legacy consumers to
   the restored MemoryV3 endpoint. Do not allow simultaneous writes to both systems.
5. If MemoryV4 data itself must be rolled back, stop MemoryV4 and restore the verified
   pre-import backup using `python -m app.recovery restore`; then restart it and rerun
   integrity, FTS, reconciliation, UNIFY, and QA10 checks.
6. Preserve failed-state databases, logs, audit events, and manifests for diagnosis.

A rollback never deletes MemoryV4 or MemoryV3 data. Re-cutover requires a new parity check,
backup, reconciliation, and explicit approval.
