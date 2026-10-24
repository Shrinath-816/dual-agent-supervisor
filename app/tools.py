"""
app/tools.py
==============

Example tools for the Dual-Agent Supervisor, each with a declared
RiskTier registered in app/security/tool_permissions.py — see
week-4-notes.md for the tool-calling mechanism, and
week-8-part3-notes.md section 4 for why the risk-tier declaration
matters more here than the implementation itself.
"""

from app.security.tool_permissions import RiskTier, ToolPermission

# Simulated backend (same pattern as Weeks 4/6).
_ORDERS: dict[str, dict] = {
    "ORD-1001": {"status": "delivered", "total": 89.99, "refunded": False},
    "ORD-1002": {"status": "processing", "total": 34.50, "refunded": False},
}


def get_order_status(order_id: str) -> str:
    """READ-ONLY tool: look up an order's status. No side effects."""
    order = _ORDERS.get(order_id)
    if order is None:
        return f"No order found with ID '{order_id}'."
    return f"Order {order_id}: status={order['status']}, total=${order['total']:.2f}, refunded={order['refunded']}"


def issue_refund(order_id: str, reason: str) -> str:
    """SIDE-EFFECTING tool: issues a real refund. MUST go through Checker approval (see supervisor.py)."""
    order = _ORDERS.get(order_id)
    if order is None:
        return f"Cannot issue refund: no order found with ID '{order_id}'."
    if order["refunded"]:
        return f"Order {order_id} has already been refunded."
    order["refunded"] = True
    return f"Refund of ${order['total']:.2f} issued for order {order_id}. Reason: {reason}"


def calculate(expression: str) -> str:
    """READ-ONLY tool: safe arithmetic evaluation (AST-restricted, no eval())."""
    import ast
    import operator as op

    allowed = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.USub: op.neg}

    def _eval(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in allowed:
            return allowed[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in allowed:
            return allowed[type(node.op)](_eval(node.operand))
        raise ValueError("unsupported expression")

    try:
        return str(_eval(ast.parse(expression, mode="eval").body))
    except Exception as exc:
        return f"Could not evaluate '{expression}': {exc}"


EXECUTABLE_TOOLS = {
    "get_order_status": get_order_status,
    "issue_refund": issue_refund,
    "calculate": calculate,
}

# Permission declarations consumed by ToolPermissionRegistry.
# "support_agent" is the only role used by the Maker in this project; a
# real deployment would have multiple roles with different tool subsets
# (least privilege — week-8-part3-notes.md section 4).
DEFAULT_TOOL_PERMISSIONS = [
    ToolPermission(
        tool_name="get_order_status", risk_tier=RiskTier.READ_ONLY, allowed_for_roles=frozenset({"support_agent"})
    ),
    ToolPermission(
        tool_name="issue_refund", risk_tier=RiskTier.SIDE_EFFECTING, allowed_for_roles=frozenset({"support_agent"})
    ),
    ToolPermission(
        tool_name="calculate", risk_tier=RiskTier.READ_ONLY, allowed_for_roles=frozenset({"support_agent"})
    ),
]
