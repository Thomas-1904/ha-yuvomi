"""Yuvomi update check."""

from unittest.mock import patch

import pytest
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.yuvomi.const import DOMAIN
from custom_components.yuvomi.update import newest, parse_version

from .test_setup import FakeYuvomi


@pytest.mark.parametrize(
    ("tags", "result"),
    [
        (["v2.84.0", "v2.85.0", "v2.9.9", "v3.0.0-beta.1", "nightly"], "2.85.0"),
        (["v2.100.0", "v2.99.9"], "2.100.0"),
        ([], None),
    ],
)
def test_newest(tags, result):
    assert newest(tags) == result


def test_parse_version():
    assert parse_version("v2.85.0") == (2, 85, 0)
    assert parse_version("2.85") is None


async def _setup(hass, latest):
    hass.config.language = "en"
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="http://yuvomi:3005",
        data={CONF_URL: "http://yuvomi:3005", CONF_TOKEN: "t", "webhook_id": "w"},
        options={"modules": ["tasks"], "push_channel": False},
    )
    entry.add_to_hass(hass)
    fake = FakeYuvomi()
    with patch("custom_components.yuvomi.api.YuvomiClient.request", side_effect=fake.request), patch(
        "custom_components.yuvomi.update.fetch_latest_from_github", return_value=latest
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    reg = er.async_get(hass)
    ent = next(e for e in er.async_entries_for_config_entry(reg, entry.entry_id) if e.domain == "update")
    return hass.states.get(ent.entity_id)


async def test_update_available(hass):
    state = await _setup(hass, "2.85.0")
    assert state.state == "on"
    assert state.attributes["installed_version"] == "2.84.0"
    assert state.attributes["latest_version"] == "2.85.0"
    assert state.attributes["release_url"].endswith("/compare/v2.84.0...v2.85.0")


async def test_no_downgrade(hass):
    state = await _setup(hass, "2.80.0")
    assert state.state == "off"
    assert state.attributes["latest_version"] == "2.84.0"
