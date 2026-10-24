"""
app/main.py
==============

FastAPI entry point for The Dual-Agent Supervisor.

Endpoints:
    POST /chat           - handle one request through the full supervisor
    GET  /health         - liveness check
    GET  /telemetry      - counts of every recorded reliability/security event
    GET  /breaker-status - current circuit breaker state per provider

Run locally with:
    uvicorn app.main:app --reload
"""

from fastapi import FastAPI

from app.agents.checker import CheckerAgent
from app.agents.maker import MakerAgent
from app.agents.supervisor import DualAgentSupervisor
from app.budget import BudgetEnforcer
from app.cache.semantic_cache import SemanticCache
from app.config import get_settings
from app.model_gateway import ModelGateway
from app.rate_limiter import RateLimiterRegistry
from app.security.injection_guard import InjectionGuard
from app.security.pii_redactor import PIIRedactor
from app.security.tool_permissions import ToolPermissionRegistry
from app.telemetry import EventType, TelemetryLog
from app.tools import DEFAULT_TOOL_PERMISSIONS

app = FastAPI(
    title="The Dual-Agent Supervisor",
    description="Week 8 project: a secure, scalable dual-agent supervisor surviving a live provider-outage drill.",
    version="0.1.0",
)

_settings = get_settings()
_telemetry = TelemetryLog()
_gateway = ModelGateway(_settings, _telemetry)
_permissions = ToolPermissionRegistry(DEFAULT_TOOL_PERMISSIONS)
_injection_guard = InjectionGuard()
_pii_redactor = PIIRedactor()
_budget_enforcer = BudgetEnforcer(fleet_limit_tokens=_settings.request_token_budget * 1000, telemetry=_telemetry)
_rate_limiters = RateLimiterRegistry(capacity=5000, refill_rate_per_second=200)
_cache = SemanticCache(similarity_threshold=0.95, ttl_seconds=3600)

_maker = MakerAgent(_gateway, _permissions, _injection_guard, max_iterations=_settings.max_iterations)
# NOTE: the Checker shares the Maker's gateway/provider chain in this simple wiring.
# Production should give the Checker its OWN ModelGateway pointed at
# settings.checker_model for full model-level independence (Part 2 section 8).
_checker = CheckerAgent(_gateway)

_supervisor = DualAgentSupervisor(
    maker=_maker,
    checker=_checker,
    permissions=_permissions,
    pii_redactor=_pii_redactor,
    budget_enforcer=_budget_enforcer,
    rate_limiters=_rate_limiters,
    semantic_cache=_cache,
    max_revision_rounds=_settings.max_revision_rounds,
    request_token_budget=_settings.request_token_budget,
    tenant_token_budget=_settings.request_token_budget * 20,
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Basic liveness check."""
    return {"status": "ok"}


@app.get("/breaker-status")
async def breaker_status() -> dict[str, str]:
    """Current circuit breaker state for every configured provider."""
    return {provider.name: _gateway.breaker_state(provider.name) for provider in _settings.provider_chain()}


@app.get("/telemetry")
async def telemetry_summary() -> dict[str, int]:
    """Count of every recorded event type so far in this process."""
    return {event_type.value: _telemetry.count(event_type) for event_type in EventType}


@app.post("/chat")
async def chat(payload: dict) -> dict:
    """
    Handle one chat request.

    Body: {"message": "...", "tenant_id": "..."}
    """
    result = await _supervisor.handle_request(
        user_message=payload["message"],
        tenant_id=payload.get("tenant_id", "default"),
        telemetry=_telemetry,
    )
    return {
        "reply": result.reply,
        "stopped_reason": result.stopped_reason,
        "tool_executed": result.tool_executed,
        "tokens_used": result.tokens_used,
    }
