"""Config flow: setup, reconfigure."""

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.yuvomi.const import CONF_PUSH, DOMAIN

VALIDATE = "custom_components.yuvomi.config_flow._validate"
SETUP = "custom_components.yuvomi.async_setup_entry"


async def test_user_flow_creates_entry_with_push_off(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    with patch(VALIDATE, return_value={"display_name": "Home"}), patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: "192.168.1.10:3005/", CONF_TOKEN: " abc "}
        )
        assert result["step_id"] == "modules"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"modules": ["tasks", "shopping"], "fire_events": True}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_URL] == "http://192.168.1.10:3005"
    assert result["data"][CONF_TOKEN] == "abc"
    assert result["options"][CONF_PUSH] is False
    assert result["options"]["modules"] == ["tasks", "shopping"]


async def test_reconfigure_changes_url_keeps_token(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="http://old:3005",
        data={CONF_URL: "http://old:3005", CONF_TOKEN: "secret", "webhook_id": "w"},
        options={"modules": ["tasks"]},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    with patch(VALIDATE, return_value={}), patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: "http://new:3005"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_URL] == "http://new:3005"
    assert entry.data[CONF_TOKEN] == "secret"
    assert entry.unique_id == "http://new:3005"


async def test_options_show_webhook_url_and_rotate(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="http://y:3005",
        data={CONF_URL: "http://y:3005", CONF_TOKEN: "t", "webhook_id": "oldsecret"},
        options={"modules": ["tasks"]},
    )
    entry.add_to_hass(hass)
    hass.config.external_url = "https://ha.example.org"
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["description_placeholders"]["webhook_url"] == "https://ha.example.org/api/webhook/oldsecret"
    with patch(SETUP, return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"modules": ["tasks"], "fire_events": True, "scan_interval": 60, "push_channel": False,
             "update_check": True, "new_webhook_id": True},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "new_webhook_id" not in entry.options
    assert entry.data["webhook_id"] not in ("oldsecret", None)


async def test_no_webhook_entity(hass):
    """The webhook id is a secret and must never be in an entity state."""
    from homeassistant.helpers import entity_registry as er

    from .test_setup import FakeYuvomi, _setup

    entry = await _setup(hass, FakeYuvomi())
    states = " ".join(str(s.state) + str(s.attributes) for s in hass.states.async_all())
    assert "hook123" not in states
    assert not any(e.unique_id.endswith("_core_webhook") for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id))
