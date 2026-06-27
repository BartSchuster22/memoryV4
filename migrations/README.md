# Migrations

MemoryV4 migrations are additive and reversible for the objects introduced by each migration. C1 defines the initial SQLite governance spine through `0012`.

Rules:
- every `up` must have a matching `down`
- `down` migrations remove only objects introduced by their paired `up`
- no destructive upgrade of MemoryV3/live data during C1
- SQLite is the only implemented backend now
- Postgres remains a documented future adapter slot

C1 sequence:
- `0001_records`: governed records table with role, lifecycle, scope, source refs, author actor, write policy, and superseded pointer columns
- `0002_entities`: extracted/domain entities linked to records
- `0003_relations`: record-to-record typed relations
- `0004_artifacts`: record artifacts with source references
- `0005_audit_events`: audited mutation events
- `0006_retrieval_events`: retrieval lane/degraded events
- `0007_roles_lifecycles`: role and lifecycle reference tables
- `0008_scopes`: hierarchical scopes and record scope index
- `0009_source_refs`: normalized source references
- `0010_author_write_policy`: author actors and write policies
- `0011_supersessions`: old-to-new supersession integrity table
- `0012_retrieval_lanes`: keyword lexical terms and vector placeholder table
