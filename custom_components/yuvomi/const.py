"""Konstanten für die Yuvomi-Integration."""

from __future__ import annotations

DOMAIN = "yuvomi"
MANUFACTURER = "Yuvomi"
VERSION = "0.5.2"

CONF_SCAN_INTERVAL = "scan_interval"
CONF_WEBHOOK_ID = "webhook_id"
CONF_MODULES = "modules"
CONF_EVENTS = "fire_events"
CONF_BRING_ENTITY = "bring_entity"
CONF_BRING_LIST = "bring_list"
CONF_PUSH = "push_channel"
CONF_UPDATE_CHECK = "update_check"
PUSH_CHANNEL_NAME = "Home Assistant"
FAST_POLL_SECONDS = 15

DEFAULT_SCAN_INTERVAL = 60  # Sekunden
MIN_SCAN_INTERVAL = 15

# Unterstützte Schnittstellen-Generation (`client_api` aus /api/v1/version, ab Yuvomi 2.84).
SUPPORTED_CLIENT_API = 1
# Ältere Instanzen ohne `client_api`: mindestens diese App-Version (damit getestet).
MIN_APP_VERSION = "2.79.0"
EXPERIMENTAL = {"waste", "budget", "loans", "split", "rewards", "notes", "contacts", "documents", "schedule", "health"}

PLATFORMS = ["binary_sensor", "button", "calendar", "event", "sensor", "todo", "update"]

# Module, die einzeln aktiviert werden können: Schlüssel -> (Name, Standard an?)
MODULES: dict[str, tuple[str, bool]] = {
    "tasks": ("Aufgaben", True),
    "shopping": ("Einkaufslisten", True),
    "calendar": ("Familienkalender", True),
    "meals": ("Essensplan", True),
    "recipes": ("Rezepte", False),
    "pantry": ("Vorrat", True),
    "inventory": ("Inventar & Wartung", True),
    "housekeeping": ("Putzplan & Haushaltshilfe", True),
    "waste": ("Müllabfuhr (experimentell)", False),
    "birthdays": ("Geburtstage", True),
    "budget": ("Budget, Konten & Abos (experimentell)", False),
    "loans": ("Kredite (experimentell)", False),
    "split": ("Geteilte Ausgaben (experimentell)", False),
    "rewards": ("Belohnungen & Taschengeld (experimentell)", False),
    "reminders": ("Erinnerungen", True),
    "notes": ("Notizen (experimentell)", False),
    "contacts": ("Kontakte (experimentell)", False),
    "documents": ("Dokumente – Fristen (experimentell)", False),
    "schedule": ("Schichtplan (experimentell)", False),
    "health": ("Gesundheit – Medikamente, Vitalwerte, Vorsorge (experimentell)", False),
}

