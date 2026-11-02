"""Version check and recurrence text."""

import pytest

from custom_components.yuvomi.util import check_compat, rrule_text


@pytest.mark.parametrize(
    ("info", "verdict"),
    [
        ({"version": "2.84.0", "client_api": 1}, "ok"),
        ({"version": "2.84.0", "client_api": "1"}, "ok"),
        ({"version": "3.0.0", "client_api": 2}, "newer_api"),
        ({"version": "2.84.0", "client_api": 0}, "too_old"),
        ({"version": "2.79.0"}, "legacy"),
        ({"version": "2.80.3-beta"}, "legacy"),
        ({"version": "2.78.9"}, "too_old"),
        ({}, "legacy"),
    ],
)
def test_check_compat(info, verdict):
    assert check_compat(info) == verdict


@pytest.mark.parametrize(
    ("rule", "text"),
    [
        (None, None),
        ("FREQ=WEEKLY", "wöchentlich"),
        ("FREQ=WEEKLY;INTERVAL=2;BYDAY=SU", "alle 2 Wochen (So)"),
        ("RRULE:FREQ=MONTHLY;BYMONTHDAY=15", "monatlich (am 15.)"),
        ("FREQ=HOURLY", "FREQ=HOURLY"),
    ],
)
def test_rrule_text(rule, text):
    assert rrule_text(rule) == text
