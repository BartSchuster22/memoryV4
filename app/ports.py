"""Engine-agnostic storage port for MemoryV4 core."""

from __future__ import annotations

from typing import Protocol

from app.schemas import AuditEvent, Lifecycle, MemoryRecord, RankedRecord, RetrievalEvent, Supersession


class Store(Protocol):
    """Governed MemoryV4 storage contract.

    App/core callers depend on this port. SQLite details live behind the concrete
    adapter in app.sqlite_store; a future Postgres adapter can implement the same
    protocol without changing domain callers.
    """

    def create_record(self, record: MemoryRecord, *, actor: str) -> None: ...

    def get_record(self, record_id: str) -> MemoryRecord: ...

    def update_record(self, record: MemoryRecord, *, actor: str) -> None: ...

    def transition_lifecycle(self, record_id: str, target: Lifecycle, *, actor: str) -> None: ...

    def supersede_record(self, old_record_id: str, new_record_id: str, *, actor: str, reason: str) -> None: ...

    def lexical_rank(self, query: str, *, scope: str | None, limit: int) -> list[RankedRecord]: ...

    def vector_rank(self, embedding: list[float], *, scope: str | None, limit: int) -> list[RankedRecord]: ...

    def list_audit_events(self, *, record_id: str | None = None) -> list[AuditEvent]: ...

    def list_retrieval_events(self) -> list[RetrievalEvent]: ...

    def list_supersessions(self) -> list[Supersession]: ...
