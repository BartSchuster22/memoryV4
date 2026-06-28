"""Embedding provider abstractions for MemoryV4 retrieval."""
from __future__ import annotations

import hashlib
import math
from typing import Protocol, Sequence


class EmbeddingUnavailable(RuntimeError):
    """Raised when an embedding provider cannot produce a vector."""


class EmbeddingProvider(Protocol):
    """Minimal embedding provider contract used by Store backfill and search."""

    model: str
    dimensions: int

    def embed(self, text: str) -> Sequence[float]: ...


class DeterministicHashEmbeddingProvider:
    """Dependency-free provider for local development and lexical fallback tests.

    This is not a semantic model. It gives a stable vector lane for smoke tests and
    backfill plumbing until a production provider is configured by a later phase.
    """

    model = "deterministic-hash-v1"

    def __init__(self, dimensions: int = 64) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions

    def embed(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * self.dimensions
        for token in [part.casefold() for part in text.split() if part.strip()]:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return tuple(vector)
        return tuple(value / norm for value in vector)
