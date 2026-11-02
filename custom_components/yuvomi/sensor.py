"""Sensoren aus der Yuvomi-Übersicht."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .util import txt
from .coordinator import YuvomiCoordinator, YuvomiData, first, parse_date
from .entity import YuvomiEntity, module_available


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _len(value: Any) -> int | None:
    return len(value) if isinstance(value, list) else None


def _open_tasks(d: YuvomiData) -> int | None:
    v = d.dashboard.get("openTaskCount")
    if v is not None:
        return int(v)
    return sum(1 for t in d.tasks if t.get("status") != "done")


def _overdue_tasks(d: YuvomiData) -> int | None:
    v = d.dashboard.get("overdueTaskCount")
    if v is not None:
        return int(v)
    today = dt_util.now().date()
    return sum(
        1
        for t in d.tasks
        if t.get("status") != "done" and (parse_date(t.get("due_date")) or date.max) < today
    )


def _meals_today(d: YuvomiData) -> list[dict[str, Any]]:
    meals = d.dashboard.get("todayMeals")
    if isinstance(meals, list):
        return [m for m in meals if isinstance(m, dict)]
    today = dt_util.now().date().isoformat()
    return [m for m in d.meals_week if (m.get("date") or "")[:10] == today]


def _meal_state(d: YuvomiData) -> str | None:
    meals = _meals_today(d)
    order = {"breakfast": 0, "lunch": 1, "snack": 2, "dinner": 3}
    # Bevorzugt Abendessen, sonst Mittag, sonst das erste
    for kind in ("dinner", "lunch"):
        for m in meals:
            if m.get("meal_type") == kind:
                return (m.get("title") or "")[:255]
    meals.sort(key=lambda m: order.get(m.get("meal_type"), 9))
    return (meals[0].get("title") or "")[:255] if meals else "Nichts geplant"


def _meal_attrs(d: YuvomiData) -> dict[str, Any]:
    return {
        "heute": [
            {
                "mahlzeit": m.get("meal_type"),
                "titel": m.get("title"),
                "koch": m.get("cook_name"),
            }
            for m in _meals_today(d)
        ],
        "woche": [
            {"datum": m.get("date"), "mahlzeit": m.get("meal_type"), "titel": m.get("title")}
            for m in sorted(d.meals_week, key=lambda m: (m.get("date") or "", m.get("meal_type") or ""))
        ],
    }


def _waste_next_all(d: YuvomiData) -> tuple[date | None, list[str]]:
    best: date | None = None
    names: list[str] = []
    for entry in d.waste_next:
        nxt = entry.get("next")
        if not isinstance(nxt, dict):
            continue
        day = parse_date(nxt.get("date_key"))
        if day is None:
            continue
        name = nxt.get("type_name") or (entry.get("type") or {}).get("name") or "?"
        if best is None or day < best:
            best, names = day, [name]
        elif day == best:
            names.append(name)
    return best, names


def _pantry(d: YuvomiData) -> dict[str, Any]:
    p = d.dashboard.get("pantryExpiring")
    return p if isinstance(p, dict) else {}


def _next_birthday(d: YuvomiData) -> tuple[date | None, dict[str, Any] | None]:
    today = dt_util.now().date()
    best: tuple[date | None, dict[str, Any] | None] = (None, None)
    for b in d.birthdays:
        born = parse_date(b.get("birth_date"))
        if born is None:
            continue
        for year in (today.year, today.year + 1):
            try:
                nxt = born.replace(year=year)
            except ValueError:
                nxt = date(year, 2, 28)
            if nxt >= today:
                if best[0] is None or nxt < best[0]:
                    best = (nxt, b)
                break
    return best


def _housekeeping_due(d: YuvomiData) -> list[dict[str, Any]]:
    """Putzaufgaben, die fällig sind (Fortschritt >= 100 % oder Status 'due')."""
    due = []
    for t in d.housekeeping:
        level = _num(t.get("level") or t.get("progress") or t.get("decay") or t.get("percent"))
        if (
            t.get("is_due")
            or t.get("status") in ("due", "overdue")
            or t.get("urgency_status") in ("due", "overdue")
            or (level is not None and level >= 100)
        ):
            due.append(t)
    return due


@dataclass(frozen=True, kw_only=True)
class YuvomiSensorDescription(SensorEntityDescription):
    value_fn: Callable[[YuvomiData], Any]
    attrs_fn: Callable[[YuvomiData], dict[str, Any]] | None = None
    module: str = "core"


SENSORS: tuple[YuvomiSensorDescription, ...] = (
    YuvomiSensorDescription(
        key="tasks_open",
        module="tasks",
        translation_key="tasks_open",
        icon="mdi:checkbox-marked-circle-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_open_tasks,
        attrs_fn=lambda d: {
            "dringend": [
                {"titel": t.get("title"), "faellig": t.get("due_date")}
                for t in (d.dashboard.get("urgentTasks") or [])
                if isinstance(t, dict)
            ]
        },
    ),
    YuvomiSensorDescription(
        key="tasks_overdue",
        module="tasks",
        translation_key="tasks_overdue",
        icon="mdi:alert-circle-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_overdue_tasks,
    ),
    YuvomiSensorDescription(
        key="tasks_done_today",
        module="tasks",
        translation_key="tasks_done_today",
        icon="mdi:check-all",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: (
            _len(d.dashboard.get("tasksDoneToday"))
            if isinstance(d.dashboard.get("tasksDoneToday"), list)
            else d.dashboard.get("tasksDoneToday")
        ),
    ),
    YuvomiSensorDescription(
        key="meal_today",
        translation_key="meal_today",
        icon="mdi:silverware-fork-knife",
        value_fn=_meal_state,
        attrs_fn=_meal_attrs,
        module="meals",
    ),
    YuvomiSensorDescription(
        key="shopping_open",
        module="shopping",
        translation_key="shopping_open",
        icon="mdi:cart-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.dashboard.get("shoppingOpenCount")
        if d.dashboard.get("shoppingOpenCount") is not None
        else sum(1 for items in d.shopping_items.values() for i in items if not i.get("is_checked")),
    ),
    YuvomiSensorDescription(
        key="waste_next",
        translation_key="waste_next",
        icon="mdi:trash-can-outline",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda d: _waste_next_all(d)[0],
        attrs_fn=lambda d: {
            "tonnen": _waste_next_all(d)[1],
            "in_tagen": (
                (_waste_next_all(d)[0] - dt_util.now().date()).days
                if _waste_next_all(d)[0]
                else None
            ),
        },
        module="waste",
    ),
    YuvomiSensorDescription(
        key="pantry_expiring",
        module="pantry",
        translation_key="pantry_expiring",
        icon="mdi:fridge-alert-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _pantry(d).get("todayCount")
        if _pantry(d).get("todayCount") is not None
        else _pantry(d).get("count"),
        attrs_fn=lambda d: {
            "artikel": [
                {"name": i.get("name"), "mhd": i.get("expires_on") or i.get("best_before")}
                for i in (_pantry(d).get("todayItems") or _pantry(d).get("items") or [])
                if isinstance(i, dict)
            ]
        },
    ),
    YuvomiSensorDescription(
        key="events_upcoming",
        module="calendar",
        translation_key="events_upcoming",
        icon="mdi:calendar-clock",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _len(d.dashboard.get("upcomingEvents")),
        attrs_fn=lambda d: {
            "termine": [
                {"titel": e.get("title"), "start": e.get("start_datetime"), "ganztags": e.get("all_day")}
                for e in (d.dashboard.get("upcomingEvents") or [])
                if isinstance(e, dict)
            ]
        },
    ),
    YuvomiSensorDescription(
        key="birthday_next",
        translation_key="birthday_next",
        icon="mdi:cake-variant-outline",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda d: _next_birthday(d)[0],
        attrs_fn=lambda d: {
            "name": (_next_birthday(d)[1] or {}).get("name"),
            "in_tagen": (
                (_next_birthday(d)[0] - dt_util.now().date()).days if _next_birthday(d)[0] else None
            ),
        },
        module="birthdays",
    ),
    YuvomiSensorDescription(
        key="housekeeping_due",
        translation_key="housekeeping_due",
        icon="mdi:broom",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(_housekeeping_due(d)),
        attrs_fn=lambda d: {
            "faellig": [t.get("name") or t.get("title") for t in _housekeeping_due(d)],
            "alle": [
                {
                    "id": t.get("id"),
                    "name": t.get("name") or t.get("title"),
                    "bereich": t.get("area"),
                    "zuletzt": t.get("last_completed"),
                    "intervall_tage": t.get("frequency_days") or t.get("interval_days") or t.get("decay_days"),
                    "status": t.get("urgency_status") or t.get("status"),
                    "faellig_am": t.get("due_date"),
                }
                for t in d.housekeeping
            ],
        },
        module="housekeeping",
    ),
    YuvomiSensorDescription(
        key="budget_expenses",
        translation_key="budget_expenses",
        icon="mdi:cash-minus",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="EUR",
        suggested_display_precision=2,
        value_fn=lambda d: abs(_num(d.budget_summary.get("expenses")) or 0) if d.budget_summary else None,
        attrs_fn=lambda d: {
            "einnahmen": d.budget_summary.get("income"),
            "saldo": d.budget_summary.get("balance"),
            "kategorien": d.budget_summary.get("byCategory"),
        },
        module="budget",
    ),
    YuvomiSensorDescription(
        key="budget_balance",
        translation_key="budget_balance",
        icon="mdi:scale-balance",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="EUR",
        suggested_display_precision=2,
        value_fn=lambda d: _num(d.budget_summary.get("balance")),
        module="budget",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: YuvomiCoordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        YuvomiSensor(coordinator, desc)
        for desc in SENSORS + SENSORS_V2
        if module_available(coordinator, desc.module)
    ]
    entities.append(YuvomiWebhookSensor(coordinator))
    if coordinator.bring_sync is not None or entry.options.get("bring_entity"):
        entities.append(YuvomiBringSyncSensor(coordinator))
    async_add_entities(entities)

    known: set[str] = set()

    @callback
    def _add_waste_types() -> None:
        if not module_available(coordinator, "waste"):
            return
        new = []
        for entry_ in coordinator.data.waste_next:
            wtype = entry_.get("type") or {}
            type_id = wtype.get("id")
            if type_id is None or str(type_id) in known:
                continue
            known.add(str(type_id))
            new.append(YuvomiWasteTypeSensor(coordinator, int(type_id), wtype.get("name") or str(type_id)))
        if new:
            async_add_entities(new)

    _add_waste_types()
    entry.async_on_unload(coordinator.async_add_listener(_add_waste_types))
    setup_dynamic(coordinator, entry, async_add_entities)


class YuvomiSensor(YuvomiEntity, SensorEntity):
    entity_description: YuvomiSensorDescription

    def __init__(self, coordinator: YuvomiCoordinator, description: YuvomiSensorDescription) -> None:
        super().__init__(coordinator, description.key, description.module)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn else None


class YuvomiWasteTypeSensor(YuvomiEntity, SensorEntity):
    """Nächste Abholung einer Tonne."""

    _attr_device_class = SensorDeviceClass.DATE
    _attr_icon = "mdi:trash-can-outline"

    def __init__(self, coordinator: YuvomiCoordinator, type_id: int, name: str) -> None:
        super().__init__(coordinator, f"type_{type_id}", "waste")
        self._type_id = type_id
        self._attr_name = f"{txt(coordinator.hass, 'Müll', 'Waste')} {name}"

    def _entry(self) -> dict[str, Any] | None:
        for e in self.coordinator.data.waste_next:
            if (e.get("type") or {}).get("id") == self._type_id:
                return e
        return None

    @property
    def available(self) -> bool:
        return super().available and self._entry() is not None

    @property
    def native_value(self) -> date | None:
        e = self._entry() or {}
        nxt = e.get("next") or {}
        return parse_date(nxt.get("date_key")) if isinstance(nxt, dict) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        e = self._entry() or {}
        wtype = e.get("type") or {}
        day = self.native_value
        nxt = e.get("next") if isinstance(e.get("next"), dict) else {}
        return {
            "in_tagen": (day - dt_util.now().date()).days if day else None,
            "farbe": wtype.get("color"),
            "symbol": wtype.get("icon"),
            "verschoben": nxt.get("moved"),
            "type_id": self._type_id,
        }


class YuvomiWebhookSensor(YuvomiEntity, SensorEntity):
    """Zeigt die Webhook-Adresse für den Yuvomi-Benachrichtigungskanal."""

    _attr_translation_key = "webhook"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:webhook"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: YuvomiCoordinator) -> None:
        super().__init__(coordinator, "webhook")

    @property
    def native_value(self) -> str | None:
        from homeassistant.components.webhook import async_generate_path

        wid = self.coordinator.config_entry.data.get("webhook_id")
        return async_generate_path(wid) if wid else None



class YuvomiBringSyncSensor(YuvomiEntity, SensorEntity):
    """Zeitpunkt und Ergebnis des letzten Bring-Abgleichs."""

    _attr_translation_key = "bring_sync"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:sync"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: YuvomiCoordinator) -> None:
        super().__init__(coordinator, "bring_sync", "shopping")

    @property
    def native_value(self):
        s = self.coordinator.bring_sync
        return dt_util.parse_datetime(s.last_run) if s and s.last_run else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self.coordinator.bring_sync
        if not s:
            return {}
        return {"bring": s.bring, "liste": s.list_id, "ergebnis": s.last_result, "fehler": s.last_error}


from .sensor_v2 import SENSORS_V2, setup_dynamic  # noqa: E402
