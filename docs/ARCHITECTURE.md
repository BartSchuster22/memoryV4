# MemoryV4 Architecture

MemoryV4 is a lightweight, robust, single-container memory core. It rebuilds the useful MemoryV3 governance model into a smaller service while absorbing runtime-efficiency ideas from TencentDB Agent Memory without creating parallel memory stores. This repository is the memory core only: no Kanban orchestration, no explorer UI, no watchdog UI, and no cutover automation live here.

This project phase creates the new core from scratch and does not cut over from, mutate, or depend on current MemoryV3/MemoryV4 production systems.

## Target deployable

The target deployable is `memoryv4-core`:

- FastAPI service exposing core memory APIs.
- SQLite database stored on a mounted volume.
- Store port plus SQLite adapter.
- Governance, retrieval, audit, distillation, health-finding, and backup/restore logic.
- Optional future modules behind explicit flags, not active by default.

Out of scope for this repository:

- Web/explorer UI.
- Kanban, board repair, watchdog, or orchestration systems.
- Live MemoryV3 cutover tooling.
- Postgres implementation.
- Cross-tenant marketplace/public-memory workflows beyond reserving the governance boundary.

## Architecture sketch

```text
Clients / agents
   |
   | scoped API key + requested scope
   v
FastAPI core
   |
   | validates auth, scope, and write policy
   v
Core services
   |-- Governance service: roles, lifecycles, supersession, audit
   |-- Retrieval service: lexical/vector lanes, fusion, governance prior, scope filter
   |-- Distillation worker: session-derived working candidates only
   |-- Health worker: findings for contradiction, compaction, decay, orphan checks
   |-- Backup/restore service: SQLite-safe snapshots and verification
   v
Store port (engine-agnostic Protocol)
   v
SqliteStore (only implemented adapter now)
   |-- records/entities/relations/artifacts
   |-- audit_events/retrieval_events
   |-- FTS and future vector tables
   |-- promotion_log/health_findings
   v
SQLite database on persistent volume
```

## One object model

MemoryV4 uses one governed record model. Tencent-style cognitive layers are represented as records, relations, artifacts, and provenance, not as separate fact/scene/persona tables.

| Cognitive layer | MemoryV4 representation | Expected role/lifecycle | Provenance |
| --- | --- | --- | --- |
| Conversation exhaust | Imported source refs or exhaust records | `exhaust/live` | raw session reference |
| Fact / atom | `record` | `active/working` then verified to `active/live` | source refs and audit event |
| Scene / scenario | `record` plus `relation(derived_from)` to facts | `active/live` or `canonical/live` when verified | fact and session refs |
| Persona / user profile | `record(entity_type=person_or_user_profile)` | `canonical/live` | relations to scenes/facts |
| Decision | `record(entity_type=decision)` with rationale and alternatives | `canonical/live` or `active/live` | source refs and review actor |
| Task canvas / symbolic offload | `artifact` plus task record and node refs | `active/working` or `active/live` | node id to artifact/source ref |

Non-negotiable invariant: there are no independent `facts`, `scenes`, or `personas` stores that bypass governance.

## Governance model

Governance is the core product boundary. Every record is governed by:

- `role`: `canonical`, `active`, `evidence`, or `exhaust`.
- `lifecycle`: `live`, `working`, `superseded`, `archived`, or `expired`.
- `scope_path`: owning tenant/project/agent/user/session path.
- `write_policy`: explicit permissions for who/what can create, transition, supersede, or promote.
- `author_actor`: the actor that proposed or wrote the record.
- `source_refs`: durable references to evidence, sessions, or artifacts.
- `audit_events`: append-only governance and mutation history.
- `retrieval_events`: query/audit trail for retrieval behavior.

Supersession preserves lineage. A superseded record remains addressable and auditable; it is not destructively overwritten.

## Autonomous write invariant

No autonomous process may write `canonical` records or promote anything to `canonical/live`.

Allowed autonomous writes:

- Distillation may propose `working` records with source refs.
- Health worker may write findings for contradiction, compaction, decay, and orphan checks.
- Maintenance jobs may write audit/retrieval telemetry and inert evidence.

Forbidden autonomous writes:

- Direct `canonical` creation.
- Direct `working` to `live/canonical` promotion.
- Direct contradiction resolution by overwriting one canonical record with another.
- Direct writes to reserved `public` scope.

Promotion to `canonical/live` requires an explicit verification actor governed by scoped auth and write policy.

## Store port

Core code depends on a narrow Store port. SQL, FTS, SQLite pragmas, vec0 details, transaction handling, and file paths stay inside `SqliteStore`.

Initial Store responsibilities:

- `create_record(record, actor)`
- `get_record(record_id)`
- `transition(record_id, lifecycle, actor)`
- `supersede(old_id, new_record, actor)`
- `lexical_rank(query, filter, k)` returning ranked record ids
- `vector_rank(query_vector, filter, k)` returning ranked record ids when enabled
- `backfill_embeddings(provider, filter=None)` for provider-driven embedding backfill
- `write_audit(event)`
- `write_finding(finding)`
- backup/restore helpers owned by the SQLite adapter or operations layer

Retrieval fusion belongs in core, not in the adapter. This keeps future storage adapters honest while avoiding dual-backend complexity now.

## SQLite-only now

SQLite is the only backend implemented in this phase.

- No Postgres code is shipped now.
- No runtime backend selector is required now.
- Postgres remains a documented future adapter slot behind the Store port.
- A Postgres adapter should only be built if a real tenant workload exceeds the practical SQLite one-file-per-slot operating model.

## Retrieval shape

Target retrieval uses:

1. Lexical lane: SQLite adapter BM25 ranking over governed record text.
2. Vector lane: persisted `record_embeddings` rows populated through the provider abstraction.
3. Fusion: reciprocal-rank fusion in core with `k=60`.
4. Governance prior: bounded additive nudge for live governed records; it must not swamp lane relevance.
5. Scope filter: identical prefix-isolated scope enforcement in every lane (`scope = prefix` or `scope` below `prefix/`).
6. Fallback: if embeddings are unavailable, retrieval returns lexical results instead of failing closed.
7. Audit: retrieval event logged with degraded/fallback state where applicable.

Candidate gathering must happen before final limiting. This avoids the known bug where newest rows are limited before governance or relevance sorting.

## Scope model

Every record and entity has exactly one owning `scope_path`:

```text
global
org:<org>
org:<org>/project:<project>
org:<org>/project:<project>/agent:<agent>
org:<org>/project:<project>/agent:<agent>/user:<user>
org:<org>/project:<project>/agent:<agent>/user:<user>/session:<session>
public
```

Retrieval for a requested scope prefix may return only:

- records exactly at that prefix; and
- records below that prefix subtree.

It must never return sibling tenant/project/agent/user/session records. The API auth layer must bind keys to an allowed scope subtree and reject requests outside that grant.

`public` is reserved-only in this project phase. Nothing auto-promotes to `public`; public writes require explicit curator/admin authorization and a separate threat model.

## Additive migration invariant

All migrations are additive and reversible.

- Existing governed tables are extended, not destructively rewritten.
- Every `up` has a matching `down` for objects introduced by that migration.
- Re-running migrations is idempotent.
- Upgrade-from-real-V3 and fresh database paths are both tested before any future cutover.

## Slim core invariant

The memory core remains small and auditable. PRs that add UI, explorer, Kanban, workflow repair, or orchestration code to core are rejected. Integrations may call MemoryV4 through API contracts, but they do not live inside this repository.
