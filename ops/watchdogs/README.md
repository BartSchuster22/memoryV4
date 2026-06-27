# Board-Local Watchdogs

These scripts are intentionally simple, local, and silent when healthy. They may be run manually, by a board-local cron, or by a future external orchestrator. They must not be imported by the MemoryV4 core app.

Scripts:
- `no_progress_detector.py`: alerts if no git commit happened within a configured age while the tree has work in progress.
- `review_deadlock_check.py`: alerts if implementation commits exist after the last review marker.
- `host_pressure_check.py`: alerts if host load/swap exceed project resume gates.
- `human_only_escalation.md`: decisions that require a human.
- `blocked_gate_rescue.md`: recovery playbook for blocked cards.

All scripts exit 0 for healthy/no-op states and print nothing unless attention is needed.
