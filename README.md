# MemoryV4 Core

MemoryV4 is a slim, single-container FastAPI memory core. This P0-P4 scaffold includes the core service baseline, SQLite `Store` adapter seam, additive schema migrations, health endpoint, embedding provider seam, BM25/vector hybrid retrieval, docs, tests, and local build commands.

Hard boundaries for this repository:

- no MemoryV3 cutover or live-memory mutation
- no UI/explorer or Kanban/orchestration code in core
- SQLite-only implementation now; Postgres is a future Store adapter slot only

## Local commands

```bash
python3 -m pip install -r requirements-dev.txt
make lint          # compile app/ and tests/
make test          # pytest unit suite
make qa            # lint + test + P2 migration QA scorecard
make qa10-gate-1  # P2 fresh/upgrade/rerun/down migration gate
make run           # uvicorn app.main:app on 127.0.0.1:8000
make docker-build  # build memoryv4-core:dev
make docker-test   # build image and run pytest inside the same container image
```

## Container boot smoke

```bash
docker compose up --build memoryv4-core
curl -fsS http://127.0.0.1:8000/health
```

Expected health response:

```json
{"status":"ok","service":"memoryv4-core","version":"0.1.0-d0","storage_backend":"sqlite"}
```

The Docker image is also the local test container:

```bash
make docker-test
```

This keeps the P0-P1 dev/runtime contract to one image while preserving the
default container command as the FastAPI runtime service.

## Runtime

The container stores its SQLite file at `/data/memoryv4.sqlite3` by default. Override with `MEMORYV4_DB_PATH` for tests or local runs. SQLite migrations are applied on Store startup and recorded in `schema_migrations`; P2 applies baseline `0001`/`0002` plus additive migrations `0006_record_embeddings` through `0012_health_findings`.

## P3-P4 retrieval

Retrieval is split between the SQLite adapter and core fusion logic:

- `app.embeddings.EmbeddingProvider` is the provider seam. `DeterministicHashEmbeddingProvider` is a deterministic local fallback for tests/disconnected installs; production model adapters can implement the same `embed(text) -> Sequence[float]` contract.
- `SqliteStore.backfill_embeddings(provider, filter)` embeds matching records missing `record_embeddings` rows and stores packed float vectors with dimensions/model metadata.
- `SqliteStore.lexical_rank()` is a BM25 lane over title/topic/content, and `SqliteStore.vector_rank()` is a cosine-similarity lane over persisted vectors.
- `HybridRetriever.search()` combines lanes with reciprocal-rank fusion using `k=60`, then applies a small bounded governance prior for live governed records.
- Both lanes enforce the same scope-prefix isolation (`scope = prefix` or `scope` below `prefix/`); sibling scopes are excluded. If the provider is unavailable, search degrades to lexical-only results.
