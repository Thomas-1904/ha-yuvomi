"""To-do-Listen: Yuvomi-Aufgaben und jede Einkaufsliste."""

from __future__ import annotations

from datetime import date, datetime, time
import logging
import re
from typing import Any

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import YuvomiError
from .const import DOMAIN
from .coordinator import YuvomiCoordinator, parse_date
from .entity import YuvomiEntity, module_available, unique_prefix
from .util import rrule_text, txt

_LOGGER = logging.getLogger(__name__)

# Felder, die beim PUT einer Aufgabe nicht zurückgeschickt werden
_TASK_READONLY = {
    "id",
    "created_at",
    "updated_at",
    "created_by",
    "creator_name",
    "document_count",
    "documents",
    "next_due_date",
    "archived_at",
    "subtasks",
    "assigned_users",
    "assigned_avatar",
    "assigned_name",
    "comment_count",
    "sort_order",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: YuvomiCoordinator = entry.runtime_data
    known: set[int] = set()

    entities: list[TodoListEntity] = []
    if module_available(coordinator, "tasks"):
        entities.append(YuvomiTasksTodo(coordinator))
    if module_available(coordinator, "pantry"):
        entities.append(YuvomiPantryTodo(coordinator))
    if module_available(coordinator, "housekeeping"):
        entities.append(YuvomiHousekeepingTodo(coordinator))
    async_add_entities(entities)

    @callback
    def _add_new_lists() -> None:
        if not module_available(coordinator, "shopping"):
            return
        current = {int(lst["id"]) for lst in coordinator.data.shopping_lists}
        # In Yuvomi gelöschte Listen auch in HA entfernen
        registry = er.async_get(hass)
        prefix = unique_prefix(entry.entry_id, "shopping") + "todo_"
        for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
            if ent.domain == "todo" and ent.unique_id.startswith(prefix):
                try:
                    lid = int(ent.unique_id[len(prefix):])
                except ValueError:
                    continue
                if lid not in current:
                    known.discard(lid)
                    registry.async_remove(ent.entity_id)
        new = []
        for list_id in sorted(current - known):
            known.add(list_id)
            new.append(YuvomiShoppingTodo(coordinator, list_id))
        if new:
            async_add_entities(new)

    _add_new_lists()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_lists))

    known_members: set[int] = set()

    @callback
    def _add_members() -> None:
        if not module_available(coordinator, "tasks"):
            return
        new = [YuvomiMemberTasksTodo(coordinator, mid) for mid in sorted(set(coordinator.data.members) - known_members)]
        known_members.update(coordinator.data.members)
        if new:
            async_add_entities(new)

    _add_members()
    entry.async_on_unload(coordinator.async_add_listener(_add_members))


def _raise(err: YuvomiError) -> None:
    raise HomeAssistantError(translation_domain=DOMAIN, translation_key="api_error", translation_placeholders={"error": str(err)}) from err


