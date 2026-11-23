"""Tiered polling: fast data every cycle, slow data only on a full refresh."""

from __future__ import annotations

from unittest.mock import patch

from .test_v055_features import Fake, _setup


def _count(fake, path):
    return sum(1 for c in fake.calls if c[0] == "GET" and c[1] == path)


async def test_fast_cycle_skips_slow_data(hass):
    fake = Fake()
    entry = await _setup(hass, fake, ["tasks", "birthdays"])
    coord = entry.runtime_data
    before = {p: _count(fake, p) for p in ("/api/v1/tasks", "/api/v1/birthdays", "/api/v1/tasks/completions", "/api/v1/version")}
    assert before["/api/v1/birthdays"] == 1

    with patch("custom_components.yuvomi.api.YuvomiClient.request", side_effect=fake.request):
        await coord.async_refresh()  # timer cycle
    assert _count(fake, "/api/v1/tasks") == before["/api/v1/tasks"] + 1
    assert _count(fake, "/api/v1/birthdays") == before["/api/v1/birthdays"]
    assert _count(fake, "/api/v1/tasks/completions") == before["/api/v1/tasks/completions"]

    with patch("custom_components.yuvomi.api.YuvomiClient.request", side_effect=fake.request):
        coord._full = True  # what a webhook or a write from HA requests
        await coord.async_refresh()
    assert _count(fake, "/api/v1/birthdays") == before["/api/v1/birthdays"] + 1
    assert _count(fake, "/api/v1/tasks/completions") == before["/api/v1/tasks/completions"] + 1


async def test_request_refresh_is_full(hass):
    fake = Fake()
    entry = await _setup(hass, fake, ["birthdays"])
    coord = entry.runtime_data
    n = _count(fake, "/api/v1/birthdays")
    with patch("custom_components.yuvomi.api.YuvomiClient.request", side_effect=fake.request):
        await coord.async_request_refresh()
        await hass.async_block_till_done()
    assert _count(fake, "/api/v1/birthdays") == n + 1
