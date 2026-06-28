from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Sequence

import pytest

from app.embeddings import EmbeddingProvider, EmbeddingUnavailable
from app.models import Filter, Lifecycle, Record, Role
from app.retrieval import HybridRetriever, MAX_GOVERNANCE_PRIOR, SearchResult, reciprocal_rank_fusion
from app.storage import SqliteStore


class MappingProvider(EmbeddingProvider):
    dimensions = 3
    model = "test-mapping-v1"

    def __init__(self, vectors: dict[str, list[float]], *, unavailable: bool = False) -> None:
        self.vectors = vectors
        self.unavailable = unavailable

    def embed(self, text: str) -> list[float]:
        if self.unavailable:
            raise EmbeddingUnavailable("test provider unavailable")
        return self.vectors[text]


class TimeoutProvider(MappingProvider):
    def embed(self, text: str) -> list[float]:
        raise TimeoutError("provider timed out")


def _record(
    rid: str,
    *,
    title: str,
    content: str,
    role: Role = Role.ACTIVE,
    lifecycle: Lifecycle = Lifecycle.LIVE,
    scope_path: str = "org:acme/project:alpha",
) -> Record:
    return Record(
        id=rid,
        entity_type="decision",
        title=title,
        topic="retrieval",
        content=content,
        role=role,
        lifecycle=lifecycle,
        scope_path=scope_path,
        author_actor="tester",
        write_policy={"supersede_requires": ["review"]},
    )


def test_embedding_provider_backfill_and_vector_rank_are_scope_isolated(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "memoryv4.sqlite3")
    store.create_record(_record("rec_alpha", title="Alpha", content="alpha-vector"), actor="tester")
    store.create_record(_record("rec_child", title="Child", content="child-vector", scope_path="org:acme/project:alpha/team:red"), actor="tester")
    store.create_record(_record("rec_sibling", title="Sibling", content="sibling-vector", scope_path="org:acme/project:alphabet"), actor="tester")
    provider = MappingProvider(
        {
            "Alpha\nretrieval\nalpha-vector": [1.0, 0.0, 0.0],
            "Child\nretrieval\nchild-vector": [0.9, 0.1, 0.0],
            "Sibling\nretrieval\nsibling-vector": [0.0, 1.0, 0.0],
            "vector query": [1.0, 0.0, 0.0],
        }
    )

    assert store.backfill_embeddings(provider) == 3

    with sqlite3.connect(tmp_path / "memoryv4.sqlite3") as conn:
        persisted = conn.execute("SELECT COUNT(*), MIN(dimensions), MAX(model) FROM record_embeddings").fetchone()
    assert persisted == (3, 3, "test-mapping-v1")
    assert store.vector_rank(provider.embed("vector query"), Filter(scope_prefixes=["org:acme/project:alpha"]), 10) == [
        "rec_alpha",
        "rec_child",
    ]


def test_rrf_hybrid_search_combines_bm25_vector_and_bounded_governance_prior(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "memoryv4.sqlite3")
    store.create_record(
        _record("rec_lexical", title="Rollback safety", content="rollback safety migration", role=Role.EVIDENCE),
        actor="tester",
    )
    store.create_record(
        _record("rec_vector", title="Governed migration", content="semantic-vector", role=Role.CANONICAL),
        actor="tester",
    )
    store.create_record(_record("rec_other_scope", title="Rollback", content="semantic-vector", scope_path="org:other"), actor="tester")
    provider = MappingProvider(
        {
            "Rollback safety\nretrieval\nrollback safety migration": [0.0, 1.0, 0.0],
            "Governed migration\nretrieval\nsemantic-vector": [1.0, 0.0, 0.0],
            "Rollback\nretrieval\nsemantic-vector": [1.0, 0.0, 0.0],
            "rollback safety": [1.0, 0.0, 0.0],
        }
    )
    store.backfill_embeddings(provider)

    results = HybridRetriever(store, provider).search("rollback safety", Filter(scope_prefixes=["org:acme"]), k=5)

    assert [result.record_id for result in results] == ["rec_vector", "rec_lexical"]
    assert all(isinstance(result, SearchResult) for result in results)
    assert results[0].score - results[1].score < 0.02
    assert "rec_other_scope" not in [result.record_id for result in results]


