# Stage3 application knowledge logical erasure

## Owner boundary and request

`POST /applications/knowledge/scrub` is registered by MemoryV4 `create_app`.
All SQL remains in MemoryV4. Gateway ApplicationKnowledge.erase derives the binding
from server-owned registration/receipt and calls the owner API, never Core SQL.

Request JSON has exactly four required string fields:
- scope_path
- application_id: lowercase UUID
- receipt_id: lowercase UUID
- subject: one nonempty scope segment

No caller-provided IDs, selectors, force/global flag, query parameters, extra fields,
JSON duplicate keys, type coercion, or scope prefix matching for deletion. Body <=4096
bytes. Existing bearer/delegated actor authorization and trimmed X-MemoryV4-Reason
are mandatory. Mutation retries from Gateway carry a stable ak: hash key; the owner
uses the exact binding as erasure idempotency identity, not request body caching.

Scope is exactly ROOT/project:SHA256([frameworkId,projectId])/application:UUID/subject:SUBJECT.
ROOT must be non-global/non-public. The grant must itself be non-global/non-public,
contain the scope, and have memory.admin. Effective actor must be unify:<owner> and
match each record author. Every record must match exact scope, policy
verified-extract-v1, applicationId, receiptId, subject, and scope-hashed topic.
An unknown receipt binding is denied, not guessed to be an already-erased receipt.
There is no privileged delete bypass. Other owners, projects and subjects are never
selected for deletion; a conflicting binding/reference blocks rather than expands it.

## Transaction and exact coverage

One BEGIN IMMEDIATE transaction performs bounded discovery, dependency checking,
all deletions, predecessor detachment, and tombstone insertion. Failures roll back
DDL and data. SQLite foreign keys and lifecycle triggers remain enabled.

Covered logical live-store content:
- all receipt-owned records, including plans, evidence, candidates, canonical facts,
  results, archived/superseded records, JSON attrs and provenance/source references;
- FTS entries (including MATCH retrieval), checked for consistency before scrub;
- historical idempotency response copies and associated record/dependent audits;
- same-owner/scope relations with both endpoints in the deletion set;
- same-owner/scope review findings on deleted records, including resolution content;
- retrieval queries carrying dedicated, validated owner-written application_id,
  receipt_id and subject columns, with exact scope/actor and resolved references.

Migration 0006 adds nullable retrieval bindings; it does not infer ownership or
backfill legacy queries. SqliteStore.write_retrieval accepts an optional validated
ApplicationScrubRequest binding for trusted owner callers. JSON inside a query is
NOT binding authority. Current Gateway GET /records creates no retrieval event.
Legacy/unbound retrievals in overlapping scope (including ancestor searches),
foreign-owned copies, unknown references and external artifacts fail closed.
Independently bound retrievals and unrelated subjects/receipts are preserved.

Source URLs in evidence are provenance of public fetched material, NOT a claim
that Memory owns or deleted the source website. Linked artifacts have no remote
disposal proof contract: their presence blocks; deleting a URI is never considered
remote artifact erasure. Unmanaged exports/replicas remain outside this live-store
completion and require separate owner verification, not a privacy-complete label.

No customer content is retained in replay tombstones: only SHA256 binding, operation
actor/key and erased object-ID hashes. Cached responses, raw keys, request hashes,
object IDs and timestamps are removed. Every SqliteStore installs guards before
writes, including after restart. Old keys conflict; fresh-key receipt recreation and
stale cross-receipt writes referencing erased object IDs conflict atomically. Replay
rechecks live dependencies using erased-ID hashes instead of blindly returning success.
Never prune tombstones separately or run old writer code against a scrubbed database.
This does not promise prevention of deliberate new ingestion without any old binding
or reference: parent must fence/cancel producers during customer erasure.

## Dependency order (reference application coordination)

Delete dependent/reuse receipts first, then receipts owning the canonical knowledge.
If receipt B copied/referenced canonical knowledge owned by A, deleting A first gives
409 with complete=false and changes nothing. Deleting B removes only B and its owned
copies; the outbound reference resolves to A but gives no authority to delete A.
Then A can be deleted. Missing or cross-scope outbound references still block.

Corrections/refreshes use the same newest/dependent-first order. The one permitted
cross-receipt mutation is a verified reciprocal supersession backlink: when deleting
replacement B, Memory removes predecessor A.superseded_by and archives A, increments
its version, and removes the associated supersession audit/backlink cache. A's old
content, provenance and unrelated caches remain; A is NEVER restored to live. This
breaks the owner-generated two-way lineage without erasing another receipt's facts.
Any other incoming reference/reuse of B blocks deletion. A can subsequently be erased.
Arbitrary dependency cycles are not bypassed. Parent must retain deletion-pending if
no dependency-free receipt exists, or an external/unknown/unbound dependency remains.
A successful receipt scrub is NOT an assertion that all customer receipts are gone.

