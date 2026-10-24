"""
app/security/tool_permissions.py
===================================

Least-privilege tool permission scoping — see week-8-part3-notes.md,
section 4: an agent's effective permission is the union of its tools'
permissions; the model's "intent" is irrelevant to that ceiling.

The concrete enforcement point: every tool has a declared RiskTier,
and the Maker/Supervisor use it to decide which tier requires Checker
approval (Part 2's maker-checker) before executing — "tiered approval
for high-risk actions" from section 4's table, made real.
"""

from dataclasses import dataclass
from enum import Enum

from app.telemetry import EventType, TelemetryLog


class RiskTier(str, Enum):
    """
    How much scrutiny a tool's use requires before execution.

    Attributes:
        READ_ONLY: No side effects — may execute without Checker review.
        SIDE_EFFECTING: A real, potentially hard-to-reverse effect —
            MUST go through the Checker before executing.
    """

    READ_ONLY = "read_only"
    SIDE_EFFECTING = "side_effecting"


@dataclass(frozen=True)
class ToolPermission:
    """
    A tool's declared permission profile — its "effective permission ceiling".

    Attributes:
        tool_name: The tool this profile describes.
        risk_tier: See RiskTier.
        allowed_for_roles: Caller roles allowed to invoke this tool at
            all — an ALLOW-list, not a deny-list (section 4's guidance).
    """

    tool_name: str
    risk_tier: RiskTier
    allowed_for_roles: frozenset[str]


class ToolPermissionRegistry:
    """Central registry of every tool's ToolPermission — the single source of truth for tool access."""

    def __init__(self, permissions: list[ToolPermission]) -> None:
        self._permissions: dict[str, ToolPermission] = {p.tool_name: p for p in permissions}

    def check(self, tool_name: str, caller_role: str, telemetry: TelemetryLog) -> ToolPermission:
        """
        Check whether `caller_role` may invoke `tool_name` at all.

        Args:
            tool_name: The tool being requested.
            caller_role: The role of the agent attempting to call it.
            telemetry: Records a TOOL_PERMISSION_DENIED event on denial.

        Returns:
            The tool's ToolPermission, if allowed.

        Raises:
            PermissionError: If the tool is unknown or the role isn't in
                its allow-list — a hard failure, not a warning: a check
                that only warns isn't enforcing least privilege.
        """
        permission = self._permissions.get(tool_name)
        if permission is None or caller_role not in permission.allowed_for_roles:
            telemetry.record(EventType.TOOL_PERMISSION_DENIED, tool=tool_name, role=caller_role)
            raise PermissionError(f"Role '{caller_role}' is not permitted to call tool '{tool_name}'.")
        return permission

    def requires_checker_approval(self, tool_name: str) -> bool:
        """Whether this tool's risk tier requires Checker review before executing."""
        permission = self._permissions.get(tool_name)
        return permission is not None and permission.risk_tier == RiskTier.SIDE_EFFECTING
