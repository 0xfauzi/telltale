"""Two arithmetic helpers. divide carries the bug this fixture exists to show."""

from __future__ import annotations


def add(left: float, right: float) -> float:
    """Return the sum of two numbers."""
    return left + right


def divide(left: float, right: float) -> float:
    """Return left divided by right.

    The zero case is unguarded on purpose. tests/test_calc.py asserts a
    ValueError that this code does not raise, so a fresh copy of this fixture
    has exactly one failing test for a scenario to fix.
    """
    return left / right
