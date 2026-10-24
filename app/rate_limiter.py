"""
app/rate_limiter.py
======================

Token-aware rate limiting — see week-8-part3-notes.md, section 12:
request-count limiting alone doesn't bound actual resource/cost
consumption, because one request's token usage varies wildly.

A token bucket whose "tokens" ARE LLM tokens (not an abstract unit) —
the AI-specific delta from the token-bucket algorithm you already know.
"""

import time
from dataclasses import dataclass, field

from app.telemetry import EventType, TelemetryLog


@dataclass
class TokenBucketRateLimiter:
    """
    A token-bucket rate limiter whose bucket holds LLM tokens.

    Attributes:
        capacity: Maximum tokens the bucket can hold (burst allowance).
        refill_rate_per_second: Tokens added back per second.
    """

    capacity: float
    refill_rate_per_second: float
    _tokens_available: float = field(init=False)
    _last_refill_time: float = field(init=False)

    def __post_init__(self) -> None:
        self._tokens_available = self.capacity
        self._last_refill_time = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill_time
        self._tokens_available = min(self.capacity, self._tokens_available + elapsed * self.refill_rate_per_second)
        self._last_refill_time = now

    def try_consume(self, telemetry: TelemetryLog, estimated_tokens: float, scope_name: str) -> bool:
        """
        Attempt to consume `estimated_tokens` from the bucket.

        Args:
            telemetry: Records a RATE_LIMITED event on rejection.
            estimated_tokens: Expected LLM tokens for the next call —
                the same pre-call estimate app/budget.py uses.
            scope_name: Which limiter this is, for telemetry (e.g. "tenant:acme").

        Returns:
            True if allowed (tokens consumed); False if there isn't
            enough capacity — the caller applies its backpressure
            policy (week-8-part3-notes.md section 13).
        """
        self._refill()
        if self._tokens_available >= estimated_tokens:
            self._tokens_available -= estimated_tokens
            return True

        telemetry.record(
            EventType.RATE_LIMITED,
            scope=scope_name,
            available_tokens=round(self._tokens_available, 1),
            requested_tokens=estimated_tokens,
        )
        return False

    @property
    def tokens_available(self) -> float:
        """Current bucket level after applying refill — for tests/inspection."""
        self._refill()
        return self._tokens_available


class RateLimiterRegistry:
    """Per-tenant TokenBucketRateLimiter instances, created on first use."""

    def __init__(self, capacity: float, refill_rate_per_second: float) -> None:
        self._capacity = capacity
        self._refill_rate = refill_rate_per_second
        self._limiters: dict[str, TokenBucketRateLimiter] = {}

    def for_tenant(self, tenant_id: str) -> TokenBucketRateLimiter:
        """Return (creating if needed) the rate limiter for a tenant."""
        if tenant_id not in self._limiters:
            self._limiters[tenant_id] = TokenBucketRateLimiter(
                capacity=self._capacity, refill_rate_per_second=self._refill_rate
            )
        return self._limiters[tenant_id]
