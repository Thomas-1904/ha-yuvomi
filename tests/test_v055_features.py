"""Per-member task lists, editing family events, missing-scope notice, meals-to-shopping button."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import patch

from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.yuvomi.api import YuvomiApiError
from custom_components.yuvomi.const import DOMAIN

from .test_setup import FakeYuvomi

TASKS = [
    {"id": 5, "title": "Müll raus", "status": "open", "assigned_to": [2]},
    {"id": 6, "title": "Einkaufen", "status": "open", "assigned_users": [{"id": 1}]},
    {"id": 7, "title": "Gemeinsam", "status": "open", "assigned_to": [1, 2]},
]
EVENT = {"id": 9, "title": "Arzt", "start_datetime": "2026-11-10T10:00", "end_datetime": "2026-11-10T11:00",
         "all_day": False, "description": "alt", "location": None, "created_by": 1}


class Fake(FakeYuvomi):
    def __init__(self, deny: set[str] = frozenset()) -> None:
        super().__init__()
        self.deny = deny

    async def request(self, method: str, path: str, json: Any = None, params: Any = None, **kw: Any) -> Any:
        if any(path.startswith(p) for p in self.deny):
            raise YuvomiApiError(403, "Forbidden", "forbidden")
        if method == "GET" and path == "/api/v1/tasks":
            self.calls.append((method, path, json))
            return {"tasks": TASKS}
        if method == "GET" and path == "/api/v1/shopping":
            self.calls.append((method, path, json))
            return {"lists": [{"id": 3, "name": "Einkauf"}]}
        if method == "GET" and path == "/api/v1/calendar/9":
            self.calls.append((method, path, json))
            return {"data": EVENT}
        return await super().request(method, path, json, params, **kw)


async def _setup(hass, fake, modules):
    hass.config.language = "en"
    hass.config.time_zone = "Europe/Berlin"
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="http://y:3005",
        data={CONF_URL: "http://y:3005", CONF_TOKEN: "t", "webhook_id": "w"},
        options={"modules": modules, "push_channel": False, "update_check": False},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.yuvomi.api.YuvomiClient.request", side_effect=fake.request):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _ents(hass, entry, domain):
    reg = er.async_get(hass)
    return {e.unique_id: e.entity_id for e in er.async_entries_for_config_entry(reg, entry.entry_id) if e.domain == domain}


async def test_member_task_lists(hass):
    fake = Fake()
    entry = await _setup(hass, fake, ["tasks"])
    todos = _ents(hass, entry, "todo")
    sam = next(v for k, v in todos.items() if k.endswith("todo_member_2"))
    alex = next(v for k, v in todos.items() if k.endswith("todo_member_1"))
    assert hass.states.get(sam).state == "2"   # tasks 5 and 7
    assert hass.states.get(alex).state == "2"  # tasks 6 and 7
    assert hass.states.get(sam).name.endswith("Tasks Sam")
    with patch("custom_components.yuvomi.api.YuvomiClient.request", side_effect=fake.request):
        await hass.services.async_call("todo", "add_item", {"entity_id": sam, "item": "Rasen mähen"}, blocking=True)
    assert ("POST", "/api/v1/tasks", {"title": "Rasen mähen", "assigned_to": [2]}) in fake.calls


async def test_update_family_event(hass):
    fake = Fake()
    entry = await _setup(hass, fake, ["calendar"])
    cal = next(iter(_ents(hass, entry, "calendar").values()))
    with patch("custom_components.yuvomi.api.YuvomiClient.request", side_effect=fake.request):
        ent = hass.data["calendar"].get_entity(cal)
        tz = dt_util.get_default_time_zone()
        await ent.async_update_event("9", {
            "dtstart": datetime(2026, 11, 10, 14, 0, tzinfo=tz), "dtend": datetime(2026, 11, 10, 15, 0, tzinfo=tz),
            "summary": "Arzt (verschoben)", "description": "neu",
        })
    put = next(c for c in fake.calls if c[0] == "PUT")
    assert put[1] == "/api/v1/calendar/9"
    assert put[2]["title"] == "Arzt (verschoben)"
    assert put[2]["start_datetime"] == "2026-11-10T14:00"
    assert put[2]["description"] == "neu"
    assert "created_by" not in put[2]


async def test_missing_scope_issue(hass):
    entry = await _setup(hass, Fake(deny={"/api/v1/pantry"}), ["tasks", "pantry"])
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"missing_scope_{entry.entry_id}")
    assert issue is not None
    assert issue.translation_placeholders["modules"] == "pantry"
    assert issue.translation_placeholders["scopes"] == "pantry:write"


async def test_meals_to_shopping_button(hass):
    fake = Fake()
    entry = await _setup(hass, fake, ["meals", "shopping"])
    btn = next(v for k, v in _ents(hass, entry, "button").items() if k.endswith("meals_to_shopping"))
    with patch("custom_components.yuvomi.api.YuvomiClient.request", side_effect=fake.request):
        await hass.services.async_call("button", "press", {"entity_id": btn}, blocking=True)
    post = next(c for c in fake.calls if c[0] == "POST")
    assert post[1] == "/api/v1/shopping/3/import-meal-plan"
    assert set(post[2]) == {"from", "to"}
