"""Points and leaderboard – computed from Yuvomi's own data.

Source, in this order:
1. Yuvomi's reward ledger (module "Rewards" on, entries present): exactly the points Yuvomi
   booked – task points incl. late-completion decay, bonuses and corrections; redemptions
   are not counted as "earned".
2. Otherwise the task completion history: the points of each completed task, credited to
   the person Yuvomi records as having done it.

Periods follow the calendar: this week (from Monday) and this month, in the HA time zone.
Ties on points are broken by the number of completed tasks.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

EARN_TYPES = {"earn", "bonus", "adjustment", "correction", "task"}


def _ts(value: Any) -> datetime | None:
    t = dt_util.parse_datetime(str(value or "").replace(" ", "T"))
    if t is None:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt_util.UTC)
    return dt_util.as_local(t)


def period_start(period: str, now: datetime | None = None) -> datetime:
    now = dt_util.as_local(now or dt_util.now())
    day = dt_util.start_of_local_day(now)
    if period == "week":
        return day - timedelta(days=now.weekday())
    if period == "month":
        return day.replace(day=1)
    raise ValueError(period)


def _int(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def ranking(
    members: dict[int, str],
    completions: list[dict[str, Any]],
    ledger: list[dict[str, Any]],
    period: str,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """[{platz, id, name, punkte, aufgaben}] for all members, best first."""
    start = period_start(period, now)
    pts: dict[int, int] = dict.fromkeys(members, 0)
    tasks: dict[int, int] = dict.fromkeys(members, 0)

    for c in completions:
        t = _ts(c.get("completed_at"))
        who = c.get("person_user_id") or c.get("done_by_user_id") or c.get("user_id")
        if t is None or t < start or who is None:
            continue
        who = _int(who)
        tasks[who] = tasks.get(who, 0) + 1
        if not ledger:
            pts[who] = pts.get(who, 0) + _int(c.get("points"))

    for e in ledger:
        t = _ts(e.get("created_at") or e.get("booked_at") or e.get("at"))
        who = e.get("user_id")
        kind = str(e.get("type") or "earn").lower()
        if t is None or t < start or who is None or kind not in EARN_TYPES:
            continue
        who = _int(who)
        pts[who] = pts.get(who, 0) + _int(e.get("points"))

    rows = [
        {"id": mid, "name": str(members.get(mid, mid)).split(" ")[0], "punkte": pts.get(mid, 0), "aufgaben": tasks.get(mid, 0)}
        for mid in set(pts) | set(tasks)
        if mid in members
    ]
    rows.sort(key=lambda r: (-r["punkte"], -r["aufgaben"], r["name"].lower()))
    place, prev = 0, None
    for i, r in enumerate(rows, 1):
        key = (r["punkte"], r["aufgaben"])
        if key != prev:
            place, prev = i, key
        r["platz"] = place
    return rows


def source(ledger: list[dict[str, Any]]) -> str:
    return "rewards" if ledger else "tasks"
