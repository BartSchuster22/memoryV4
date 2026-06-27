# Human-Only Escalation

The following MemoryV4 actions require explicit human approval and must not be automated by watchdogs or autonomous workers:

- Any live deploy or cutover from an existing memory system.
- Any destructive operation, data deletion, or overwrite of live memory data.
- Promotion of autonomous `working` output to `canonical`/`live` memory.
- Resolving contradiction findings by superseding canonical records.
- Accepting implementation output as reviewed; `ulrich` owns review.
- Raising worker concurrency above board policy.
- Introducing UI/explorer or Kanban/orchestration code into this core repository.