def test_lexical_fallback_returns_bm25_results_when_embeddings_are_unavailable(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "memoryv4.sqlite3")
    store.create_record(_record("rec_lexical", title="Lexical fallback", content="exact fallback match"), actor="tester")
    provider = MappingProvider({}, unavailable=True)

    results = HybridRetriever(store, provider).search("fallback match", Filter(scope_prefixes=["org:acme"]), k=5)

    assert [result.record_id for result in results] == ["rec_lexical"]
    assert results[0].lanes == {"bm25": 1}


def test_scope_prefix_filter_escapes_sql_like_wildcards(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "memoryv4.sqlite3")
    store.create_record(_record("rec_literal", title="Literal", content="alpha", scope_path="org:acme/project:a_b"), actor="tester")
    store.create_record(_record("rec_wildcard_leak", title="Leak", content="alpha", scope_path="org:acme/project:acb/team:red"), actor="tester")

    assert store.lexical_rank("alpha", Filter(scope_prefixes=["org:acme/project:a_b"]), 10) == ["rec_literal"]


def test_governance_prior_cannot_overcome_large_rrf_rank_gap() -> None:
    assert MAX_GOVERNANCE_PRIOR < (1 / 61 - 1 / 70)


def test_search_degrades_to_lexical_on_timeout_and_vector_lane_failure(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "memoryv4.sqlite3")
    store.create_record(_record("rec_lexical", title="Timeout fallback", content="exact match"), actor="tester")

    timeout_results = HybridRetriever(store, TimeoutProvider({})).search("exact match", Filter(scope_prefixes=["org:acme"]), k=5)
    assert [result.record_id for result in timeout_results] == ["rec_lexical"]
    assert timeout_results[0].lanes == {"bm25": 1}

    class BrokenVectorStore(SqliteStore):
        def vector_rank(self, qvec: Sequence[float], f: Filter, k: int, *, model: str | None = None) -> list[str]:
            raise TimeoutError("vector lane timed out")

    broken = BrokenVectorStore(tmp_path / "broken.sqlite3")
    broken.create_record(_record("rec_lexical", title="Vector fallback", content="exact match"), actor="tester")
    provider = MappingProvider({"exact match": [1.0, 0.0, 0.0]})
    vector_failure_results = HybridRetriever(broken, provider).search("exact match", Filter(scope_prefixes=["org:acme"]), k=5)
    assert [result.record_id for result in vector_failure_results] == ["rec_lexical"]
    assert vector_failure_results[0].lanes == {"bm25": 1}


def test_hybrid_vector_lane_filters_embedding_model_metadata(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "memoryv4.sqlite3")
    store.create_record(_record("rec_model_a", title="A", content="model-a-vector"), actor="tester")
    store.create_record(_record("rec_model_b", title="B", content="model-b-vector"), actor="tester")
    provider_a = MappingProvider(
        {
            "A\nretrieval\nmodel-a-vector": [1.0, 0.0, 0.0],
            "B\nretrieval\nmodel-b-vector": [1.0, 0.0, 0.0],
        }
    )

    assert store.backfill_embeddings(provider_a, Filter(scope_prefixes=["org:acme/project:alpha"])) == 2
    with sqlite3.connect(tmp_path / "memoryv4.sqlite3") as conn:
        conn.execute("UPDATE record_embeddings SET model = ? WHERE record_id = ?", ("test-mapping-v2", "rec_model_b"))
    assert store.vector_rank([1.0, 0.0, 0.0], Filter(scope_prefixes=["org:acme"]), 10, model="test-mapping-v2") == [
        "rec_model_b"
    ]
    provider_query = MappingProvider({"semantic only": [1.0, 0.0, 0.0]})
    provider_query.model = "test-mapping-v2"
    assert [
        result.record_id
        for result in HybridRetriever(store, provider_query).search("semantic only", Filter(scope_prefixes=["org:acme"]), k=10)
    ] == ["rec_model_b"]


def test_reciprocal_rank_fusion_uses_default_k_60_and_stable_tiebreaks() -> None:
    assert reciprocal_rank_fusion([["b", "a"], ["a"]]) == [
        ("a", pytest.approx(1 / 62 + 1 / 61)),
        ("b", pytest.approx(1 / 61)),
    ]
