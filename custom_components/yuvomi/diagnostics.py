"""Diagnostics download (Settings → Devices & services → Yuvomi → ⋮ → Download diagnostics).

Contains no family data: only counts, field names and the setup. Token, URL and webhook id
are redacted.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant

from .api import YuvomiError
from .const import CONF_WEBHOOK_ID, VERSION

TO_REDACT = {CONF_TOKEN, CONF_URL, CONF_WEBHOOK_ID}


def _shape(value: Any) -> Any:
    """Describe a value without its content: count plus the field names of the first entry."""
    if isinstance(value, list):
        first = next((v for v in value if isinstance(v, dict)), None)
        return {"count": len(value), "fields": sorted(first) if first else []}
    if isinstance(value, dict):
        if value and all(isinstance(v, list) for v in value.values()):
            return {"keys": len(value), "entries": sum(len(v) for v in value.values())}
        return {"fields": sorted(str(k) for k in value)}
    return type(value).__name__ if value is not None else None


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    coordinator = getattr(entry, "runtime_data", None)
    out: dict[str, Any] = {
        "integration_version": VERSION,
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
    }
    if coordinator is None:
        out["state"] = "not loaded"
        return out

    try:
        version = await coordinator.client.version_info()
    except YuvomiError as err:
        version = {"error": str(err)}
    data = coordinator.data
    out["yuvomi_version"] = version
    out["coordinator"] = {
        "last_update_success": coordinator.last_update_success,
        "update_interval_s": coordinator.update_interval.total_seconds() if coordinator.update_interval else None,
        "enabled_modules": sorted(coordinator.modules),
    }
    if data is not None:
        out["modules_ok"] = data.modules_ok
        out["data"] = {
            f.name: _shape(getattr(data, f.name))
            for f in fields(data)
            if f.name not in ("modules_ok", "version", "members", "member_info")
        }
        out["data"]["members"] = len(data.members)
    sync = coordinator.bring_sync
    if sync is not None:
        out["bring_sync"] = {
            "last_run": sync.last_run,
            "last_result": sync.last_result,
            "last_error": sync.last_error,
            "tracked_items": len(sync._snap or {}),
        }
    return out
