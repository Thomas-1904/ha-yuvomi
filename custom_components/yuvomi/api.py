"""Kleiner asynchroner Client für die Yuvomi-REST-API (v1)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=20)


class YuvomiError(Exception):
    """Allgemeiner Fehler der API."""


class YuvomiAuthError(YuvomiError):
    """Token ungültig oder fehlende Rechte."""


class YuvomiConnectionError(YuvomiError):
    """Server nicht erreichbar."""


class YuvomiApiError(YuvomiError):
    """Server hat einen Fehler gemeldet."""

    def __init__(self, status: int, message: str, reason: str | None = None) -> None:
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message
        self.reason = reason


def unwrap(payload: Any) -> Any:
    """Die meisten Antworten stecken in {"data": ...}."""
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def as_list(payload: Any, *keys: str) -> list[dict[str, Any]]:
    """Liefert eine Liste – egal ob direkt, in data oder unter einem Schlüssel."""
    data = unwrap(payload)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


RATE_LIMIT_PER_MIN = 120  # Hälfte des Yuvomi-Limits (300/min je IP)


class YuvomiClient:
    """Zugriff auf eine Yuvomi-Instanz mit API-Token."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        self._session = session
        self._base = url.rstrip("/")
        self._token = token
        # Yuvomi erlaubt 300 Anfragen/Minute je IP – HA teilt sich das ggf. mit den
        # Handys im selben Netz. Wir bleiben deutlich darunter (gleitendes Fenster).
        self._stamps: list[float] = []
        self._rl_lock = asyncio.Lock()
        self._blocked_until = 0.0

    async def _throttle(self) -> None:
        loop = asyncio.get_running_loop()
        async with self._rl_lock:
            now = loop.time()
            if self._blocked_until > now:
                await asyncio.sleep(self._blocked_until - now)
                now = loop.time()
            self._stamps = [t for t in self._stamps if now - t < 60]
            if len(self._stamps) >= RATE_LIMIT_PER_MIN:
                wait = 60 - (now - self._stamps[0]) + 0.05
                await asyncio.sleep(max(wait, 0))
                now = loop.time()
                self._stamps = [t for t in self._stamps if now - t < 60]
            self._stamps.append(now)

    @property
    def base_url(self) -> str:
        return self._base

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | list[tuple[str, Any]] | None = None,
        json: Any = None,
    ) -> Any:
        """Führt eine Anfrage aus und gibt das JSON (oder Text) zurück."""
        if not path.startswith("/"):
            path = "/" + path
        url = f"{self._base}{path}"
        await self._throttle()
        headers = {
            "X-API-Key": self._token,
            "Accept": "application/json",
        }
        try:
            async with self._session.request(
                method.upper(),
                url,
                params=params,
                json=json,
                headers=headers,
                timeout=TIMEOUT,
            ) as resp:
                if resp.status == 204:
                    return None
                ctype = resp.headers.get("Content-Type", "")
                body: Any
                if "application/json" in ctype:
                    body = await resp.json(content_type=None)
                else:
                    body = await resp.text()
                if resp.status in (401, 403):
                    msg = body.get("error") if isinstance(body, dict) else str(body)
                    reason = body.get("reason") if isinstance(body, dict) else None
                    if resp.status == 401:
                        raise YuvomiAuthError(msg or "Not authenticated")
                    raise YuvomiApiError(resp.status, msg or "Permission denied", reason)
                if resp.status == 429:
                    try:
                        retry = float(resp.headers.get("Retry-After", "30"))
                    except ValueError:
                        retry = 30.0
                    self._blocked_until = asyncio.get_running_loop().time() + min(max(retry, 1), 300)
                    raise YuvomiApiError(429, f"Rate limit reached, pausing {int(retry)} s", "rate_limited")
                if resp.status >= 400:
                    msg = body.get("error") if isinstance(body, dict) else str(body)
                    reason = body.get("reason") if isinstance(body, dict) else None
                    raise YuvomiApiError(resp.status, msg or "Error", reason)
                return body
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as err:
            raise YuvomiConnectionError(str(err) or "Timeout") from err

    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, json: Any = None, **kwargs: Any) -> Any:
        return await self.request("POST", path, json=json if json is not None else {}, **kwargs)

    async def put(self, path: str, json: Any = None, **kwargs: Any) -> Any:
        return await self.request("PUT", path, json=json if json is not None else {}, **kwargs)

    async def patch(self, path: str, json: Any = None, **kwargs: Any) -> Any:
        return await self.request("PATCH", path, json=json if json is not None else {}, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> Any:
        return await self.request("DELETE", path, **kwargs)

    # --- Komfort -----------------------------------------------------------

    async def me(self) -> dict[str, Any]:
        data = await self.get("/api/v1/auth/me")
        if isinstance(data, dict):
            return data.get("user") or unwrap(data) or {}
        return {}

    async def version_info(self) -> dict[str, Any]:
        """`/api/v1/version` – ab Yuvomi 2.84 mit `client_api` (Schnittstellen-Generation)."""
        data = await self.get("/api/v1/version")
        return data if isinstance(data, dict) else {}

    async def version(self) -> str | None:
        data = await self.get("/api/v1/version")
        if isinstance(data, dict):
            return data.get("version")
        return None
