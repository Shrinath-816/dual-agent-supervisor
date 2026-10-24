"""
test_supervisor.py
=====================

Unit tests for app/agents/supervisor.py, using FAKE Maker and Checker
agents (duck-typed to the real classes' async interfaces) — this lets
us test the Supervisor's own orchestration logic (fail-closed,
escalation, cache-hit short-circuit, budget/rate-limit blocking)
deterministically, without any real model/network call.
"""

import pytest

from app.agents.checker import CheckerVerdict
from app.agents.maker import MakerStepResult
from app.agents.supervisor import DualAgentSupervisor
from app.budget import BudgetEnforcer
from app.cache.semantic_cache import SemanticCache
from app.rate_limiter import RateLimiterRegistry
from app.security.pii_redactor import PIIRedactor
from app.security.tool_permissions import RiskTier, ToolPermission, ToolPermissionRegistry
from app.telemetry import EventType, TelemetryLog


class _FakeMaker:
    """Returns a pre-configured MakerStepResult, no real model call."""

    def __init__(self, result: MakerStepResult) -> None:
        self._result = result

    async def run(self, user_message: str, telemetry: TelemetryLog) -> MakerStepResult:
        return self._result


class _FakeChecker:
    """Returns a pre-configured CheckerVerdict, or raises, no real model call."""

    def __init__(self, verdict: CheckerVerdict | None = None, raises: Exception | None = None) -> None:
        self._verdict = verdict
        self._raises = raises

    async def review(self, original_request, tool_name, tool_args, telemetry) -> CheckerVerdict:
        if self._raises:
            raise self._raises
        return self._verdict


def _build_supervisor(maker, checker, *, tenant_budget=100_000, request_budget=100_000, rate_capacity=100_000):
    permissions = ToolPermissionRegistry(
        [
            ToolPermission(
                tool_name="issue_refund",
                risk_tier=RiskTier.SIDE_EFFECTING,
                allowed_for_roles=frozenset({"support_agent"}),
            )
        ]
    )
    telemetry = TelemetryLog()
    supervisor = DualAgentSupervisor(
        maker=maker,
        checker=checker,
        permissions=permissions,
        pii_redactor=PIIRedactor(),
        budget_enforcer=BudgetEnforcer(fleet_limit_tokens=1_000_000, telemetry=telemetry),
        rate_limiters=RateLimiterRegistry(capacity=rate_capacity, refill_rate_per_second=0),
        semantic_cache=SemanticCache(similarity_threshold=0.95, ttl_seconds=60),
        max_revision_rounds=2,
        request_token_budget=request_budget,
        tenant_token_budget=tenant_budget,
    )
    return supervisor, telemetry


@pytest.mark.asyncio
async def test_final_answer_passes_through_unchanged() -> None:
    maker = _FakeMaker(MakerStepResult(kind="final_answer", final_text="Your order is delivered.", tokens_used=100))
    supervisor, telemetry = _build_supervisor(maker, _FakeChecker())

    result = await supervisor.handle_request("What's my order status?", tenant_id="acme", telemetry=telemetry)

    assert result.stopped_reason == "completed"
    assert result.reply == "Your order is delivered."


@pytest.mark.asyncio
async def test_approved_side_effecting_action_executes() -> None:
    maker = _FakeMaker(
        MakerStepResult(
            kind="proposed_action",
            proposed_tool_name="issue_refund",
            proposed_tool_args={"order_id": "ORD-1001", "reason": "damaged"},
            tokens_used=50,
        )
    )
    checker = _FakeChecker(verdict=CheckerVerdict(approved=True, reason="Looks legitimate.", tokens_used=30))
    supervisor, telemetry = _build_supervisor(maker, checker)

    result = await supervisor.handle_request("Refund my order please.", tenant_id="acme", telemetry=telemetry)

    assert result.stopped_reason == "completed"
    assert result.tool_executed == "issue_refund"


@pytest.mark.asyncio
async def test_rejected_action_escalates_rather_than_executing() -> None:
    maker = _FakeMaker(
        MakerStepResult(
            kind="proposed_action",
            proposed_tool_name="issue_refund",
            proposed_tool_args={"order_id": "ORD-1001", "reason": "suspicious"},
            tokens_used=50,
        )
    )
    checker = _FakeChecker(
        verdict=CheckerVerdict(approved=False, reason="Reason does not match a real complaint.", tokens_used=30)
    )
    supervisor, telemetry = _build_supervisor(maker, checker)

    result = await supervisor.handle_request("Refund my order please.", tenant_id="acme", telemetry=telemetry)

    assert result.stopped_reason == "escalated"
    assert result.tool_executed is None
    assert telemetry.count(EventType.ESCALATED_TO_HUMAN) == 1


@pytest.mark.asyncio
async def test_checker_failure_fails_closed_not_open() -> None:
    """THE core security property from week-8-part3-notes.md section 13: a checker ERROR must block, never auto-approve."""
    maker = _FakeMaker(
        MakerStepResult(
            kind="proposed_action",
            proposed_tool_name="issue_refund",
            proposed_tool_args={"order_id": "ORD-1001", "reason": "damaged"},
            tokens_used=50,
        )
    )
    checker = _FakeChecker(raises=RuntimeError("checker service unavailable"))
    supervisor, telemetry = _build_supervisor(maker, checker)

    result = await supervisor.handle_request("Refund my order please.", tenant_id="acme", telemetry=telemetry)

    assert result.stopped_reason == "error"
    assert result.tool_executed is None  # THE ACTION MUST NOT HAVE EXECUTED
    assert telemetry.count(EventType.CHECKER_FAILED_CLOSED) == 1


@pytest.mark.asyncio
async def test_rate_limited_request_never_reaches_maker() -> None:
    maker = _FakeMaker(MakerStepResult(kind="final_answer", final_text="should not be reached", tokens_used=0))
    supervisor, telemetry = _build_supervisor(maker, _FakeChecker(), rate_capacity=1)  # effectively zero capacity

    result = await supervisor.handle_request("Hello", tenant_id="acme", telemetry=telemetry)

    assert result.stopped_reason == "rate_limited"
    assert telemetry.count(EventType.RATE_LIMITED) == 1


@pytest.mark.asyncio
async def test_budget_exceeded_request_never_reaches_maker() -> None:
    maker = _FakeMaker(MakerStepResult(kind="final_answer", final_text="should not be reached", tokens_used=0))
    supervisor, telemetry = _build_supervisor(maker, _FakeChecker(), request_budget=1)  # effectively zero budget

    result = await supervisor.handle_request("Hello", tenant_id="acme", telemetry=telemetry)

    assert result.stopped_reason == "budget_exceeded"


@pytest.mark.asyncio
async def test_cache_hit_bypasses_maker_entirely() -> None:
    maker = _FakeMaker(
        MakerStepResult(kind="final_answer", final_text="FROM MAKER (should not be used)", tokens_used=999)
    )
    supervisor, telemetry = _build_supervisor(maker, _FakeChecker())

    # Prime the cache directly through the supervisor's own cache instance.
    supervisor._cache.store("What is the refund policy?", [1.0, 0.0], "Refunds within 30 days (CACHED).")

    result = await supervisor.handle_request(
        "What is the refund policy?", tenant_id="acme", telemetry=telemetry, query_embedding=[1.0, 0.0]
    )

    assert result.stopped_reason == "cache_hit"
    assert result.reply == "Refunds within 30 days (CACHED)."
