# UNIFY Adapter Contract

MemoryV4 remains the authoritative governed memory service. UNIFY is the named-user
authentication, RBAC, CSRF, and access gateway. The adapter implementation lives in
`/srv/unify/apps/gateway/src/memory-v4`; this document defines the MemoryV4 side of
the integration.

## Service grant

Provision one dedicated MemoryV4 grant for each UNIFY deployment:

```json
{
  "actor": "service:unify",
  "scope_path": "tenant:example",
  "permissions": [
    "memory.read",
    "memory.search",
    "memory.create",
    "memory.edit",
    "memory.promote",
    "memory.archive",
    "memory.review",
    "memory.audit.read",
    "memory.admin"
  ],
  "allow_actor_delegation": true
}
```

Narrow the permissions to the UNIFY roles actually enabled. `scope_path` must equal
or be narrower than UNIFY's `MEMORY_V4_SCOPE_PATH`. A tenant deployment must not use
a global grant.

Store the token only in UNIFY's secret file. Do not put it in browser configuration,
URLs, logs, audit metadata, Compose environment values, or repository files.

## Request invariants

UNIFY sends:

- service bearer authorization;
- `X-MemoryV4-Actor: unify:{stable UNIFY user UUID}`;
- `X-Request-ID` from the Gateway request;
- `Idempotency-Key` on every mutation;
- `If-Match` for optimistic concurrency where required; and
- `X-MemoryV4-Reason` for governed lifecycle/review actions.

MemoryV4 independently enforces the service grant permissions and subtree, delegated
actor validity, object policy, lifecycle transitions, idempotency, optimistic
concurrency, and audit/retrieval evidence.

Every response must retain
`X-MemoryV4-Contract-Version: 1.0.0`. UNIFY fails closed on a missing or
changed header, invalid JSON, malformed error body, oversized response, redirect, or
out-of-scope request.

## Ownership and failure semantics

- MemoryV4 is the sole memory writer and truth source.
- UNIFY does not cache memory as authoritative state, dual-write, migrate, or fall
  back to another memory system.
- UNIFY mutation admission is audited in Gateway; the authoritative memory operation
  is audited again in MemoryV4 under the delegated named actor.
- Upstream outage is a truthful error, never an empty successful result.
- Read and idempotent-write retries preserve the original idempotency key.

## Acceptance

The integration is accepted only when a live container test proves capability
negotiation, scoped create, read, search, replay, and actor-attributed audit through
the adapter, plus rejection of sibling scope and unsupported routes. MemoryV3 is not
mutated or cut over by this integration.