## Result and limits

Success JSON has exactly:
{"complete":true,"replayed":false,"records_scrubbed":N,
 "erasure":"logical-live-store","physical_erasure":false}

Repeated verified binding yields replayed=true and records_scrubbed=0 only after
rechecking dependencies. 403 ownership/unknown binding, 409 dependency/schema/scan
conflict, 413 body size or 422 strict request failure never means privacy completion.
Gateway returns true only on the exact success schema; errors, partial responses,
unavailable transport or malformed output return false, keeping parent deletion pending.

Scan bounds: 10,000 rows per scanned content table, 16 MiB decoded aggregate, JSON
nesting bounded at 32. Unknown tables/columns/triggers/views and inconsistent FTS deny.
This is deliberately bounded, not a general-purpose global deletion engine.

Backups, physical SQLite free pages/WAL, replicas and exports are NOT erased by this
API. Physical erasure is explicitly false. Preserve the backup-retention/expiry caveat;
operator-controlled expiry and restore/replay safeguards remain required. Parent owns
native evidence/Core/customer projection cleanup, producer fencing, integrated real
acceptance, and packaging. No deployment or integrated acceptance was performed here.

## Conservative correction default

When resolveCorrection is unset, one prior canonical fact from the corrected receipt
must match both approved source URL and byte-exact quote, with the prior excerpt
containing the quote. New native evidence must independently pass verifyEvidence;
its timestamp cannot predate the prior source. Ambiguous matches, changed claims,
missing verification, uncertainty, conflict, or different URLs quarantine. Explicit
trusted resolveCorrection can map a reviewed semantic correction, but evidence checks
still apply; explicit null is denial. No LLM/model boolean grants semantic authority.

## Verification scope

Owner tests use migrated temporary SQLite and real FTS5/FastAPI TestClient; no live DB.
Gateway knowledge tests use explicitly labelled mocked HTTP transport and synthetic
source text. Existing Memory adapter tests are also run. This is owner/API unit and
contract regression evidence, not parent real integrated acceptance.


## Exact development verification commands and results

All remote commands were executed through:
`ssh -i /home/herman/.ssh/alica_v1_deploy_ed25519 deploy@167.233.135.142`

From `/srv/alica-dsh-development/repos/MemoryV4`:

```
.application-scrub-test-support/py312/.venv/bin/python -m pytest -q
.application-scrub-test-support/py312/.venv/bin/python -m pytest tests/test_application_scrub.py --collect-only -q
.application-scrub-test-support/py312/.venv/bin/python -m ruff check app/application_lifecycle.py app/main.py app/storage.py app/migrations.py app/contracts.py tests/test_application_scrub.py tests/test_foundation.py
git diff --check
```

Result: 109 tests pass; 46 erasure test cases collected; targeted lint and diff check
pass. One third-party Starlette/AnyIO BlockingPortal deprecation warning remains.
The pre-existing Python 3.12 test environment was used, without installing packages.
All test SQLite databases use pytest temporary paths; no running service was used.

From `/srv/alica-dsh-development/repos/UNIFY`:

```
node_modules/.bin/vitest run apps/gateway/src/applications/knowledge.test.ts apps/gateway/src/memory-v4 --maxWorkers=1 --no-file-parallelism
node_modules/.bin/tsc -p apps/gateway/tsconfig.json --noEmit
git diff --check
```

Result: 33 tests pass across four files (23 knowledge, 5 adapter client, 4 adapter
routes, 1 route contract); full gateway no-emit typecheck and diff check pass.
Gateway HTTP is explicitly mocked; this does not claim real integrated acceptance.

Only permitted UNIFY sources were edited: applications/knowledge.ts,
applications/knowledge.test.ts, memory-v4/types.ts and memory-v4/types.test.ts.
Pre-existing unrelated edits were left in place. No deployment, container startup,
production access, credential inspection, commit/push or profile/config/harness change.
Parent owns real integrated acceptance, packaging and coordinated external erasure.

## QA4 empty-record completion (2026-09-10)

The owner request now supports strict boolean `allow_empty`, default false.
Core explicitly sets true for its validated deletion receipt; the owner never
interprets a 403 as success. For an empty binding, the entire dependency scan
and authorization still run before an atomic hash-only tombstone is committed.
Wrong ownership, permission/grant, unowned references and unresolved dependencies
remain denied. Fresh-key recreation is blocked even when no record ever existed.

Final owner suite: 117 tests pass; targeted Ruff and diff checks pass. QA4 real
installed cancellation-before-knowledge now deletes successfully. Full account
erasure, other-customer preservation and timestamp-fixture retention passed.
See UNIFY `dsh/rebuild/stage3/QA4-AS-BUILT.md` and `QA4-ACCEPTANCE.json`.
Earlier 109-test/unknown-empty-binding descriptions are historical QA3 results.
Physical/WAL/backup/replica/export erasure is still not claimed.
