# MemoryV4 Operations

## D0 commands

Initial local setup:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

- `make run`: run the FastAPI app locally with Uvicorn.
- `make test`: run the local test suite.
- `make docker-build`: build the single core container image.
- `make qa10`: D0 placeholder that records QA10 as pending until gates are implemented; gate inventory is in `docs/QA10.md`.
- `make guardrails-check`: run the read-only Kanban guardrail self-test.

## Health checks

- Service endpoint: `GET /health` returns `{"status":"ok","service":"memoryv4-core","version":"0.1.0-d0"}`.
- Container healthcheck: `Dockerfile` checks `http://127.0.0.1:8000/health` every 30 seconds.

## CI/test entrypoints

- GitHub Actions workflow: `.github/workflows/ci.yml`.
- CI runs dependency install, `make test`, `make qa10`, `make guardrails-check`, and `make docker-build`.

## Board-local autonomy guardrails

- Script: `scripts/kanban_guardrails.sh`.
- Documentation: `docs/ops/autonomy-guardrails.md`.
- Behavior: silent when healthy, read-only, and human-escalation-only for no-progress, blocked-gate rescue, review deadlocks, and host-pressure resume alerts.

## Live operations boundary

D0 performs no live deployment, no cutover, and no writes to MemoryV3 or live memory databases.
