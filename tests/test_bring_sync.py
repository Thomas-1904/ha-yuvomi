"""Bring ↔ Yuvomi three-way merge (no network, no Home Assistant services)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from custom_components.yuvomi.bring_sync import BringSync


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    async def patch(self, path, body=None):
        self.calls.append(("PATCH", path, body))

    async def post(self, path, body=None):
        self.calls.append(("POST", path, body))

    async def delete(self, path):
        self.calls.append(("DELETE", path, None))


class FakeStore:
    def __init__(self) -> None:
        self.saved = None

    async def async_save(self, data):
        self.saved = data


def make(yuvomi: list[dict], bring: list[dict], snap: dict | None = None, recent: dict | None = None):
    sync = object.__new__(BringSync)
    sync.hass = SimpleNamespace(states=SimpleNamespace(get=lambda _e: SimpleNamespace(state="0")))
    client = FakeClient()
    sync.coordinator = SimpleNamespace(client=client)
    sync.bring = "todo.bring"
    sync.list_id = 1
    sync._snap = snap or {}
    sync._recent = dict(recent or {})
    sync._store = FakeStore()
    bring_calls: list[tuple[str, dict]] = []

    async def y_items():
        return yuvomi

    async def b_items():
        return bring

    async def b_call(service, data):
        bring_calls.append((service, data))

    sync._yuvomi_items = y_items
    sync._bring_items = b_items
    sync._bring = b_call
    return sync, client.calls, bring_calls


def y(i, name, checked=False, qty=None):
    return {"id": i, "name": name, "is_checked": checked, "quantity": qty}


def b(uid, name, done=False, desc=""):
    return {"uid": uid, "summary": name, "status": "completed" if done else "needs_action", "description": desc}


async def test_new_yuvomi_item_goes_to_bring():
    sync, ycalls, bcalls = make([y(1, "Milch", qty="2 l")], [])
    await sync._sync()
    assert bcalls == [("add_item", {"item": "Milch", "description": "2 l"})]
    assert ycalls == []
    assert sync._snap == {"milch": {"o": True, "q": "2 l"}}


async def test_new_bring_item_goes_to_yuvomi():
    sync, ycalls, bcalls = make([], [b("a", "Brot")])
    await sync._sync()
    assert ycalls == [("POST", "/api/v1/shopping/1/items", {"name": "Brot"})]
    assert bcalls == []


async def test_completed_bring_only_item_is_not_imported():
    sync, ycalls, bcalls = make([], [b("a", "Eier", done=True)])
    await sync._sync()
    assert ycalls == [] and bcalls == []


async def test_checked_in_bring_checks_in_yuvomi():
    snap = {"milch": {"o": True, "q": ""}}
    sync, ycalls, bcalls = make([y(1, "Milch")], [b("a", "milch", done=True)], snap)
    await sync._sync()
    assert ycalls == [("PATCH", "/api/v1/shopping/items/1", {"is_checked": True})]
    assert bcalls == []


async def test_checked_in_yuvomi_checks_in_bring():
    snap = {"milch": {"o": True, "q": ""}}
    sync, ycalls, bcalls = make([y(1, "Milch", checked=True)], [b("a", "Milch")], snap)
    await sync._sync()
    assert bcalls == [("update_item", {"item": "a", "status": "completed"})]
    assert ycalls == []


async def test_deleted_in_yuvomi_removed_from_bring():
    snap = {"milch": {"o": True, "q": ""}}
    sync, ycalls, bcalls = make([], [b("a", "Milch")], snap)
    await sync._sync()
    assert bcalls == [("remove_item", {"item": "a"})]


async def test_deleted_in_yuvomi_but_done_in_bring_stays():
    snap = {"milch": {"o": False, "q": ""}}
    sync, ycalls, bcalls = make([], [b("a", "Milch", done=True)], snap)
    await sync._sync()
    assert bcalls == [] and ycalls == []


async def test_deleted_in_bring_removed_from_yuvomi():
    snap = {"milch": {"o": True, "q": ""}}
    sync, ycalls, bcalls = make([y(1, "Milch")], [b("x", "Brot")], snap)
    await sync._sync()
    assert ("DELETE", "/api/v1/shopping/items/1", None) in ycalls


async def test_freshly_added_to_bring_is_protected():
    import time

    snap = {"milch": {"o": True, "q": ""}}
    sync, ycalls, bcalls = make([y(1, "Milch")], [b("x", "Brot")], snap, recent={"milch": time.monotonic()})
    await sync._sync()
    assert not any(c[0] == "DELETE" for c in ycalls)
    assert sync._snap["milch"] == {"o": True, "q": ""}


async def test_quantity_changed_in_bring():
    snap = {"milch": {"o": True, "q": "1 l"}}
    sync, ycalls, bcalls = make([y(1, "Milch", qty="1 l")], [b("a", "Milch", desc="3 l")], snap)
    await sync._sync()
    assert ycalls == [("PATCH", "/api/v1/shopping/items/1", {"quantity": "3 l"})]


async def test_empty_bring_answer_deletes_nothing():
    snap = {k: {"o": True, "q": ""} for k in ("a", "b", "c", "d")}
    sync, ycalls, bcalls = make([y(i, k) for i, k in enumerate("abcd")], [], snap)
    await sync._sync()
    assert not any(c[0] == "DELETE" for c in ycalls)
