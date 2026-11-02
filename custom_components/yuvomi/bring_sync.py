"""Zwei-Wege-Abgleich zwischen einer Yuvomi-Einkaufsliste und einer Bring-Liste.

Yuvomi ist der Master. Abgeglichen wird über den Artikelnamen (ohne Groß-/Kleinschreibung).
Ein gespeicherter Stand des letzten Abgleichs zeigt, auf welcher Seite sich etwas geändert hat:

* neu auf einer Seite (offen)           -> auf der anderen anlegen
* auf einer Seite abgehakt/wieder offen -> auf der anderen übernehmen
* auf einer Seite gelöscht              -> auf der anderen löschen
* Menge (Yuvomi) <-> Spezifikation (Bring)

Bereits erledigte Bring-Einträge („Zuletzt verwendet“) werden nicht nach Yuvomi geholt.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store

from .api import YuvomiError, as_list
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
PERIODIC = timedelta(minutes=5)
DEBOUNCE = 3  # Sekunden


def _key(name: Any) -> str:
    return " ".join(str(name or "").split()).casefold()


def _qty(value: Any) -> str:
    """Menge vergleichbar machen: leer und „1“ gelten als keine Angabe."""
    v = str(value or "").strip()
    return "" if v in ("", "1", "1.0") else v


class BringSync:
    """Hält eine Yuvomi-Einkaufsliste und eine Bring-Liste (todo-Entität) gleich."""

    def __init__(self, hass: HomeAssistant, coordinator, bring_entity: str, list_id: int) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.bring = bring_entity
        self.list_id = int(list_id)
        self._store: Store[dict[str, Any]] = Store(
            hass, STORE_VERSION, f"{DOMAIN}.bring_sync.{coordinator.config_entry.entry_id}"
        )
        self._snap: dict[str, dict[str, Any]] | None = None
        self._lock = asyncio.Lock()
        self._pending: CALLBACK_TYPE | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._last_version: Any = None
        self.last_run: str | None = None
        self.last_result: dict[str, int] = {}
        self.last_error: str | None = None
        self._recent: dict[str, float] = {}  # frisch nach Bring geschrieben: Schlüssel -> Zeitpunkt

    # --- Lebenszyklus ------------------------------------------------------

    async def async_start(self) -> None:
        stored = await self._store.async_load() or {}
        if stored.get("bring") == self.bring and stored.get("list_id") == self.list_id:
            self._snap = stored.get("items") or {}
        else:
            self._snap = {}
        self._unsubs.append(async_track_state_change_event(self.hass, [self.bring], self._on_bring))
        self._unsubs.append(self.coordinator.async_add_listener(self._on_coordinator))
        self._unsubs.append(async_track_time_interval(self.hass, self._on_timer, PERIODIC))
        self.schedule(5)

    @callback
    def async_stop(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()
        if self._pending:
            self._pending()
            self._pending = None

    # --- Auslöser ----------------------------------------------------------

    @callback
    def _on_bring(self, event: Event) -> None:
        self.schedule()

    @callback
    def _on_coordinator(self) -> None:
        ver = (self.coordinator.data.shopping_versions or {}).get(self.list_id)
        if ver != self._last_version:
            self._last_version = ver
            self.schedule()

    @callback
    def _on_timer(self, _now) -> None:
        self.schedule(0)

    @callback
    def schedule(self, delay: float = DEBOUNCE) -> None:
        if self._pending:
            self._pending()

        @callback
        def _run(_now) -> None:
            self._pending = None
            self.hass.async_create_task(self.async_sync(), eager_start=False)

        self._pending = async_call_later(self.hass, delay, _run)

    # --- Abgleich ----------------------------------------------------------

    async def _bring_items(self) -> list[dict[str, Any]]:
        resp = await self.hass.services.async_call(
            "todo",
            "get_items",
            {"entity_id": self.bring, "status": ["needs_action", "completed"]},
            blocking=True,
            return_response=True,
        )
        return list(((resp or {}).get(self.bring) or {}).get("items") or [])

    async def _yuvomi_items(self) -> list[dict[str, Any]]:
        return as_list(await self.coordinator.client.get(f"/api/v1/shopping/{self.list_id}/items"), "items")

    async def _bring(self, service: str, data: dict[str, Any]) -> None:
        await self.hass.services.async_call(
            "todo", service, {"entity_id": self.bring, **data}, blocking=True
        )

    async def async_sync(self) -> dict[str, int]:
        if self._lock.locked():
            self.schedule()
            return {}
        async with self._lock:
            try:
                result = await self._sync()
                self.last_error = None
            except (YuvomiError, Exception) as err:  # noqa: BLE001 – Abgleich darf HA nie stören
                self.last_error = str(err)
                _LOGGER.warning("Bring-Abgleich fehlgeschlagen: %s", err)
                return {}
            from homeassistant.util import dt as dt_util

            self.last_run = dt_util.now().isoformat()
            self.last_result = result
            if any(result.values()):
                _LOGGER.debug("Bring-Abgleich: %s", result)
                await self.coordinator.async_request_refresh()
            else:
                self.coordinator.async_update_listeners()
            return result

    async def _sync(self) -> dict[str, int]:
        if self.hass.states.get(self.bring) is None or self.hass.states.get(self.bring).state == "unavailable":
            raise RuntimeError(f"{self.bring} nicht verfügbar")
        y_raw = await self._yuvomi_items()
        b_raw = await self._bring_items()
        snap = dict(self._snap or {})
        stats = {"yuvomi_neu": 0, "bring_neu": 0, "yuvomi_geaendert": 0, "bring_geaendert": 0, "yuvomi_geloescht": 0, "bring_geloescht": 0}

        def pick(items, open_fn):
            out: dict[str, dict[str, Any]] = {}
            for it in items:
                k = _key(it.get("name") if "name" in it else it.get("summary"))
                if not k:
                    continue
                if k not in out or (open_fn(it) and not open_fn(out[k])):
                    out[k] = it
            return out

        y_open = lambda i: not i.get("is_checked")  # noqa: E731
        b_open = lambda i: i.get("status") == "needs_action"  # noqa: E731
        Y = pick(y_raw, y_open)
        B = pick(b_raw, b_open)

        # Schutz: Liefert eine Seite plötzlich gar nichts mehr, obwohl vorher viel abgeglichen war,
        # wird nicht gelöscht (z. B. Ausfall/Leerantwort).
        allow_delete_y = not (snap and not B and len(snap) > 3)
        allow_delete_b = not (snap and not Y and len(snap) > 3)

        import time

        now = time.monotonic()
        self._recent = {k: t for k, t in self._recent.items() if now - t < 300}
        new_snap: dict[str, dict[str, Any]] = {}
        for k in set(Y) | set(B) | set(snap):
            y, b, s = Y.get(k), B.get(k), snap.get(k)
            if y and b:
                yo, bo = y_open(y), b_open(b)
                yq, bq = _qty(y.get("quantity")), _qty(b.get("description"))
                o = yo
                if yo != bo:
                    if s is not None and yo == s.get("o"):
                        o = bo  # Bring hat sich geändert
                    elif s is not None:
                        o = yo  # Yuvomi hat sich geändert
                    else:
                        o = yo or bo  # erster Abgleich: offen gewinnt
                    if o != yo:
                        await self.coordinator.client.patch(f"/api/v1/shopping/items/{y['id']}", {"is_checked": not o})
                        stats["yuvomi_geaendert"] += 1
                    if o != bo:
                        await self._bring("update_item", {"item": b["uid"], "status": "needs_action" if o else "completed"})
                        stats["bring_geaendert"] += 1
                q = yq
                if yq != bq:
                    if s is not None and yq == s.get("q"):
                        q = bq
                    elif s is not None:
                        q = yq
                    else:
                        q = yq or bq
                    if q != yq:
                        await self.coordinator.client.patch(f"/api/v1/shopping/items/{y['id']}", {"quantity": q})
                        stats["yuvomi_geaendert"] += 1
                    if q != bq:
                        await self._bring("update_item", {"item": b["uid"], "description": q})
                        stats["bring_geaendert"] += 1
                new_snap[k] = {"o": o, "q": q}
            elif y and not b:
                if s is not None and k in self._recent:
                    new_snap[k] = s  # Bring hat den neuen Eintrag noch nicht zurückgemeldet
                elif s is not None:
                    if allow_delete_y:
                        await self.coordinator.client.delete(f"/api/v1/shopping/items/{y['id']}")
                        stats["yuvomi_geloescht"] += 1
                    else:
                        new_snap[k] = s
                elif y_open(y):
                    q = _qty(y.get("quantity"))
                    data: dict[str, Any] = {"item": y.get("name")}
                    if q:
                        data["description"] = q
                    await self._bring("add_item", data)
                    self._recent[k] = now
                    stats["bring_neu"] += 1
                    new_snap[k] = {"o": True, "q": q}
            elif b and not y:
                if s is not None and not b_open(b):
                    pass  # in Yuvomi aufgeräumt, in Bring schon erledigt: bleibt unter „Zuletzt verwendet“
                elif s is not None:
                    if allow_delete_b:
                        await self._bring("remove_item", {"item": b["uid"]})
                        stats["bring_geloescht"] += 1
                    else:
                        new_snap[k] = s
                elif b_open(b):
                    q = _qty(b.get("description"))
                    body: dict[str, Any] = {"name": " ".join(str(b.get("summary")).split())}
                    if q:
                        body["quantity"] = q
                    await self.coordinator.client.post(f"/api/v1/shopping/{self.list_id}/items", body)
                    stats["yuvomi_neu"] += 1
                    new_snap[k] = {"o": True, "q": q}
            # beide weg: Eintrag fällt aus dem Stand
        self._snap = new_snap
        await self._store.async_save({"bring": self.bring, "list_id": self.list_id, "items": new_snap})
        return stats
