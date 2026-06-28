"""Hybrid retrieval orchestration for MemoryV4."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.embeddings import EmbeddingProvider, EmbeddingUnavailable
from app.models import Filter, Lifecycle, Role
from app.ports import Store

RRF_K = 60
DEFAULT_RRF_K = RRF_K
_GOVERNANCE_PRIOR = {
    (Role.CANONICAL.value, Lifecycle.LIVE.value): 0.010,
    (Role.ACTIVE.value, Lifecycle.LIVE.value): 0.006,
    (Role.EVIDENCE.value, Lifecycle.LIVE.value): 0.003,
}
MAX_GOVERNANCE_PRIOR = 0.010


@dataclass(frozen=True)
class SearchResult:
    record_id: str
    score: float
    lanes: dict[str, int]
    governance_prior: float


class HybridRetriever:
    """Combine BM25 and vector lanes with RRF and a bounded governance prior."""

    def __init__(self, store: Store, provider: EmbeddingProvider) -> None:
        self.store = store
        self.provider = provider

    def search(self, query: str, f: Filter, k: int = 10) -> list[SearchResult]:
        if k <= 0:
            return []
        bm25_ids = self.store.lexical_rank(query, f, k)
        lanes: list[tuple[str, list[str]]] = [("bm25", bm25_ids)]
        try:
            qvec = self.provider.embed(query)
        except (EmbeddingUnavailable, RuntimeError, KeyError):
            qvec = []
        if qvec:
            vector_ids = self.store.vector_rank(qvec, f, k)
            if vector_ids:
                lanes.append(("vector", vector_ids))
        fused = reciprocal_rank_fusion([ids for _, ids in lanes])
        results: list[SearchResult] = []
        for record_id, base_score in fused:
            rec = self.store.get_record(record_id)
            if rec is None:
                continue
            role = rec.role.value if isinstance(rec.role, Role) else rec.role
            lifecycle = rec.lifecycle.value if isinstance(rec.lifecycle, Lifecycle) else rec.lifecycle
            prior = min(MAX_GOVERNANCE_PRIOR, _GOVERNANCE_PRIOR.get((role, lifecycle), 0.0))
            result_lanes = {
                lane_name: lane.index(record_id) + 1
                for lane_name, lane in lanes
                if record_id in lane
            }
            results.append(SearchResult(record_id, base_score + prior, result_lanes, prior))
        results.sort(key=lambda result: (-result.score, result.record_id))
        return results[:k]


@dataclass(frozen=True)
class RetrievalResponse:
    results: list[SearchResult]
    degraded: bool

    @property
    def record_ids(self) -> list[str]:
        return [result.record_id for result in self.results]


class RetrievalService:
    """Compatibility service wrapper exposing degraded fallback metadata."""

    def __init__(self, store: Store, provider: EmbeddingProvider) -> None:
        self.store = store
        self.provider = provider
        self.hybrid = HybridRetriever(store, provider)

    def backfill_embeddings(self, f: Filter | None = None) -> int:
        return self.store.backfill_embeddings(self.provider, f)

    def search(self, query: str, f: Filter, limit: int) -> RetrievalResponse:
        results = self.hybrid.search(query, f, limit)
        degraded = all("vector" not in result.lanes for result in results)
        return RetrievalResponse(results=results, degraded=degraded)


def reciprocal_rank_fusion(lanes: Sequence[Sequence[str]], *, rrf_k: int = RRF_K) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for lane in lanes:
        seen: set[str] = set()
        for rank, record_id in enumerate(lane, start=1):
            if record_id in seen:
                continue
            seen.add(record_id)
            scores[record_id] = scores.get(record_id, 0.0) + 1.0 / (rrf_k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
