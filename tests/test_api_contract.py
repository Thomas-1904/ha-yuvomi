"""Request bodies match what Yuvomi requires (its API checks every request since 2.89+)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.yuvomi.const import DOMAIN
from custom_components.yuvomi.todo import pantry_unit

from .test_v055_features import Fake, _setup


class WorkerFake(Fake):
    def __init__(self, workers: list[dict[str, Any]]) -> None:
        super().__init__()
        self.workers = workers

    async def request(self, method: str, path: str, json: Any = None, params: Any = None, **kw: Any) -> Any:
        if method == "GET" and path == "/api/v1/housekeeping/workers":
            return {"data": self.workers}
        return await super().request(method, path, json, params, **kw)


def _posted(fake, path):
    return next(c[2] for c in fake.calls if c[0] == "POST" and c[1] == path)


async def _call(hass, fake, service, data):
    with patch("custom_components.yuvomi.api.YuvomiClient.request", side_effect=fake.request):
        await hass.services.async_call(DOMAIN, service, data, blocking=True)


async def test_reward_bonus_sends_delta(hass):
    fake = Fake()
    await _setup(hass, fake, ["tasks"])
    await _call(hass, fake, "reward_bonus", {"user_id": 2, "points": -5})
    assert _posted(fake, "/api/v1/rewards/bonus") == {"user_id": 2, "delta": -5, "reason": None}


async def test_inventory_date_sends_performed_on(hass):
    fake = Fake()
    await _setup(hass, fake, ["tasks"])
    await _call(hass, fake, "complete_inventory_date", {"item_id": 1, "date_id": 2, "odometer": 1234.0})
    body = _posted(fake, "/api/v1/inventory/items/1/dates/2/complete")
    assert body == {"odometer": 1234, "performed_on": dt_util.now().date().isoformat()}


async def test_housekeeper_check_picks_only_worker(hass):
    fake = WorkerFake([{"id": 4, "name": "Sam"}])
    await _setup(hass, fake, ["tasks"])
    await _call(hass, fake, "housekeeper_check", {"direction": "in"})
    assert _posted(fake, "/api/v1/housekeeping/work-sessions/check-in")["worker_id"] == 4


async def test_housekeeper_check_needs_worker_when_several(hass):
    fake = WorkerFake([{"id": 4}, {"id": 5}])
    await _setup(hass, fake, ["tasks"])
    with pytest.raises(ServiceValidationError):
        await _call(hass, fake, "housekeeper_check", {"direction": "out"})


@pytest.mark.parametrize(
    ("text", "unit"),
    [("Stück", "pcs"), ("Flaschen", "bottle"), ("L", "l"), ("Packung", "pkg"), ("Prise", None), (None, None)],
)
def test_pantry_unit(text, unit):
    assert pantry_unit(text) == unit


class PantryFake(Fake):
    async def request(self, method: str, path: str, json: Any = None, params: Any = None, **kw: Any) -> Any:
        if method == "GET" and path == "/api/v1/pantry":
            return {"items": [
                {"id": 1, "name": "Milch", "quantity": 0},
                {"id": 2, "name": "Reis", "quantity": 1, "min_quantity": 2},
                {"id": 3, "name": "Mehl", "quantity": 5, "min_quantity": 1},
            ]}
        return await super().request(method, path, json, params, **kw)


async def test_pantry_to_shopping_sends_low_items(hass):
    fake = PantryFake()
    await _setup(hass, fake, ["shopping"])
    await _call(hass, fake, "pantry_to_shopping", {})
    assert _posted(fake, "/api/v1/shopping/3/import-pantry") == {"items": [{"pantry_item_id": 1}, {"pantry_item_id": 2}]}
