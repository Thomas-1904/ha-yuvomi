"""Binärsensoren: Müll heute/morgen, Aufgaben überfällig, Haushaltshilfe da."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import YuvomiCoordinator, YuvomiData, parse_date
from .entity import YuvomiEntity, module_available
from .sensor import _overdue_tasks


def _waste_on(d: YuvomiData, offset: int) -> list[str]:
    target = dt_util.now().date() + timedelta(days=offset)
    names: list[str] = []
    for entry in d.waste_next:
        nxt = entry.get("next")
        if isinstance(nxt, dict) and parse_date(nxt.get("date_key")) == target:
            names.append(nxt.get("type_name") or (entry.get("type") or {}).get("name") or "?")
    if not names:
        for p in d.dashboard.get("wastePickups") or []:
            if isinstance(p, dict) and parse_date(p.get("date_key") or p.get("date")) == target:
                names.append(p.get("type_name") or p.get("name") or "?")
    return names


def _housekeeping(d: YuvomiData) -> dict[str, Any]:
    hk = d.dashboard.get("housekeeping")
    return hk if isinstance(hk, dict) else {}


@dataclass(frozen=True, kw_only=True)
class YuvomiBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[YuvomiData], bool]
    attrs_fn: Callable[[YuvomiData], dict[str, Any]] | None = None
    module: str = "core"


BINARY_SENSORS: tuple[YuvomiBinaryDescription, ...] = (
    YuvomiBinaryDescription(
        key="waste_today",
        translation_key="waste_today",
        icon="mdi:delete-clock-outline",
        value_fn=lambda d: bool(_waste_on(d, 0)),
        attrs_fn=lambda d: {"tonnen": _waste_on(d, 0)},
        module="waste",
    ),
    YuvomiBinaryDescription(
        key="waste_tomorrow",
        translation_key="waste_tomorrow",
        icon="mdi:delete-clock",
        value_fn=lambda d: bool(_waste_on(d, 1)),
        attrs_fn=lambda d: {"tonnen": _waste_on(d, 1)},
        module="waste",
    ),
    YuvomiBinaryDescription(
        key="tasks_overdue_any",
        translation_key="tasks_overdue_any",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: (_overdue_tasks(d) or 0) > 0,
        module="tasks",
    ),
    YuvomiBinaryDescription(
        key="reminder_due",
        translation_key="reminder_due",
        icon="mdi:bell-alert-outline",
        value_fn=lambda d: bool(d.reminders),
        module="reminders",
    ),
    YuvomiBinaryDescription(
        key="pantry_expired",
        translation_key="pantry_expired",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: any(
            (parse_date(i.get("expires_on") or i.get("best_before")) or dt_util.now().date())
            < dt_util.now().date()
            for i in d.pantry
        ),
        module="pantry",
    ),
    YuvomiBinaryDescription(
        key="housekeeper_present",
        module="housekeeping",
        translation_key="housekeeper_present",
        device_class=BinarySensorDeviceClass.PRESENCE,
        value_fn=lambda d: bool(_housekeeping(d).get("present")),
        attrs_fn=lambda d: {
            "seit": _housekeeping(d).get("presentSince"),
            "name": _housekeeping(d).get("workerName"),
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: YuvomiCoordinator = entry.runtime_data
    async_add_entities(
        YuvomiBinarySensor(coordinator, desc)
        for desc in BINARY_SENSORS
        if module_available(coordinator, desc.module)
    )


class YuvomiBinarySensor(YuvomiEntity, BinarySensorEntity):
    entity_description: YuvomiBinaryDescription

    def __init__(self, coordinator: YuvomiCoordinator, description: YuvomiBinaryDescription) -> None:
        super().__init__(coordinator, description.key, description.module)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn else None
