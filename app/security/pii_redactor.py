"""
app/security/pii_redactor.py
===============================

PII detection and redaction — see week-8-part3-notes.md, section 5:
PII handling in agentic systems is a cross-cutting policy enforced at
EVERY boundary, not a single code review — and often PII doesn't need
to reach the model at all.

Implements the pattern/regex layer from section 5's table (fast,
cheap, catches STRUCTURED PII). An NER or LLM-based layer would be a
natural addition for unstructured PII (a bare name in prose).
"""

import re
from dataclasses import dataclass

from app.telemetry import EventType, TelemetryLog

# Structured PII only; unstructured PII (names in prose) is NOT caught here.
_PATTERNS: dict[str, re.Pattern] = {
    "email": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "phone": re.compile(r"\b(\+?\d{1,2}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
}


@dataclass
class RedactionResult:
    """
    Result of redacting one piece of text.

    Attributes:
        redacted_text: Text with each detected PII span replaced by
            a `[REDACTED_<TYPE>]` placeholder.
        redaction_counts: How many spans of each type were redacted —
            used for telemetry WITHOUT ever logging the PII values.
    """

    redacted_text: str
    redaction_counts: dict[str, int]


class PIIRedactor:
    """Detects and redacts structured PII from text at any boundary."""

    def redact(self, text: str, telemetry: TelemetryLog, boundary: str) -> RedactionResult:
        """
        Args:
            text: Text to scan and redact.
            telemetry: Records a PII_REDACTED event if anything was found.
            boundary: Which boundary this redaction protects
                ("model_input", "memory_write", "trace_log", "output") —
                EVERY boundary needs its own explicit call, since PII
                can flow through any of them independently.

        Returns:
            A RedactionResult (unchanged text with empty counts if no PII).
        """
        redacted_text = text
        counts: dict[str, int] = {}

        for pii_type, pattern in _PATTERNS.items():
            match_count = len(pattern.findall(redacted_text))
            if match_count:
                counts[pii_type] = match_count
                redacted_text = pattern.sub(f"[REDACTED_{pii_type.upper()}]", redacted_text)

        if counts:
            telemetry.record(EventType.PII_REDACTED, boundary=boundary, counts=counts)

        return RedactionResult(redacted_text=redacted_text, redaction_counts=counts)
