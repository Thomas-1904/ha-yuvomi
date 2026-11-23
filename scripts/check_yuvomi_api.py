"""Check the integration against a Yuvomi OpenAPI description.

    python scripts/check_yuvomi_api.py openapi.json [--client-api N]

1. Every API path the integration calls exists in the description.
2. Typical request bodies the integration sends pass the request schemas
   (Yuvomi refuses requests outside them since 2.89).
3. The `client_api` generation of that Yuvomi is the one the integration supports.

Exit code 1 with a Markdown report on stdout when something does not fit.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "custom_components" / "yuvomi"

# Paths built from a placeholder that stands for a fixed set of words
EXPAND = {
    "/api/v1/housekeeping/work-sessions/check-{}": ["/api/v1/housekeeping/work-sessions/check-in", "/api/v1/housekeeping/work-sessions/check-out"],
    "/api/v1/health/logs/{}/{}": ["/api/v1/health/logs/{}/take", "/api/v1/health/logs/{}/skip"],
}

# (method, path, body) – what the integration sends (see services.py, todo.py, calendar.py, button.py)
DAY, TIME = "2026-01-05", "2026-01-05T08:00"
CASES: list[tuple[str, str, dict]] = [
    ("post", "/api/v1/tasks", {"title": "x", "assigned_to": [2], "due_date": DAY, "due_time": "08:00", "priority": "high", "points": 3}),
    ("patch", "/api/v1/tasks/{id}/status", {"status": "done"}),
    ("post", "/api/v1/shopping/{listId}/items", {"name": "Milch", "quantity": "2", "category": "Milchprodukte"}),
    ("patch", "/api/v1/shopping/items/{itemId}", {"is_checked": True}),
    ("post", "/api/v1/shopping/{listId}/import-meal-plan", {"from": DAY, "to": DAY}),
    ("post", "/api/v1/shopping/{listId}/import-pantry", {"items": [{"pantry_item_id": 1}]}),
    ("post", "/api/v1/pantry/import-shopping", {"list_id": 3}),
    ("post", "/api/v1/pantry", {"name": "Milch", "quantity": 2, "unit": "bottle", "expires_on": DAY, "notes": "x"}),
    ("patch", "/api/v1/pantry/{itemId}", {"name": "Milch", "quantity": 0, "expires_on": None}),
    ("post", "/api/v1/meals", {"title": "Pasta", "date": DAY, "meal_type": "dinner", "notes": "x"}),
    ("post", "/api/v1/calendar", {"title": "x", "start_datetime": TIME, "end_datetime": TIME, "all_day": False}),
    ("post", "/api/v1/birthdays", {"name": "Sam", "birth_date": "2000-01-01"}),
    ("post", "/api/v1/housekeeping/decay-tasks", {"name": "Bad", "area": "Bad", "frequency_days": 7}),
    ("patch", "/api/v1/housekeeping/decay-tasks/{taskId}", {"name": "Bad", "frequency_days": 7}),
    ("post", "/api/v1/housekeeping/work-sessions/check-in", {"worker_id": 4, "timezone": "Europe/Berlin"}),
    ("post", "/api/v1/housekeeping/work-sessions/check-out", {"worker_id": 4, "extras": 5.0}),
    ("post", "/api/v1/inventory/items/{id}/dates/{dateId}/complete", {"performed_on": DAY, "odometer": 1234}),
    ("post", "/api/v1/rewards/bonus", {"user_id": 2, "delta": -5, "reason": None}),
    ("post", "/api/v1/rewards/redemptions", {"catalog_id": 1, "user_id": 2}),
    ("patch", "/api/v1/notes/{id}/pin", {"pinned": True}),
    ("patch", "/api/v1/notes/{id}/check", {"line": 1, "checked": True}),
    ("post", "/api/v1/budget", {"title": "x", "amount": -5.5, "date": DAY, "category": "financial_other"}),
    ("post", "/api/v1/budget/loans/{id}/payments", {"paid_date": DAY, "amount": 12.5}),
    ("post", "/api/v1/health/vitals", {"type": "weight", "value": 80.0, "measured_at": TIME}),
    ("post", "/api/v1/health/medications/{id}/logs", {"status": "taken", "taken_at": TIME, "stock_deduct": 1.0}),
    ("post", "/api/v1/notifications/channels", {"provider": "webhook", "name": "Home Assistant", "enabled": True, "config": {"baseUrl": "https://ha.example/api/webhook/x"}}),
    ("put", "/api/v1/notifications/channels/{id}", {"provider": "webhook", "name": "Home Assistant", "enabled": True, "config": {"baseUrl": "https://ha.example/api/webhook/x"}}),
]


def used_paths() -> set[str]:
    """API paths in the source, placeholders as `{}` (handles f-strings with nested quotes)."""
    found: set[str] = set()
    for file in SRC.glob("*.py"):
        text = file.read_text(encoding="utf-8")
        for m in re.finditer(r"(?<=[\"'])/api/v1/", text):
            quote, i, depth, out = text[m.start() - 1], m.start(), 0, []
            while i < len(text):
                ch = text[i]
                if ch == "{":
                    depth += 1
                    if depth == 1:
                        out.append("{}")
                elif ch == "}":
                    depth -= 1
                elif depth == 0:
                    if ch == quote:
                        break
                    out.append(ch)
                i += 1
            path = "".join(out).rstrip("/")
            found.update(EXPAND.get(path, [path]))
    return {p for p in found if p.count("/") > 2 and p != "/api/v1/{}"}


def norm(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--client-api", type=int)
    args = ap.parse_args()
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    sys.path.insert(0, str(ROOT))
    problems: list[str] = []

    if args.client_api is not None:
        const = (SRC / "const.py").read_text(encoding="utf-8")
        supported = int(re.search(r"SUPPORTED_CLIENT_API\s*=\s*(\d+)", const)[1])
        if args.client_api != supported:
            problems.append(f"`client_api` is {args.client_api}, the integration supports {supported}.")

    spec_paths = {norm(p): p for p in spec["paths"]}
    for p in sorted(used_paths()):
        if norm(p) not in spec_paths:
            problems.append(f"Path no longer in the API description: `{p}`")

    for method, path, body in CASES:
        op = spec["paths"].get(path, {}).get(method)
        if op is None:
            problems.append(f"Operation missing: `{method.upper()} {path}`")
            continue
        rb = (op.get("requestBody") or {}).get("content", {}).get("application/json", {}).get("schema")
        if not rb:
            continue
        validator = Draft202012Validator({**rb, "components": spec.get("components", {})})
        for err in validator.iter_errors(body):
            where = "/".join(map(str, err.path)) or "(body)"
            problems.append(f"`{method.upper()} {path}` – {where}: {err.message}")

    if problems:
        print("\n".join(f"- {p}" for p in problems))
        return 1
    print(f"OK – {len(used_paths())} paths, {len(CASES)} request bodies checked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
