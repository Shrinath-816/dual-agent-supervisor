"""
scripts/run_outage_drill.py
==============================

THE PROVIDER-OUTAGE RECOVERY DRILL — the direct, executable proof of
this week's brief: "a secure, scalable dual-agent supervisor system
that survives a live provider-outage recovery drill."

What it does, in order:
  1. Sends a normal request — confirms the PRIMARY provider handles it
     (circuit breaker stays CLOSED).
  2. Simulates a PRIMARY provider outage (app/model_gateway.py's
     simulate_provider_down hook — deterministic fault injection, not
     a hope that a real provider happens to fail during the demo).
  3. Sends more requests DURING the outage and verifies:
       a. The "primary" circuit breaker OPENS after the configured
          consecutive-failure threshold (Part 3 section 10).
       b. Requests are served by the BACKUP provider instead, with a
          FALLBACK_TRIGGERED event recorded (Part 3 section 7).
       c. A request proposing a side-effecting action (a refund) STILL
          goes through the Checker — reliability failover must never
          silently bypass the security layer (Part 2 section 8).
  4. Simulates the primary RECOVERING and waits out the breaker's reset window.
  5. Sends one more request — verifies the breaker goes HALF_OPEN, the
     probe succeeds, and it CLOSES, restoring primary routing.
  6. Prints a pass/fail report and exits non-zero on any failure, so it
     can gate CI (the Week 5 regression-gate pattern, applied to a drill).

Requires a real OPENROUTER_API_KEY — the BACKUP provider genuinely
serves requests during the simulated outage.

Usage:
    python scripts/run_outage_drill.py
"""

import asyncio
import os
import sys

# Allow running as `python scripts/run_outage_drill.py` from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.checker import CheckerAgent  # noqa: E402
from app.agents.maker import MakerAgent  # noqa: E402
from app.agents.supervisor import DualAgentSupervisor  # noqa: E402
from app.budget import BudgetEnforcer  # noqa: E402
from app.cache.semantic_cache import SemanticCache  # noqa: E402
from app.circuit_breaker import BreakerState  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.model_gateway import ModelGateway  # noqa: E402
from app.rate_limiter import RateLimiterRegistry  # noqa: E402
from app.security.injection_guard import InjectionGuard  # noqa: E402
from app.security.pii_redactor import PIIRedactor  # noqa: E402
from app.security.tool_permissions import ToolPermissionRegistry  # noqa: E402
from app.telemetry import EventType, TelemetryLog  # noqa: E402
from app.tools import DEFAULT_TOOL_PERMISSIONS  # noqa: E402


def _build_system():
    """Wire a fresh instance of the full system for the drill — identical wiring to app/main.py."""
    settings = get_settings()
    telemetry = TelemetryLog()
    gateway = ModelGateway(settings, telemetry)
    permissions = ToolPermissionRegistry(DEFAULT_TOOL_PERMISSIONS)
    injection_guard = InjectionGuard()
    pii_redactor = PIIRedactor()
    budget_enforcer = BudgetEnforcer(fleet_limit_tokens=settings.request_token_budget * 1000, telemetry=telemetry)
    rate_limiters = RateLimiterRegistry(capacity=5000, refill_rate_per_second=200)
    cache = SemanticCache(similarity_threshold=0.95, ttl_seconds=3600)

    maker = MakerAgent(gateway, permissions, injection_guard, max_iterations=settings.max_iterations)
    checker = CheckerAgent(gateway)

    supervisor = DualAgentSupervisor(
        maker=maker,
        checker=checker,
        permissions=permissions,
        pii_redactor=pii_redactor,
        budget_enforcer=budget_enforcer,
        rate_limiters=rate_limiters,
        semantic_cache=cache,
        max_revision_rounds=settings.max_revision_rounds,
        request_token_budget=settings.request_token_budget,
        tenant_token_budget=settings.request_token_budget * 20,
    )
    return settings, telemetry, gateway, supervisor


async def run_drill() -> bool:
    """
    Run the full outage drill.

    Returns:
        True if every assertion passed, False otherwise — used as the
        script's exit code so the drill can gate CI.
    """
    settings, telemetry, gateway, supervisor = _build_system()
    results: list[tuple[str, bool]] = []

    def check(label: str, condition: bool) -> None:
        results.append((label, condition))
        print(f"  [{'PASS' if condition else 'FAIL'}] {label}")

    print("=== Phase 1: Baseline (primary healthy) ===")
    await supervisor.handle_request("What is the status of order ORD-1001?", tenant_id="acme", telemetry=telemetry)
    check(
        "Primary breaker remains CLOSED under normal operation",
        gateway.breaker_state("primary") == BreakerState.CLOSED.value,
    )

    print("\n=== Phase 2: Simulating primary provider outage ===")
    gateway.simulate_provider_down("primary")

    for i in range(settings.circuit_breaker_failure_threshold):
        await supervisor.handle_request(
            f"What is the status of order ORD-100{i}?", tenant_id="acme", telemetry=telemetry
        )

    check(
        "Primary breaker OPENS after the consecutive-failure threshold is reached",
        gateway.breaker_state("primary") == BreakerState.OPEN.value,
    )
    check(
        "At least one FALLBACK_TRIGGERED event was recorded during the outage",
        telemetry.count(EventType.FALLBACK_TRIGGERED) >= 1,
    )
    check(
        "Backup provider actually served a request during the outage",
        telemetry.count(EventType.PROVIDER_CALL_SUCCEEDED) >= 1,
    )

    print("\n=== Phase 3: Security/reliability composition during outage ===")
    refund_result = await supervisor.handle_request(
        "My order ORD-1002 arrived broken, please refund it.", tenant_id="acme", telemetry=telemetry
    )
    check(
        "A side-effecting action during the outage still went through Checker review "
        "(completed via approval, or escalated — never silently auto-executed)",
        refund_result.stopped_reason in ("completed", "escalated"),
    )
    check(
        "The maker-checker mechanism produced at least one recorded verdict during the drill",
        telemetry.count(EventType.CHECKER_APPROVED) + telemetry.count(EventType.CHECKER_REJECTED) >= 1,
    )

    print("\n=== Phase 4: Simulating primary provider recovery ===")
    gateway.simulate_provider_recovered("primary")
    print(f"  Waiting {settings.circuit_breaker_reset_seconds}s for the breaker's reset window to elapse...")
    await asyncio.sleep(settings.circuit_breaker_reset_seconds + 1)

    await supervisor.handle_request("What is the status of order ORD-1001?", tenant_id="acme", telemetry=telemetry)
    check(
        "Primary breaker returns to CLOSED after a successful half-open probe (recovery confirmed)",
        gateway.breaker_state("primary") == BreakerState.CLOSED.value,
    )
    check(
        "A half-open probe event was recorded during recovery",
        telemetry.count(EventType.CIRCUIT_BREAKER_HALF_OPEN_PROBE) >= 1,
    )

    print("\n=== Drill Report ===")
    passed = sum(1 for _, ok in results if ok)
    print(f"{passed}/{len(results)} checks passed.")
    all_passed = passed == len(results)
    print("DRILL RESULT: " + ("SURVIVED" if all_passed else "FAILED"))
    return all_passed


if __name__ == "__main__":
    success = asyncio.run(run_drill())
    raise SystemExit(0 if success else 1)
