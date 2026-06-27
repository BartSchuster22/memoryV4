# MemoryV4 Board-Local Autonomy and Watchdog Guidance

Purpose: keep this project recoverable without adding orchestration into the MemoryV4 core service.

These artifacts are board-local operational aids. They are not part of the runtime API, not imported by `app/`, and must not grow into Kanban/orchestration code inside the core.

## Watchdog principles

- Healthy checks are silent: exit 0 with no stdout.
- Alert checks print one concise line that includes the condition, evidence path, and suggested Kanban action.
- Obsolete checks self-remove their own state under `artifacts/watchdogs/` or become no-ops when the target gate is closed.
- Human-only escalation remains human-only: no script promotes records, accepts review, cuts over, deletes data, or changes live memory systems.

## Gates covered

1. No-progress detector: identifies stale git progress for this repo and suggests a visible Kanban comment/block.
2. Blocked-gate rescue: reminds the operator how to unblock or split a card after a legitimate block.
3. Review/deadlock check: detects implementation commits waiting for review and asks for `ulrich`, not self-review.
4. Host-pressure resume notes: checks load/swap before resuming build/test work.
5. Human-only escalation: lists decisions that must not be automated.

See scripts under `ops/watchdogs/`.
