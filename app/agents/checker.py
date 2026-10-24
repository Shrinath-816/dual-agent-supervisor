"""
app/agents/checker.py
========================

The CHECKER agent — see week-8-part2-notes.md, section 8, for the
independence requirements this class exists to satisfy: the Checker is
given ONLY the original request and the specific proposed action,
evaluated against fixed, specific criteria — never the Maker's
reasoning trace, which could bias it toward agreeing.
"""

import json
from dataclasses import dataclass

from app.model_gateway import ModelGateway
from app.telemetry import EventType, TelemetryLog

_CHECKER_SYSTEM_PROMPT = """\
You are an independent reviewer for a customer support system. You will be shown a
user's original request and a PROPOSED action another agent wants to take. You did NOT
see that agent's reasoning — evaluate the proposed action ONLY against these criteria:

1. Does the proposed action directly and reasonably address the user's stated request?
2. Is the action within normal customer-support policy (e.g. a refund reason should be
   plausible and tied to an actual complaint, not fabricated or excessive)?
3. Does the action target the correct, specific resource the user referred to (e.g. the
   correct order ID), not a different or unspecified one?

Respond with ONLY a JSON object: {"approved": true|false, "reason": "<one sentence>"}
"""


@dataclass
class CheckerVerdict:
    """
    Attributes:
        approved: Whether the Checker approved the proposed action.
        reason: One-sentence justification — always populated, even on
            approval, so approvals are auditable too.
        tokens_used: Tokens consumed by this review call.
    """

    approved: bool
    reason: str
    tokens_used: int = 0


class CheckerAgent:
    """Independently reviews a Maker's proposed side-effecting action before it may execute."""

    def __init__(self, gateway: ModelGateway) -> None:
        """
        Args:
            gateway: A ModelGateway. Production wiring should give the
                Checker its OWN gateway/provider chain pointed at a
                distinct model (settings.checker_model) for full model-level independence.
        """
        self._gateway = gateway

    async def review(
        self, original_request: str, tool_name: str, tool_args: dict, telemetry: TelemetryLog
    ) -> CheckerVerdict:
        """
        Args:
            original_request: The user's ORIGINAL request (not the Maker's reasoning).
            tool_name: The proposed tool call's name.
            tool_args: The proposed tool call's arguments.
            telemetry: Records CHECKER_APPROVED / CHECKER_REJECTED.

        Returns:
            A CheckerVerdict.

        Raises:
            Exception: Any ModelGateway failure (e.g.
                AllProvidersUnavailableError) propagates UNCAUGHT —
                deliberately. The Supervisor treats a Checker failure
                as FAIL-CLOSED (week-8-part3-notes.md section 13);
                swallowing it here would make failing open too easy.
        """
        prompt = (
            f"User's original request: {original_request}\n\n"
            f"Proposed action: call `{tool_name}` with arguments {json.dumps(tool_args)}"
        )
        messages = [
            {"role": "system", "content": _CHECKER_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        _provider, usage, response = await self._gateway.complete(messages)
        tokens_used = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
        raw_content = response.choices[0].message.content or ""

        try:
            parsed = json.loads(raw_content)
            approved = bool(parsed["approved"])
            reason = str(parsed["reason"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            # A malformed verdict is a REJECTION, never an approval —
            # fail-closed applies here too.
            approved = False
            reason = f"Checker response could not be parsed; treated as rejection: {raw_content[:200]!r}"

        telemetry.record(
            EventType.CHECKER_APPROVED if approved else EventType.CHECKER_REJECTED,
            tool=tool_name,
            reason=reason,
        )
        return CheckerVerdict(approved=approved, reason=reason, tokens_used=tokens_used)
