# MemoryV4 Operations

## D0 commands

- `python3 -m pip install -r requirements-dev.txt`: install local test dependencies.
- `make run`: run the FastAPI app locally with Uvicorn.
- `make test`: run the local test suite.
- `make docker-build`: build the single core container image.
- `make qa10`: D0 placeholder that records QA10 as pending until gates are implemented.

## Runtime configuration

- `MEMORYV4_DB_PATH`: SQLite database path. Defaults to `/data/memoryv4.sqlite3` in the container.
- Docker Compose mounts `memoryv4-data` at `/data`.

## Health and readiness

`GET /health` is unauthenticated and checks that the SQLite path can be opened and passes `PRAGMA quick_check`. All future non-health endpoints must enforce scoped keys.

## Board-local autonomy

See `docs/AUTONOMY.md` and `ops/watchdogs/`. These checks are outside the core runtime and must remain silent when healthy.

## Live operations boundary

D0 performs no live deployment, no cutover, and no writes to MemoryV3 or live memory databases.
