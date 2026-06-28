# MemoryV4 Cutover Plan

No cutover is performed in this project phase.

This repository is building a new MemoryV4 core from scratch. It must not mutate MemoryV3, legacy Shared Memory Vault material, current Hermes profile memories, or any production memory database while P0/P1 documentation and core scaffolding are being built.

## Current phase boundary

Allowed now:

- Create MemoryV4 documentation.
- Build and test a standalone single-container core.
- Use local fixtures and disposable SQLite databases.
- Import anonymized/captured fixtures only when explicitly part of a test.
- Document future cutover requirements.

Forbidden now:

- Switch live agents to MemoryV4.
- Write to current MemoryV3 production data.
- Run dual-write from live agents.
- Promote MemoryV4-generated records into any current canonical memory store.
- Modify legacy memory systems to make this project pass.
- Enable public/cross-tenant memory sharing.

## Future cutover stages

Cutover is deferred until MemoryV4 passes QA10 and receives explicit operator approval. The future plan is staged so each step is observable and reversible.

### Stage 0: readiness gate

Prerequisites:

- All QA10 gates pass and emit a scorecard.
- Core container runs from clean checkout.
- Backup/restore round trip is proven with checksums.
- Scope isolation tests prove no sibling tenant leakage.
- Security gate proves every non-health route enforces scoped keys.
- Migration tests pass for fresh and upgrade-from-real-V3 fixtures.
- Rollback runbook has been tested on a non-production copy.
- Operator approves moving from build phase to dual-run phase.

Exit criteria:

- Signed/recorded decision naming the verification actor and rollback owner.

### Stage 1: read-only shadow

MemoryV4 receives copied or replayed inputs, not live write authority.

Rules:

- Source systems remain canonical.
- MemoryV4 output is marked shadow/evidence only.
- No production agent reads MemoryV4 for decisions.
- No MemoryV4 record is promoted into current live memory.

Evidence:

- replay counts;
- parse/import errors;
- scope-isolation report;
- retrieval parity report;
- governance invariant report.

Rollback:

- Stop replay.
- Delete disposable MemoryV4 shadow database if needed.
- No production state changed.

### Stage 2: dual-run parity

MemoryV4 and the existing memory system process the same representative workload, but the existing system remains source of truth.

Rules:

- Existing memory remains canonical.
- MemoryV4 records remain shadow, working, or evidence unless explicitly reviewed.
- Any mismatch is triaged before proceeding.
- Autonomous MemoryV4 workers still cannot write canonical.

Required parity checks:

- governed record counts by role/lifecycle;
- source-ref preservation;
- supersession lineage;
- retrieval results for canonical test queries;
- tenant scope boundaries;
- backup/restore after replay;
- no unintended public records.

Rollback:

- Stop dual-run feed.
- Keep evidence database for analysis or archive it.
- Do not alter source system.

### Stage 3: limited read pilot

Selected non-critical consumers may read MemoryV4 under a narrow scope after explicit approval.

Rules:

- Pilot keys are tightly scoped.
- Public reads remain disabled unless separately approved.
- Existing memory remains fallback.
- Retrieval events are reviewed for scope and relevance.
- Canonical promotions require verification actor approval.

Rollback:

- Revoke pilot keys.
- Disable MemoryV4 reads in the consumer.
- Restore consumers to existing memory source.

### Stage 4: controlled write pilot

Selected actors may write governed records to MemoryV4.

Rules:

- Autonomous actors may write only `working` records or findings.
- Human/privileged verification actor handles promotion.
- Backups run before and after migration windows.
- Scope and audit dashboards are checked after each write batch.

Rollback:

- Stop write traffic.
- Revoke write keys.
- Restore from pre-pilot backup if needed.
- Preserve audit trail for incident review.

### Stage 5: source-of-truth switch

Only after successful pilot and explicit operator approval can MemoryV4 become source of truth for a named scope.

Rules:

- Scope-by-scope switch, not fleet-wide.
- Pre-switch backup is mandatory.
- Rollback owner is on call.
- Existing memory source remains available for rollback window.
- QA10 scorecard and dual-run parity evidence are linked in the approval record.

Rollback:

- Route consumers back to prior memory source.
- Restore MemoryV4 from pre-switch backup if writes must be discarded.
- Record supersession/correction actions if writes are retained but adjusted.

## Data import rules

Any future import from MemoryV3 must preserve governance:

- roles and lifecycles map explicitly;
- source refs are preserved;
- supersession lineage is preserved;
- imported records receive scope paths;
- imports are idempotent;
- import failures do not partially promote canonical records;
- public scope is never inferred from cross-tenant content.

## Human approval checkpoints

Explicit approval is required for:

- entering dual-run with live-derived data;
- allowing any production consumer to read MemoryV4;
- allowing any production actor to write to MemoryV4;
- promoting MemoryV4 as source of truth for a scope;
- enabling public-scope reads or writes;
- granting `promote`, `supersede`, `restore`, or `admin_public` capabilities.

## Rollback evidence

Every future cutover stage must produce:

- backup artifact id and checksum;
- git commit/image digest;
- migration version;
- scope(s) affected;
- actor approving the step;
- verification commands and results;
- rollback command path.

## Public scope deferral

The `public` scope is reserved for future curated cross-tenant memory. It is not part of this cutover plan. Enabling it requires a separate threat model, tenant opt-in design, provenance policy, and review workflow.
