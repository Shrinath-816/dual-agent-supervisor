"""
app/telemetry.py
===================

Structured event log for every reliability/security mechanism in this
project — see week-8-part3-notes.md, section 20's core discipline:
"every reliability mechanism should emit its own telemetry when it
activates. A fallback, retry, circuit-breaker trip, or validation
failure that isn't observable is a mechanism you can't tell is
working — or silently failing."

Deliberately a simple in-memory event log (not a full Week 5 tracing
integration) so the mechanisms can be tested and demonstrated
(including by the outage drill) without external infrastructure.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class EventType(str, Enum):
    """Every kind of event this project's mechanisms can emit."""

    PROVIDER_CALL_SUCCEEDED = "provider_call_succeeded"
    PROVIDER_CALL_FAILED = "provider_call_failed"
    CIRCUIT_BREAKER_OPENED = "circuit_breaker_opened"
    CIRCUIT_BREAKER_HALF_OPEN_PROBE = "circuit_breaker_half_open_probe"
    CIRCUIT_BREAKER_CLOSED = "circuit_breaker_closed"
    FALLBACK_TRIGGERED = "fallback_triggered"
    BUDGET_EXCEEDED = "budget_exceeded"
    RATE_LIMITED = "rate_limited"
    INJECTION_FLAGGED = "injection_flagged"
    PII_REDACTED = "pii_redacted"
    TOOL_PERMISSION_DENIED = "tool_permission_denied"
    CACHE_HIT = "cache_hit"
    CACHE_MISS = "cache_miss"
    CACHE_EXCLUDED = "cache_excluded"
    CHECKER_APPROVED = "checker_approved"
    CHECKER_REJECTED = "checker_rejected"
    CHECKER_FAILED_CLOSED = "checker_failed_closed"
    ESCALATED_TO_HUMAN = "escalated_to_human"


@dataclass
class TelemetryEvent:
    """
    One recorded event.

    Attributes:
        event_type: What happened — see EventType.
        details: Free-form structured detail specific to the event.
        timestamp: UTC time the event was recorded.
    """

    event_type: EventType
    details: dict
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TelemetryLog:
    """
    Append-only, in-memory log of TelemetryEvents — queried by the
    outage drill to verify the reliability mechanisms actually
    activated as expected, not just that requests "seemed to work."
    """

    def __init__(self) -> None:
        self._events: list[TelemetryEvent] = []

    def record(self, event_type: EventType, **details) -> TelemetryEvent:
        """Record one event with arbitrary structured detail fields."""
        event = TelemetryEvent(event_type=event_type, details=details)
        self._events.append(event)
        return event

    def events_of_type(self, event_type: EventType) -> list[TelemetryEvent]:
        """Return every recorded event of a given type, in order."""
        return [e for e in self._events if e.event_type == event_type]

    def all_events(self) -> list[TelemetryEvent]:
        """Return every recorded event, in order."""
        return list(self._events)

    def count(self, event_type: EventType) -> int:
        """How many events of this type have been recorded."""
        return len(self.events_of_type(event_type))
