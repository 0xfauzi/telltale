"""One passing test and one failing test. The failing one is the fixture's point."""

from __future__ import annotations

import pytest
from pkg.calc import add, divide


def test_add_sums_two_numbers() -> None:
    assert add(2, 3) == 5


def test_divide_by_zero_raises_value_error() -> None:
    with pytest.raises(ValueError, match="divide by zero"):
        divide(1, 0)
