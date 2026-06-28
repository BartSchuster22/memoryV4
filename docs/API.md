# MemoryV4 API

## GET /health

Unauthenticated health check for local and container smoke tests. This is the only public P0-P1 HTTP endpoint.

Response:

```json
{
  "status": "ok",
  "service": "memoryv4-core",
  "version": "0.1.0-d0",
  "storage_backend": "sqlite"
}
```

Behavior:

- creates/opens the configured SQLite file path through `SqliteStore`
- initializes the P1 core tables when absent
- runs `PRAGMA quick_check`
- reports `degraded` only if the SQLite integrity check fails

## Internal Store port

The current core API for memory behavior is the Python `Store` Protocol, not an HTTP surface yet:

- `create_record(rec, actor=...) -> Record`
- `get_record(rid) -> Record | None`
- `transition(rid, lifecycle, actor=...) -> Record`
- `supersede(old_id, new, actor=...) -> Record`
- `lexical_rank(query, filter, k) -> list[str]`
- `vector_rank(qvec, filter, k) -> list[str]`
- `write_audit(event) -> None`
- `write_finding(finding) -> None`

HTTP CRUD/search routes are deferred to later phases and must enforce scoped keys when added.
