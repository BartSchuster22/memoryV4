# MemoryV4 Migrations

SQLite migrations live in `app/migrations.py` and are applied in ordered additive
steps. Current history is `0001_foundation` through
`0005_review_audit_operations`.

For each pending migration, the schema change and `schema_migrations` claim commit in
one `BEGIN IMMEDIATE` transaction. The same atomic rule applies to rollback and claim
removal. Failure injection verifies that neither partial schema nor false claims
survive.

The registry stores a SHA-256 checksum of immutable up/down migration source. Startup
requires applied versions to be an exact prefix of the code's known history and
fails closed on checksum drift, missing predecessors, or unknown/future versions.
Legacy rows without checksums are adopted once by the first hardened startup.

Migrations are idempotent at the registry level. Do not edit an applied migration or
manually alter `schema_migrations`; add a new ordered migration instead. Back up with
`python -m app.recovery backup` before operator-initiated rollback. See
[PERSISTENCE_RECOVERY.md](PERSISTENCE_RECOVERY.md) for verification and restore.
