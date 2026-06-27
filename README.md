# MemoryV4 Core

MemoryV4 is a slim, single-container FastAPI memory core. This D0 scaffold intentionally includes only the core service baseline, health endpoint, docs skeleton, tests, migrations directory, and local build commands.

Hard boundaries for this repository:
- no MemoryV3 cutover or live-memory mutation
- no UI/explorer or Kanban/orchestration code in core
- SQLite-only implementation now; Postgres is a future Store adapter slot only

## Durable working tree

Canonical fresh clone path for this project:

```text
/srv/memoryV4
```

Remote:

```text
git@github.com:BartSchuster22/memoryV4.git
```

Use the deployed write-key helper when pushing from this host:

```bash
/srv/memoryV4/bin/git-memoryV4 fetch origin
/srv/memoryV4/bin/git-memoryV4 push origin HEAD
```

## Local commands

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
make test
make run
make docker-build
make qa10
make guardrails-check
```

## Operational guardrails

Board-local autonomy guardrails are installed outside the core container at `scripts/kanban_guardrails.sh` and documented in `docs/ops/autonomy-guardrails.md`. They are silent when healthy and read-only/human-escalation-only when an alert condition is detected.