# Generische Ressourcen für yuvomi.create / update / delete
# Schlüssel -> (Modul, Anlegen-Pfad, Einzel-Pfad mit {id}, Ändern-Methode)
RESOURCES: dict[str, tuple[str, str, str, str]] = {
    "task": ("tasks", "/api/v1/tasks", "/api/v1/tasks/{id}", "PUT"),
    "task_comment": ("tasks", "/api/v1/tasks/{parent_id}/comments", "/api/v1/tasks/{parent_id}/comments/{id}", "PATCH"),
    "task_category": ("tasks", "/api/v1/tasks/categories", "/api/v1/tasks/categories/{id}", "PUT"),
    "shopping_list": ("shopping", "/api/v1/shopping", "/api/v1/shopping/{id}", "PUT"),
    "shopping_item": ("shopping", "/api/v1/shopping/{parent_id}/items", "/api/v1/shopping/items/{id}", "PATCH"),
    "shopping_category": ("shopping", "/api/v1/shopping/categories", "/api/v1/shopping/categories/{id}", "PUT"),
    "shopping_store": ("shopping", "/api/v1/shopping/stores", "/api/v1/shopping/stores/{id}", "PUT"),
    "event": ("calendar", "/api/v1/calendar", "/api/v1/calendar/{id}", "PUT"),
    "local_calendar": ("calendar", "/api/v1/calendar/calendars", "/api/v1/calendar/calendars/{id}", "PUT"),
    "meal": ("meals", "/api/v1/meals", "/api/v1/meals/{id}", "PUT"),
    "meal_ingredient": ("meals", "/api/v1/meals/{parent_id}/ingredients", "/api/v1/meals/ingredients/{id}", "PATCH"),
    "recipe": ("recipes", "/api/v1/recipes", "/api/v1/recipes/{id}", "PUT"),
    "pantry_item": ("pantry", "/api/v1/pantry", "/api/v1/pantry/{id}", "PATCH"),
    "pantry_location": ("pantry", "/api/v1/pantry/locations", "/api/v1/pantry/locations/{id}", "PUT"),
    "inventory_item": ("inventory", "/api/v1/inventory/items", "/api/v1/inventory/items/{id}", "PUT"),
    "inventory_service_log": ("inventory", "/api/v1/inventory/items/{parent_id}/service-log", "/api/v1/inventory/items/{parent_id}/service-log/{id}", "PUT"),
    "inventory_location": ("inventory", "/api/v1/inventory/locations", "/api/v1/inventory/locations/{id}", "PUT"),
    "housekeeping_task": ("housekeeping", "/api/v1/housekeeping/decay-tasks", "/api/v1/housekeeping/decay-tasks/{id}", "PATCH"),
    "housekeeping_log": ("housekeeping", "/api/v1/housekeeping/maintenance-log", "", ""),
    "housekeeping_visit": ("housekeeping", "", "/api/v1/housekeeping/visits/{id}", "PUT"),
    "waste_type": ("waste", "/api/v1/waste/types", "/api/v1/waste/types/{id}", "PUT"),
    "waste_schedule": ("waste", "/api/v1/waste/schedules", "/api/v1/waste/schedules/{id}", "PUT"),
    "waste_pickup": ("waste", "/api/v1/waste/pickups", "/api/v1/waste/pickups/{id}", "PUT"),
    "birthday": ("birthdays", "/api/v1/birthdays", "/api/v1/birthdays/{id}", "PUT"),
    "budget_entry": ("budget", "/api/v1/budget", "/api/v1/budget/{id}", "PUT"),
    "budget_transfer": ("budget", "/api/v1/budget/transfers", "/api/v1/budget/transfers/{id}", "PUT"),
    "budget_account": ("budget", "/api/v1/budget/accounts", "/api/v1/budget/accounts/{id}", "PUT"),
    "budget_category": ("budget", "/api/v1/budget/categories", "/api/v1/budget/categories/{id}", "PUT"),
    "subscription": ("budget", "/api/v1/budget/subscriptions", "/api/v1/budget/subscriptions/{id}", "PUT"),
    "loan": ("loans", "/api/v1/budget/loans", "/api/v1/budget/loans/{id}", "PUT"),
    "loan_payment": ("loans", "/api/v1/budget/loans/{parent_id}/payments", "/api/v1/budget/loans/{parent_id}/payments/{id}", ""),
    "split_group": ("split", "/api/v1/split-expenses/groups", "/api/v1/split-expenses/groups/{id}", "PATCH"),
    "split_expense": ("split", "/api/v1/split-expenses/groups/{parent_id}/expenses", "/api/v1/split-expenses/expenses/{id}", "PUT"),
    "split_settlement": ("split", "/api/v1/split-expenses/groups/{parent_id}/settlements", "", ""),
    "reward": ("rewards", "/api/v1/rewards/catalog", "/api/v1/rewards/catalog/{id}", "PATCH"),
    "reward_redemption": ("rewards", "/api/v1/rewards/redemptions", "/api/v1/rewards/redemptions/{id}", "PATCH"),
    "reward_bonus": ("rewards", "/api/v1/rewards/bonus", "", ""),
    "money_entry": ("rewards", "/api/v1/rewards/money/entries", "", ""),
    "reminder": ("reminders", "/api/v1/reminders", "/api/v1/reminders/{id}", ""),
    "note": ("notes", "/api/v1/notes", "/api/v1/notes/{id}", "PUT"),
    "contact": ("contacts", "/api/v1/contacts", "/api/v1/contacts/{id}", "PUT"),
    "document": ("documents", "/api/v1/documents", "/api/v1/documents/{id}", "PUT"),
    "shift_type": ("schedule", "/api/v1/schedule/shift-types", "/api/v1/schedule/shift-types/{id}", "PUT"),
    "shift_extra": ("schedule", "/api/v1/schedule/extras", "/api/v1/schedule/extras/{id}", "PUT"),
    "shift_override": ("schedule", "", "/api/v1/schedule/overrides/{id}", "PUT"),
    "vital": ("health", "/api/v1/health/vitals", "/api/v1/health/vitals/{id}", "PATCH"),
    "medication": ("health", "/api/v1/health/medications", "/api/v1/health/medications/{id}", "PATCH"),
    "medication_log": ("health", "/api/v1/health/medications/{parent_id}/logs", "/api/v1/health/logs/{id}", "PATCH"),
    "activity": ("health", "/api/v1/health/activities", "/api/v1/health/activities/{id}", "PATCH"),
    "lab_report": ("health", "/api/v1/health/labs", "/api/v1/health/labs/{id}", "PATCH"),
    "prevention_record": ("health", "/api/v1/health/prevention/records", "/api/v1/health/prevention/records/{id}", "PATCH"),
    "nutrition_entry": ("health", "/api/v1/health/nutrition/entries", "/api/v1/health/nutrition/entries/{id}", "PATCH"),
    "fast": ("health", "/api/v1/health/fasting", "/api/v1/health/fasting/{id}", "PATCH"),
    "quick_link": ("dashboard", "/api/v1/quick-links", "/api/v1/quick-links/{id}", "PUT"),
}

EVENT_WEBHOOK = "yuvomi_webhook"
EVENT_CHANGED = "yuvomi_changed"
