"""
test_budget_and_rate_limiter.py
==================================

Unit tests for app/budget.py and app/rate_limiter.py — both enforce
limits BEFORE spending (week-8-part3-notes.md sections 12, 18).
"""

import time

import pytest

from app.budget import BudgetEnforcer, BudgetExceededError, TokenBudgetTracker
from app.rate_limiter import RateLimiterRegistry, TokenBucketRateLimiter
from app.telemetry import EventType, TelemetryLog


def test_budget_tracker_allows_spending_within_limit() -> None:
    telemetry = TelemetryLog()
    tracker = TokenBudgetTracker(scope_name="request", limit_tokens=1000)

    tracker.check_and_reserve(telemetry, estimated_tokens=500)  # should not raise
    tracker.record_actual_usage(500)

    assert tracker.used_tokens == 500


def test_budget_tracker_blocks_before_exceeding_limit() -> None:
    telemetry = TelemetryLog()
    tracker = TokenBudgetTracker(scope_name="request", limit_tokens=1000)
    tracker.record_actual_usage(800)

    with pytest.raises(BudgetExceededError):
        tracker.check_and_reserve(telemetry, estimated_tokens=300)  # 800 + 300 > 1000

    assert telemetry.count(EventType.BUDGET_EXCEEDED) == 1


def test_enforcer_checks_all_three_scopes() -> None:
    telemetry = TelemetryLog()
    enforcer = BudgetEnforcer(fleet_limit_tokens=100, telemetry=telemetry)  # fleet limit is the tightest
    request_tracker = TokenBudgetTracker(scope_name="request", limit_tokens=10_000)

    with pytest.raises(BudgetExceededError):
        enforcer.check_all_scopes(request_tracker, "tenant-a", tenant_limit=10_000, estimated_tokens=200)


def test_enforcer_records_usage_against_all_scopes() -> None:
    telemetry = TelemetryLog()
    enforcer = BudgetEnforcer(fleet_limit_tokens=10_000, telemetry=telemetry)
    request_tracker = TokenBudgetTracker(scope_name="request", limit_tokens=5000)
    enforcer.check_all_scopes(request_tracker, "tenant-a", tenant_limit=5000, estimated_tokens=100)

    enforcer.record_actual_usage(request_tracker, "tenant-a", tokens_used=250)

    assert request_tracker.used_tokens == 250
    assert enforcer.get_or_create_tenant_tracker("tenant-a", 5000).used_tokens == 250


def test_rate_limiter_allows_within_capacity() -> None:
    telemetry = TelemetryLog()
    limiter = TokenBucketRateLimiter(capacity=1000, refill_rate_per_second=100)

    assert limiter.try_consume(telemetry, estimated_tokens=500, scope_name="tenant:a") is True


def test_rate_limiter_rejects_when_capacity_exhausted() -> None:
    telemetry = TelemetryLog()
    limiter = TokenBucketRateLimiter(capacity=1000, refill_rate_per_second=0)  # no refill: isolate capacity logic

    limiter.try_consume(telemetry, estimated_tokens=900, scope_name="tenant:a")
    allowed = limiter.try_consume(telemetry, estimated_tokens=200, scope_name="tenant:a")

    assert allowed is False
    assert telemetry.count(EventType.RATE_LIMITED) == 1


def test_rate_limiter_refills_over_time() -> None:
    telemetry = TelemetryLog()
    limiter = TokenBucketRateLimiter(capacity=100, refill_rate_per_second=1000)  # fast refill for a quick test
    limiter.try_consume(telemetry, estimated_tokens=100, scope_name="tenant:a")  # empty the bucket

    time.sleep(0.15)

    assert limiter.try_consume(telemetry, estimated_tokens=100, scope_name="tenant:a") is True


def test_registry_isolates_tenants() -> None:
    telemetry = TelemetryLog()
    registry = RateLimiterRegistry(capacity=100, refill_rate_per_second=0)

    registry.for_tenant("tenant-a").try_consume(telemetry, estimated_tokens=100, scope_name="tenant:a")  # exhaust A

    assert registry.for_tenant("tenant-b").try_consume(telemetry, estimated_tokens=50, scope_name="tenant:b") is True
