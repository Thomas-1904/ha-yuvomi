"""Meals and birthdays can be created/deleted from the HA calendars."""

from datetime import time
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.yuvomi.calendar import meal_type_for, split_birth_year
from custom_components.yuvomi.const import DOMAIN

from .test_setup import FakeYuvomi


@pytest.mark.parametrize(
    ("t", "kind"),
    [(time(7, 30), "breakfast"), (time(12, 0), "lunch"), (time(16, 0), "snack"), (time(18, 30), "dinner")],
)
def test_meal_type_for(t, kind):
    assert meal_type_for(t) == kind


def test_split_birth_year():
    assert split_birth_year("Anna (1990)", None) == ("Anna", 1990)
    assert split_birth_year("Ben", "born 1985") == ("Ben", 1985)
    assert split_birth_year("Oma", None) == ("Oma", None)


async def _setup(hass, fake):
    hass.config.language = "en"
    hass.config.time_zone = "Europe/Berlin"
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="http://yuvomi:3005",
        data={CONF_URL: "http://yuvomi:3005", CONF_TOKEN: "t", "webhook_id": "w"},
        options={"modules": ["meals", "birthdays"], "push_channel": False, "update_check": False},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.yuvomi.api.YuvomiClient.request", side_effect=fake.request):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    reg = er.async_get(hass)
    ids = {e.unique_id.split("_")[-2]: e.entity_id for e in er.async_entries_for_config_entry(reg, entry.entry_id) if e.domain == "calendar"}
    return ids


async def test_create_meal_and_birthday(hass):
    fake = FakeYuvomi()
    cals = await _setup(hass, fake)
    with patch("custom_components.yuvomi.api.YuvomiClient.request", side_effect=fake.request):
        await hass.services.async_call(
            "calendar", "create_event",
            {"entity_id": cals["meals"], "summary": "Lasagne", "start_date_time": "2026-11-10 12:30:00",
             "end_date_time": "2026-11-10 13:30:00", "description": "Rezept von Oma"},
            blocking=True,
        )
        await hass.services.async_call(
            "calendar", "create_event",
            {"entity_id": cals["birthdays"], "summary": "Anna (1990)", "start_date": "2026-12-24", "end_date": "2026-12-25"},
            blocking=True,
        )
    assert ("POST", "/api/v1/meals", {"title": "Lasagne", "date": "2026-11-10", "meal_type": "lunch", "notes": "Rezept von Oma"}) in fake.calls
    assert ("POST", "/api/v1/birthdays", {"name": "Anna", "birth_date": "1990-12-24", "year_unknown": False}) in fake.calls
