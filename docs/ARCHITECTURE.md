# MemoryV4 Architecture

## Foundation slice

MemoryV4 core is a slim FastAPI service around a governed memory object model and a SQLite-only store adapter. UI/explorer surfaces, Kanban/orchestration, distillation workers, health workers, vector embeddings, Postgres, and cutover logic are outside this repository slice unless added in a later reviewed phase.

## Non-negotiables

1. One object model. Fact/Scene/Persona/Decision concepts are represented as governed records and relations, not parallel fact stores.
2. Governance is the moat. The foundation validates roles `canonical`, `active`, `evidence`, `exhaust`; lifecycles `live`, `working`, `superseded`, `archived`, `expired`; source/provenance fields; author metadata; audit events; retrieval events; and scoped keys.
3. No autonomous process writes canonical. This service exposes governed write paths only; future autonomous workers must write `working` or findings unless a separate verification path promotes records.
4. Slim boundary. Memory core only. UI/explorer and orchestration belong elsewhere.
5. Additive migrations. The current `0001_foundation` migration has a reversible down path and is idempotent on rerun.
6. Scope isolation is a safety property. Queries return records whose `scope_path` is an ancestor-or-equal of the requested scope. Sibling branches are excluded.

## Package layout

- `app/main.py`: FastAPI app factory, health route, authenticated record/search routes, scope-aware authz.
- `app/schemas.py`: Pydantic governed object model, role/lifecycle enums, scope helper.
- `app/migrations.py`: additive SQLite migration registry and rollback helper.
- `app/storage.py`: `Store` protocol and `SqliteStore` adapter. SQL, FTS5, WAL, and fallback search stay below this boundary.
- `tests/test_foundation.py`: validators, migrations, store CRUD, audit/retrieval events, auth, search, and negative sibling leak tests.
- `tests/test_health.py`: public health endpoint test.

## Storage and retrieval

SQLite is the only implemented backend. `SqliteStore` initializes the migration registry, creates the governed foundation tables, writes audit events inside governed write transactions, and records search/retrieval events. Search uses SQLite FTS5 and `bm25()` when available; minimal SQLite builds fall back to `LIKE` matching with the same scope filter applied.

## Scope model

`scope_path` is a slash-separated owning path such as:

```text
org:acme/project:psi/agent:alice/user:u123/session:s001
```

A request at that scope can see `global`, `org:acme`, `org:acme/project:psi`, `org:acme/project:psi/agent:alice`, and so on through its exact requested scope. It cannot see sibling tenants/projects/agents/users. The reserved `public` scope exists in helpers only and is included only when a caller opts in with `include_public=true`; no automatic public promotion is implemented.
