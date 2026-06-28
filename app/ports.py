"""Storage port for the MemoryV4 core.

Core code depends on this Protocol. SQLite-specific SQL, FTS behavior, and WAL
configuration belong in app.storage.SqliteStore. Postgres is intentionally only a
future adapter slot documented in docs; it is not implemented or configurable in
P1.
"""
from __future__ import annotations

from typing import Any, Protocol, Sequence

from app.embeddings import EmbeddingProvider
from app.models import AuditEvent, Filter, Lifecycle, Record


class Store(Protocol):
    def create_record(self, rec: Record, *, actor: str) -> Record: ...

    def get_record(self, rid: str) -> Record | None: ...

    def supersede(self, old_id: str, new: Record, *, actor: str) -> Record: ...

    def transition(self, rid: str, lifecycle: Lifecycle, *, actor: str) -> Record: ...

    def lexical_rank(self, query: str, f: Filter, k: int) -> list[str]: ...

    def vector_rank(self, qvec: Sequence[float], f: Filter, k: int, *, model: str | None = None) -> list[str]: ...

    def backfill_embeddings(self, provider: EmbeddingProvider, f: Filter | None = None) -> int: ...

    def write_audit(self, ev: AuditEvent) -> None: ...

    def write_finding(self, finding: dict[str, Any]) -> None: ...
