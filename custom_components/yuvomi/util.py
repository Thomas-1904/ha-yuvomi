"""Hilfsfunktionen."""

from __future__ import annotations

from typing import Any

_DAYS = {"MO": "Mo", "TU": "Di", "WE": "Mi", "TH": "Do", "FR": "Fr", "SA": "Sa", "SU": "So"}


def rrule_text(rule: str | None) -> str | None:
    """RRULE in deutschen Klartext: 'FREQ=WEEKLY;INTERVAL=2;BYDAY=SU' -> 'alle 2 Wochen (So)'."""
    if not rule:
        return None
    parts = dict(p.split("=", 1) for p in str(rule).replace("RRULE:", "").split(";") if "=" in p)
    freq = parts.get("FREQ", "").upper()
    try:
        n = int(parts.get("INTERVAL", "1"))
    except ValueError:
        n = 1
    unit = {"DAILY": ("täglich", "Tage"), "WEEKLY": ("wöchentlich", "Wochen"),
            "MONTHLY": ("monatlich", "Monate"), "YEARLY": ("jährlich", "Jahre")}.get(freq)
    if not unit:
        return rule
    txt = unit[0] if n == 1 else f"alle {n} {unit[1]}"
    days = [_DAYS.get(d[-2:], d) for d in parts.get("BYDAY", "").split(",") if d]
    if days:
        txt += f" ({', '.join(days)})"
    if parts.get("BYMONTHDAY"):
        txt += f" (am {parts['BYMONTHDAY']}.)"
    return txt


def _ver(v: Any) -> tuple[int, ...]:
    parts = []
    for p in str(v or "0").split("-")[0].split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts + [0] * (3 - len(parts)))


def check_compat(info: dict[str, Any]) -> str:
    """Bewertet /api/v1/version.

    Rückgabe: "ok", "too_old" (nicht unterstützt), "legacy" (ohne client_api, aber
    mindestens die getestete Version – läuft, Update empfohlen) oder "newer_api"
    (neuere Schnittstellen-Generation als bekannt – läuft, aber ungetestet).
    """
    from .const import MIN_APP_VERSION, SUPPORTED_CLIENT_API

    api = info.get("client_api")
    if isinstance(api, int) or (isinstance(api, str) and api.isdigit()):
        api = int(api)
        if api < SUPPORTED_CLIENT_API:
            return "too_old"
        return "newer_api" if api > SUPPORTED_CLIENT_API else "ok"
    version = info.get("version")
    if version and _ver(version) < _ver(MIN_APP_VERSION):
        return "too_old"
    return "legacy"


def is_de(hass: Any) -> bool:
    """Home Assistant runs in German – for the few names built at runtime (not translatable)."""
    return str(getattr(getattr(hass, "config", None), "language", "") or "").lower().startswith("de")


def txt(hass: Any, de: str, en: str) -> str:
    return de if is_de(hass) else en
