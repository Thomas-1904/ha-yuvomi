"""Buttons: Putzaufgabe als erledigt markieren, Daten neu laden."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import YuvomiError
from .const import DOMAIN
from .coordinator import YuvomiCoordinator
from .util import txt
from .entity import YuvomiEntity, module_available


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: YuvomiCoordinator = entry.runtime_data
    async_add_entities([YuvomiRefreshButton(coordinator)])

    known: set[int] = set()

    @callback
    def _add_new() -> None:
        if not module_available(coordinator, "housekeeping"):
            return
        new = []
        for t in coordinator.data.housekeeping:
            tid = t.get("id")
            if tid is None or int(tid) in known:
                continue
            known.add(int(tid))
            new.append(YuvomiHousekeepingButton(coordinator, int(tid)))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))

    known2: set[str] = set()

    @callback
    def _add_v2() -> None:
        new: list[ButtonEntity] = []
        if module_available(coordinator, "budget"):
            for sub in coordinator.data.subscriptions:
                sid = sub.get("id")
                if sid is not None and f"s{sid}" not in known2:
                    known2.add(f"s{sid}")
                    new.append(YuvomiRenewButton(coordinator, sid))
        if module_available(coordinator, "inventory"):
            for item in coordinator.data.inventory:
                iid = item.get("id")
                if iid is not None and item.get("tracked_dates") and f"i{iid}" not in known2:
                    known2.add(f"i{iid}")
                    new.append(YuvomiInventoryDoneButton(coordinator, iid))
        if new:
            async_add_entities(new)

    _add_v2()
    entry.async_on_unload(coordinator.async_add_listener(_add_v2))


class YuvomiRefreshButton(YuvomiEntity, ButtonEntity):
    _attr_translation_key = "refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: YuvomiCoordinator) -> None:
        super().__init__(coordinator, "refresh")

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()


class YuvomiHousekeepingButton(YuvomiEntity, ButtonEntity):
    """Eine Putzaufgabe aus dem Haushaltsplan als erledigt markieren."""

    _attr_icon = "mdi:broom"

    def __init__(self, coordinator: YuvomiCoordinator, task_id: int) -> None:
        super().__init__(coordinator, f"done_{task_id}", "housekeeping")
        self._task_id = task_id

    def _task(self) -> dict[str, Any] | None:
        for t in self.coordinator.data.housekeeping:
            if t.get("id") == self._task_id:
                return t
        return None

    @property
    def name(self) -> str:
        t = self._task() or {}
        return f"{txt(self.hass, 'Erledigt', 'Done')}: {t.get('name') or t.get('title') or self._task_id}"

    @property
    def available(self) -> bool:
        return super().available and self._task() is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        t = self._task() or {}
        return {"task_id": self._task_id, "zuletzt": t.get("last_completed")}

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.post(
                f"/api/v1/housekeeping/decay-tasks/{self._task_id}/complete"
            )
        except YuvomiError as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="api_error", translation_placeholders={"error": str(err)}) from err
        await self.coordinator.async_request_refresh()


class YuvomiRenewButton(YuvomiEntity, ButtonEntity):
    """Abo auf den nächsten Zahlungstermin weiterschalten."""

    _attr_icon = "mdi:autorenew"

    def __init__(self, coordinator: YuvomiCoordinator, sub_id: Any) -> None:
        super().__init__(coordinator, f"renew_{sub_id}", "budget")
        self._id = sub_id

    def _sub(self) -> dict[str, Any] | None:
        return next((s for s in self.coordinator.data.subscriptions if s.get("id") == self._id), None)

    @property
    def name(self) -> str:
        return f"{txt(self.hass, 'Abo bezahlt', 'Subscription paid')}: {(self._sub() or {}).get('name') or self._id}"

    @property
    def available(self) -> bool:
        return super().available and self._sub() is not None

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.post(f"/api/v1/budget/subscriptions/{self._id}/renew")
        except YuvomiError as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="api_error", translation_placeholders={"error": str(err)}) from err
        await self.coordinator.async_request_refresh()


class YuvomiInventoryDoneButton(YuvomiEntity, ButtonEntity):
    """Nächsten Wartungstermin eines Inventar-Gegenstands als erledigt markieren."""

    _attr_icon = "mdi:wrench-check-outline"

    def __init__(self, coordinator: YuvomiCoordinator, item_id: Any) -> None:
        super().__init__(coordinator, f"maint_{item_id}", "inventory")
        self._id = item_id

    def _item(self) -> dict[str, Any] | None:
        return next((i for i in self.coordinator.data.inventory if i.get("id") == self._id), None)

    def _next_date(self) -> dict[str, Any] | None:
        dates = [t for t in (self._item() or {}).get("tracked_dates") or [] if isinstance(t, dict)]
        dates.sort(key=lambda t: t.get("date") or "9999")
        return dates[0] if dates else None

    @property
    def name(self) -> str:
        nd = self._next_date() or {}
        return f"{txt(self.hass, 'Erledigt', 'Done')}: {nd.get('label') or txt(self.hass, 'Wartung', 'Maintenance')} {(self._item() or {}).get('name') or ''}".strip()

    @property
    def available(self) -> bool:
        return super().available and self._next_date() is not None

    async def async_press(self) -> None:
        nd = self._next_date()
        if not nd:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_open_visit")
        try:
            await self.coordinator.client.post(
                f"/api/v1/inventory/items/{self._id}/dates/{nd.get('id')}/complete"
            )
        except YuvomiError as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="api_error", translation_placeholders={"error": str(err)}) from err
        await self.coordinator.async_request_refresh()
