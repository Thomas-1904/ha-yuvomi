"""Cleaning-plan to-do: description parsing."""

import pytest

from custom_components.yuvomi.todo import YuvomiHousekeepingTodo


@pytest.mark.parametrize(
    ("text", "result"),
    [
        (None, (None, None)),
        ("Bad · 7", ("Bad", 7)),
        ("Bathroom · every 14 days", ("Bathroom", 14)),
        ("Küche · alle 3 Tage", ("Küche", 3)),
        ("10", (None, 10)),
        ("Flur", ("Flur", None)),
    ],
)
def test_parse_description(text, result):
    assert YuvomiHousekeepingTodo.parse_description(text) == result
