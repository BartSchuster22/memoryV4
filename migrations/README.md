# Migrations

MemoryV4 migrations are additive and reversible. SQLite is the only implemented backend now; Postgres remains a documented future adapter slot.

Rules:
- every `up` has a matching non-empty `down`
- no destructive upgrade of MemoryV3/live data
- new P2 schema objects are introduced only by migrations `0006` through `0012`
- re-running migrations must be a no-op
- rollback drops only objects/columns introduced by the migration being rolled back

## Applied migration set

Baseline migrations:
- `0001_core_governed_objects` — entities, records, relations, artifacts, audit events, retrieval events
- `0002_record_author_and_write_policy` — governed record author/policy compatibility for already-bootstrapped DBs

P2 additive migrations:
- `0006_record_embeddings` — reserves the embeddings lane with `record_embeddings`
- `0007_promotion_log` — idempotent source-ref promotion decisions
- `0008_task_canvas` — task canvas and node-to-ref linkage tables
- `0009_registry_entity_types` — registry rows for persona/project/decision/runbook/incident/release/skill/tool/capability/workflow/agent_profile/task
- `0010_public_scope_policy` — reserved `public` scope policy; opt-in read and curator/admin-only write
- `0011_scope_paths` — `scope_path` defaults and indexes for governed tables
- `0012_health_findings` — idempotent health finding queue

There are intentionally no `0003`, `0004`, or `0005` migrations in this rebuild line.

## Validation

```bash
make qa10-gate-1
```

Gate 1 covers:
- fresh database migration through `0012`
- seeded P1 upgrade fixture preserving existing rows
- rerun/no-op safety
- non-empty rollback paths for P2 migrations
