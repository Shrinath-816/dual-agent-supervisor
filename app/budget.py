"""
app/budget.py
================

Multi-scope token budget enforcement — see week-8-part3-notes.md,
section 18: budgets exist at multiple scopes simultaneously
(per-request, per-tenant, fleet-wide), and MUST be checked BEFORE the
next model call, not after — a check that only fires after the
over-budget call already happened has already spent the money it was
meant to prevent spending.
"""

from dataclasses import dataclass, field

from app.telemetry import EventType, TelemetryLog


class BudgetExceededError(Exception):
    """Raised when a budget check fails — the caller must stop BEFORE the next model call."""


@dataclass
class TokenBudgetTracker:
    """
    Tracks cumulative token usage against a hard cap at ONE scope.

    Attributes:
        scope_name: Identifies this tracker in telemetry
            (e.g. "request", "tenant:acme-corp", "fleet").
        limit_tokens: The hard cap for this scope.
    """

    scope_name: str
    limit_tokens: int
    _used_tokens: int = field(default=0, init=False)

    def check_and_reserve(self, telemetry: TelemetryLog, estimated_tokens: int) -> None:
        """
        Check whether spending `estimated_tokens` more would exceed
        this scope's budget, BEFORE the call is made.

        Args:
            telemetry: Records a BUDGET_EXCEEDED event on failure.
            estimated_tokens: A conservative pre-call estimate — actual
                usage isn't known until after the call, so enforcement
                before spending must work from an estimate.

        Raises:
            BudgetExceededError: If this would exceed the scope's limit.
        """
        if self._used_tokens + estimated_tokens > self.limit_tokens:
            telemetry.record(
                EventType.BUDGET_EXCEEDED,
                scope=self.scope_name,
                used_tokens=self._used_tokens,
                limit_tokens=self.limit_tokens,
            )
            raise BudgetExceededError(
                f"Budget exceeded for scope '{self.scope_name}': "
                f"{self._used_tokens} used + {estimated_tokens} estimated > {self.limit_tokens} limit"
            )

    def record_actual_usage(self, tokens_used: int) -> None:
        """Record the ACTUAL tokens consumed by a completed call."""
        self._used_tokens += tokens_used

    @property
    def used_tokens(self) -> int:
        """Cumulative tokens recorded against this scope so far."""
        return self._used_tokens


class BudgetEnforcer:
    """
    Composes request, tenant, and fleet-wide TokenBudgetTracker scopes
    so one check validates ALL of them (week-8-part3-notes.md section 18).
    """

    def __init__(self, fleet_limit_tokens: int, telemetry: TelemetryLog) -> None:
        """
        Args:
            fleet_limit_tokens: The last-resort global spend cap that
                catches a systemic bug per-request caps alone wouldn't
                catch fast enough.
            telemetry: Shared telemetry log.
        """
        self._telemetry = telemetry
        self._fleet_tracker = TokenBudgetTracker(scope_name="fleet", limit_tokens=fleet_limit_tokens)
        self._tenant_trackers: dict[str, TokenBudgetTracker] = {}

    def get_or_create_tenant_tracker(self, tenant_id: str, limit_tokens: int) -> TokenBudgetTracker:
        """Return this tenant's tracker, creating it with the given limit if new."""
        if tenant_id not in self._tenant_trackers:
            self._tenant_trackers[tenant_id] = TokenBudgetTracker(
                scope_name=f"tenant:{tenant_id}", limit_tokens=limit_tokens
            )
        return self._tenant_trackers[tenant_id]

    def check_all_scopes(
        self, request_tracker: TokenBudgetTracker, tenant_id: str, tenant_limit: int, estimated_tokens: int
    ) -> None:
        """
        Check request, tenant, AND fleet budgets together — ALL must pass.

        Raises:
            BudgetExceededError: If any of the three scopes would be exceeded.
        """
        request_tracker.check_and_reserve(self._telemetry, estimated_tokens)
        self.get_or_create_tenant_tracker(tenant_id, tenant_limit).check_and_reserve(
            self._telemetry, estimated_tokens
        )
        self._fleet_tracker.check_and_reserve(self._telemetry, estimated_tokens)

    def record_actual_usage(self, request_tracker: TokenBudgetTracker, tenant_id: str, tokens_used: int) -> None:
        """Record actual usage against all three scopes after a call completes."""
        request_tracker.record_actual_usage(tokens_used)
        if tenant_id in self._tenant_trackers:
            self._tenant_trackers[tenant_id].record_actual_usage(tokens_used)
        self._fleet_tracker.record_actual_usage(tokens_used)
