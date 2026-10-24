"""
app/security/injection_guard.py
==================================

Input guardrail for prompt injection detection — see
week-8-part3-notes.md, section 2: you cannot "solve" prompt injection
at the model level; you defend in depth, and the goal shifts from
"prevent injection" to "bound the damage when injection succeeds."

This module is ONE LAYER of that defense (a cheap, fast, pre-model
heuristic classifier) — deliberately NOT claimed to be a complete
solution. It's paired with least-privilege tool scoping
(tool_permissions.py, bounds the damage) and instruction-hierarchy
prompting + delimiting (applied in app/agents/maker.py).
"""

import re
from dataclasses import dataclass

from app.telemetry import EventType, TelemetryLog

# Heuristic patterns common to direct and indirect injection attempts
# (week-8-part3-notes.md sections 2-3). NOT exhaustive — no
# pattern-based approach can be; this is the cheap first layer.
_SUSPICIOUS_PATTERNS = [
    r"ignore (all |the )?(previous|prior|above) instructions",
    r"disregard (all |the )?(previous|prior|above) instructions",
    r"you are now in (debug|developer|admin) mode",
    r"system\s*override",
    r"reveal (your|the) (system prompt|instructions)",
    r"print (your|the) (system prompt|full instructions)",
    r"act as if you (have no|had no) (restrictions|rules|guidelines)",
    r"new instructions?:",
]

_COMPILED_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in _SUSPICIOUS_PATTERNS]


@dataclass
class InjectionCheckResult:
    """
    Result of checking one piece of text for injection-like content.

    Attributes:
        is_flagged: True if one or more suspicious patterns matched.
        matched_patterns: Which pattern(s) matched — for telemetry/audit
            only, NOT for showing to the model or end user (narrating
            detection mechanics to the party being checked teaches evasion).
    """

    is_flagged: bool
    matched_patterns: list[str]


class InjectionGuard:
    """Scans text for known injection-attempt patterns before it reaches the model."""

    def check(self, text: str, telemetry: TelemetryLog, source: str) -> InjectionCheckResult:
        """
        Args:
            text: Text to check — called on BOTH direct user input and
                any retrieved/tool-result content (section 3: treat ALL
                retrieved content as untrusted data).
            telemetry: Records an INJECTION_FLAGGED event on a match.
            source: Where the text came from ("user_input",
                "tool_result", ...) so indirect-injection attempts are
                distinguishable from direct ones in telemetry.

        Returns:
            An InjectionCheckResult. A flagged result does NOT
            hard-block the request here — the Maker still applies
            instruction-hierarchy prompting, and side-effecting actions
            always go through the Checker regardless: defense in depth
            rather than a single block a clever injection might evade.
        """
        matched = [pattern.pattern for pattern in _COMPILED_PATTERNS if pattern.search(text)]

        if matched:
            telemetry.record(EventType.INJECTION_FLAGGED, source=source, pattern_count=len(matched))

        return InjectionCheckResult(is_flagged=bool(matched), matched_patterns=matched)

    @staticmethod
    def delimit_untrusted_content(content: str, label: str) -> str:
        """
        Wrap untrusted content in explicit structural delimiters before
        it enters a prompt (section 2's "delimiting user input"
        defense) — a stronger signal about what is DATA, not a hard boundary.

        Args:
            content: The untrusted text.
            label: Short source tag (e.g. "user_input", "tool_result:get_order_status").
        """
        return f"<untrusted_{label}>\n{content}\n</untrusted_{label}>"
