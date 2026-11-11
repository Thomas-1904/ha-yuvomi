"""Services: generisches Anlegen/Ändern/Löschen jeder Ressource plus Komfort-Aktionen."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .api import YuvomiError, as_list, unwrap
from .const import DOMAIN, RESOURCES
from .coordinator import YuvomiCoordinator
from .shopping_cat import category_for, is_uncategorized
from .util import echo_body

BASE = {vol.Optional("config_entry_id"): cv.string}
OPT = SupportsResponse.OPTIONAL


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> YuvomiCoordinator:
    entries = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED
        and isinstance(getattr(e, "runtime_data", None), YuvomiCoordinator)
    ]
    wanted = call.data.get("config_entry_id")
    if wanted:
        entries = [e for e in entries if e.entry_id == wanted]
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_instance")
    return entries[0].runtime_data


async def _run(c: YuvomiCoordinator, method: str, path: str, body: Any = None, params: Any = None) -> Any:
    try:
        result = await c.client.request(method, path, json=body, params=params)
    except YuvomiError as err:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="api_error", translation_placeholders={"error": str(err)}) from err
    await c.async_request_refresh()
    return result


def _resp(call: ServiceCall, result: Any) -> ServiceResponse:
    if not call.return_response:
        return None
    data = unwrap(result)
    return data if isinstance(data, dict) else {"data": data}


def _path(template: str, call: ServiceCall, need_id: bool) -> str:
    if not template:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="action_not_supported")
    values = {"id": call.data.get("id"), "parent_id": call.data.get("parent_id")}
    if need_id and values["id"] is None:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="id_missing")
    if "{parent_id}" in template and values["parent_id"] is None:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="parent_id_missing")
    return template.format(id=values["id"] or "", parent_id=values["parent_id"] or "")


def _first_list(c: YuvomiCoordinator, list_id: Any) -> Any:
    if list_id is not None:
        return list_id
    if not c.data.shopping_lists:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_shopping_list")
    return c.data.shopping_lists[0]["id"]


@callback
def register_services(hass: HomeAssistant) -> None:
    def reg(name: str, handler, schema: dict, response: SupportsResponse = SupportsResponse.NONE) -> None:
        hass.services.async_register(DOMAIN, name, handler, schema=vol.Schema({**BASE, **schema}), supports_response=response)

    resource = vol.In(sorted(RESOURCES))

    # --- generisch -----------------------------------------------------------

    async def api_request(call: ServiceCall) -> ServiceResponse:
        c = _coordinator(hass, call)
        path = call.data["path"]
        if not path.startswith("/api/v1/"):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="path_not_v1")
        return _resp(call, await _run(c, call.data["method"], path, call.data.get("body"), call.data.get("params")))

    async def create(call: ServiceCall) -> ServiceResponse:
        c = _coordinator(hass, call)
        tpl = RESOURCES[call.data["resource"]][1]
        return _resp(call, await _run(c, "POST", _path(tpl, call, False), call.data.get("data") or {}))

    async def get(call: ServiceCall) -> ServiceResponse:
        c = _coordinator(hass, call)
        _, coll, one, _ = RESOURCES[call.data["resource"]]
        path = _path(one, call, True) if call.data.get("id") is not None else _path(coll, call, False)
        try:
            result = await c.client.get(path, params=call.data.get("params"))
        except YuvomiError as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="api_error", translation_placeholders={"error": str(err)}) from err
        data = unwrap(result)
        return data if isinstance(data, dict) else {"data": data}

    async def update(call: ServiceCall) -> ServiceResponse:
        c = _coordinator(hass, call)
        _, _, one, method = RESOURCES[call.data["resource"]]
        if not method:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_updatable")
        path = _path(one, call, True)
        body = dict(call.data.get("data") or {})
        if method == "PUT" and call.data.get("merge", True):
            # PUT ersetzt alles: aktuellen Stand holen und nur die Änderungen einsetzen
            try:
                current = unwrap(await c.client.get(path))
            except YuvomiError:
                current = None
            if isinstance(current, dict):
                body = {**echo_body(current, ("id", "created_at", "updated_at")), **body}
        return _resp(call, await _run(c, method, path, body))

    async def delete(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        await _run(c, "DELETE", _path(RESOURCES[call.data["resource"]][2], call, True), params=call.data.get("params"))

    reg("api_request", api_request, {
        vol.Required("path"): cv.string,
        vol.Optional("method", default="GET"): vol.In(["GET", "POST", "PUT", "PATCH", "DELETE"]),
        vol.Optional("body"): vol.Any(dict, list),
        vol.Optional("params"): dict,
    }, OPT)
    reg("create", create, {vol.Required("resource"): resource, vol.Optional("parent_id"): cv.string, vol.Optional("data"): dict}, OPT)
    reg("get", get, {vol.Required("resource"): resource, vol.Optional("id"): cv.string, vol.Optional("parent_id"): cv.string, vol.Optional("params"): dict}, SupportsResponse.ONLY)
    reg("update", update, {vol.Required("resource"): resource, vol.Required("id"): cv.string, vol.Optional("parent_id"): cv.string, vol.Optional("data"): dict, vol.Optional("merge", default=True): cv.boolean}, OPT)
    reg("delete", delete, {vol.Required("resource"): resource, vol.Required("id"): cv.string, vol.Optional("parent_id"): cv.string, vol.Optional("params"): dict})

    # --- Komfort: Aufgaben, Einkauf, Essen ------------------------------------

    async def add_task(call: ServiceCall) -> ServiceResponse:
        c = _coordinator(hass, call)
        body = {k: call.data[k] for k in ("title", "description", "priority", "category", "points", "assigned_to", "visibility") if k in call.data}
        if "due_date" in call.data:
            body["due_date"] = str(call.data["due_date"])
        if call.data.get("due_time"):
            body["due_time"] = str(call.data["due_time"])[:5]
        if call.data.get("recurrence"):
            # Yuvomi erwartet is_recurring + recurrence_rule (RRULE, z. B. FREQ=WEEKLY;BYDAY=MO)
            rule = str(call.data["recurrence"]).strip()
            body["is_recurring"] = True
            body["recurrence_rule"] = rule[6:] if rule.upper().startswith("RRULE:") else rule
        return _resp(call, await _run(c, "POST", "/api/v1/tasks", body))

    async def complete_task(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        body: dict[str, Any] = {"status": call.data.get("status", "done")}
        if "done_by_user_id" in call.data:
            body["done_by_user_id"] = call.data["done_by_user_id"]
        elif body["status"] == "done" and call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user and user.name:
                first = user.name.strip().lower().split(" ")[0]
                for mid, name in c.data.members.items():
                    if str(name).strip().lower().split(" ")[0] == first:
                        body["done_by_user_id"] = mid
                        break
        await _run(c, "PATCH", f"/api/v1/tasks/{call.data['task_id']}/status", body)

    async def add_shopping_item(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        body = {k: call.data[k] for k in ("name", "quantity", "category", "notes") if k in call.data}
        if "category" not in body:
            cat = await category_for(c.client, body["name"], [str(x.get("name")) for x in c.data.shopping_categories])
            if cat:
                body["category"] = cat
        await _run(c, "POST", f"/api/v1/shopping/{_first_list(c, call.data.get('list_id'))}/items", body)

    async def sort_shopping(call: ServiceCall) -> ServiceResponse:
        """Give uncategorized items of a list (or all lists) a category – same logic as for new items."""
        c = _coordinator(hass, call)
        lists = [call.data["list_id"]] if call.data.get("list_id") else [x["id"] for x in c.data.shopping_lists]
        names = [str(x.get("name")) for x in c.data.shopping_categories]
        changed: list[dict[str, Any]] = []
        try:
            for lid in lists:
                for it in as_list(await c.client.get(f"/api/v1/shopping/{lid}/items"), "items"):
                    if not is_uncategorized(it.get("category")):
                        continue
                    cat = await category_for(c.client, str(it.get("name") or ""), names)
                    if cat:
                        await c.client.patch(f"/api/v1/shopping/items/{it['id']}", {"category": cat})
                        changed.append({"name": it.get("name"), "category": cat})
        except YuvomiError as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="api_error", translation_placeholders={"error": str(err)}) from err
        await c.async_request_refresh()
        return {"changed": changed}

    async def clear_checked(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        await _run(c, "DELETE", f"/api/v1/shopping/{_first_list(c, call.data.get('list_id'))}/items/checked")

    async def add_meal(call: ServiceCall) -> ServiceResponse:
        c = _coordinator(hass, call)
        body = {k: call.data[k] for k in ("title", "meal_type", "notes", "recipe_id", "cook_user_id") if k in call.data}
        body["date"] = str(call.data["date"])
        return _resp(call, await _run(c, "POST", "/api/v1/meals", body))

    async def meals_to_shopping(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        await _run(
            c, "POST", f"/api/v1/shopping/{_first_list(c, call.data.get('list_id'))}/import-meal-plan",
            {"from": str(call.data["from_date"]), "to": str(call.data["to_date"])},
        )

    async def pantry_to_shopping(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        await _run(c, "POST", f"/api/v1/shopping/{_first_list(c, call.data.get('list_id'))}/import-pantry")

    async def shopping_to_pantry(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        await _run(c, "POST", "/api/v1/pantry/import-shopping", {"list_id": _first_list(c, call.data.get("list_id"))})

    reg("add_task", add_task, {
        vol.Required("title"): cv.string, vol.Optional("description"): cv.string, vol.Optional("due_date"): cv.date, vol.Optional("due_time"): cv.time,
        vol.Optional("assigned_to"): vol.All(cv.ensure_list, [vol.Coerce(int)]),
        vol.Optional("priority"): vol.In(["none", "low", "medium", "high", "urgent"]),
        vol.Optional("category"): cv.string, vol.Optional("points"): vol.Coerce(int),
        vol.Optional("visibility"): vol.In(["all", "assignees", "private"]), vol.Optional("recurrence"): cv.string,
    }, OPT)
    reg("complete_task", complete_task, {
        vol.Required("task_id"): vol.Coerce(int), vol.Optional("done_by_user_id"): vol.Coerce(int),
        vol.Optional("status", default="done"): vol.In(["done", "open", "in_progress"]),
    })
    reg("add_shopping_item", add_shopping_item, {
        vol.Required("name"): cv.string, vol.Optional("list_id"): vol.Coerce(int), vol.Optional("quantity"): cv.string,
        vol.Optional("category"): cv.string, vol.Optional("notes"): cv.string,
    })
    reg("clear_checked", clear_checked, {vol.Optional("list_id"): vol.Coerce(int)})
    reg("sort_shopping", sort_shopping, {vol.Optional("list_id"): vol.Coerce(int)}, SupportsResponse.OPTIONAL)
    reg("add_meal", add_meal, {
        vol.Required("date"): cv.date, vol.Required("title"): cv.string,
        vol.Optional("meal_type", default="dinner"): vol.In(["breakfast", "lunch", "dinner", "snack"]),
        vol.Optional("notes"): cv.string, vol.Optional("recipe_id"): vol.Coerce(int), vol.Optional("cook_user_id"): vol.Coerce(int),
    }, OPT)
    reg("meals_to_shopping", meals_to_shopping, {vol.Required("from_date"): cv.date, vol.Required("to_date"): cv.date, vol.Optional("list_id"): vol.Coerce(int)})
    reg("pantry_to_shopping", pantry_to_shopping, {vol.Optional("list_id"): vol.Coerce(int)})
    reg("shopping_to_pantry", shopping_to_pantry, {vol.Optional("list_id"): vol.Coerce(int)})

    # --- Haushalt, Inventar, Budget -------------------------------------------

    async def complete_housekeeping(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        await _run(c, "POST", f"/api/v1/housekeeping/decay-tasks/{call.data['task_id']}/complete")

    async def housekeeper_check(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        body: dict[str, Any] = {"timezone": str(dt_util.get_default_time_zone())}
        if "worker_id" in call.data:
            body["worker_id"] = call.data["worker_id"]
        if "extras" in call.data:
            body["extras"] = call.data["extras"]
        await _run(c, "POST", f"/api/v1/housekeeping/work-sessions/check-{call.data['direction']}", body)

    async def complete_inventory_date(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        body = {k: call.data[k] for k in ("odometer", "vendor", "note") if k in call.data}
        await _run(c, "POST", f"/api/v1/inventory/items/{call.data['item_id']}/dates/{call.data['date_id']}/complete", body)

    async def add_budget_entry(call: ServiceCall) -> ServiceResponse:
        c = _coordinator(hass, call)
        # Yuvomi: Art ergibt sich aus der Kategorie, Ausgaben sind negativ
        expense = call.data.get("entry_type", "expense") == "expense"
        amount = abs(float(call.data["amount"]))
        body: dict[str, Any] = {
            "title": call.data["title"],
            "amount": -amount if expense else amount,
            "date": str(call.data["date"]),
            "category": call.data.get("category") or ("financial_other" if expense else "Sonstiges Einkommen"),
        }
        body |= {k: call.data[k] for k in ("category", "subcategory", "account_id", "visibility") if k in call.data}
        return _resp(call, await _run(c, "POST", "/api/v1/budget", body))

    async def renew_subscription(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        await _run(c, "POST", f"/api/v1/budget/subscriptions/{call.data['subscription_id']}/renew")

    async def loan_payment(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        body: dict[str, Any] = {"paid_date": str(call.data.get("paid_date") or dt_util.now().date())}
        if "amount" in call.data:
            body["amount"] = call.data["amount"]
        await _run(c, "POST", f"/api/v1/budget/loans/{call.data['loan_id']}/payments", body)

    reg("complete_housekeeping", complete_housekeeping, {vol.Required("task_id"): vol.Coerce(int)})
    reg("housekeeper_check", housekeeper_check, {
        vol.Required("direction"): vol.In(["in", "out"]), vol.Optional("worker_id"): vol.Coerce(int), vol.Optional("extras"): vol.Coerce(float),
    })
    reg("complete_inventory_date", complete_inventory_date, {
        vol.Required("item_id"): vol.Coerce(int), vol.Required("date_id"): vol.Coerce(int),
        vol.Optional("odometer"): vol.Coerce(float), vol.Optional("vendor"): cv.string, vol.Optional("note"): cv.string,
    })
    reg("add_budget_entry", add_budget_entry, {
        vol.Required("title"): cv.string, vol.Required("amount"): vol.Coerce(float), vol.Required("date"): cv.date,
        vol.Optional("entry_type", default="expense"): vol.In(["expense", "income"]),
        vol.Optional("category"): cv.string, vol.Optional("subcategory"): cv.string, vol.Optional("account_id"): vol.Coerce(int),
        vol.Optional("visibility"): vol.In(["private", "shared", "shared_amount"]),
    }, OPT)
    reg("renew_subscription", renew_subscription, {vol.Required("subscription_id"): vol.Coerce(int)})
    reg("loan_payment", loan_payment, {vol.Required("loan_id"): vol.Coerce(int), vol.Optional("paid_date"): cv.date, vol.Optional("amount"): cv.string})

    # --- Belohnungen, Erinnerungen, Notizen -----------------------------------

    async def reward_bonus(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        await _run(c, "POST", "/api/v1/rewards/bonus", {"user_id": call.data["user_id"], "points": call.data["points"], "reason": call.data.get("reason", "")})

    async def redeem_reward(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        body: dict[str, Any] = {"catalog_id": call.data["catalog_id"]}
        if "user_id" in call.data:
            body["user_id"] = call.data["user_id"]
        await _run(c, "POST", "/api/v1/rewards/redemptions", body)

    async def dismiss_reminder(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        await _run(c, "PATCH", f"/api/v1/reminders/{call.data['reminder_id']}/dismiss")

    async def pin_note(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        await _run(c, "PATCH", f"/api/v1/notes/{call.data['note_id']}/pin", {"pinned": call.data.get("pinned", True)})

    async def check_note_item(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        await _run(c, "PATCH", f"/api/v1/notes/{call.data['note_id']}/check", {"line": call.data["line"], "checked": call.data.get("checked", True)})

    reg("reward_bonus", reward_bonus, {vol.Required("user_id"): vol.Coerce(int), vol.Required("points"): vol.Coerce(int), vol.Optional("reason"): cv.string})
    reg("redeem_reward", redeem_reward, {vol.Required("catalog_id"): vol.Coerce(int), vol.Optional("user_id"): vol.Coerce(int)})
    reg("dismiss_reminder", dismiss_reminder, {vol.Required("reminder_id"): vol.Coerce(int)})
    reg("pin_note", pin_note, {vol.Required("note_id"): vol.Coerce(int), vol.Optional("pinned", default=True): cv.boolean})
    reg("check_note_item", check_note_item, {vol.Required("note_id"): vol.Coerce(int), vol.Required("line"): vol.Coerce(int), vol.Optional("checked", default=True): cv.boolean})

    # --- Gesundheit ----------------------------------------------------------

    async def add_vital(call: ServiceCall) -> ServiceResponse:
        c = _coordinator(hass, call)
        body = {k: call.data[k] for k in ("type", "value", "unit", "systolic", "diastolic", "pulse", "note", "user_id", "visibility") if k in call.data}
        body["measured_at"] = dt_util.as_local(call.data.get("measured_at") or dt_util.now()).strftime("%Y-%m-%dT%H:%M")
        return _resp(call, await _run(c, "POST", "/api/v1/health/vitals", body))

    async def medication_dose(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        log_id = call.data.get("log_id")
        action = call.data.get("action", "take")
        if log_id is not None:
            body = {"stock_deduct": call.data["stock_deduct"]} if "stock_deduct" in call.data and action == "take" else {}
            await _run(c, "POST", f"/api/v1/health/logs/{log_id}/{action}", body)
            return
        if "medication_id" not in call.data:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="log_or_medication")
        body = {"status": "taken" if action == "take" else "skipped", "taken_at": dt_util.now().strftime("%Y-%m-%dT%H:%M")}
        if "stock_deduct" in call.data and action == "take":
            body["stock_deduct"] = call.data["stock_deduct"]
        await _run(c, "POST", f"/api/v1/health/medications/{call.data['medication_id']}/logs", body)

    async def add_nutrition(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        body = {k: call.data[k] for k in ("title", "meal_type", "energy_kcal", "protein_g", "carbs_g", "fat_g", "sugar_g", "fiber_g", "salt_g", "note") if k in call.data}
        body["consumed_at"] = dt_util.now().strftime("%Y-%m-%dT%H:%M")
        await _run(c, "POST", "/api/v1/health/nutrition/entries", body)

    reg("add_vital", add_vital, {
        vol.Required("type"): cv.string, vol.Optional("value"): vol.Coerce(float), vol.Optional("unit"): cv.string,
        vol.Optional("systolic"): vol.Coerce(int), vol.Optional("diastolic"): vol.Coerce(int), vol.Optional("pulse"): vol.Coerce(int),
        vol.Optional("note"): cv.string, vol.Optional("user_id"): vol.Coerce(int),
        vol.Optional("visibility"): vol.In(["private", "family"]), vol.Optional("measured_at"): cv.datetime,
    }, OPT)
    reg("medication_dose", medication_dose, {
        vol.Optional("log_id"): vol.Coerce(int), vol.Optional("medication_id"): vol.Coerce(int),
        vol.Optional("action", default="take"): vol.In(["take", "skip"]), vol.Optional("stock_deduct"): vol.Coerce(float),
    })
    reg("add_nutrition", add_nutrition, {
        vol.Required("title"): cv.string, vol.Optional("meal_type"): vol.In(["breakfast", "lunch", "dinner", "snack"]),
        **{vol.Optional(k): vol.Coerce(float) for k in ("energy_kcal", "protein_g", "carbs_g", "fat_g", "sugar_g", "fiber_g", "salt_g")},
        vol.Optional("note"): cv.string,
    })

    # --- Sonstiges ----------------------------------------------------------

    async def refresh(call: ServiceCall) -> None:
        await _coordinator(hass, call).async_request_refresh()

    reg("refresh", refresh, {})

    async def sync_bring(call: ServiceCall) -> ServiceResponse:
        c = _coordinator(hass, call)
        sync = getattr(c, "bring_sync", None)
        if sync is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_bring_sync")
        return {"ergebnis": await sync.async_sync()}

    reg("sync_bring", sync_bring, {}, OPT)
