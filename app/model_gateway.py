"""
app/model_gateway.py
=======================

The Model Gateway — see week-8-part1-notes.md, section 10 (Production
Architecture) and week-8-part3-notes.md, sections 7/10/16: the ONE
place that owns provider abstraction, circuit breaking, retries,
timeouts, and fallback routing, so every caller (the Maker and Checker
agents) gets these properties uniformly.

THIS is the component the outage drill (scripts/run_outage_drill.py)
exercises directly: it simulates the PRIMARY provider failing
repeatedly, and verifies the gateway (a) trips that provider's circuit
breaker, (b) falls back to the backup provider automatically, and (c)
recovers (half-open probe succeeds, breaker closes) once the primary
is simulated as healthy again.
"""

import asyncio

from openai import AsyncOpenAI

from app.circuit_breaker import CircuitBreaker
from app.config import ProviderConfig, Settings
from app.telemetry import EventType, TelemetryLog


class AllProvidersUnavailableError(Exception):
    """Raised when every provider in the chain is either circuit-open or failed on this call."""


class ModelGateway:
    """
    Tries providers in priority order, with per-provider circuit
    breakers, a bounded retry per provider, and a per-call timeout —
    falling back to the next provider on failure or an open breaker
    (week-8-part3-notes.md section 7's fallback strategy).
    """

    def __init__(self, settings: Settings, telemetry: TelemetryLog) -> None:
        """
        Args:
            settings: Provides the provider chain and reliability tuning
                (timeouts, breaker thresholds).
            telemetry: Shared telemetry log — every provider call,
                breaker transition, and fallback is recorded here.
        """
        self._settings = settings
        self._telemetry = telemetry
        self._providers: list[ProviderConfig] = settings.provider_chain()
        self._breakers: dict[str, CircuitBreaker] = {
            provider.name: CircuitBreaker(
                name=provider.name,
                failure_threshold=settings.circuit_breaker_failure_threshold,
                reset_seconds=settings.circuit_breaker_reset_seconds,
                telemetry=telemetry,
            )
            for provider in self._providers
        }
        # Fault-injection hook for testing/drilling ONLY. In real
        # operation this stays empty and providers are genuinely called.
        self._forced_failures: set[str] = set()

    def simulate_provider_down(self, provider_name: str) -> None:
        """
        TEST/DRILL HOOK: force every call to the named provider to fail
        without a real network call — how scripts/run_outage_drill.py
        simulates an outage deterministically and repeatably.
        """
        self._forced_failures.add(provider_name)

    def simulate_provider_recovered(self, provider_name: str) -> None:
        """TEST/DRILL HOOK: stop forcing failures for the named provider."""
        self._forced_failures.discard(provider_name)

    async def complete(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> tuple[str, dict, object]:
        """
        Call the provider chain in priority order.

        Args:
            messages: OpenAI-compatible message list.
            tools: Optional OpenAI-compatible tool schema list.

        Returns:
            (provider_name_that_succeeded, usage_dict, raw_response).

        Raises:
            AllProvidersUnavailableError: If every provider's breaker is
                open or every attempted call failed — callers must
                treat this as a hard failure, not assume a response exists.
        """
        last_exception: Exception | None = None

        for index, provider in enumerate(self._providers):
            breaker = self._breakers[provider.name]

            if not breaker.allow_request():
                # OPEN breaker, reset window not elapsed: skip WITHOUT a
                # network call — the point of a breaker is to stop
                # hammering a known-bad dependency.
                continue

            if index > 0:
                self._telemetry.record(
                    EventType.FALLBACK_TRIGGERED,
                    from_provider=self._providers[0].name,
                    to_provider=provider.name,
                )

            try:
                usage, response = await self._call_provider(provider, messages, tools)
                breaker.record_success()
                self._telemetry.record(EventType.PROVIDER_CALL_SUCCEEDED, provider=provider.name)
                return provider.name, usage, response
            except Exception as exc:  # noqa: BLE001 - any failure triggers fallback
                breaker.record_failure()
                self._telemetry.record(EventType.PROVIDER_CALL_FAILED, provider=provider.name, error=str(exc))
                last_exception = exc
                continue

        raise AllProvidersUnavailableError(
            f"Every provider in the chain failed or is circuit-open. Last error: {last_exception}"
        )

    async def _call_provider(
        self, provider: ProviderConfig, messages: list[dict], tools: list[dict] | None
    ) -> tuple[dict, object]:
        """
        One timed, single-retry attempt against a single provider.

        Raises:
            Exception: Any underlying failure after the retry is
                exhausted — caught by complete(), which records it and
                moves to the next provider.
        """
        if provider.name in self._forced_failures:
            raise RuntimeError(f"[drill] Simulated outage for provider '{provider.name}'")

        client = AsyncOpenAI(base_url=provider.base_url, api_key=provider.api_key)
        kwargs: dict = {"model": provider.model_id, "messages": messages}
        if tools:
            kwargs["tools"] = tools

        # Step-granular retry (week-8-part3-notes.md section 8): retries
        # THIS one provider call only, never the whole maker-checker exchange.
        last_exception: Exception | None = None
        for attempt in range(2):
            try:
                response = await asyncio.wait_for(
                    client.chat.completions.create(**kwargs),
                    timeout=self._settings.request_timeout_seconds,
                )
                usage = {"prompt_tokens": 0, "completion_tokens": 0}
                if response.usage:
                    usage = {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                    }
                return usage, response
            except Exception as exc:  # noqa: BLE001
                last_exception = exc
                if attempt == 0:
                    await asyncio.sleep(0.5)  # brief backoff before the single retry
                continue

        assert last_exception is not None
        raise last_exception

    def breaker_state(self, provider_name: str) -> str:
        """A provider's current breaker state, for the drill report / tests."""
        return self._breakers[provider_name].state.value
