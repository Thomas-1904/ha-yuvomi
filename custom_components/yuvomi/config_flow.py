"""Einrichtung über die Oberfläche."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.components import webhook
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import YuvomiApiError, YuvomiAuthError, YuvomiClient, YuvomiConnectionError, YuvomiError
from .util import check_compat
from homeassistant.helpers import selector

from .const import (
    CONF_BRING_ENTITY,
    CONF_BRING_LIST,
    CONF_EVENTS,
    CONF_PUSH,
    CONF_MODULES,
    MODULES,
    CONF_SCAN_INTERVAL,
    CONF_WEBHOOK_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)


class YuvomiTooOld(YuvomiError):
    """Instanz älter als unterstützt."""


async def _validate(hass, url: str, token: str) -> dict[str, Any]:
    client = YuvomiClient(async_get_clientsession(hass), url, token)
    try:
        me = await client.me()
    except YuvomiApiError as err:
        if err.status != 403:
            raise
        # Token mit Scopes: /auth/me gehört zu keinem Modul und ist gesperrt.
        # Gültigkeit stattdessen über die Mitgliederliste prüfen (family:read).
        await client.get("/api/v1/family/members")
        me = {}
    try:
        info = await client.version_info()
    except YuvomiError:
        info = {}
    if check_compat(info) == "too_old":
        raise YuvomiTooOld(str(info.get("version")))
    return me


class YuvomiConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            if not url.startswith(("http://", "https://")):
                url = "http://" + url
            token = user_input[CONF_TOKEN].strip()
            await self.async_set_unique_id(url.lower())
            self._abort_if_unique_id_configured()
            try:
                me = await _validate(self.hass, url, token)
            except YuvomiAuthError:
                errors["base"] = "invalid_auth"
            except YuvomiConnectionError:
                errors["base"] = "cannot_connect"
            except YuvomiTooOld:
                errors["base"] = "unsupported_version"
            except YuvomiApiError as err:
                errors["base"] = "missing_scope" if err.status == 403 else "unknown"
            except YuvomiError:
                errors["base"] = "unknown"
            else:
                name = me.get("display_name") or me.get("username")
                self._title = f"Yuvomi ({name})" if name else "Yuvomi"
                self._data = {
                    CONF_URL: url,
                    CONF_TOKEN: token,
                    CONF_WEBHOOK_ID: webhook.async_generate_id(),
                }
                return await self.async_step_modules()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_URL, default=(user_input or {}).get(CONF_URL, "http://")): str,
                    vol.Required(CONF_TOKEN): str,
                }
            ),
            errors=errors,
        )

    async def async_step_modules(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=self._title,
                data=self._data,
                options={
                    CONF_MODULES: user_input[CONF_MODULES],
                    CONF_EVENTS: user_input.get(CONF_EVENTS, True),
                    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                    # Neu eingerichtet: Kanal nicht automatisch anlegen (bräuchte ein Admin-Token ohne Scopes).
                    CONF_PUSH: False,
                },
            )
        return self.async_show_form(step_id="modules", data_schema=_modules_schema(None, True))

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                await _validate(self.hass, entry.data[CONF_URL], user_input[CONF_TOKEN].strip())
            except YuvomiAuthError:
                errors["base"] = "invalid_auth"
            except YuvomiConnectionError:
                errors["base"] = "cannot_connect"
            except YuvomiError:
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: user_input[CONF_TOKEN].strip()}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change URL and/or token without removing the integration."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            if not url.startswith(("http://", "https://")):
                url = "http://" + url
            token = (user_input.get(CONF_TOKEN) or "").strip() or entry.data[CONF_TOKEN]
            await self.async_set_unique_id(url.lower())
            if url.lower() != (entry.unique_id or ""):
                self._abort_if_unique_id_configured()
            try:
                await _validate(self.hass, url, token)
            except YuvomiAuthError:
                errors["base"] = "invalid_auth"
            except YuvomiConnectionError:
                errors["base"] = "cannot_connect"
            except YuvomiTooOld:
                errors["base"] = "unsupported_version"
            except YuvomiApiError as err:
                errors["base"] = "missing_scope" if err.status == 403 else "unknown"
            except YuvomiError:
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=url.lower(),
                    data_updates={CONF_URL: url, CONF_TOKEN: token},
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_URL, default=entry.data[CONF_URL]): str,
                    vol.Optional(CONF_TOKEN): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return YuvomiOptionsFlow()


def _modules_schema(options: Mapping[str, Any] | None, events: bool) -> vol.Schema:
    current = (options or {}).get(CONF_MODULES) or [k for k, (_, on) in MODULES.items() if on]
    return vol.Schema(
        {
            vol.Required(CONF_MODULES, default=list(current)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(MODULES),
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                    translation_key="modules",
                )
            ),
            vol.Optional(CONF_EVENTS, default=(options or {}).get(CONF_EVENTS, events)): bool,
        }
    )


class YuvomiOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        opts = self.config_entry.options
        schema = _modules_schema(opts, True).extend(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL, default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=3600)),
                vol.Required(CONF_PUSH, default=opts.get(CONF_PUSH, True)): bool,
            }
        )
        lists = []
        coord = getattr(self.config_entry, "runtime_data", None)
        if coord is not None and getattr(coord, "data", None):
            lists = [
                selector.SelectOptionDict(value=str(lst["id"]), label=str(lst.get("name") or lst["id"]))
                for lst in coord.data.shopping_lists
            ]
        bring = opts.get(CONF_BRING_ENTITY)
        schema = schema.extend(
            {
                vol.Optional(
                    CONF_BRING_ENTITY, description={"suggested_value": bring} if bring else None
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="todo")),
            }
        )
        if lists:
            schema = schema.extend(
                {
                    vol.Optional(
                        CONF_BRING_LIST, default=str(opts.get(CONF_BRING_LIST) or lists[0]["value"])
                    ): selector.SelectSelector(selector.SelectSelectorConfig(options=lists)),
                }
            )
        return self.async_show_form(step_id="init", data_schema=schema)
