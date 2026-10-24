"""
app/circuit_breaker.py
=========================

Per-provider circuit breaker — see week-8-part3-notes.md, section 10.

The SAME three-state (closed/open/half-open) mechanic you already know
from traditional backend reliability engineering — the AI-specific
delta is in HOW it's used (one breaker per PROVIDER, see
app/model_gateway.py) and in what a half-open probe costs (a real,
billed LLM call).
"""

import time
from dataclasses import dataclass, field
from enum import Enum

from app.telemetry import EventType, TelemetryLog


class BreakerState(str, Enum):
    """The three standard circuit breaker states."""

    CLOSED = "closed"        # Normal operation — calls pass through.
    OPEN = "open"            # Tripped — calls rejected immediately, no network call made.
    HALF_OPEN = "half_open"  # Reset window elapsed — the NEXT call is allowed through as a probe.


@dataclass
class CircuitBreaker:
    """
    A single circuit breaker instance, scoped to ONE dependency.

    Attributes:
        name: Identifies which dependency this breaker guards (telemetry).
        failure_threshold: Consecutive failures before the breaker opens.
        reset_seconds: How long the breaker stays OPEN before allowing
            one HALF_OPEN probe attempt.
        telemetry: Shared telemetry log.
    """

    name: str
    failure_threshold: int
    reset_seconds: float
    telemetry: TelemetryLog

    _state: BreakerState = field(default=BreakerState.CLOSED, init=False)
    _consecutive_failures: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)

    def allow_request(self) -> bool:
        """
        Whether a call should be attempted right now.

        Returns:
            True if CLOSED, or if a HALF_OPEN probe is due. False if
            OPEN and the reset window hasn't elapsed — the caller
            should skip this dependency entirely (no network call),
            since a probe is a real, billed call.
        """
        if self._state == BreakerState.CLOSED:
            return True

        if self._state == BreakerState.OPEN:
            assert self._opened_at is not None
            if time.monotonic() - self._opened_at >= self.reset_seconds:
                self._state = BreakerState.HALF_OPEN
                self.telemetry.record(EventType.CIRCUIT_BREAKER_HALF_OPEN_PROBE, breaker=self.name)
                return True
            return False

        # HALF_OPEN: the single probe is allowed through.
        return True

    def record_success(self) -> None:
        """Record a successful call — resets the failure count; closes the breaker if it wasn't closed."""
        self._consecutive_failures = 0
        if self._state != BreakerState.CLOSED:
            self._state = BreakerState.CLOSED
            self._opened_at = None
            self.telemetry.record(EventType.CIRCUIT_BREAKER_CLOSED, breaker=self.name)

    def record_failure(self) -> None:
        """
        Record a failed call. Opens the breaker when consecutive
        failures reach the threshold, or immediately re-opens if a
        HALF_OPEN probe itself failed.
        """
        self._consecutive_failures += 1

        if self._state == BreakerState.HALF_OPEN:
            self._open()
            return

        if self._consecutive_failures >= self.failure_threshold:
            self._open()

    def _open(self) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = time.monotonic()
        self.telemetry.record(
            EventType.CIRCUIT_BREAKER_OPENED,
            breaker=self.name,
            consecutive_failures=self._consecutive_failures,
        )

    @property
    def state(self) -> BreakerState:
        """Current state — read-only, for tests/telemetry inspection."""
        return self._state
