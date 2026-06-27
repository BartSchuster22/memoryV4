# MemoryV4 API

## GET /health

Unauthenticated health check for local and container smoke tests.

Response:

```json
{"status":"ok","service":"memoryv4-core","version":"0.1.0-d0"}
```

All non-health API routes are deferred to later phases and must enforce scoped keys when added.
