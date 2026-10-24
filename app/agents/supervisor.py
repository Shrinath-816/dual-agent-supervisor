"""
app/agents/supervisor.py
===========================

THE Dual-Agent Supervisor — the orchestrator wiring together every
mechanism this project builds:

1. Rate limiting + token budget checks BEFORE calling the Maker at all
   (week-8-part3-notes.md sections 12, 18).
2. Semantic cache lookup (section 14) — skips the Maker entirely on a hit.
3. Runs the Maker — may return a final answer OR a proposed
   side-effecting action.
4. A proposed side-effecting action is routed to the Checker for
   INDEPENDENT review, with a hard max_revision_rounds cap
   (Part 2 section 8) and FAIL-CLOSED behavior if the Checker errors
   (Part 3 section 13: "must fail closed, never fail open").
5. On rejection (or rounds exhausted): escalates to a human rather
   than looping or executing unapproved.
6. PII redaction (section 5) before the input is used as a cache key.

The single component the outage drill calls directly — the one place
all of the above compose into one observable request lifecycle.
"""

from dataclasses import dataclass

from app.agents.checker import CheckerAgent
from app.agents.maker import MakerAgent
from app.budget import BudgetEnforcer, BudgetExceededError, TokenBudgetTracker
from app.cache.semantic_cache import SemanticCache
from app.rate_limiter import RateLimiterRegistry
from app.security.pii_redactor import PIIRedactor
from app.security.tool_permissions import ToolPermissionRegistry
from app.telemetry import EventType, TelemetryLog
from app.tools import EXECUTABLE_TOOLS

# A fixed, conservative per-call token estimate for PRE-call budget/rate
# checks (both need an ESTIMATE; actual usage is only known after).
_ESTIMATED_TOKENS_PER_CALL = 600


@dataclass
class SupervisorResult:
    """
    Attributes:
        reply: The final text shown to the user.
        stopped_reason: "completed" | "cache_hit" | "escalated" |
            "budget_exceeded" | "rate_limited" | "error" — always
            populated, never implicit ("degrade gracefully, never silently").
        tool_executed: Name of the tool actually executed, if any.
        tokens_used: Total tokens consumed (Maker + Checker rounds).
    """

    reply: str
    stopped_reason: str
    tool_executed: str | None = None
    tokens_used: int = 0


class DualAgentSupervisor:
    """Orchestrates the Maker, Checker, and every reliability/security mechanism for one request."""

    def __init__(
        self,
        maker: MakerAgent,
        checker: CheckerAgent,
        permissions: ToolPermissionRegistry,
        pii_redactor: PIIRedactor,
        budget_enforcer: BudgetEnforcer,
        rate_limiters: RateLimiterRegistry,
        semantic_cache: SemanticCache,
        max_revision_rounds: int,
        request_token_budget: int,
        tenant_token_budget: int,
    ) -> None:
        self._maker = maker
        self._checker = checker
        self._permissions = permissions
        self._pii_redactor = pii_redactor
        self._budget_enforcer = budget_enforcer
        self._rate_limiters = rate_limiters
        self._cache = semantic_cache
        self._max_revision_rounds = max_revision_rounds
        self._request_token_budget = request_token_budget
        self._tenant_token_budget = tenant_token_budget

    async def handle_request(
        self,
        user_message: str,
        tenant_id: str,
        telemetry: TelemetryLog,
        query_embedding: list[float] | None = None,
    ) -> SupervisorResult:
        """
        Handle one end-to-end request.

        Args:
            user_message: The raw user request.
            tenant_id: Tenant for per-tenant rate limiting/budgets (isolation).
            telemetry: Shared telemetry log for this request.
            query_embedding: Precomputed embedding for the semantic
                cache, or None to skip caching entirely.

        Returns:
            A SupervisorResult — see its docstring for every stopped_reason.
        """
        redaction = self._pii_redactor.redact(user_message, telemetry, boundary="model_input")
        safe_message_for_cache_key = redaction.redacted_text

        rate_limiter = self._rate_limiters.for_tenant(tenant_id)
        if not rate_limiter.try_consume(telemetry, _ESTIMATED_TOKENS_PER_CALL, scope_name=f"tenant:{tenant_id}"):
            return SupervisorResult(
                reply="Too many requests right now — please try again shortly.", stopped_reason="rate_limited"
            )

        request_tracker = TokenBudgetTracker(scope_name="request", limit_tokens=self._request_token_budget)
        try:
            self._budget_enforcer.check_all_scopes(
                request_tracker, tenant_id, self._tenant_token_budget, _ESTIMATED_TOKENS_PER_CALL
            )
        except BudgetExceededError:
            return SupervisorResult(
                reply="This request would exceed the current cost budget.", stopped_reason="budget_exceeded"
            )

        if query_embedding is not None:
            cached = self._cache.lookup(safe_message_for_cache_key, query_embedding, telemetry)
            if cached is not None:
                return SupervisorResult(reply=cached, stopped_reason="cache_hit")

        total_tokens = 0
        maker_result = await self._maker.run(user_message, telemetry)
        total_tokens += maker_result.tokens_used

        if maker_result.kind == "error":
            return SupervisorResult(
                reply=f"Unable to complete this request: {maker_result.error_message}",
                stopped_reason="error",
                tokens_used=total_tokens,
            )

        if maker_result.kind == "final_answer":
            reply = maker_result.final_text or ""
            self._budget_enforcer.record_actual_usage(request_tracker, tenant_id, total_tokens)
            if query_embedding is not None:
                self._cache.store(safe_message_for_cache_key, query_embedding, reply)
            return SupervisorResult(reply=reply, stopped_reason="completed", tokens_used=total_tokens)

        # kind == "proposed_action": route through the Checker with a hard round cap.
        tool_name = maker_result.proposed_tool_name
        tool_args = maker_result.proposed_tool_args or {}

        for _round in range(self._max_revision_rounds):
            try:
                verdict = await self._checker.review(user_message, tool_name, tool_args, telemetry)
            except Exception as exc:  # noqa: BLE001
                # FAIL CLOSED: the action does NOT execute if the Checker is unavailable.
                telemetry.record(EventType.CHECKER_FAILED_CLOSED, error=str(exc))
                return SupervisorResult(
                    reply="This action requires review, and the review service is currently unavailable. "
                    "Your request has not been executed.",
                    stopped_reason="error",
                    tokens_used=total_tokens,
                )

            total_tokens += verdict.tokens_used

            if verdict.approved:
                result_text = EXECUTABLE_TOOLS[tool_name](**tool_args)
                self._budget_enforcer.record_actual_usage(request_tracker, tenant_id, total_tokens)
                return SupervisorResult(
                    reply=str(result_text),
                    stopped_reason="completed",
                    tool_executed=tool_name,
                    tokens_used=total_tokens,
                )

            # Rejected: in this simplified loop a rejection goes straight to
            # escalation rather than an automated revise-and-resubmit (see
            # README.md's known limitations).
            break

        telemetry.record(EventType.ESCALATED_TO_HUMAN, tool=tool_name, reason="checker_rejected_or_rounds_exhausted")
        self._budget_enforcer.record_actual_usage(request_tracker, tenant_id, total_tokens)
        return SupervisorResult(
            reply="This action needs human approval before it can proceed. It has been escalated.",
            stopped_reason="escalated",
            tokens_used=total_tokens,
        )
