# Migrations

MemoryV4 migrations will be additive and reversible. Phase D0 creates the migration directory only; schema migrations begin in later implementation phases.

Rules:
- every `up` must have a matching `down`
- no destructive upgrade of MemoryV3/live data during D0
- SQLite is the only implemented backend now
- Postgres remains a documented future adapter slot
