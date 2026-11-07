"""Yuvomi-Familienplaner für Home Assistant."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import YuvomiClient
from datetime import timedelta

from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .api import YuvomiError, as_list
from .bring_sync import BringSync
from .const import (
    CONF_BRING_ENTITY,
    CONF_BRING_LIST,
    CONF_PUSH,
    CONF_WEBHOOK_ID,
    DOMAIN,
    EVENT_WEBHOOK,
    FAST_POLL_SECONDS,
    MODULES,
    PLATFORMS,
    PUSH_CHANNEL_NAME,
)
from .coordinator import YuvomiCoordinator
from .entity import unique_prefix
from .services import register_services

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type YuvomiConfigEntry = ConfigEntry[YuvomiCoordinator]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: YuvomiConfigEntry) -> bool:
    client = YuvomiClient(async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_TOKEN])
    coordinator = YuvomiCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    _remove_disabled_module_entities(hass, entry, coordinator.modules)
    hass.async_create_task(_check_version(hass, entry, client), eager_start=False)
    _check_scopes(hass, entry, coordinator)
    entry.async_on_unload(coordinator.async_add_listener(lambda: _check_scopes(hass, entry, coordinator)))

    wid = entry.data.get(CONF_WEBHOOK_ID)
    if not wid and entry.options.get(CONF_PUSH, True):
        # Ältere Einträge haben noch keine Webhook-ID – jetzt nachrüsten.
        wid = webhook.async_generate_id()
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_WEBHOOK_ID: wid})
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    if wid:
        webhook.async_register(
            hass, DOMAIN, "Yuvomi", wid, _make_webhook_handler(entry.entry_id),
            allowed_methods=["POST", "PUT"],
        )
        entry.async_on_unload(lambda: webhook.async_unregister(hass, wid))
        if entry.options.get(CONF_PUSH, True):
            hass.async_create_task(_ensure_push_channel(hass, client, wid), eager_start=False)

    # Schnellprüfung: Änderungszähler der Einkaufslisten alle paar Sekunden (sehr leichtgewichtig).
    if coordinator.on("shopping"):
        async def _fast(_now) -> None:
            try:
                versions = as_list(await client.get("/api/v1/shopping/versions"))
            except YuvomiError:
                return
            cur = {int(v["list_id"]): v.get("version") for v in versions if v.get("list_id") is not None}
            if coordinator.data and cur and cur != coordinator.data.shopping_versions:
                await coordinator.async_request_refresh()

        entry.async_on_unload(async_track_time_interval(hass, _fast, timedelta(seconds=FAST_POLL_SECONDS)))

    # Bring-Abgleich
    bring = entry.options.get(CONF_BRING_ENTITY)
    if bring and coordinator.on("shopping") and coordinator.data.shopping_lists:
        lid = entry.options.get(CONF_BRING_LIST) or coordinator.data.shopping_lists[0]["id"]
        sync = BringSync(hass, coordinator, bring, int(lid))
        coordinator.bring_sync = sync
        await sync.async_start()
        entry.async_on_unload(sync.async_stop)
    return True


# Integration module -> Yuvomi token scope
MODULE_SCOPES = {
    "tasks": "tasks", "shopping": "shopping", "calendar": "calendar", "meals": "meals", "recipes": "meals",
    "pantry": "pantry", "inventory": "inventory", "housekeeping": "housekeeping", "waste": "waste",
    "birthdays": "calendar", "reminders": "calendar", "budget": "budget", "loans": "budget", "split": "budget",
    "rewards": "rewards", "notes": "notes", "contacts": "contacts", "documents": "documents",
    "schedule": "schedule", "health": "health",
}


def _check_scopes(hass: HomeAssistant, entry: ConfigEntry, coordinator: YuvomiCoordinator) -> None:
    """Repair notice when enabled modules get no data because the token lacks their scope."""
    if not coordinator.last_update_success or coordinator.data is None:
        return
    ok = coordinator.data.modules_ok
    missing = sorted(m for m in coordinator.modules if m in MODULE_SCOPES and ok.get(m) is False)
    if ok.get("dashboard") is False and "calendar" in coordinator.modules and "calendar" not in missing:
        missing.append("calendar")
    issue_id = f"missing_scope_{entry.entry_id}"
    if not missing:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    scopes = sorted({f"{MODULE_SCOPES[m]}:write" for m in missing})
    if "calendar" in missing:
        scopes.append("dashboard:read")
    ir.async_create_issue(
        hass, DOMAIN, issue_id, is_fixable=False, severity=ir.IssueSeverity.WARNING,
        translation_key="missing_scope",
        translation_placeholders={"modules": ", ".join(missing), "scopes": ", ".join(scopes)},
    )


async def _check_version(hass: HomeAssistant, entry: ConfigEntry, client: YuvomiClient) -> None:
    """Hinweis in den Reparaturen, wenn die Yuvomi-Instanz nicht zur Integration passt."""
    from .util import check_compat

    try:
        info = await client.version_info()
    except YuvomiError:
        return
    verdict = check_compat(info)
    for key in ("too_old", "legacy", "newer_api"):
        issue_id = f"{key}_{entry.entry_id}"
        if key == verdict:
            ir.async_create_issue(
                hass, DOMAIN, issue_id, is_fixable=False,
                severity=ir.IssueSeverity.ERROR if key == "too_old" else ir.IssueSeverity.WARNING,
                translation_key=key,
                translation_placeholders={"version": str(info.get("version") or "?"), "api": str(info.get("client_api") or "–")},
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, issue_id)


async def _ensure_push_channel(hass: HomeAssistant, client: YuvomiClient, wid: str) -> None:
    """Benachrichtigungskanal „Webhook“ in Yuvomi anlegen, der an diese HA-Instanz meldet."""
    try:
        base = get_url(hass, allow_external=False, allow_cloud=False, prefer_external=False)
    except NoURLAvailableError:
        _LOGGER.warning("Yuvomi notification channel not set up: no Home Assistant URL configured")
        return
    urls = [f"{base}/api/webhook/{wid}"]
    try:
        # Yuvomi lehnt private Adressen ab (außer NOTIFICATION_ALLOW_PRIVATE_NETWORK=true) – dann extern.
        ext = get_url(hass, allow_internal=False, prefer_external=True)
        urls.append(f"{ext}/api/webhook/{wid}")
    except NoURLAvailableError:
        pass
    try:
        channels = as_list(await client.get("/api/v1/notifications/channels"))
    except YuvomiError as err:
        _LOGGER.warning("Yuvomi notification channel not set up: %s", err)
        return
    mine = next((c for c in channels if c.get("provider") == "webhook" and c.get("name") == PUSH_CHANNEL_NAME), None)
    if mine is not None and (mine.get("config") or {}).get("baseUrl") in urls and mine.get("enabled"):
        return
    last_err: Exception | None = None
    for url in urls:
        body = {"provider": "webhook", "name": PUSH_CHANNEL_NAME, "enabled": True, "config": {"baseUrl": url}}
        try:
            if mine is None:
                await client.post("/api/v1/notifications/channels", body)
            else:
                await client.put(f"/api/v1/notifications/channels/{mine['id']}", body)
            _LOGGER.info("Yuvomi notification channel set up: %s", url.split("/api/webhook/")[0])
            return
        except YuvomiError as err:
            last_err = err
    _LOGGER.warning("Yuvomi notification channel not set up: %s", last_err)


async def async_unload_entry(hass: HomeAssistant, entry: YuvomiConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: YuvomiConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _remove_disabled_module_entities(hass: HomeAssistant, entry: ConfigEntry, enabled: set[str]) -> None:
    """Entitäten abgewählter Module aus der Registry entfernen."""
    registry = er.async_get(hass)
    disabled = [m for m in MODULES if m not in enabled]
    prefixes = tuple(unique_prefix(entry.entry_id, m) for m in disabled)
    if not prefixes:
        return
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        if ent.unique_id.startswith(prefixes):
            registry.async_remove(ent.entity_id)


def _make_webhook_handler(entry_id: str):
    async def _handle(hass: HomeAssistant, webhook_id: str, request: web.Request) -> web.Response:
        try:
            payload: Any = await request.json()
        except ValueError:
            payload = {"text": await request.text()}
        hass.bus.async_fire(EVENT_WEBHOOK, {"entry_id": entry_id, "data": payload})
        async_dispatcher_send(hass, f"{DOMAIN}_webhook_{entry_id}", payload)
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry and isinstance(getattr(entry, "runtime_data", None), YuvomiCoordinator):
            # Sofort antworten – Yuvomi bricht sonst nach wenigen Sekunden ab.
            hass.async_create_task(entry.runtime_data.async_request_refresh(), eager_start=False)
        return web.Response(status=200)

    return _handle
