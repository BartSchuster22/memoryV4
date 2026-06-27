# stream_events Audit

## Scope

Task requirement: audit whether `stream_events` exists or has consumers in the fresh MemoryV4 baseline or V3 reference, then record a fold/drop recommendation for P0/P2.

## Fresh MemoryV4 baseline

Result: no `stream_events` symbol exists in the fresh baseline checked out for this task.

Command used:

```bash
search_files pattern=stream_events path=/home/herman/.hermes/state/kanban/workspaces/memoryv4-next/memoryV4
```

## V3 reference

The source rebuilding plan identifies `stream_events` as an explicit audit target and notes that the prior store is pre-production scale. During this run, direct access to `BartSchuster22/memoryV3` was not available from the worker environment with the currently available credentials; both SSH and HTTPS probes failed before cloning.

Commands attempted:

```bash
git ls-remote git@github.com:BartSchuster22/memoryV3.git HEAD
git ls-remote https://github.com/BartSchuster22/memoryV3.git HEAD
```

Observed failures:
- SSH: `Permission denied (publickey)`.
- HTTPS: `could not read Username for 'https://github.com': No such device or address`.

## Recommendation

P0: do not introduce `stream_events` into the new MemoryV4 baseline. Keep the core API surface limited to `/health` until the governed Store/schema is implemented.

P2: if V3 access becomes available and consumers are found, fold event-like needs into governed `audit_events`/`retrieval_events` or a narrowly documented migration. If no live consumers are proven, drop `stream_events` rather than preserving a pre-production surface area by default.

Rationale: MemoryV4 must remain slim, governed, SQLite-only, and core-only. A streaming/event table without demonstrated consumers adds API and migration surface before the Store port and audit model are stable.
