"""Update entity: is a newer Yuvomi version available?

Installed version: the instance's `/api/v1/version` (checked hourly).
Latest version: the release tags of the Yuvomi repository on GitHub (checked every 6 h,
public API, no account needed). If Yuvomi itself reports a newer version in
`/api/v1/version` (`latest` / `latest_version`), that is used and GitHub is not asked.

Yuvomi runs in its own container, so there is no install button – the entity only tells
you that an update exists and links to what changed.
"""

from __future__ import annotations

from datetime import timedelta
import logging
import re
import time
from typing import Any

import aiohttp

from homeassistant.components.update import UpdateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import YuvomiError
from .const import CONF_UPDATE_CHECK, DOMAIN
from .coordinator import YuvomiCoordinator
from .entity import YuvomiEntity
from .util import txt

_LOGGER = logging.getLogger(__name__)

REPO = "ulsklyc/yuvomi"
TAGS_URL = f"https://api.github.com/repos/{REPO}/tags?per_page=100"
GITHUB_EVERY = 6 * 3600
_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse_version(tag: Any) -> tuple[int, int, int] | None:
    """'v2.85.0' -> (2, 85, 0); pre-releases and other tags -> None."""
    m = _SEMVER.match(str(tag or "").strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def newest(tags: list[str]) -> str | None:
    """Highest stable version from a list of tag names, without the leading 'v'."""
    best = max((v for v in (parse_version(t) for t in tags) if v), default=None)
    return ".".join(map(str, best)) if best else None


async def fetch_latest_from_github(session: aiohttp.ClientSession) -> str | None:
    async with session.get(
        TAGS_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "ha-yuvomi"},
        timeout=aiohttp.ClientTimeout(total=20),
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return newest([t.get("name") for t in data if isinstance(t, dict)])


class VersionCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Installed version hourly, latest version from GitHub at most every 6 hours."""

    def __init__(self, hass: HomeAssistant, main: YuvomiCoordinator) -> None:
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_version", config_entry=main.config_entry,
            update_interval=timedelta(hours=1),
        )
        self.main = main
        self._latest: str | None = None
        self._checked = 0.0

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            info = await self.main.client.version_info()
        except YuvomiError:
            info = {}
        installed = str(info.get("version") or self.main.data.version or "") or None
        own = info.get("latest") or info.get("latest_version")
        if parse_version(own):
            latest = str(own).lstrip("v")
        else:
            if time.monotonic() - self._checked > GITHUB_EVERY or self._latest is None:
                try:
                    self._latest = await fetch_latest_from_github(async_get_clientsession(self.hass))
                    self._checked = time.monotonic()
                except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                    _LOGGER.debug("Yuvomi update check on GitHub failed: %s", err)
            latest = self._latest
        # Never offer a "downgrade" (e.g. GitHub not yet tagged, or a dev build installed)
        if installed and latest and (parse_version(latest) or (0,)) < (parse_version(installed) or (0,)):
            latest = installed
        return {"installed": installed, "latest": latest or installed}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    if not entry.options.get(CONF_UPDATE_CHECK, True):
        return
    main: YuvomiCoordinator = entry.runtime_data
    versions = VersionCoordinator(hass, main)
    await versions.async_refresh()
    async_add_entities([YuvomiUpdate(main, versions)])


class YuvomiUpdate(YuvomiEntity, UpdateEntity):
    """Yuvomi server version."""

    _attr_translation_key = "server"
    _attr_title = "Yuvomi"

    def __init__(self, main: YuvomiCoordinator, versions: VersionCoordinator) -> None:
        super().__init__(main, "update")
        self._versions = versions

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._versions.async_add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        return self._versions.last_update_success and bool((self._versions.data or {}).get("installed"))

    @property
    def installed_version(self) -> str | None:
        return (self._versions.data or {}).get("installed")

    @property
    def latest_version(self) -> str | None:
        return (self._versions.data or {}).get("latest")

    @property
    def release_url(self) -> str | None:
        inst, latest = self.installed_version, self.latest_version
        if inst and latest and inst != latest:
            return f"https://github.com/{REPO}/compare/v{inst}...v{latest}"
        return f"https://github.com/{REPO}/tags"

    @property
    def release_summary(self) -> str | None:
        if self.installed_version != self.latest_version:
            return txt(
                self.hass,
                "Yuvomi dort aktualisieren, wo es läuft (z. B. neues Docker-Image ziehen). Der Link zeigt alle Änderungen seit deiner Version.",
                "Update Yuvomi on the host it runs on (e.g. pull the new Docker image). The link shows all changes since your version.",
            )
        return None
