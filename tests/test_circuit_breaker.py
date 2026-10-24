"""
test_circuit_breaker.py
==========================

Unit tests for app/circuit_breaker.py — the core mechanism the outage
drill depends on. Uses real (short) sleeps rather than mocking time,
since the breaker's logic is simple enough that this stays correct and fast.
"""

import time

from app.circuit_breaker import BreakerState, CircuitBreaker
from app.telemetry import EventType, TelemetryLog


def _breaker(threshold=3, reset_seconds=0.2) -> tuple[CircuitBreaker, TelemetryLog]:
    telemetry = TelemetryLog()
    breaker = CircuitBreaker(name="test", failure_threshold=threshold, reset_seconds=reset_seconds, telemetry=telemetry)
    return breaker, telemetry


def test_starts_closed_and_allows_requests() -> None:
    breaker, _ = _breaker()
    assert breaker.state == BreakerState.CLOSED
    assert breaker.allow_request() is True


def test_opens_after_threshold_consecutive_failures() -> None:
    breaker, telemetry = _breaker(threshold=3)

    for _ in range(3):
        breaker.record_failure()

    assert breaker.state == BreakerState.OPEN
    assert telemetry.count(EventType.CIRCUIT_BREAKER_OPENED) == 1


def test_open_breaker_rejects_requests_before_reset_window_elapses() -> None:
    breaker, _ = _breaker(threshold=1, reset_seconds=5.0)
    breaker.record_failure()

    assert breaker.allow_request() is False


def test_success_resets_consecutive_failure_count() -> None:
    breaker, _ = _breaker(threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()  # resets the count
    breaker.record_failure()

    assert breaker.state == BreakerState.CLOSED  # only 1 consecutive failure since the reset


def test_half_open_probe_allowed_after_reset_window() -> None:
    breaker, telemetry = _breaker(threshold=1, reset_seconds=0.05)
    breaker.record_failure()
    assert breaker.state == BreakerState.OPEN

    time.sleep(0.1)

    assert breaker.allow_request() is True
    assert breaker.state == BreakerState.HALF_OPEN
    assert telemetry.count(EventType.CIRCUIT_BREAKER_HALF_OPEN_PROBE) == 1


def test_successful_half_open_probe_closes_breaker() -> None:
    breaker, telemetry = _breaker(threshold=1, reset_seconds=0.05)
    breaker.record_failure()
    time.sleep(0.1)
    breaker.allow_request()  # transitions to HALF_OPEN

    breaker.record_success()

    assert breaker.state == BreakerState.CLOSED
    assert telemetry.count(EventType.CIRCUIT_BREAKER_CLOSED) == 1


def test_failed_half_open_probe_reopens_immediately() -> None:
    breaker, _ = _breaker(threshold=1, reset_seconds=0.05)
    breaker.record_failure()
    time.sleep(0.1)
    breaker.allow_request()  # transitions to HALF_OPEN

    breaker.record_failure()  # the probe itself failed

    assert breaker.state == BreakerState.OPEN
