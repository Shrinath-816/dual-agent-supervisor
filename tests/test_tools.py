"""
test_tools.py
================

Unit tests for app/tools.py's backend logic — independent of any
model/agent concerns, same pattern as prior weeks' tools tests.
"""

from app.tools import calculate, get_order_status, issue_refund


def test_calculate_basic_arithmetic() -> None:
    assert calculate("2 * (3 + 4)") == "14"


def test_calculate_rejects_unsafe_expression() -> None:
    result = calculate("__import__('os').system('echo hi')")
    assert "Could not evaluate" in result


def test_get_order_status_known_order() -> None:
    result = get_order_status("ORD-1001")
    assert "delivered" in result


def test_get_order_status_unknown_order() -> None:
    assert "No order found" in get_order_status("ORD-9999")


def test_issue_refund_then_reject_second_attempt() -> None:
    first = issue_refund("ORD-1002", "damaged")
    assert "Refund" in first
    second = issue_refund("ORD-1002", "again")
    assert "already been refunded" in second
