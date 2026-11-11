"""Datenabruf: ein Koordinator für alle Yuvomi-Entitäten (nur aktivierte Module)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
import json
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import YuvomiApiError, YuvomiAuthError, YuvomiClient, YuvomiError, as_list, unwrap
from .const import (
    CONF_EVENTS,
    CONF_MODULES,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_CHANGED,
    MODULES,
)

_LOGGER = logging.getLogger(__name__)

TASK_STATUSES = ("open", "in_progress", "done")


def enabled_modules(entry: ConfigEntry) -> set[str]:
    mods = entry.options.get(CONF_MODULES)
    if mods is None:
        mods = entry.data.get(CONF_MODULES)
    if mods is None:
        mods = [k for k, (_, default) in MODULES.items() if default]
    return set(mods)


@dataclass
class YuvomiData:
    """Momentaufnahme aller abgefragten Daten."""

    dashboard: dict[str, Any] = field(default_factory=dict)
    members: dict[int, str] = field(default_factory=dict)
    member_info: dict[int, dict[str, Any]] = field(default_factory=dict)
    completions: list[dict[str, Any]] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    shopping_lists: list[dict[str, Any]] = field(default_factory=list)
    shopping_items: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    shopping_versions: dict[int, Any] = field(default_factory=dict)
    housekeeping: list[dict[str, Any]] = field(default_factory=list)
    waste_next: list[dict[str, Any]] = field(default_factory=list)
    meals_week: list[dict[str, Any]] = field(default_factory=list)
    recipes: list[dict[str, Any]] = field(default_factory=list)
    pantry: list[dict[str, Any]] = field(default_factory=list)
    inventory: list[dict[str, Any]] = field(default_factory=list)
    birthdays: list[dict[str, Any]] = field(default_factory=list)
    budget_summary: dict[str, Any] = field(default_factory=dict)
    accounts: list[dict[str, Any]] = field(default_factory=list)
    subscriptions: list[dict[str, Any]] = field(default_factory=list)
    loans: list[dict[str, Any]] = field(default_factory=list)
    loans_summary: dict[str, Any] = field(default_factory=dict)
    split: dict[str, Any] = field(default_factory=dict)
    rewards: dict[str, Any] = field(default_factory=dict)
    ledger: list[dict[str, Any]] = field(default_factory=list)
    shopping_categories: list[dict[str, Any]] = field(default_factory=list)
    money: list[dict[str, Any]] = field(default_factory=list)
    reminders: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    contacts: list[dict[str, Any]] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)
    shifts: list[dict[str, Any]] = field(default_factory=list)
    medications: list[dict[str, Any]] = field(default_factory=list)
    vitals: list[dict[str, Any]] = field(default_factory=list)
    prevention_due: list[dict[str, Any]] = field(default_factory=list)
    nutrition: dict[str, Any] = field(default_factory=dict)
    modules_ok: dict[str, bool] = field(default_factory=dict)
    version: str | None = None


# Sammlungen, für die Änderungsereignisse gefeuert werden: Ressource -> Attribut
TRACKED = {
    "task": "tasks",
    "pantry_item": "pantry",
    "inventory_item": "inventory",
    "meal": "meals_week",
    "birthday": "birthdays",
    "subscription": "subscriptions",
    "reminder": "reminders",
    "note": "notes",
    "contact": "contacts",
    "medication": "medications",
    "document": "documents",
    "housekeeping_task": "housekeeping",
}


class YuvomiCoordinator(DataUpdateCoordinator[YuvomiData]):
    """Fragt Yuvomi regelmäßig ab – nur die aktivierten Module."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: YuvomiClient) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.modules = enabled_modules(entry)
        self.fire_events = entry.options.get(CONF_EVENTS, True)
        self._last: YuvomiData | None = None
        self.bring_sync = None  # BringSync, wenn in den Optionen eingerichtet

    def on(self, module: str) -> bool:
        return module in self.modules

    async def _opt(self, key: str, data: YuvomiData, path: str, params=None) -> Any:
        """Abruf; 403/404 heißt: Modul in Yuvomi nicht verfügbar/kein Recht."""
        try:
            result = await self.client.get(path, params=params)
        except YuvomiApiError as err:
            if err.status in (403, 404):
                if data.modules_ok.get(key) is not True:
                    data.modules_ok[key] = False
                _LOGGER.debug("Yuvomi %s not available (%s)", path, err.status)
                return None
            raise
        data.modules_ok[key] = True
        return result

    async def _async_update_data(self) -> YuvomiData:
        d = YuvomiData()
        prev = self._last or YuvomiData()
        today = dt_util.now().date()
        try:
            # After an outage (e.g. Yuvomi was restarted for an update) read the version again
            d.version = (prev.version if self.last_update_success else None) or await self.client.version()

            dash = unwrap(await self._opt("dashboard", d, "/api/v1/dashboard", {"events_limit": 12}))
            d.dashboard = dash if isinstance(dash, dict) else {}

            fam = [m for m in as_list(await self._opt("family", d, "/api/v1/family/members")) if m.get("id") is not None]
            d.members = {int(m["id"]): m.get("display_name") or str(m["id"]) for m in fam}
            d.member_info = {
                int(m["id"]): {"name": m.get("display_name"), "farbe": m.get("avatar_color"), "geburtstag": m.get("birth_date")}
                for m in fam
            }

            if self.on("tasks"):
                tasks = await self._opt(
                    "tasks",
                    d,
                    "/api/v1/tasks",
                    [("status", s) for s in TASK_STATUSES] + [("include_future", "1")],
                )
                d.tasks = [t for t in as_list(tasks, "tasks") if not t.get("parent_task_id")]
                if d.modules_ok.get("tasks"):
                    try:
                        d.completions = as_list(
                            await self.client.get("/api/v1/tasks/completions", params={"limit": 200}), "completions"
                        )
                    except YuvomiApiError:
                        d.completions = prev.completions

            if self.on("shopping"):
                d.shopping_lists = as_list(await self._opt("shopping", d, "/api/v1/shopping"), "lists")
                if d.modules_ok.get("shopping"):
                    try:
                        d.shopping_categories = as_list(await self.client.get("/api/v1/shopping/categories"), "categories")
                    except YuvomiApiError:
                        d.shopping_categories = prev.shopping_categories
                    versions = as_list(await self.client.get("/api/v1/shopping/versions"))
                    d.shopping_versions = {
                        int(v["list_id"]): v.get("version") for v in versions if v.get("list_id") is not None
                    }
                    for lst in d.shopping_lists:
                        lid = int(lst["id"])
                        ver = d.shopping_versions.get(lid)
                        if lid in prev.shopping_items and ver is not None and prev.shopping_versions.get(lid) == ver:
                            d.shopping_items[lid] = prev.shopping_items[lid]
                        else:
                            d.shopping_items[lid] = as_list(
                                await self.client.get(f"/api/v1/shopping/{lid}/items"), "items"
                            )

            if self.on("calendar"):
                d.modules_ok["calendar"] = d.modules_ok.get("dashboard", False)

            if self.on("meals"):
                d.meals_week = as_list(
                    await self._opt("meals", d, "/api/v1/meals", {"week": today.isoformat()}), "meals"
                )

            if self.on("recipes"):
                d.recipes = as_list(await self._opt("recipes", d, "/api/v1/recipes"), "recipes")

            if self.on("pantry"):
                d.pantry = as_list(await self._opt("pantry", d, "/api/v1/pantry"), "items")

            if self.on("inventory"):
                d.inventory = as_list(await self._opt("inventory", d, "/api/v1/inventory/items"), "items")

            if self.on("housekeeping"):
                d.housekeeping = as_list(
                    await self._opt("housekeeping", d, "/api/v1/housekeeping/decay-tasks"), "tasks"
                )

            if self.on("waste"):
                d.waste_next = as_list(await self._opt("waste", d, "/api/v1/waste/occurrences/next"))

            if self.on("birthdays"):
                d.birthdays = as_list(await self._opt("birthdays", d, "/api/v1/birthdays"), "birthdays")

            if self.on("budget"):
                s = unwrap(
                    await self._opt("budget", d, "/api/v1/budget/summary", {"month": today.strftime("%Y-%m")})
                )
                d.budget_summary = s if isinstance(s, dict) else {}
                d.accounts = as_list(await self._opt("budget", d, "/api/v1/budget/accounts"), "accounts")
                d.subscriptions = as_list(
                    await self._opt("budget", d, "/api/v1/budget/subscriptions"), "subscriptions"
                )

            if self.on("loans"):
                raw = unwrap(await self._opt("loans", d, "/api/v1/budget/loans"))
                d.loans = as_list(raw, "loans")
                if isinstance(raw, dict):
                    d.loans_summary = raw.get("summary") or {}

            if self.on("split"):
                s = unwrap(await self._opt("split", d, "/api/v1/split-expenses/dashboard"))
                d.split = s if isinstance(s, dict) else {}

            if self.on("rewards"):
                r = unwrap(await self._opt("rewards", d, "/api/v1/rewards/overview"))
                d.rewards = r if isinstance(r, dict) else {}
                m = unwrap(await self._opt("rewards", d, "/api/v1/rewards/money"))
                d.money = as_list(m, "accounts")
                if d.modules_ok.get("rewards"):
                    try:
                        d.ledger = as_list(
                            await self.client.get("/api/v1/rewards/ledger", params={"limit": 500}), "entries", "ledger"
                        )
                    except YuvomiApiError:
                        d.ledger = prev.ledger

            if self.on("reminders"):
                d.reminders = as_list(await self._opt("reminders", d, "/api/v1/reminders/pending"), "reminders")

            if self.on("notes"):
                d.notes = as_list(await self._opt("notes", d, "/api/v1/notes"), "notes")

            if self.on("contacts"):
                d.contacts = as_list(await self._opt("contacts", d, "/api/v1/contacts"), "contacts")

            if self.on("documents"):
                d.documents = as_list(
                    await self._opt("documents", d, "/api/v1/documents", {"expiring": 60}), "documents"
                )

            if self.on("schedule"):
                d.shifts = as_list(
                    await self._opt(
                        "schedule",
                        d,
                        "/api/v1/schedule/entries",
                        {"from": today.isoformat(), "to": (today + timedelta(days=7)).isoformat()},
                    ),
                    "entries",
                )

            if self.on("health"):
                d.medications = as_list(
                    await self._opt("health", d, "/api/v1/health/medications", {"active": 1}), "medications"
                )
                d.vitals = as_list(
                    await self._opt(
                        "health",
                        d,
                        "/api/v1/health/vitals",
                        {"from": (today - timedelta(days=30)).isoformat()},
                    ),
                    "vitals",
                )
                d.prevention_due = as_list(await self._opt("health", d, "/api/v1/health/prevention/due"))
                n = unwrap(await self._opt("health", d, "/api/v1/health/nutrition/summary"))
                d.nutrition = n if isinstance(n, dict) else {}

        except YuvomiAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except YuvomiError as err:
            raise UpdateFailed(f"Yuvomi not reachable: {err}") from err

        if self._last is not None and self.fire_events:
            self._fire_changes(prev, d)
        self._last = d
        return d

    # --- Änderungsereignisse (Yuvomi -> HA) ------------------------------

    def _fire_changes(self, old: YuvomiData, new: YuvomiData) -> None:
        def index(items: list[dict[str, Any]]) -> dict[str, str]:
            return {
                str(i.get("id")): json.dumps(i, sort_keys=True, default=str)
                for i in items
                if i.get("id") is not None
            }

        pairs = [(res, getattr(old, attr), getattr(new, attr)) for res, attr in TRACKED.items()]
        for lid, items in new.shopping_items.items():
            pairs.append(("shopping_item", old.shopping_items.get(lid, []), items))
        for resource, before, after in pairs:
            a, b = index(before), index(after)
            if not a and not b:
                continue
            after_by_id = {str(i.get("id")): i for i in after}
            before_by_id = {str(i.get("id")): i for i in before}
            for key in b.keys() - a.keys():
                self._fire(resource, "created", after_by_id[key])
            for key in a.keys() - b.keys():
                self._fire(resource, "deleted", before_by_id[key])
            for key in a.keys() & b.keys():
                if a[key] != b[key]:
                    self._fire(resource, "updated", after_by_id[key], before_by_id[key])

    def _fire(self, resource: str, action: str, item: dict[str, Any], old: dict[str, Any] | None = None) -> None:
        payload = {
            "entry_id": self.config_entry.entry_id,
            "resource": resource,
            "action": action,
            "id": item.get("id"),
            "title": item.get("title") or item.get("name"),
            "item": item,
        }
        if old is not None:
            payload["changed"] = sorted(
                k for k in set(item) | set(old) if item.get(k) != old.get(k)
            )
            if item.get("status") != old.get("status"):
                payload["old_status"] = old.get("status")
        self.hass.bus.async_fire(EVENT_CHANGED, payload)

    # --- Hilfen --------------------------------------------------------------

    def member_name(self, user_id: Any) -> str | None:
        try:
            return self.data.members.get(int(user_id))
        except (TypeError, ValueError):
            return None


def parse_date(value: Any) -> date | None:
    """YYYY-MM-DD (oder Datum mit Zeit) zu date."""
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def first(item: dict[str, Any], *keys: str) -> Any:
    """Erster vorhandener Wert – die API benennt manche Felder je Modul anders."""
    for k in keys:
        v = item.get(k)
        if v not in (None, ""):
            return v
    return None
