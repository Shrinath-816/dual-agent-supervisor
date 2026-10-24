"""
app/cache/semantic_cache.py
==============================

Semantic caching — see week-8-part3-notes.md, sections 14-15: a large
cost/latency win for repeated/similar queries whose correctness is
"fundamentally an evaluation problem" — tuned by measuring false-hit
rate, and by EXCLUDING query classes structurally unsafe to cache
(time-sensitive, personalized) rather than threshold-tuning around them.

Structurally a small RAG system (Weeks 2-3): embed the query,
similarity-search cached query/response pairs, serve a hit above a threshold.
"""

import math
import re
import time
from dataclasses import dataclass, field

from app.telemetry import EventType, TelemetryLog

# Queries structurally unsafe to semantically cache (section 15):
# route them AROUND the cache rather than tuning thresholds to cope.
_EXCLUSION_PATTERNS = [
    re.compile(r"\bcurrent\b", re.IGNORECASE),
    re.compile(r"\btoday\b", re.IGNORECASE),
    re.compile(r"\bright now\b", re.IGNORECASE),
    re.compile(r"\bmy account\b", re.IGNORECASE),
    re.compile(r"\bmy order\b", re.IGNORECASE),
]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity (see Week 2's InMemoryVectorStore for the dependency-free rationale)."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


@dataclass
class CacheEntry:
    """One cached query/response pair."""

    query_embedding: list[float]
    query_text: str
    response_text: str
    cached_at: float = field(default_factory=time.monotonic)


def is_excluded_from_caching(query: str) -> bool:
    """Whether a query matches an exclusion pattern and must bypass the semantic cache entirely."""
    return any(pattern.search(query) for pattern in _EXCLUSION_PATTERNS)


class SemanticCache:
    """In-memory semantic cache keyed by embedding similarity — see module docstring for the correctness caveats."""

    def __init__(self, similarity_threshold: float, ttl_seconds: float) -> None:
        """
        Args:
            similarity_threshold: Minimum cosine similarity for a hit —
                tuned empirically (section 15), not guessed.
            ttl_seconds: Max entry age before it's treated as stale and
                ignored — nothing is cached forever without an expiry.
        """
        self._threshold = similarity_threshold
        self._ttl_seconds = ttl_seconds
        self._entries: list[CacheEntry] = []

    def lookup(self, query: str, query_embedding: list[float], telemetry: TelemetryLog) -> str | None:
        """
        Check for a semantic cache hit.

        Args:
            query: Raw query text — checked against the exclusion
                patterns BEFORE any similarity search is attempted.
            query_embedding: The query's embedding (computed by the
                caller — see Weeks 2-3's embedding client).
            telemetry: Records CACHE_HIT / CACHE_MISS / CACHE_EXCLUDED.

        Returns:
            The cached response on a hit, else None (miss or exclusion).
        """
        if is_excluded_from_caching(query):
            telemetry.record(EventType.CACHE_EXCLUDED, query_preview=query[:60])
            return None

        now = time.monotonic()
        best_entry: CacheEntry | None = None
        best_similarity = 0.0

        for entry in self._entries:
            if now - entry.cached_at > self._ttl_seconds:
                continue  # stale: treat as absent
            similarity = _cosine_similarity(query_embedding, entry.query_embedding)
            if similarity > best_similarity:
                best_similarity = similarity
                best_entry = entry

        if best_entry is not None and best_similarity >= self._threshold:
            telemetry.record(
                EventType.CACHE_HIT, similarity=round(best_similarity, 4), matched_query=best_entry.query_text
            )
            return best_entry.response_text

        telemetry.record(EventType.CACHE_MISS, best_similarity=round(best_similarity, 4))
        return None

    def store(self, query: str, query_embedding: list[float], response_text: str) -> None:
        """
        Store a new query/response pair. Does NOT re-check exclusion —
        the caller is expected to have bypassed the cache for excluded
        queries via lookup()'s exclusion check.
        """
        self._entries.append(
            CacheEntry(query_embedding=query_embedding, query_text=query, response_text=response_text)
        )

    def entry_count(self) -> int:
        """Entries currently stored (including possibly-stale ones) — for tests/inspection."""
        return len(self._entries)
