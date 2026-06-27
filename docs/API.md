# MemoryV4 API

## GET /health

Unauthenticated health check for local and container smoke tests. This is the only public D0 endpoint.

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
- creates/opens the configured D0 SQLite file path
- runs `PRAGMA quick_check`
- reports `degraded` only if the SQLite probe fails its integrity check

All non-health API routes are deferred to later phases and must enforce scoped keys when added.