class YuvomiTasksTodo(YuvomiEntity, TodoListEntity):
    """Alle Yuvomi-Aufgaben als eine Liste."""

    _attr_translation_key = "tasks"
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    def __init__(self, coordinator: YuvomiCoordinator) -> None:
        super().__init__(coordinator, "todo", "tasks")

    @staticmethod
    def _due(t: dict[str, Any]) -> date | datetime | None:
        d = parse_date(t.get("due_date"))
        tm = t.get("due_time")
        if d and tm:
            try:
                return datetime.combine(d, time.fromisoformat(str(tm)[:5]), dt_util.get_default_time_zone())
            except ValueError:
                return d
        return d

    @staticmethod
    def _split_due(due: Any) -> tuple[str | None, str | None]:
        if isinstance(due, datetime):
            local = dt_util.as_local(due)
            return local.date().isoformat(), local.strftime("%H:%M")
        if isinstance(due, date):
            return due.isoformat(), None
        return None, None

    async def _done_by(self) -> int | None:
        """Yuvomi-Mitglied zum HA-Benutzer, der gerade abhakt (Vorname oder voller Name)."""
        ctx = self._context
        if not ctx or not ctx.user_id:
            return None
        user = await self.hass.auth.async_get_user(ctx.user_id)
        if not user or not user.name:
            return None
        full = user.name.strip().lower()
        first = full.split(" ")[0]
        for mid, name in self.coordinator.data.members.items():
            n = str(name).strip().lower()
            if n == full or n.split(" ")[0] == first:
                return mid
        return None

    def _task(self, uid: str) -> dict[str, Any] | None:
        for task in self.coordinator.data.tasks:
            if str(task.get("id")) == str(uid):
                return task
        return None

    @property
    def todo_items(self) -> list[TodoItem]:
        items = []
        for t in self.coordinator.data.tasks:
            items.append(
                TodoItem(
                    uid=str(t.get("id")),
                    summary=t.get("title") or "",
                    status=(
                        TodoItemStatus.COMPLETED
                        if t.get("status") == "done"
                        else TodoItemStatus.NEEDS_ACTION
                    ),
                    due=self._due(t),
                    description=t.get("description") or None,
                )
            )
        return items

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Zuweisung, Priorität und Kategorie je Aufgabe (für Karten und Automationen)."""
        details = {}
        for t in self.coordinator.data.tasks:
            assigned = t.get("assigned_to")
            if not isinstance(assigned, list):
                assigned = [assigned] if assigned else []
            names = [
                self.coordinator.member_name(a.get("id") if isinstance(a, dict) else a)
                for a in assigned
            ]
            details[str(t.get("id"))] = {
                "zugewiesen": [n for n in names if n],
                "prioritaet": t.get("priority"),
                "kategorie": t.get("category"),
                "status": t.get("status"),
                "punkte": t.get("points"),
                "faellig": t.get("due_date"),
                "uhrzeit": t.get("due_time"),
                "wiederkehrend": rrule_text(t.get("recurrence_rule")) if t.get("is_recurring") else None,
                "serie": t.get("recurrence_series_id") or (t.get("id") if t.get("is_recurring") else None),
            }
        dash = self.coordinator.data.dashboard
        return {
            "aufgaben": details,
            "mitglieder": {str(k): v for k, v in self.coordinator.data.members.items()},
            "offen": dash.get("openTaskCount"),
            "ueberfaellig": dash.get("overdueTaskCount"),
        }

    async def async_create_todo_item(self, item: TodoItem) -> None:
        body: dict[str, Any] = {"title": item.summary}
        due_d, due_t = self._split_due(item.due)
        if due_d:
            body["due_date"] = due_d
        if due_t:
            body["due_time"] = due_t
        if item.description:
            body["description"] = item.description
        try:
            await self.coordinator.client.post("/api/v1/tasks", body)
        except YuvomiError as err:
            _raise(err)
        await self.coordinator.async_request_refresh()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        current = self._task(item.uid)
        if current is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="task_not_found")
        client = self.coordinator.client
        new_status = "done" if item.status == TodoItemStatus.COMPLETED else "open"
        old_status = current.get("status")
        try:
            # Status getrennt setzen: Punkte und Wiederholungen laufen wie in der App
            if (new_status == "done") != (old_status == "done"):
                body_s: dict[str, Any] = {"status": new_status}
                if new_status == "done":
                    who = await self._done_by()
                    if who is not None:
                        body_s["done_by_user_id"] = who
                await client.patch(f"/api/v1/tasks/{item.uid}/status", body_s)
                current = {**current, "status": new_status}

            due, due_t = self._split_due(item.due)
            changes: dict[str, Any] = {}
            if (item.summary or "") != (current.get("title") or ""):
                changes["title"] = item.summary
            if (item.description or None) != (current.get("description") or None):
                changes["description"] = item.description or ""
            old_due = (current.get("due_date") or "")[:10] or None
            old_t = (str(current.get("due_time"))[:5] if current.get("due_time") else None)
            if due != old_due:
                changes["due_date"] = due
            if due_t != old_t:
                changes["due_time"] = due_t
            if changes:
                body = {k: v for k, v in current.items() if k not in _TASK_READONLY}
                body.update(changes)
                await client.put(f"/api/v1/tasks/{item.uid}", body)
        except YuvomiError as err:
            _raise(err)
        await self.coordinator.async_request_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        try:
            for uid in uids:
                await self.coordinator.client.delete(f"/api/v1/tasks/{uid}")
        except YuvomiError as err:
            _raise(err)
        await self.coordinator.async_request_refresh()


def assignees(t: dict[str, Any]) -> list[int]:
    """Member ids a task is assigned to (Yuvomi returns either list or single id)."""
    ids = [u.get("id") for u in t.get("assigned_users") or [] if isinstance(u, dict)]
    if not ids:
        a = t.get("assigned_to")
        a = a if isinstance(a, list) else ([a] if a else [])
        ids = [x.get("id") if isinstance(x, dict) else x for x in a]
    out = []
    for i in ids:
        try:
            out.append(int(i))
        except (TypeError, ValueError):
            continue
    return out


class YuvomiMemberTasksTodo(YuvomiTasksTodo):
    """Tasks of one family member. New items are assigned to that member."""

    _attr_translation_key = None

    def __init__(self, coordinator: YuvomiCoordinator, member_id: int) -> None:
        YuvomiEntity.__init__(self, coordinator, f"todo_member_{member_id}", "tasks")
        self._mid = member_id

    @property
    def name(self) -> str:
        who = self.coordinator.data.members.get(self._mid, str(self._mid))
        return f"{txt(self.hass, 'Aufgaben', 'Tasks')} {str(who).split(' ')[0]}"

    @property
    def available(self) -> bool:
        return super().available and self._mid in self.coordinator.data.members

    @property
    def todo_items(self) -> list[TodoItem]:
        mine = {str(t.get("id")) for t in self.coordinator.data.tasks if self._mid in assignees(t)}
        return [i for i in super().todo_items if i.uid in mine]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"member_id": self._mid}

    async def async_create_todo_item(self, item: TodoItem) -> None:
        body: dict[str, Any] = {"title": item.summary, "assigned_to": [self._mid]}
        due_d, due_t = self._split_due(item.due)
        if due_d:
            body["due_date"] = due_d
        if due_t:
            body["due_time"] = due_t
        if item.description:
            body["description"] = item.description
        try:
            await self.coordinator.client.post("/api/v1/tasks", body)
        except YuvomiError as err:
            _raise(err)
        await self.coordinator.async_request_refresh()


class YuvomiShoppingTodo(YuvomiEntity, TodoListEntity):
    """Eine Yuvomi-Einkaufsliste."""

    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )
    _attr_icon = "mdi:cart-outline"

    def __init__(self, coordinator: YuvomiCoordinator, list_id: int) -> None:
        super().__init__(coordinator, f"todo_{list_id}", "shopping")
        self._list_id = list_id

    @property
    def _list(self) -> dict[str, Any] | None:
        for lst in self.coordinator.data.shopping_lists:
            if int(lst["id"]) == self._list_id:
                return lst
        return None

    @property
    def name(self) -> str:
        lst = self._list
        prefix = txt(self.hass, "Einkauf", "Shopping")
        return f"{prefix} {lst.get('name')}" if lst else f"{prefix} {self._list_id}"

    @property
    def available(self) -> bool:
        return super().available and self._list is not None

    def _items(self) -> list[dict[str, Any]]:
        return self.coordinator.data.shopping_items.get(self._list_id, [])

    @property
    def todo_items(self) -> list[TodoItem]:
        result = []
        for it in self._items():
            name = it.get("name") or ""
            qty = it.get("quantity")
            summary = f"{name} ({qty})" if qty else name
            result.append(
                TodoItem(
                    uid=str(it.get("id")),
                    summary=summary,
                    status=(
                        TodoItemStatus.COMPLETED if it.get("is_checked") else TodoItemStatus.NEEDS_ACTION
                    ),
                    description=it.get("notes") or None,
                )
            )
        return result

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "list_id": self._list_id,
            "artikel": {
                str(i.get("id")): {
                    "name": i.get("name"),
                    "menge": i.get("quantity"),
                    "kategorie": i.get("category"),
                }
                for i in self._items()
            },
        }

    @staticmethod
    def _split(summary: str) -> tuple[str, str | None]:
        """'Milch (2 l)' -> ('Milch', '2 l')."""
        summary = summary.strip()
        if summary.endswith(")") and " (" in summary:
            name, qty = summary[:-1].rsplit(" (", 1)
            return name.strip(), qty.strip() or None
        return summary, None

    async def async_create_todo_item(self, item: TodoItem) -> None:
        name, qty = self._split(item.summary or "")
        body: dict[str, Any] = {"name": name}
        if qty:
            body["quantity"] = qty
        if item.description:
            body["notes"] = item.description
        try:
            await self.coordinator.client.post(f"/api/v1/shopping/{self._list_id}/items", body)
        except YuvomiError as err:
            _raise(err)
        await self.coordinator.async_request_refresh()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        current = next((i for i in self._items() if str(i.get("id")) == item.uid), None)
        if current is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="item_not_found")
        name, qty = self._split(item.summary or "")
        body: dict[str, Any] = {}
        checked = item.status == TodoItemStatus.COMPLETED
        if bool(current.get("is_checked")) != checked:
            body["is_checked"] = checked
        if name != (current.get("name") or ""):
            body["name"] = name
        if (qty or None) != (current.get("quantity") or None):
            body["quantity"] = qty or ""
        if (item.description or None) != (current.get("notes") or None):
            body["notes"] = item.description or ""
        if not body:
            return
        try:
            await self.coordinator.client.patch(f"/api/v1/shopping/items/{item.uid}", body)
        except YuvomiError as err:
            _raise(err)
        await self.coordinator.async_request_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        try:
            for uid in uids:
                await self.coordinator.client.delete(f"/api/v1/shopping/items/{uid}")
        except YuvomiError as err:
            _raise(err)
        await self.coordinator.async_request_refresh()


class YuvomiPantryTodo(YuvomiEntity, TodoListEntity):
    """Vorrat: jede Charge ein Eintrag, Fälligkeit = Mindesthaltbarkeit.

    Abhaken = aufgebraucht (Menge 0), Löschen entfernt die Charge.
    Titel 'Milch (2 l)' setzt Name und Menge.
    """

    _attr_translation_key = "pantry"
    _attr_icon = "mdi:fridge-outline"
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    def __init__(self, coordinator: YuvomiCoordinator) -> None:
        super().__init__(coordinator, "todo", "pantry")

    @staticmethod
    def _qty(i: dict[str, Any]) -> str | None:
        q = i.get("quantity")
        if q in (None, ""):
            return None
        unit = i.get("unit") or ""
        return f"{q} {unit}".strip()

    @staticmethod
    def _due(i: dict[str, Any]) -> date | None:
        return parse_date(i.get("expires_on") or i.get("best_before"))

    @property
    def todo_items(self) -> list[TodoItem]:
        out = []
        for i in self.coordinator.data.pantry:
            qty = self._qty(i)
            empty = str(i.get("quantity")) in ("0", "0.0")
            out.append(
                TodoItem(
                    uid=str(i.get("id")),
                    summary=f"{i.get('name') or ''} ({qty})" if qty else (i.get("name") or ""),
                    status=TodoItemStatus.COMPLETED if empty else TodoItemStatus.NEEDS_ACTION,
                    due=self._due(i),
                    description=i.get("location_name") or i.get("notes") or None,
                )
            )
        return out

    @staticmethod
    def _parse(summary: str) -> tuple[str, Any, str | None]:
        name, qty = YuvomiShoppingTodo._split(summary)
        if not qty:
            return name, None, None
        parts = qty.split(" ", 1)
        try:
            amount: Any = float(parts[0].replace(",", "."))
            if amount == int(amount):
                amount = int(amount)
        except ValueError:
            return name, None, None
        return name, amount, parts[1] if len(parts) > 1 else None

    async def async_create_todo_item(self, item: TodoItem) -> None:
        name, qty, unit = self._parse(item.summary or "")
        body: dict[str, Any] = {"name": name, "quantity": qty if qty is not None else 1}
        if unit:
            body["unit"] = unit
        if isinstance(item.due, date):
            body["expires_on"] = item.due.isoformat()
        if item.description:
            body["notes"] = item.description
        try:
            await self.coordinator.client.post("/api/v1/pantry", body)
        except YuvomiError as err:
            _raise(err)
        await self.coordinator.async_request_refresh()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        name, qty, unit = self._parse(item.summary or "")
        body: dict[str, Any] = {"name": name}
        if item.status == TodoItemStatus.COMPLETED:
            body["quantity"] = 0
        elif qty is not None:
            body["quantity"] = qty
            if unit:
                body["unit"] = unit
        body["expires_on"] = item.due.isoformat() if isinstance(item.due, date) else None
        try:
            await self.coordinator.client.patch(f"/api/v1/pantry/{item.uid}", body)
        except YuvomiError as err:
            _raise(err)
        await self.coordinator.async_request_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        try:
            for uid in uids:
                await self.coordinator.client.delete(f"/api/v1/pantry/{uid}")
        except YuvomiError as err:
            _raise(err)
        await self.coordinator.async_request_refresh()


class YuvomiHousekeepingTodo(YuvomiEntity, TodoListEntity):
    """Cleaning plan: every chore is an item, due chores are open.

    Checking a due chore marks it as done in Yuvomi (it then reappears when it is due again).
    New items: title = chore; description "area · 7" sets area and interval in days.
    """

    _attr_translation_key = "housekeeping"
    _attr_icon = "mdi:broom"
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    def __init__(self, coordinator: YuvomiCoordinator) -> None:
        super().__init__(coordinator, "todo", "housekeeping")

    def _due_ids(self) -> set[str]:
        from .sensor import _housekeeping_due  # noqa: PLC0415

        return {str(t.get("id")) for t in _housekeeping_due(self.coordinator.data)}

    @staticmethod
    def _interval(t: dict[str, Any]) -> Any:
        return t.get("frequency_days") or t.get("interval_days") or t.get("decay_days")

    def _describe(self, t: dict[str, Any]) -> str | None:
        parts = []
        if t.get("area"):
            parts.append(str(t["area"]))
        if (n := self._interval(t)) is not None:
            parts.append(txt(self.hass, f"alle {n} Tage", f"every {n} days"))
        return " · ".join(parts) or None

    @property
    def todo_items(self) -> list[TodoItem]:
        due = self._due_ids()
        out = []
        for t in self.coordinator.data.housekeeping:
            uid = str(t.get("id"))
            out.append(
                TodoItem(
                    uid=uid,
                    summary=t.get("name") or t.get("title") or "",
                    status=TodoItemStatus.NEEDS_ACTION if uid in due else TodoItemStatus.COMPLETED,
                    due=parse_date(t.get("due_date")),
                    description=self._describe(t),
                )
            )
        # due chores first, then by due date
        out.sort(key=lambda i: (i.status != TodoItemStatus.NEEDS_ACTION, str(i.due or "9999")))
        return out

    @staticmethod
    def parse_description(text: str | None) -> tuple[str | None, int | None]:
        """'Bad · 7' / 'Bathroom · every 7 days' -> ('Bad', 7)."""
        if not text:
            return None, None
        area: str | None = None
        days: int | None = None
        for part in (p.strip() for p in text.split("·")):
            num = re.search(r"\d+", part)
            if num and days is None:
                days = int(num.group())
            elif part and area is None:
                area = part
        return area, days

    async def async_create_todo_item(self, item: TodoItem) -> None:
        area, days = self.parse_description(item.description)
        body: dict[str, Any] = {"name": (item.summary or "").strip(), "frequency_days": days or 7}
        if area:
            body["area"] = area
        try:
            await self.coordinator.client.post("/api/v1/housekeeping/decay-tasks", body)
        except YuvomiError as err:
            _raise(err)
        await self.coordinator.async_request_refresh()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        current = next(
            (t for t in self.coordinator.data.housekeeping if str(t.get("id")) == item.uid), None
        )
        if current is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="chore_not_found")
        try:
            body: dict[str, Any] = {}
            if item.summary and item.summary != (current.get("name") or current.get("title")):
                body["name"] = item.summary.strip()
            if item.description != self._describe(current):
                area, days = self.parse_description(item.description)
                body["area"] = area
                if days:
                    body["frequency_days"] = days
            if body:
                await self.coordinator.client.patch(f"/api/v1/housekeeping/decay-tasks/{item.uid}", body)
            if item.status == TodoItemStatus.COMPLETED and item.uid in self._due_ids():
                await self.coordinator.client.post(f"/api/v1/housekeeping/decay-tasks/{item.uid}/complete")
        except YuvomiError as err:
            _raise(err)
        await self.coordinator.async_request_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        try:
            for uid in uids:
                await self.coordinator.client.delete(f"/api/v1/housekeeping/decay-tasks/{uid}")
        except YuvomiError as err:
            _raise(err)
        await self.coordinator.async_request_refresh()
