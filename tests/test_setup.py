"""Full setup with a fake Yuvomi: entities, cleaning-plan to-do, notification event, diagnostics."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.yuvomi.const import DOMAIN
from custom_components.yuvomi.diagnostics import async_get_config_entry_diagnostics

CHORES = [
    {"id": 1, "name": "Bad putzen", "area": "Bad", "frequency_days": 7, "urgency_status": "overdue", "due_date": "2026-11-01"},
    {"id": 2, "name": "Fenster", "area": "Wohnzimmer", "frequency_days": 30, "urgency_status": "ok", "due_date": "2026-11-20"},
]


class FakeYuvomi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    async def request(self, method: str, path: str, json: Any = None, params: Any = None, **_: Any) -> Any:
        self.calls.append((method, path, json))
        if method != "GET":
            return {}
        if path == "/api/v1/version":
            return {"version": "2.84.0", "client_api": 1}
        if path == "/api/v1/family/members":
            return {"data": [{"id": 1, "display_name": "Alex"}, {"id": 2, "display_name": "Sam"}]}
        if path == "/api/v1/housekeeping/decay-tasks":
            return {"tasks": CHORES}
        if path == "/api/v1/tasks":
            return {"tasks": [{"id": 5, "title": "Müll raus", "status": "open", "assigned_to": [2]}]}
        return {}


async def _setup(hass, fake: FakeYuvomi) -> MockConfigEntry:
    hass.config.language = "en"
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="http://yuvomi:3005",
        data={CONF_URL: "http://yuvomi:3005", CONF_TOKEN: "secret-token", "webhook_id": "hook123"},
        options={"modules": ["tasks", "housekeeping"], "fire_events": True, "push_channel": False},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.yuvomi.api.YuvomiClient.request", side_effect=fake.request):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _entity(hass, entry, domain: str, suffix: str) -> str:
    reg = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(reg, entry.entry_id):
        if ent.domain == domain and ent.unique_id.endswith(suffix):
            return ent.entity_id
    raise AssertionError(f"{domain} *{suffix} not found")


async def test_setup_and_cleaning_plan_todo(hass):
    fake = FakeYuvomi()
    entry = await _setup(hass, fake)
    assert entry.state is ConfigEntryState.LOADED

    todo = _entity(hass, entry, "todo", "housekeeping_todo")
    assert hass.states.get(todo).state == "1"  # one due chore

    with patch("custom_components.yuvomi.api.YuvomiClient.request", side_effect=fake.request):
        await hass.services.async_call(
            "todo", "update_item", {"entity_id": todo, "item": "Bad putzen", "status": "completed"}, blocking=True
        )
        await hass.services.async_call(
            "todo", "add_item", {"entity_id": todo, "item": "Kühlschrank", "description": "Küche · 14"}, blocking=True
        )
    assert ("POST", "/api/v1/housekeeping/decay-tasks/1/complete", {}) in fake.calls
    assert ("POST", "/api/v1/housekeeping/decay-tasks", {"name": "Kühlschrank", "frequency_days": 14, "area": "Küche"}) in fake.calls


async def test_notification_event(hass):
    entry = await _setup(hass, FakeYuvomi())
    event = _entity(hass, entry, "event", "core_notification")
    assert hass.states.get(event).state == "unknown"
    async_dispatcher_send(
        hass,
        f"{DOMAIN}_webhook_{entry.entry_id}",
        {"event": "notification", "notification": {"title": "Due", "body": "Müll raus", "tag": "task-5"}},
    )
    await hass.async_block_till_done()
    state = hass.states.get(event)
    assert state.attributes["event_type"] == "notification"
    assert state.attributes["task_id"] == 5
    assert state.attributes["body"] == "Müll raus"


async def test_diagnostics_has_no_secrets(hass):
    fake = FakeYuvomi()
    entry = await _setup(hass, fake)
    with patch("custom_components.yuvomi.api.YuvomiClient.request", side_effect=fake.request):
        diag = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diag)
    assert "secret-token" not in text and "yuvomi:3005" not in text and "hook123" not in text
    assert "Bad putzen" not in text and "Alex" not in text
    assert diag["data"]["housekeeping"]["count"] == 2
    assert diag["data"]["members"] == 2
