"""Basisklasse für Yuvomi-Entitäten."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import YuvomiCoordinator


def unique_prefix(entry_id: str, module: str) -> str:
    """Eindeutige IDs tragen das Modul – so lassen sich abgewählte Module aufräumen."""
    return f"{entry_id}_{module}_"


class YuvomiEntity(CoordinatorEntity[YuvomiCoordinator]):
    """Gemeinsames Gerät und Namensschema."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: YuvomiCoordinator, key: str, module: str = "core") -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self.module = module
        self._attr_unique_id = unique_prefix(entry.entry_id, module) + key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Yuvomi",
            manufacturer=MANUFACTURER,
            model="Family planner",
            sw_version=coordinator.data.version if coordinator.data else None,
            configuration_url=coordinator.client.base_url,
            entry_type=DeviceEntryType.SERVICE,
        )


def module_available(coordinator: YuvomiCoordinator, module: str) -> bool:
    """Modul aktiviert und in Yuvomi erreichbar (Recht vorhanden)."""
    if module == "core":
        return True
    if not coordinator.on(module):
        return False
    return coordinator.data.modules_ok.get(module, False) is not False
