# MemoryV4 Implementation Log

## D0 bootstrap

Scope: bootstrap a fresh MemoryV4 core repository without cutting over from any current memory system.

Baseline already present at task start:
- `app/main.py` exposes `GET /health`.
- `Dockerfile` builds a single FastAPI core container.
- `docker-compose.yml`, `Makefile`, `pyproject.toml`, `requirements.txt`, and `tests/test_health.py` exist.
- Docs skeleton exists under `docs/`.

This run adds board-local durability/autonomy artifacts and records the `stream_events` P0 audit recommendation.

Commit discipline:
- Use `/srv/memoryV4/bin/git-memoryV4` for pushes with the deployed MemoryV4 key.
- Commit and push after each coherent step.

Live impact:
- No live deploy.
- No MemoryV3 mutation.
- No cutover.
