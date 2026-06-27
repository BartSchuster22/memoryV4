# MemoryV4 Durable Artifacts

This directory is the board-local durable artifact root for implementation evidence that belongs with this repository but is not runtime source.

Expected contents:
- `evidence/`: command transcripts, smoke-test outputs, and QA scorecards copied from worker runs when useful.
- `watchdogs/`: state files created by board-local watchdog scripts.
- `patches/`: dirty-tree preservation snapshots, if recovery is needed.

Rules:
- Do not store secrets, tokens, private keys, raw PII, or live memory exports here.
- Healthy watchdogs stay silent.
- Watchdog state is safe to delete once the associated Kanban/review gate is closed.
- This project is not a cutover path for any current memory system.
