# Blocked-Gate Rescue Guidance

When a MemoryV4 card is blocked:

1. Read the card comment thread and identify the exact missing decision, credential, or dependency.
2. If the block is review-required, route to `ulrich`; do not let the implementing worker self-approve.
3. If the block is test-validation, route to `test-agent` with exact commands and artifact paths.
4. If host pressure caused the block, resume only one card first after load and swap fall below policy gates.
5. If scope is too broad, ask `pm-agent` to split it rather than continuing hidden work.
6. Leave a visible Kanban comment before unblocking so the next worker has context.

This project has no cutover authority unless a future card explicitly grants it with rollback instructions.
