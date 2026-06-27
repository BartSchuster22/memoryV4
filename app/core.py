"""Core governance service that depends only on the Store port."""

from __future__ import annotations

from dataclasses import dataclass

from app.ports import Store
from app.schemas import Lifecycle, MemoryRecord


@dataclass(frozen=True)
class GovernanceCore:
    """Small domain service layer over the storage port."""

    store: Store

    def create_record(self, record: MemoryRecord, *, actor: str) -> None:
        self.store.create_record(record, actor=actor)

    def promote_to_live(self, record_id: str, *, actor: str) -> None:
        self.store.transition_lifecycle(record_id, Lifecycle.LIVE, actor=actor)

    def archive_record(self, record_id: str, *, actor: str) -> None:
        self.store.transition_lifecycle(record_id, Lifecycle.ARCHIVED, actor=actor)
