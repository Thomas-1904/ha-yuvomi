"""Leaderboard from Yuvomi data and shopping categories."""

from datetime import datetime
import pytest
from homeassistant.util import dt as dt_util

from custom_components.yuvomi.leaderboard import period_start, ranking
from custom_components.yuvomi.shopping_cat import category_for, guess_from_keywords

CATS = ["Obst & Gemüse", "Backwaren", "Milchprodukte", "Fleisch & Fisch", "Tiefkühl", "Getränke", "Haushalt", "Drogerie", "Sonstiges"]
MEMBERS = {1: "Alex Doe", 2: "Sam Doe", 3: "Kim"}


@pytest.fixture(autouse=True)
async def berlin(hass):
    await hass.config.async_set_time_zone("Europe/Berlin")


def test_period_start():
    now = datetime(2026, 11, 7, 12, 0, tzinfo=dt_util.get_time_zone("Europe/Berlin"))  # Saturday
    assert period_start("week", now).date().isoformat() == "2026-11-02"
    assert period_start("month", now).date().isoformat() == "2026-11-01"


def test_ranking_from_completions():
    now = datetime(2026, 11, 7, 12, 0, tzinfo=dt_util.get_time_zone("Europe/Berlin"))
    comps = [
        {"person_user_id": 2, "points": 5, "completed_at": "2026-11-06T10:00:00Z"},
        {"person_user_id": 2, "points": 0, "completed_at": "2026-11-05T10:00:00Z"},
        {"person_user_id": 1, "points": 3, "completed_at": "2026-11-04T10:00:00Z"},
        {"person_user_id": 1, "points": 10, "completed_at": "2026-10-30T10:00:00Z"},  # last week
    ]
    week = ranking(MEMBERS, comps, [], "week", now)
    assert [(r["name"], r["punkte"], r["aufgaben"], r["platz"]) for r in week] == [
        ("Sam", 5, 2, 1), ("Alex", 3, 1, 2), ("Kim", 0, 0, 3)]
    month = ranking(MEMBERS, comps, [], "month", now)
    assert month[0]["name"] == "Sam"


def test_ranking_prefers_ledger_points_and_ties():
    now = datetime(2026, 11, 7, 12, 0, tzinfo=dt_util.get_time_zone("Europe/Berlin"))
    comps = [{"person_user_id": 1, "points": 99, "completed_at": "2026-11-06T10:00:00Z"}]
    ledger = [
        {"user_id": 1, "points": 4, "type": "earn", "created_at": "2026-11-06T10:00:00Z"},
        {"user_id": 2, "points": 4, "type": "bonus", "created_at": "2026-11-05T10:00:00Z"},
        {"user_id": 2, "points": -20, "type": "redemption", "created_at": "2026-11-05T11:00:00Z"},
    ]
    week = ranking(MEMBERS, comps, ledger, "week", now)
    assert [(r["name"], r["punkte"], r["platz"]) for r in week][:2] == [("Alex", 4, 1), ("Sam", 4, 2)]


@pytest.mark.parametrize(
    ("name", "cat"),
    [("Milch", "Milchprodukte"), ("Mandelmilch", "Milchprodukte"), ("Hähnchenbrust", "Fleisch & Fisch"),
     ("Weisswein", "Getränke"), ("Reis", None), ("Eier", "Milchprodukte"), ("Lauchzwiebeln", "Obst & Gemüse"),
     ("Toilettenpapier", "Haushalt"), ("Zahnpasta", "Drogerie"), ("Brötchen", "Backwaren"), ("Öl", None)],
)
def test_keywords(name, cat):
    assert guess_from_keywords(name, CATS) == cat


def test_keywords_only_existing_categories():
    assert guess_from_keywords("Milch", ["Sonstiges"]) is None
    assert guess_from_keywords("Milk", ["Dairy", "Other"]) == "Dairy"


async def test_household_history_wins():
    class C:
        async def get(self, path, params=None):
            return {"data": [{"name": "Milch", "category": "Getränke", "quantity": None}]}
    assert await category_for(C(), "milch", CATS) == "Getränke"

    class D:
        async def get(self, path, params=None):
            return {"data": [{"name": "Milch", "category": "Sonstiges"}]}
    assert await category_for(D(), "Milch", CATS) == "Milchprodukte"
