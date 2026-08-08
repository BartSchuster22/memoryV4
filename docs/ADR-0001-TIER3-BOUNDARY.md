# ADR-0001: MemoryV4 Tier-3 architecture boundary

- **Status:** Accepted and locked for contract v1
- **Date:** 2026-08-08
- **Contract:** MemoryV4 `1.0.0`

## Decision

MemoryV4 is the framework-independent Tier-3 source of truth for durable, reusable knowledge. It runs as one private FastAPI/SQLite service container. UNIFY is the sole application gateway and owns application authentication, permission mapping, actor propagation, and operation journaling. MemoryV4 remains authoritative for content, scope enforcement, lifecycle, write policy, supersession, durable audit, and retrieval evidence.

UNIUI owns the human Memory & Knowledge interface. Alica, Herman, and future frameworks own their Tier-2 sessions/messages and use governed UNIFY memory tools. They never receive SQLite access or MemoryV4 administrative credentials.

```text
UNIUI ───────────────┐
                     ▼
Framework tools ──> UNIFY ──private network──> MemoryV4
                     │                         FastAPI + SQLite
                     └─ auth/permissions       Tier-3 source of truth
```

## Locked boundaries

1. **One object model:** entities, governed records, relations, artifacts, and review findings. Explorer folders are collection entities; files are document entities plus records; containment is a relation; uploads are artifacts. Recent/favorites/trash are projections, not parallel stores.
2. **Tier separation:** durable facts, decisions, documents, runbooks, project knowledge, relations, provenance, and lifecycle belong to Tier 3. Sessions, messages, transient task context, secrets, and framework runtime state do not.
3. **Governance:** roles are `canonical|active|evidence|exhaust`; lifecycles are `live|working|superseded|archived|expired`; write policies are `team_editable|author_only|admin_only|immutable`.
4. **Autonomous safety:** autonomous writers create `active/working` candidates. They cannot create or promote canonical truth. Promotion requires `memory.promote` and an attributable reason.
5. **Scope:** reads at a scope see ancestor-or-equal scopes and optionally `public`; siblings are denied. Mutations may target only equal-or-descendant scopes of the actor grant.
6. **Mutation safety:** actor identity comes from authentication, every mutation is audited and idempotent, versioned changes use `If-Match`, and lifecycle actions require a reason.
7. **Canonical history:** canonical revisions use supersession. Canonical content is never silently overwritten.
8. **Deployment:** one private MemoryV4 container, SQLite only in v1, no public application port, no separate MemoryV4 UI/login, and no framework/database coupling.
9. **Cutover:** MemoryV3 remains untouched until export/import parity, shadow reads, rollback preparation, and explicit human approval are complete.

## Ownership

| Concern | Owner |
|---|---|
| Application identity and permissions | UNIFY |
| Scope/lifecycle/write-policy enforcement | MemoryV4 |
| Durable content, relations, provenance and audit | MemoryV4 |
| Human interface | UNIUI |
| Sessions/messages/transient context | Framework |
| Backup/restore of Tier-3 state | MemoryV4 operations |

## Consequences

- MemoryV4 can evolve independently of any framework while preserving one governed knowledge graph.
- The browser and agents integrate only through UNIFY contracts.
- The core cannot be treated as a generic event dump, secret store, session database, vector-only database, or duplicate Kanban.
- Breaking contract changes require a new major contract. Additive fields and endpoints may remain in v1.
