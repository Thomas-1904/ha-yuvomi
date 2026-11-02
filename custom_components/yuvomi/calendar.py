"""Kalender: Familienkalender, Müllabfuhr, Essensplan, Geburtstage."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
import logging
from typing import Any

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEntityFeature,
    CalendarEvent,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import YuvomiError, as_list
from .coordinator import YuvomiCoordinator, parse_date
from .entity import YuvomiEntity, module_available

_LOGGER = logging.getLogger(__name__)

MEAL_TIMES = {
    "breakfast": (time(7, 30), time(8, 30)),
    "lunch": (time(12, 0), time(13, 0)),
    "dinner": (time(18, 30), time(19, 30)),
    "snack": (time(15, 30), time(16, 0)),
}
MEAL_NAMES = {
    "breakfast": "Frühstück",
    "lunch": "Mittagessen",
    "dinner": "Abendessen",
    "snack": "Snack",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: YuvomiCoordinator = entry.runtime_data
    entities: list[CalendarEntity] = []
    for module, cls in (
        ("calendar", YuvomiFamilyCalendar),
        ("waste", YuvomiWasteCalendar),
        ("meals", YuvomiMealsCalendar),
        ("birthdays", YuvomiBirthdayCalendar),
        ("schedule", YuvomiShiftCalendar),
    ):
        if module_available(coordinator, module):
            entities.append(cls(coordinator))
    if any(module_available(coordinator, m) for m in ("inventory", "budget", "documents", "health")):
        entities.append(YuvomiDeadlineCalendar(coordinator))
    async_add_entities(entities)


def _parse_dt(value: Any) -> datetime | date | None:
    """Datum, Wanduhrzeit (Haushaltszone) oder Zeitpunkt mit Offset."""
    if not value or not isinstance(value, str):
        return None
    if len(value) == 10:
        return parse_date(value)
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return parsed


def _to_event(ev: dict[str, Any]) -> CalendarEvent | None:
    start = _parse_dt(ev.get("start_datetime"))
    end = _parse_dt(ev.get("end_datetime"))
    if start is None:
        return None
    all_day = bool(ev.get("all_day")) or not isinstance(start, datetime)
    if all_day:
        s = start.date() if isinstance(start, datetime) else start
        e = end.date() if isinstance(end, datetime) else end
        if e is None or e <= s:
            e = s + timedelta(days=1)
        start, end = s, e
    elif end is None or not isinstance(end, datetime) or end <= start:
        end = start + timedelta(hours=1)
    uid = ev.get("id")
    rid = ev.get("recurrence_id")
    return CalendarEvent(
        start=start,
        end=end,
        summary=ev.get("title") or "",
        description=ev.get("description") or None,
        location=ev.get("location") or None,
        uid=str(uid) if uid is not None else None,
        recurrence_id=str(rid) if rid else None,
    )


def _next(events: list[CalendarEvent]) -> CalendarEvent | None:
    now = dt_util.now()
    upcoming = [e for e in events if e.end_datetime_local >= now]
    upcoming.sort(key=lambda e: e.start_datetime_local)
    return upcoming[0] if upcoming else None


class YuvomiFamilyCalendar(YuvomiEntity, CalendarEntity):
    """Familienkalender (lesen, anlegen, löschen)."""

    _attr_translation_key = "family"
    _attr_supported_features = (
        CalendarEntityFeature.CREATE_EVENT | CalendarEntityFeature.DELETE_EVENT
    )

    def __init__(self, coordinator: YuvomiCoordinator) -> None:
        super().__init__(coordinator, "calendar", "calendar")

    @property
    def event(self) -> CalendarEvent | None:
        raw = self.coordinator.data.dashboard.get("upcomingEvents") or []
        events = [e for e in (_to_event(x) for x in raw if isinstance(x, dict)) if e]
        return _next(events)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        try:
            data = await self.coordinator.client.get(
                "/api/v1/calendar",
                params={
                    "from": dt_util.as_local(start_date).date().isoformat(),
                    "to": dt_util.as_local(end_date).date().isoformat(),
                },
            )
        except YuvomiError as err:
            raise HomeAssistantError(f"Yuvomi: {err}") from err
        events = [e for e in (_to_event(x) for x in as_list(data, "events")) if e]
        return [
            e
            for e in events
            if e.end_datetime_local > start_date and e.start_datetime_local < end_date
        ]

    async def async_create_event(self, **kwargs: Any) -> None:
        start = kwargs["dtstart"]
        end = kwargs["dtend"]
        all_day = not isinstance(start, datetime)
        body: dict[str, Any] = {
            "title": kwargs.get("summary") or "",
            "all_day": all_day,
            "description": kwargs.get("description"),
            "location": kwargs.get("location"),
        }
        if all_day:
            body["start_datetime"] = start.isoformat()
            # HA: Ende exklusiv, Yuvomi: letzter Tag
            body["end_datetime"] = (end - timedelta(days=1)).isoformat() if end > start else start.isoformat()
        else:
            body["start_datetime"] = dt_util.as_local(start).strftime("%Y-%m-%dT%H:%M")
            body["end_datetime"] = dt_util.as_local(end).strftime("%Y-%m-%dT%H:%M")
        if kwargs.get("rrule"):
            body["recurrence_rule"] = kwargs["rrule"]
        try:
            await self.coordinator.client.post("/api/v1/calendar", body)
        except YuvomiError as err:
            raise HomeAssistantError(f"Yuvomi: {err}") from err
        await self.coordinator.async_request_refresh()

    async def async_delete_event(
        self, uid: str, recurrence_id: str | None = None, recurrence_range: str | None = None
    ) -> None:
        client = self.coordinator.client
        try:
            if recurrence_id:
                path = f"/api/v1/calendar/{uid}/occurrences/{recurrence_id}"
                if recurrence_range == "THISANDFUTURE":
                    path += "/following"
                await client.delete(path)
            else:
                await client.delete(f"/api/v1/calendar/{uid}")
        except YuvomiError as err:
            raise HomeAssistantError(f"Yuvomi: {err}") from err
        await self.coordinator.async_request_refresh()


class YuvomiWasteCalendar(YuvomiEntity, CalendarEntity):
    """Müllabfuhr (ganztägige Termine je Tonne)."""

    _attr_translation_key = "waste"

    def __init__(self, coordinator: YuvomiCoordinator) -> None:
        super().__init__(coordinator, "calendar", "waste")

    @staticmethod
    def _occ_to_event(o: dict[str, Any]) -> CalendarEvent | None:
        d = parse_date(o.get("date_key"))
        if d is None:
            return None
        return CalendarEvent(
            start=d,
            end=d + timedelta(days=1),
            summary=o.get("type_name") or "Müll",
            description="Verschoben" if o.get("moved") else None,
            uid=o.get("key"),
        )

    @property
    def event(self) -> CalendarEvent | None:
        events = []
        for entry in self.coordinator.data.waste_next:
            nxt = entry.get("next")
            if isinstance(nxt, dict):
                ev = self._occ_to_event(nxt)
                if ev:
                    events.append(ev)
        return _next(events)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        frm = dt_util.as_local(start_date).date()
        to = dt_util.as_local(end_date).date()
        if (to - frm).days > 730:
            to = frm + timedelta(days=730)
        try:
            data = await self.coordinator.client.get(
                "/api/v1/waste/occurrences", params={"from": frm.isoformat(), "to": to.isoformat()}
            )
        except YuvomiError as err:
            raise HomeAssistantError(f"Yuvomi: {err}") from err
        return [e for e in (self._occ_to_event(o) for o in as_list(data)) if e]


class YuvomiMealsCalendar(YuvomiEntity, CalendarEntity):
    """Essensplan."""

    _attr_translation_key = "meals"

    def __init__(self, coordinator: YuvomiCoordinator) -> None:
        super().__init__(coordinator, "calendar", "meals")

    @staticmethod
    def _meal_to_event(m: dict[str, Any]) -> CalendarEvent | None:
        d = parse_date(m.get("date"))
        if d is None:
            return None
        kind = m.get("meal_type") or "dinner"
        t_start, t_end = MEAL_TIMES.get(kind, MEAL_TIMES["dinner"])
        tz = dt_util.get_default_time_zone()
        cook = m.get("cook_name")
        desc = [MEAL_NAMES.get(kind, kind)]
        if cook:
            desc.append(f"Koch: {cook}")
        if m.get("notes"):
            desc.append(m["notes"])
        return CalendarEvent(
            start=datetime.combine(d, t_start, tz),
            end=datetime.combine(d, t_end, tz),
            summary=m.get("title") or "",
            description=" · ".join(desc),
            uid=str(m.get("id")) if m.get("id") is not None else None,
        )

    @property
    def event(self) -> CalendarEvent | None:
        events = [e for e in (self._meal_to_event(m) for m in self.coordinator.data.meals_week) if e]
        return _next(events)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        result: list[CalendarEvent] = []
        day = dt_util.as_local(start_date).date()
        last = dt_util.as_local(end_date).date()
        seen: set[str] = set()
        weeks = 0
        try:
            while day <= last and weeks < 60:
                data = await self.coordinator.client.get("/api/v1/meals", params={"week": day.isoformat()})
                for m in as_list(data, "meals"):
                    key = str(m.get("id"))
                    if key in seen:
                        continue
                    seen.add(key)
                    ev = self._meal_to_event(m)
                    if ev and ev.end_datetime_local > start_date and ev.start_datetime_local < end_date:
                        result.append(ev)
                day += timedelta(days=7)
                weeks += 1
        except YuvomiError as err:
            raise HomeAssistantError(f"Yuvomi: {err}") from err
        return result


class YuvomiBirthdayCalendar(YuvomiEntity, CalendarEntity):
    """Geburtstage (aus /birthdays, ganztägig, jährlich)."""

    _attr_translation_key = "birthdays"

    def __init__(self, coordinator: YuvomiCoordinator) -> None:
        super().__init__(coordinator, "calendar", "birthdays")

    def _events_between(self, frm: date, to: date) -> list[CalendarEvent]:
        out: list[CalendarEvent] = []
        for b in self.coordinator.data.birthdays:
            born = parse_date(b.get("birth_date"))
            if born is None:
                continue
            for year in range(frm.year, to.year + 1):
                try:
                    d = born.replace(year=year)
                except ValueError:  # 29.02.
                    d = date(year, 2, 28)
                if frm <= d <= to:
                    age = None if b.get("year_unknown") else year - born.year
                    name = b.get("name") or ""
                    out.append(
                        CalendarEvent(
                            start=d,
                            end=d + timedelta(days=1),
                            summary=f"{name} ({age})" if age else name,
                            uid=f"bday-{b.get('id')}-{year}",
                        )
                    )
        return out

    @property
    def event(self) -> CalendarEvent | None:
        today = dt_util.now().date()
        return _next(self._events_between(today, today + timedelta(days=366)))

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return self._events_between(
            dt_util.as_local(start_date).date(), dt_util.as_local(end_date).date()
        )


class YuvomiShiftCalendar(YuvomiEntity, CalendarEntity):
    """Schichtplan aller Haushaltsmitglieder."""

    _attr_translation_key = "schedule"

    def __init__(self, coordinator: YuvomiCoordinator) -> None:
        super().__init__(coordinator, "calendar", "schedule")

    def _to_event(self, s: dict[str, Any]) -> CalendarEvent | None:
        d = parse_date(s.get("date_key") or s.get("date"))
        if d is None:
            return None
        st = s.get("shift_type") if isinstance(s.get("shift_type"), dict) else s
        name = st.get("name") or s.get("shift_type_name") or "Schicht"
        who = s.get("user_name") or self.coordinator.member_name(s.get("user_id"))
        start_t, end_t = st.get("start_time"), st.get("end_time")
        tz = dt_util.get_default_time_zone()
        summary = f"{who}: {name}" if who else name
        try:
            if start_t and end_t:
                start = datetime.combine(d, time.fromisoformat(start_t[:5]), tz)
                end = datetime.combine(d, time.fromisoformat(end_t[:5]), tz)
                if end <= start:
                    end += timedelta(days=1)
                return CalendarEvent(start=start, end=end, summary=summary)
        except ValueError:
            pass
        return CalendarEvent(start=d, end=d + timedelta(days=1), summary=summary)

    @property
    def event(self) -> CalendarEvent | None:
        return _next([e for e in (self._to_event(s) for s in self.coordinator.data.shifts) if e])

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        frm = dt_util.as_local(start_date).date()
        to = min(dt_util.as_local(end_date).date(), frm + timedelta(days=730))
        try:
            data = await self.coordinator.client.get(
                "/api/v1/schedule/entries", params={"from": frm.isoformat(), "to": to.isoformat()}
            )
        except YuvomiError as err:
            raise HomeAssistantError(f"Yuvomi: {err}") from err
        return [e for e in (self._to_event(s) for s in as_list(data, "entries")) if e]


class YuvomiDeadlineCalendar(YuvomiEntity, CalendarEntity):
    """Fristen: Wartung/Garantie (Inventar), Abo-Zahlungen, Dokumente, Vorsorge."""

    _attr_translation_key = "deadlines"

    def __init__(self, coordinator: YuvomiCoordinator) -> None:
        super().__init__(coordinator, "calendar_deadlines", "core")

    def _all(self) -> list[CalendarEvent]:
        d = self.coordinator.data
        out: list[CalendarEvent] = []

        def add(day: Any, summary: str, uid: str, desc: str | None = None) -> None:
            dd = parse_date(day)
            if dd:
                out.append(CalendarEvent(start=dd, end=dd + timedelta(days=1), summary=summary, uid=uid, description=desc))

        for item in d.inventory:
            name = item.get("name") or "Gerät"
            add(item.get("warranty_until") or item.get("warranty_end"), f"Garantieende: {name}", f"inv-w-{item.get('id')}")
            for td in item.get("tracked_dates") or []:
                if isinstance(td, dict):
                    add(td.get("date"), f"{td.get('label') or 'Termin'}: {name}", f"inv-d-{td.get('id')}")
        for sub in d.subscriptions:
            amount = sub.get("amount")
            add(
                sub.get("next_payment_date") or sub.get("next_payment"),
                f"Abo: {sub.get('name') or ''}",
                f"sub-{sub.get('id')}",
                f"{amount} {sub.get('currency') or ''}".strip() if amount is not None else None,
            )
        for doc in d.documents:
            add(doc.get("expires_at"), f"Läuft ab: {doc.get('name') or ''}", f"doc-{doc.get('id')}")
        for p in d.prevention_due:
            add(p.get("next_due_on") or p.get("due_on"), f"Vorsorge: {p.get('name') or p.get('type_name') or ''}", f"prev-{p.get('type_id') or p.get('id')}")
        return out

    @property
    def event(self) -> CalendarEvent | None:
        return _next(self._all())

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        s, e = dt_util.as_local(start_date).date(), dt_util.as_local(end_date).date()
        return [ev for ev in self._all() if s <= ev.start <= e]
