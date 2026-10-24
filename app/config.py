"""
app/config.py
================

Centralized, typed configuration — see Week 1's config.py for the
rationale (fail-fast validation, no secrets in source).

New this project: a PRIORITY-ORDERED LIST of providers (not a single
model), since Part 3's reliability stack requires fallback routing
between providers — this list IS the failover chain the outage drill
(scripts/run_outage_drill.py) exercises.
"""

from dataclasses import dataclass
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class ProviderConfig:
    """
    One LLM provider entry in the Model Gateway's fallback chain — see
    week-8-part3-notes.md, section 16 (Model Routing).

    Attributes:
        name: Human-readable identifier, used in telemetry and the
            circuit breaker's per-provider state key.
        base_url: OpenAI-compatible API base URL.
        model_id: Model slug to request from this provider entry.
        api_key: API key for this provider entry.
    """

    name: str
    base_url: str
    model_id: str
    api_key: str


class Settings(BaseSettings):
    """
    Strongly-typed application settings.

    Attributes:
        openrouter_api_key: Used to construct the default provider chain.
        primary_model: Model slug tried first.
        backup_model: Model slug used on primary failure/circuit-open.
        checker_model: Model slug intended for the CHECKER agent —
            ideally distinct from the maker's model (Part 2 section 8).
        max_revision_rounds: Hard cap on maker-checker rejection rounds.
        max_iterations: Hard cap on the maker's own tool-calling loop.
        request_timeout_seconds: Per-model-call timeout (Part 3 section 9).
        tool_timeout_seconds: Per-tool-call timeout.
        overall_request_timeout_seconds: Wall-clock cap on one whole request.
        request_token_budget: Per-request hard token cap.
        circuit_breaker_failure_threshold: Consecutive failures before
            a provider's circuit opens (Part 3 section 10).
        circuit_breaker_reset_seconds: How long a breaker stays open
            before allowing a half-open probe.
    """

    openrouter_api_key: str
    primary_model: str = "anthropic/claude-sonnet-4.6"
    backup_model: str = "openai/gpt-4o-mini"
    checker_model: str = "openai/gpt-4o-mini"

    max_revision_rounds: int = 2
    max_iterations: int = 6
    request_timeout_seconds: float = 20.0
    tool_timeout_seconds: float = 8.0
    overall_request_timeout_seconds: float = 60.0

    request_token_budget: int = 8000

    circuit_breaker_failure_threshold: int = 3
    circuit_breaker_reset_seconds: float = 30.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def provider_chain(self) -> list[ProviderConfig]:
        """Ordered fallback chain the Model Gateway tries in sequence: primary first, backup second."""
        return [
            ProviderConfig(
                name="primary",
                base_url="https://openrouter.ai/api/v1",
                model_id=self.primary_model,
                api_key=self.openrouter_api_key,
            ),
            ProviderConfig(
                name="backup",
                base_url="https://openrouter.ai/api/v1",
                model_id=self.backup_model,
                api_key=self.openrouter_api_key,
            ),
        ]


@lru_cache
def get_settings() -> Settings:
    """Return a cached, validated Settings singleton for the process."""
    return Settings()
