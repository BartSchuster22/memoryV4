# MemoryV4 Board-Local Autonomy Guardrails

These guardrails protect the standalone `memoryv4-next` Kanban board and `/srv/memoryV4` working tree. They are intentionally outside the MemoryV4 core container; the core container must remain memory-service-only and must not embed Kanban/orchestration logic.

## Invariants

- Silent when healthy: no output means no alert condition was detected.
- Read-only: the watchdog never mutates Kanban task state, comments, files, or git history.
- Human-only escalation: alerts tell a human/operator what to verify; the script does not unblock, reclaim, resume, or approve cards.
- Single-workspace safety: operators should resume at most one blocked/running MemoryV4 card at a time unless PM explicitly raises concurrency.

## Installed script

```bash
/srv/memoryV4/scripts/kanban_guardrails.sh
```

Default board database:

```text
/home/herman/.hermes/kanban/boards/memoryv4-next/kanban.db
```

Override it for dry runs or board copies:

```bash
/srv/memoryV4/scripts/kanban_guardrails.sh --db /path/to/kanban.db
```

## Checks

1. No-progress detector: reports running cards with no heartbeat/progress comments/events beyond the threshold.
2. Blocked-gate rescue: reports blocked cards that received post-block comments indicating remediation, approval, or readiness to resume.
3. Review-gate/deadlock: reports stale `review-required` blocks that have no open child review card.
4. Host-pressure resume: if a card was blocked for load/swap pressure and the host is now below gates, reports that a human may resume one card after worktree checks.
5. Human-only escalation: every alert is advisory and requires human verification before unblocking or resuming.

## Manual checks

```bash
/srv/memoryV4/scripts/kanban_guardrails.sh --self-test
/srv/memoryV4/scripts/kanban_guardrails.sh
/srv/memoryV4/scripts/kanban_guardrails.sh --json
```

## Cron installation

The active scheduled job is named:

```text
memoryv4-next board guardrails
```

Current job id at installation time:

```text
88a064e2f705
```

Cron runs the profile-local wrapper:

```text
/home/herman/.hermes/profiles/devops-agent/scripts/memoryv4-next-kanban-guardrails.sh
```

The wrapper delegates to the project script at `/srv/memoryV4/scripts/kanban_guardrails.sh`.

It runs every 10 minutes with `no_agent=True`, so it consumes no model tokens. Delivery is silent when stdout is empty; non-empty stdout is an alert.

List jobs:

```bash
hermes cron list
```

Run once:

```bash
hermes cron run <job_id>
```

Pause:

```bash
hermes cron pause <job_id>
```

Resume:

```bash
hermes cron resume <job_id>
```

Remove/stop permanently:

```bash
hermes cron remove <job_id>
```

If the cron job is recreated, use this script path:

```text
/srv/memoryV4/scripts/kanban_guardrails.sh
```

Recommended schedule:

```text
every 10m
```

## Safety response to alerts

1. Inspect the card and comment thread on the board.
2. Inspect host health: `uptime`, `free -h`, and current worker processes.
3. Inspect `/srv/memoryV4` git state: `git status --short --branch`.
4. If the alert is valid, take exactly one human-approved action: unblock, create a review card, reclaim, or leave a new comment explaining why no action was taken.
5. Do not perform live deployment or MemoryV3/live-memory cutover from this project.
