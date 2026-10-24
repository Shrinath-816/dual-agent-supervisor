"""
test_semantic_cache.py
=========================

Unit tests for app/cache/semantic_cache.py — see week-8-part3-notes.md
sections 14-15 for the correctness caveats this exercises.
"""

import time

from app.cache.semantic_cache import SemanticCache, is_excluded_from_caching
from app.telemetry import EventType, TelemetryLog


def test_excludes_time_sensitive_queries() -> None:
    assert is_excluded_from_caching("What is our current pricing?") is True
    assert is_excluded_from_caching("What's on my account balance today?") is True
    assert is_excluded_from_caching("What is the refund policy?") is False


def test_cache_miss_on_empty_cache() -> None:
    cache = SemanticCache(similarity_threshold=0.9, ttl_seconds=60)
    telemetry = TelemetryLog()

    result = cache.lookup("What is the refund policy?", [1.0, 0.0], telemetry)

    assert result is None
    assert telemetry.count(EventType.CACHE_MISS) == 1


def test_cache_hit_on_highly_similar_query() -> None:
    cache = SemanticCache(similarity_threshold=0.99, ttl_seconds=60)
    telemetry = TelemetryLog()
    cache.store("What is the refund policy?", [1.0, 0.0], "Refunds within 30 days.")

    result = cache.lookup("What is the refund policy?", [1.0, 0.0], telemetry)

    assert result == "Refunds within 30 days."
    assert telemetry.count(EventType.CACHE_HIT) == 1


def test_cache_miss_below_similarity_threshold() -> None:
    cache = SemanticCache(similarity_threshold=0.99, ttl_seconds=60)
    telemetry = TelemetryLog()
    cache.store("What is the refund policy for electronics?", [1.0, 0.0], "30-day refund window.")

    # An orthogonal vector — deliberately dissimilar, simulating a genuinely different query.
    result = cache.lookup("How do I contact support?", [0.0, 1.0], telemetry)

    assert result is None


def test_excluded_query_never_hits_cache_even_if_similar() -> None:
    """The core correctness protection: exclusion is checked BEFORE similarity search — see section 15."""
    cache = SemanticCache(similarity_threshold=0.5, ttl_seconds=60)
    telemetry = TelemetryLog()
    cache.store("What is my current account balance?", [1.0, 0.0], "Balance: $42 (STALE, cached answer).")

    result = cache.lookup("What is my current account balance?", [1.0, 0.0], telemetry)

    assert result is None
    assert telemetry.count(EventType.CACHE_EXCLUDED) == 1


def test_stale_entry_beyond_ttl_is_treated_as_miss() -> None:
    cache = SemanticCache(similarity_threshold=0.9, ttl_seconds=0.05)
    telemetry = TelemetryLog()
    cache.store("What is the refund policy?", [1.0, 0.0], "Refunds within 30 days.")

    time.sleep(0.1)  # exceed the TTL

    result = cache.lookup("What is the refund policy?", [1.0, 0.0], telemetry)

    assert result is None
