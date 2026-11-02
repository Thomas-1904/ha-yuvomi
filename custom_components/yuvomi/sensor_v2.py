"""Sensoren der Version 2: Vorrat, Inventar, Budget, Abos, Kredite, Belohnungen,
Erinnerungen, Notizen, Kontakte, Dokumente, Schichten, Gesundheit."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import YuvomiCoordinator, YuvomiData, first, parse_date
from .entity import YuvomiEntity, module_available
from .sensor import YuvomiSensorDescription
from .util import rrule_text, txt


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _minor(v: Any, minor_unit: Any = 2) -> float | None:
    x = _f(v)
    if x is None:
        return None
    try:
        return round(x / (10 ** int(minor_unit if minor_unit is not None else 2)), 2)
    except (TypeError, ValueError):
        return x


def _days(d: date | None) -> int | None:
    return (d - dt_util.now().date()).days if d else None


def _pantry_low(d: YuvomiData) -> list[dict[str, Any]]:
    out = []
    for i in d.pantry:
        q, mn = _f(i.get("quantity")), _f(first(i, "min_quantity", "minimum", "min_stock"))
        if q is not None and (q <= 0 or (mn is not None and q <= mn)):
            out.append(i)
    return out


def _subs_monthly(d: YuvomiData) -> float | None:
    total = 0.0
    found = False
    for s in d.subscriptions:
        if s.get("status") in ("paused", "completed", "cancelled"):
            continue
        m = _f(first(s, "monthly_cost", "monthly_amount", "normalized_monthly"))
        if m is None:
            amount = _f(s.get("amount"))
            cycle = (s.get("billing_cycle") or s.get("cycle") or "monthly").lower()
            factor = {"weekly": 52 / 12, "monthly": 1, "quarterly": 1 / 3, "half_yearly": 1 / 6, "yearly": 1 / 12, "annually": 1 / 12}.get(cycle, 1)
            m = amount * factor if amount is not None else None
        if m is not None:
            total += m
            found = True
    return round(total, 2) if found else None


def _latest_vital(d: YuvomiData, vtype: str) -> dict[str, Any] | None:
    items = [v for v in d.vitals if v.get("type") == vtype]
    items.sort(key=lambda v: v.get("measured_at") or "", reverse=True)
    return items[0] if items else None


def _vital_value(v: dict[str, Any] | None) -> Any:
    if not v:
        return None
    if v.get("type") == "bp" or v.get("systolic") is not None:
        return f"{v.get('systolic')}/{v.get('diastolic')}"
    return _f(first(v, "value", "value_num"))


def _split_net(d: YuvomiData) -> float | None:
    net = d.split.get("net") or (d.split.get("balance") if isinstance(d.split.get("balance"), (int, float, str)) else None)
    if isinstance(net, dict):
        net = next(iter(net.values()), None)
    if isinstance(net, list) and net:
        net = net[0].get("amount") if isinstance(net[0], dict) else None
    return _f(net)


def _today_shifts(d: YuvomiData) -> list[dict[str, Any]]:
    today = dt_util.now().date()
    return [s for s in d.shifts if parse_date(s.get("date_key") or s.get("date")) == today]


# --- Aufgaben-Statistik aus Yuvomis Erledigungs-Historie --------------------


def _done_dt(c: dict[str, Any]):
    return dt_util.parse_datetime(str(c.get("completed_at") or "").replace(" ", "T"))


def _done_since(d: YuvomiData, days: int, until_days: int = 0) -> list[dict[str, Any]]:
    now = dt_util.now()
    start = dt_util.start_of_local_day(now) - timedelta(days=days - 1)
    end = dt_util.start_of_local_day(now) - timedelta(days=until_days - 1) if until_days else now + timedelta(days=1)
    out = []
    for c in d.completions:
        t = _done_dt(c)
        if t and start <= dt_util.as_local(t) < end:
            out.append(c)
    return out


def _who(c: dict[str, Any]) -> str:
    return str(c.get("person_name") or c.get("user_name") or "?").split(" ")[0]


def _count_by(items: list[dict[str, Any]], key) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in items:
        k = key(c)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _week_attrs(d: YuvomiData) -> dict[str, Any]:
    week, prev = _done_since(d, 7), _done_since(d, 14, 7)
    today = dt_util.now().date()
    per_day = []
    for i in range(13, -1, -1):
        day = today - timedelta(days=i)
        per_day.append({"datum": day.isoformat(), "anzahl": sum(1 for c in d.completions if (t := _done_dt(c)) and dt_util.as_local(t).date() == day)})
    return {
        "vorwoche": len(prev),
        "je_person": _count_by(week, _who),
        "je_kategorie": _count_by(week, lambda c: c.get("category") or "misc"),
        "je_tag": per_day,
        "punkte": sum(int(c.get("points") or 0) for c in week),
    }


def _streak(d: YuvomiData) -> int:
    days = {dt_util.as_local(t).date() for c in d.completions if (t := _done_dt(c))}
    n, day = 0, dt_util.now().date()
    if day not in days:
        day -= timedelta(days=1)
    while day in days:
        n += 1
        day -= timedelta(days=1)
    return n


def _recurring(d: YuvomiData) -> list[dict[str, Any]]:
    out = []
    for t in d.tasks:
        if not t.get("is_recurring") or t.get("status") == "done":
            continue
        sid = t.get("recurrence_series_id") or t.get("id")
        runs = [c for c in d.completions if c.get("series_id") == sid or c.get("task_id") == t.get("id")]
        last = max((_done_dt(c) for c in runs if _done_dt(c)), default=None)
        out.append({
            "id": t.get("id"), "titel": t.get("title"), "regel": rrule_text(t.get("recurrence_rule")),
            "faellig": t.get("due_date"), "uhrzeit": t.get("due_time"),
            "zugewiesen": [u.get("display_name") for u in t.get("assigned_users") or []],
            "erledigt": len(runs), "zuletzt": last.isoformat() if last else None,
        })
    return sorted(out, key=lambda x: x["faellig"] or "9999")


SENSORS_V2: tuple[YuvomiSensorDescription, ...] = (
    YuvomiSensorDescription(
        key="tasks_done_week", translation_key="tasks_done_week", module="tasks", icon="mdi:check-decagram-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(_done_since(d, 7)), attrs_fn=_week_attrs,
    ),
    YuvomiSensorDescription(
        key="tasks_done_month", translation_key="tasks_done_month", module="tasks", icon="mdi:calendar-check-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(_done_since(d, 30)),
        attrs_fn=lambda d: {"je_person": _count_by(_done_since(d, 30), _who), "je_kategorie": _count_by(_done_since(d, 30), lambda c: c.get("category") or "misc")},
    ),
    YuvomiSensorDescription(
        key="tasks_streak", translation_key="tasks_streak", module="tasks", icon="mdi:fire",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_streak,
    ),
    YuvomiSensorDescription(
        key="tasks_recurring", translation_key="tasks_recurring", module="tasks", icon="mdi:repeat",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(_recurring(d)), attrs_fn=lambda d: {"serien": _recurring(d)},
    ),
    YuvomiSensorDescription(
        key="pantry_items", translation_key="pantry_items", module="pantry", icon="mdi:fridge-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.pantry),
    ),
    YuvomiSensorDescription(
        key="pantry_low", translation_key="pantry_low", module="pantry", icon="mdi:package-variant-remove",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(_pantry_low(d)),
        attrs_fn=lambda d: {"artikel": [i.get("name") for i in _pantry_low(d)]},
    ),
    YuvomiSensorDescription(
        key="recipes", translation_key="recipes", module="recipes", icon="mdi:book-open-variant",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.recipes),
        attrs_fn=lambda d: {"rezepte": {str(r.get("id")): r.get("title") or r.get("name") for r in d.recipes}},
    ),
    YuvomiSensorDescription(
        key="inventory_items", translation_key="inventory_items", module="inventory", icon="mdi:archive-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.inventory),
    ),
    YuvomiSensorDescription(
        key="subscriptions_monthly", translation_key="subscriptions_monthly", module="budget", icon="mdi:repeat",
        device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement="EUR", suggested_display_precision=2,
        value_fn=_subs_monthly,
        attrs_fn=lambda d: {"abos": len(d.subscriptions)},
    ),
    YuvomiSensorDescription(
        key="loans_remaining", translation_key="loans_remaining", module="loans", icon="mdi:bank-outline",
        device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement="EUR", suggested_display_precision=2,
        value_fn=lambda d: _f(first(d.loans_summary, "remaining_amount", "remaining_principal")),
        attrs_fn=lambda d: {"zusammenfassung": d.loans_summary},
    ),
    YuvomiSensorDescription(
        key="split_balance", translation_key="split_balance", module="split", icon="mdi:account-cash-outline",
        device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement="EUR", suggested_display_precision=2,
        value_fn=_split_net,
        attrs_fn=lambda d: {"details": d.split},
    ),
    YuvomiSensorDescription(
        key="rewards_pending", translation_key="rewards_pending", module="rewards", icon="mdi:gift-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: (d.rewards.get("pendingCount") or 0) + (d.rewards.get("moneyPendingCount") or 0),
    ),
    YuvomiSensorDescription(
        key="reminders_pending", translation_key="reminders_pending", module="reminders", icon="mdi:bell-ring-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.reminders),
        attrs_fn=lambda d: {
            "erinnerungen": [
                {
                    "id": r.get("id"),
                    "titel": first(r, "title", "entity_title", "message"),
                    "art": r.get("entity_type"),
                    "objekt_id": r.get("entity_id"),
                    "zeit": r.get("remind_at"),
                }
                for r in d.reminders
            ]
        },
    ),
    YuvomiSensorDescription(
        key="notes_pinned", translation_key="notes_pinned", module="notes", icon="mdi:pin-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for n in d.notes if n.get("pinned")),
        attrs_fn=lambda d: {
            "angeheftet": [
                {"id": n.get("id"), "titel": n.get("title"), "text": (n.get("content") or "")[:500]}
                for n in d.notes
                if n.get("pinned")
            ],
            "gesamt": len(d.notes),
        },
    ),
    YuvomiSensorDescription(
        key="contacts", translation_key="contacts", module="contacts", icon="mdi:card-account-phone-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.contacts),
    ),
    YuvomiSensorDescription(
        key="documents_expiring", translation_key="documents_expiring", module="documents", icon="mdi:file-clock-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for x in d.documents if x.get("expires_at")),
        attrs_fn=lambda d: {
            "dokumente": [
                {"name": x.get("name"), "ablauf": x.get("expires_at"), "in_tagen": _days(parse_date(x.get("expires_at")))}
                for x in d.documents
                if x.get("expires_at")
            ]
        },
    ),
    YuvomiSensorDescription(
        key="shifts_today", translation_key="shifts_today", module="schedule", icon="mdi:briefcase-clock-outline",
        value_fn=lambda d: ", ".join(
            f"{s.get('user_name') or ''} {first(s.get('shift_type') or {}, 'name') or s.get('shift_type_name') or ''}".strip()
            for s in _today_shifts(d)
        )[:255] or "Frei",
        attrs_fn=lambda d: {"schichten": _today_shifts(d)},
    ),
    YuvomiSensorDescription(
        key="meds_due", translation_key="meds_due", module="health", icon="mdi:pill",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.medications),
        attrs_fn=lambda d: {
            "medikamente": [
                {"id": m.get("id"), "name": m.get("name"), "vorrat": first(m, "stock", "stock_qty")}
                for m in d.medications
            ]
        },
    ),
    YuvomiSensorDescription(
        key="prevention_due", translation_key="prevention_due", module="health", icon="mdi:needle",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.prevention_due),
        attrs_fn=lambda d: {"faellig": d.prevention_due},
    ),
    YuvomiSensorDescription(
        key="weight", translation_key="weight", module="health", icon="mdi:scale-bathroom",
        device_class=SensorDeviceClass.WEIGHT, native_unit_of_measurement="kg",
        value_fn=lambda d: _vital_value(_latest_vital(d, "weight")),
        attrs_fn=lambda d: {"gemessen": (_latest_vital(d, "weight") or {}).get("measured_at")},
    ),
    YuvomiSensorDescription(
        key="blood_pressure", translation_key="blood_pressure", module="health", icon="mdi:heart-pulse",
        value_fn=lambda d: _vital_value(_latest_vital(d, "bp")),
        attrs_fn=lambda d: {"gemessen": (_latest_vital(d, "bp") or {}).get("measured_at")},
    ),
    YuvomiSensorDescription(
        key="nutrition_kcal", translation_key="nutrition_kcal", module="health", icon="mdi:food-apple-outline",
        native_unit_of_measurement="kcal", state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _f((d.nutrition.get("totals") or {}).get("energy_kcal")),
        attrs_fn=lambda d: {"summen": d.nutrition.get("totals"), "ziel": d.nutrition.get("target")},
    ),
)


# --- Dynamische Sensoren (je Objekt) ----------------------------------------


class _DynamicSensor(YuvomiEntity, SensorEntity):
    """Ein Sensor je Yuvomi-Objekt, verschwindet (nicht verfügbar), wenn es weg ist."""

    collection: str = ""
    module_key: str = ""

    def __init__(self, coordinator: YuvomiCoordinator, obj_id: Any) -> None:
        super().__init__(coordinator, f"{self.collection}_{obj_id}", self.module_key)
        self._obj_id = obj_id

    def _obj(self) -> dict[str, Any] | None:
        for o in getattr(self.coordinator.data, self.collection):
            if str(o.get(self.id_field)) == str(self._obj_id):
                return o
        return None

    id_field = "id"

    @property
    def available(self) -> bool:
        return super().available and self._obj() is not None


class InventorySensor(_DynamicSensor):
    collection, module_key = "inventory", "inventory"
    _attr_device_class = SensorDeviceClass.DATE
    _attr_icon = "mdi:wrench-clock"

    @property
    def name(self) -> str:
        return f"{txt(self.hass, 'Wartung', 'Maintenance')} {(self._obj() or {}).get('name') or self._obj_id}"

    def _next(self) -> tuple[date | None, str | None, Any]:
        o = self._obj() or {}
        best: tuple[date | None, str | None, Any] = (None, None, None)
        for td in o.get("tracked_dates") or []:
            if not isinstance(td, dict):
                continue
            dd = parse_date(td.get("date"))
            if dd and (best[0] is None or dd < best[0]):
                best = (dd, td.get("label"), td.get("id"))
        return best

    @property
    def native_value(self) -> date | None:
        return self._next()[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        o = self._obj() or {}
        day, label, date_id = self._next()
        return {
            "item_id": self._obj_id,
            "termin": label,
            "date_id": date_id,
            "in_tagen": _days(day),
            "garantie_bis": first(o, "warranty_until", "warranty_end"),
            "kategorie": o.get("category"),
            "ort": o.get("location_name"),
            "kilometerstand": o.get("odometer"),
        }


class SubscriptionSensor(_DynamicSensor):
    collection, module_key = "subscriptions", "budget"
    _attr_device_class = SensorDeviceClass.DATE
    _attr_icon = "mdi:credit-card-clock-outline"

    @property
    def name(self) -> str:
        return f"{txt(self.hass, 'Abo', 'Subscription')} {(self._obj() or {}).get('name') or self._obj_id}"

    @property
    def native_value(self) -> date | None:
        return parse_date(first(self._obj() or {}, "next_payment_date", "next_payment", "next_due_date"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        o = self._obj() or {}
        return {
            "subscription_id": self._obj_id,
            "betrag": o.get("amount"),
            "waehrung": o.get("currency"),
            "zyklus": first(o, "billing_cycle", "cycle"),
            "status": o.get("status"),
            "in_tagen": _days(self.native_value),
        }


class LoanSensor(_DynamicSensor):
    collection, module_key = "loans", "loans"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "EUR"
    _attr_icon = "mdi:bank-outline"

    @property
    def name(self) -> str:
        return f"{txt(self.hass, 'Kredit', 'Loan')} {(self._obj() or {}).get('name') or self._obj_id}"

    @property
    def native_value(self) -> float | None:
        return _f(first(self._obj() or {}, "remaining_amount", "remaining_principal", "remaining"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        o = self._obj() or {}
        return {
            "loan_id": self._obj_id,
            "richtung": o.get("direction"),
            "naechste_rate": o.get("next_due_date"),
            "ende": o.get("projected_end_month"),
            "rate": first(o, "installment", "monthly_payment"),
        }


class AccountSensor(_DynamicSensor):
    collection, module_key = "accounts", "budget"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:wallet-outline"

    @property
    def name(self) -> str:
        return f"{txt(self.hass, 'Konto', 'Account')} {(self._obj() or {}).get('name') or self._obj_id}"

    @property
    def native_unit_of_measurement(self) -> str:
        return (self._obj() or {}).get("currency") or "EUR"

    @property
    def native_value(self) -> float | None:
        return _f(first(self._obj() or {}, "balance", "running_balance", "current_balance"))


class MoneySensor(_DynamicSensor):
    collection, module_key = "money", "rewards"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:piggy-bank-outline"

    @property
    def name(self) -> str:
        return f"{txt(self.hass, 'Taschengeld', 'Pocket money')} {(self._obj() or {}).get('display_name') or self._obj_id}"

    @property
    def native_unit_of_measurement(self) -> str:
        return (self._obj() or {}).get("currency") or "EUR"

    @property
    def native_value(self) -> float | None:
        o = self._obj() or {}
        return _minor(o.get("balance_minor"), o.get("minor_unit"))


class MedicationSensor(_DynamicSensor):
    collection, module_key = "medications", "health"
    _attr_icon = "mdi:pill"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def name(self) -> str:
        return f"{txt(self.hass, 'Vorrat', 'Pantry')} {(self._obj() or {}).get('name') or self._obj_id}"

    @property
    def native_value(self) -> float | None:
        return _f(first(self._obj() or {}, "stock", "stock_qty", "stock_quantity"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        o = self._obj() or {}
        return {"medication_id": self._obj_id, "dosis": o.get("dose"), "einheit": o.get("unit")}


class PointsSensor(YuvomiEntity, SensorEntity):
    """Punktestand je Person (Belohnungen)."""

    _attr_icon = "mdi:star-circle-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_unit_of_measurement(self) -> str:
        return txt(self.hass, "Punkte", "points")

    def __init__(self, coordinator: YuvomiCoordinator, user_id: str) -> None:
        super().__init__(coordinator, f"points_{user_id}", "rewards")
        self._uid = user_id

    def _balances(self) -> dict[str, Any]:
        b = self.coordinator.data.rewards.get("balances")
        if isinstance(b, list):
            return {str(x.get("user_id") or x.get("id")): x.get("balance") for x in b if isinstance(x, dict)}
        return {str(k): v for k, v in b.items()} if isinstance(b, dict) else {}

    @property
    def name(self) -> str:
        return f"{txt(self.hass, 'Punkte', 'Points')} {self.coordinator.member_name(self._uid) or self._uid}"

    @property
    def available(self) -> bool:
        return super().available and self._uid in self._balances()

    @property
    def native_value(self) -> float | None:
        return _f(self._balances().get(self._uid))


class MemberSensor(YuvomiEntity, SensorEntity):
    """Aufgaben je Person: offen zugewiesen, erledigt Woche/Monat."""

    _attr_icon = "mdi:account-check-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_unit_of_measurement(self) -> str:
        return txt(self.hass, "offen", "open")

    def __init__(self, coordinator: YuvomiCoordinator, mid: int) -> None:
        super().__init__(coordinator, f"member_{mid}", "tasks")
        self._mid = mid

    @property
    def name(self) -> str:
        return f"{txt(self.hass, 'Aufgaben', 'Tasks')} {self.coordinator.data.members.get(self._mid, self._mid)}"

    @property
    def available(self) -> bool:
        return super().available and self._mid in self.coordinator.data.members

    def _mine(self) -> list[dict[str, Any]]:
        out = []
        for t in self.coordinator.data.tasks:
            ids = [u.get("id") for u in t.get("assigned_users") or []] or ([t.get("assigned_to")] if t.get("assigned_to") else [])
            if self._mid in ids and t.get("status") != "done":
                out.append(t)
        return out

    @property
    def native_value(self) -> int:
        return len(self._mine())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        mine = lambda cs: [c for c in cs if (c.get("person_user_id") or c.get("done_by_user_id") or c.get("user_id")) == self._mid]
        today = dt_util.now().date()
        over = [t for t in self._mine() if (dd := parse_date(t.get("due_date"))) and dd < today]
        info = d.member_info.get(self._mid, {})
        return {
            "member_id": self._mid,
            "farbe": info.get("farbe"),
            "erledigt_heute": len(mine(_done_since(d, 1))),
            "erledigt_woche": len(mine(_done_since(d, 7))),
            "erledigt_monat": len(mine(_done_since(d, 30))),
            "punkte_woche": sum(int(c.get("points") or 0) for c in mine(_done_since(d, 7))),
            "ueberfaellig": len(over),
            "offen": [t.get("title") for t in self._mine()],
        }


DYNAMIC: list[tuple[type[_DynamicSensor], str]] = [
    (InventorySensor, "inventory"),
    (SubscriptionSensor, "subscriptions"),
    (LoanSensor, "loans"),
    (AccountSensor, "accounts"),
    (MoneySensor, "money"),
    (MedicationSensor, "medications"),
]


def setup_dynamic(
    coordinator: YuvomiCoordinator, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    known: set[str] = set()

    @callback
    def _add() -> None:
        new: list[SensorEntity] = []
        for cls, coll in DYNAMIC:
            if not module_available(coordinator, cls.module_key):
                continue
            for o in getattr(coordinator.data, coll):
                oid = o.get(cls.id_field)
                key = f"{coll}:{oid}"
                if oid is None or key in known:
                    continue
                known.add(key)
                new.append(cls(coordinator, oid))
        if module_available(coordinator, "tasks"):
            for mid in coordinator.data.members:
                if f"member:{mid}" not in known:
                    known.add(f"member:{mid}")
                    new.append(MemberSensor(coordinator, mid))
        if module_available(coordinator, "rewards"):
            b = coordinator.data.rewards.get("balances")
            ids = (
                [str(x.get("user_id") or x.get("id")) for x in b if isinstance(x, dict)]
                if isinstance(b, list)
                else [str(k) for k in b] if isinstance(b, dict) else []
            )
            for uid in ids:
                if f"points:{uid}" not in known:
                    known.add(f"points:{uid}")
                    new.append(PointsSensor(coordinator, uid))
        if new:
            async_add_entities(new)

    _add()
    entry.async_on_unload(coordinator.async_add_listener(_add))

