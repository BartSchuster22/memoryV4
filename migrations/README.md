# Migrations

MemoryV4 migrations are additive, ordered, idempotent, and reversible.

- `0001_foundation`: core objects, audit/retrieval events, and FTS.
- `0002_governance`: record `write_policy`/`version` and actor-scoped idempotency ledger.

Rules:
- every `up` must have a matching `down`
- no destructive upgrade of MemoryV3/live data during D0
- SQLite is the only implemented backend now
- Postgres remains a documented future adapter slot
