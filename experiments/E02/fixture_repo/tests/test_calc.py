import pytest
from pkg.calc import add, divide


def test_add() -> None:
    assert add(2, 3) == 5


def test_divide_by_zero_raises_value_error() -> None:
    # Deliberately broad. The agent under capture chooses the message, and pinning it
    # would grade the agent's prose rather than the surfaces this experiment measures.
    with pytest.raises(ValueError):  # noqa: PT011
        divide(1, 0)
