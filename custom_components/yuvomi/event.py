"""Event entity: one event per Yuvomi notification that reaches the webhook.

Makes notifications usable in the automation editor without YAML (trigger: entity
"Yuvomi notification" → event type "notification").
"""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import YuvomiCoordinator
from .entity import YuvomiEntity

EVENT_TYPE = "notification"
_TASK_ID = re.compile(r"tasks?[-_/:=](\d+)", re.IGNORECASE)


def task_id_of(notification: dict[str, Any]) -> int | None:
    """Task id from the notification's tag or url (e.g. 'task-42', '/tasks/42'), if any."""
    for key in ("tag", "url"):
        m = _TASK_ID.search(str(notification.get(key) or ""))
        if m:
            return int(m.group(1))
    return None


def event_data(payload: Any) -> dict[str, Any]:
    note = payload.get("notification") if isinstance(payload, dict) else None
    if not isinstance(note, dict):
        note = {"body": payload.get("text") if isinstance(payload, dict) else str(payload)}
    return {
        "title": note.get("title"),
        "body": note.get("body"),
        "url": note.get("url"),
        "tag": note.get("tag"),
        "priority": note.get("priority"),
        "task_id": task_id_of(note),
        "sent_at": payload.get("sentAt") if isinstance(payload, dict) else None,
    }


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([YuvomiNotificationEvent(entry.runtime_data)])


class YuvomiNotificationEvent(YuvomiEntity, EventEntity):
    """Last Yuvomi notification."""

    _attr_translation_key = "notification"
    _attr_icon = "mdi:bell-ring-outline"
    _attr_event_types = [EVENT_TYPE]

    def __init__(self, coordinator: YuvomiCoordinator) -> None:
        super().__init__(coordinator, "notification")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"{DOMAIN}_webhook_{self.coordinator.config_entry.entry_id}", self._on_webhook
            )
        )

    @callback
    def _on_webhook(self, payload: Any) -> None:
        self._trigger_event(EVENT_TYPE, event_data(payload))
        self.async_write_ha_state()
