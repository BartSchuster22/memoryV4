# MemoryV4 Cutover Runbook

No cutover is performed by this foundation slice.

This repository currently implements a local SQLite-backed governed core foundation only. It does not modify `/srv/memory-v3`, `memoryv3.aquiero.com`, Hermes `MEMORY.md`/`USER.md` generation, live MemoryV3 deployment configuration, or any production memory database.

Future cutover prerequisites must include:

1. Explicit human approval.
2. Backup and restore evidence for the current live system and MemoryV4 target.
3. Dual-run parity evidence against representative MemoryV3 workloads.
4. A green QA10 scorecard for implemented gates.
5. Scope-isolation and auth evidence for PSI/Alice/Herman tenant boundaries.
6. Rollback instructions tested against a staging copy.
7. A statement of which autonomous workers, if any, are enabled and proof that they cannot write canonical/live records without verification.

Until those prerequisites exist, MemoryV4 remains non-cutover foundation work only.
