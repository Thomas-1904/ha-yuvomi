"""Datenabruf: ein Koordinator für alle Yuvomi-Entitäten (nur aktivierte Module)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import copy
from dataclasses import dataclass, field
from datetime import date, timedelta
import json
import logging
import time
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
# Data that rarely changes (birthdays, recipes, budget, points history …) is fetched in full
# only this often – and right away after a webhook from Yuvomi or a change made in HA.
SLOW_EVERY = 600  # seconds
PARALLEL = 4  # requests at the same time


def _changed(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> bool:
    return json.dumps(before, sort_keys=True, default=str) != json.dumps(after, sort_keys=True, default=str)


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
        self._full = True
        self._slow_at = 0.0
        self._day: date | None = None
        self._sem = asyncio.Semaphore(PARALLEL)
        # Called when Yuvomi reports another version (it was updated) – set up in __init__
        self.on_version_change: Callable[[], Awaitable[None]] | None = None
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

    async def async_request_refresh(self) -> None:
        """A change (webhook from Yuvomi, a write from HA) reloads everything, not only the fast part."""
        self._full = True
        await super().async_request_refresh()

    async def _gather(self, *coros: Any) -> None:
        """Run fetches side by side (at most PARALLEL at once); the first error wins, auth errors first."""

        async def limited(c: Any) -> Any:
            async with self._sem:
                return await c

        results = await asyncio.gather(*(limited(c) for c in coros), return_exceptions=True)
        errors = [r for r in results if isinstance(r, BaseException)]
        if errors:
            raise next((e for e in errors if isinstance(e, YuvomiAuthError)), errors[0])

    async def _async_update_data(self) -> YuvomiData:
        prev = self._last or YuvomiData()
        today = dt_util.now().date()
        now = time.monotonic()
        full = (
            self._full
            or self._last is None
            or today != self._day
            or now - self._slow_at >= SLOW_EVERY
        )
        self._full = False
        if full:
            d = YuvomiData()
        else:
            # Fast cycle: keep the slow data of the last full refresh, fetch only what changes often
            d = copy.copy(prev)
            d.modules_ok = dict(prev.modules_ok)
            d.shopping_items = {}
        try:
            await self._fetch(d, prev, today, full)
        except YuvomiAuthError as err:
            self._full = True
            raise ConfigEntryAuthFailed(str(err)) from err
        except YuvomiError as err:
            self._full = True
            raise UpdateFailed(f"Yuvomi not reachable: {err}") from err

        if full:
            self._slow_at, self._day = now, today
        if prev.version and d.version and prev.version != d.version and self.on_version_change:
            self.hass.async_create_task(self.on_version_change(), eager_start=False)
        if self._last is not None and self.fire_events:
            self._fire_changes(prev, d)
        self._last = d
        return d

    async def _fetch(self, d: YuvomiData, prev: YuvomiData, today: date, full: bool) -> None:
        # Re-read the version with every full refresh and after an outage (Yuvomi restarted for an update)
        d.version = (prev.version if self.last_update_success and not full else None) or await self.client.version()

        async def dashboard() -> None:
            dash = unwrap(await self._opt("dashboard", d, "/api/v1/dashboard", {"events_limit": 12}))
            d.dashboard = dash if isinstance(dash, dict) else {}
            if self.on("calendar"):
                d.modules_ok["calendar"] = d.modules_ok.get("dashboard", False)

        async def family() -> None:
            fam = [m for m in as_list(await self._opt("family", d, "/api/v1/family/members")) if m.get("id") is not None]
            d.members = {int(m["id"]): m.get("display_name") or str(m["id"]) for m in fam}
            d.member_info = {
                int(m["id"]): {"name": m.get("display_name"), "farbe": m.get("avatar_color"), "geburtstag": m.get("birth_date")}
                for m in fam
            }

        async def tasks() -> None:
            raw = await self._opt(
                "tasks", d, "/api/v1/tasks", [("status", s) for s in TASK_STATUSES] + [("include_future", "1")]
            )
            d.tasks = [t for t in as_list(raw, "tasks") if not t.get("parent_task_id")]
            # The completion history feeds points and leaderboard: reload it when tasks changed
            if d.modules_ok.get("tasks") and (full or _changed(prev.tasks, d.tasks)):
                try:
                    d.completions = as_list(
                        await self.client.get("/api/v1/tasks/completions", params={"limit": 200}), "completions"
                    )
                except YuvomiApiError:
                    d.completions = prev.completions
            if self.on("rewards") and not full and d.modules_ok.get("rewards") and _changed(prev.tasks, d.tasks):
                await rewards()

        async def shopping() -> None:
            d.shopping_lists = as_list(await self._opt("shopping", d, "/api/v1/shopping"), "lists")
            if not d.modules_ok.get("shopping"):
                return
            if full:
                try:
                    d.shopping_categories = as_list(await self.client.get("/api/v1/shopping/categories"), "categories")
                except YuvomiApiError:
                    d.shopping_categories = prev.shopping_categories
            versions = as_list(await self.client.get("/api/v1/shopping/versions"))
            d.shopping_versions = {int(v["list_id"]): v.get("version") for v in versions if v.get("list_id") is not None}
            for lst in d.shopping_lists:
                lid = int(lst["id"])
                ver = d.shopping_versions.get(lid)
                if lid in prev.shopping_items and ver is not None and prev.shopping_versions.get(lid) == ver:
                    d.shopping_items[lid] = prev.shopping_items[lid]
                else:
                    d.shopping_items[lid] = as_list(await self.client.get(f"/api/v1/shopping/{lid}/items"), "items")

        async def simple(module: str, attr: str, path: str, params: Any = None, *keys: str) -> None:
            setattr(d, attr, as_list(await self._opt(module, d, path, params), *keys))

        async def budget() -> None:
            s = unwrap(await self._opt("budget", d, "/api/v1/budget/summary", {"month": today.strftime("%Y-%m")}))
            d.budget_summary = s if isinstance(s, dict) else {}
            d.accounts = as_list(await self._opt("budget", d, "/api/v1/budget/accounts"), "accounts")
            d.subscriptions = as_list(await self._opt("budget", d, "/api/v1/budget/subscriptions"), "subscriptions")

        async def loans() -> None:
            raw = unwrap(await self._opt("loans", d, "/api/v1/budget/loans"))
            d.loans = as_list(raw, "loans")
            if isinstance(raw, dict):
                d.loans_summary = raw.get("summary") or {}

        async def split() -> None:
            s = unwrap(await self._opt("split", d, "/api/v1/split-expenses/dashboard"))
            d.split = s if isinstance(s, dict) else {}

        async def rewards() -> None:
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

        async def health() -> None:
            d.medications = as_list(await self._opt("health", d, "/api/v1/health/medications", {"active": 1}), "medications")
            d.vitals = as_list(
                await self._opt("health", d, "/api/v1/health/vitals", {"from": (today - timedelta(days=30)).isoformat()}),
                "vitals",
            )
            d.prevention_due = as_list(await self._opt("health", d, "/api/v1/health/prevention/due"))
            n = unwrap(await self._opt("health", d, "/api/v1/health/nutrition/summary"))
            d.nutrition = n if isinstance(n, dict) else {}

        # Fast part: every cycle – what the family changes during the day
        jobs: list[Any] = [dashboard()]
        if self.on("tasks"):
            jobs.append(tasks())
        if self.on("shopping"):
            jobs.append(shopping())
        if self.on("meals"):
            jobs.append(simple("meals", "meals_week", "/api/v1/meals", {"week": today.isoformat()}, "meals"))
        if self.on("pantry"):
            jobs.append(simple("pantry", "pantry", "/api/v1/pantry", None, "items"))
        if self.on("housekeeping"):
            jobs.append(simple("housekeeping", "housekeeping", "/api/v1/housekeeping/decay-tasks", None, "tasks"))
        if self.on("reminders"):
            jobs.append(simple("reminders", "reminders", "/api/v1/reminders/pending", None, "reminders"))

        # Slow part: every SLOW_EVERY, at midnight, and right after any change (webhook / write from HA)
        if full:
            jobs.append(family())
            if self.on("recipes"):
                jobs.append(simple("recipes", "recipes", "/api/v1/recipes", None, "recipes"))
            if self.on("inventory"):
                jobs.append(simple("inventory", "inventory", "/api/v1/inventory/items", None, "items"))
            if self.on("waste"):
                jobs.append(simple("waste", "waste_next", "/api/v1/waste/occurrences/next"))
            if self.on("birthdays"):
                jobs.append(simple("birthdays", "birthdays", "/api/v1/birthdays", None, "birthdays"))
            if self.on("budget"):
                jobs.append(budget())
            if self.on("loans"):
                jobs.append(loans())
            if self.on("split"):
                jobs.append(split())
            if self.on("rewards"):
                jobs.append(rewards())
            if self.on("notes"):
                jobs.append(simple("notes", "notes", "/api/v1/notes", None, "notes"))
            if self.on("contacts"):
                jobs.append(simple("contacts", "contacts", "/api/v1/contacts", None, "contacts"))
            if self.on("documents"):
                jobs.append(simple("documents", "documents", "/api/v1/documents", {"expiring": 60}, "documents"))
            if self.on("schedule"):
                jobs.append(simple(
                    "schedule", "shifts", "/api/v1/schedule/entries",
                    {"from": today.isoformat(), "to": (today + timedelta(days=7)).isoformat()}, "entries",
                ))
            if self.on("health"):
                jobs.append(health())

        await self._gather(*jobs)

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
