<img src="brand/icon.png" alt="" width="96" align="right">

# Yuvomi für Home Assistant

*Inoffizielle Community-Integration – nicht Teil des Yuvomi-Projekts.* · [English](README.md)

Bindet den selbst gehosteten Familienplaner [Yuvomi](https://github.com/ulsklyc/yuvomi) an Home Assistant an. Yuvomi bleibt der Master: Was Home Assistant anlegt, ändert oder löscht, landet sofort in Yuvomi, und Änderungen in Yuvomi kommen in Home Assistant als Zustände und Ereignisse an.

Die Integration spricht **ausschließlich über die öffentliche REST-API (`/api/v1`)** mit einem API-Token und den unten genannten Scopes – die Schnittstelle, die der Yuvomi-Entwickler stabil zusagt. Jeder genutzte Endpunkt steht in Yuvomis OpenAPI-Spezifikation; interne Endpunkte, Datenbank oder Dateien werden nie angefasst. Fragen zu dieser Integration bitte hier im Repository stellen, nicht beim Yuvomi-Projekt.

## Voraussetzungen

- Yuvomi **ab 2.79**, **empfohlen ab 2.84**: Ab 2.84 meldet Yuvomi seine Schnittstellen-Generation (`client_api` in `/api/v1/version`). Die Integration prüft sie beim Einrichten und bei jedem Start. Ältere Versionen laufen, zeigen aber einen Hinweis unter Reparaturen.
- Home Assistant ab 2025.1.

## Installation

**HACS (benutzerdefiniertes Repository):** HACS → ⋮ → Benutzerdefinierte Repositories → `https://github.com/Thomas-1904/ha-yuvomi` hinzufügen (Typ *Integration*) → *Yuvomi* installieren → Home Assistant neu starten.

**Manuell:** ZIP des neuesten [Releases](https://github.com/Thomas-1904/ha-yuvomi/releases) laden, `custom_components/yuvomi` nach `/config/custom_components/yuvomi` kopieren, Home Assistant neu starten.

Danach: Einstellungen → Geräte & Dienste → Integration hinzufügen → **Yuvomi**, URL (z. B. `http://192.168.1.10:3005`) und API-Token eingeben, Module auswählen.

## API-Token und Scopes

Token in Yuvomi unter *Einstellungen → API-Tokens* anlegen. Nur die Scopes für die gewünschten Module vergeben (`write` schließt `read` ein). Module ohne Berechtigung bleiben einfach leer.

| Modul der Integration | Scope | Hinweis |
|---|---|---|
| Immer (Mitglieder, Übersicht) | `family:read`, `dashboard:read` | |
| Aufgaben | `tasks:write` | `tasks:read` nur zum Anzeigen |
| Einkaufslisten | `shopping:write` | auch für den Bring-Abgleich |
| Familienkalender, Erinnerungen, Geburtstage | `calendar:write` | Yuvomi fasst alle drei unter `calendar` zusammen |
| Essensplan, Rezepte | `meals:write` | |
| Vorrat | `pantry:write` | |
| Inventar & Wartung | `inventory:write` | |
| Putzplan & Haushaltshilfe | `housekeeping:write` | |
| Müllabfuhr | `waste:read` | |
| Budget, Konten, Abos, Kredite, geteilte Ausgaben | `budget:write` | |
| Belohnungen & Taschengeld | `rewards:write` | |
| Notizen / Kontakte / Dokumente / Schichtplan / Gesundheit | `notes` / `contacts` / `documents` / `schedule` / `health` | `:read` oder `:write` |
| **Sofort-Aktualisierung (Benachrichtigungskanal)** | Token eines **Administrators ohne Scopes** | Benachrichtigungskanäle sind Admin-Sache und nicht über Scopes abgedeckt; mit eingeschränktem Token die Option aus lassen und den Kanal von Hand anlegen (siehe unten) |

## Module

Mit echten Daten getestet: Aufgaben, Einkaufslisten, Vorrat, Familienkalender, Essensplan, Rezepte, Putzplan, Geburtstage, Erinnerungen. Inventar: Schnittstellen geprüft, bisher aber nur mit leerem Inventar.

**Experimentell** (nach der OpenAPI-Beschreibung gebaut, noch nicht mit echten Daten gelaufen – Rückmeldungen willkommen): Müllabfuhr, Budget/Konten/Abos, Kredite, geteilte Ausgaben, Belohnungen, Notizen, Kontakte, Dokumente, Schichtplan, Gesundheit. In der Modulauswahl als *(experimentell)* gekennzeichnet und standardmäßig aus.

## Aktuell halten

- **Abfrage** aller aktiven Module (Standard alle 60 s, einstellbar 15–3600 s).
- **Änderungszähler:** Alle 15 s liest die Integration `/api/v1/shopping/versions` (eine sehr kleine Anfrage) und aktualisiert sofort, wenn sich eine Liste geändert hat.
- **Benachrichtigungskanal (empfohlen):** Mit der Option *Sofort-Aktualisierung* legt die Integration in Yuvomi einen Kanal vom Typ *Webhook* namens „Home Assistant“ an. Jede Yuvomi-Benachrichtigung kommt als Ereignis `yuvomi_webhook` an und löst eine sofortige Aktualisierung aus. Kommen die Webhooks zuverlässig an, kann das Abfrageintervall großzügiger sein.
  - Yuvomi lehnt Adressen im Heimnetz ab, solange beim Yuvomi-Container nicht `NOTIFICATION_ALLOW_PRIVATE_NETWORK=true` gesetzt ist. Ohne diese Einstellung weicht die Integration auf die externe Home-Assistant-Adresse aus (z. B. Nabu Casa), falls eine eingerichtet ist.
  - Hinweis: Der Kanal transportiert *Benachrichtigungen* (Erinnerungen, fällige Aufgaben …), nicht jede Datenänderung. Die regelmäßige Abfrage bleibt die Absicherung.
- **Ratenlimit:** Yuvomi erlaubt 300 Anfragen pro Minute **je IP-Adresse**. Hinter NAT oder Reverse Proxy teilt sich Home Assistant dieses Kontingent womöglich mit den Handys der Familie. Die Integration begrenzt sich deshalb selbst auf 120 Anfragen pro Minute und pausiert automatisch, wenn Yuvomi mit `429` antwortet. Ein vollständiger Abruf aller Standardmodule braucht rund ein Dutzend Anfragen.

## Bring-Abgleich (optional)

In den Optionen lässt sich eine beliebige To-do-Liste aus Home Assistant (z. B. eine Bring-Liste aus der Bring-Integration) wählen, die in beide Richtungen mit einer Yuvomi-Einkaufsliste gleich gehalten wird: neue Artikel, abgehakt/wieder offen, gelöscht und Menge (Yuvomi-Menge ↔ Bring-Spezifikation). Ein gespeicherter Stand des letzten Abgleichs entscheidet, welche Seite sich geändert hat. Einträge, die nur unter Bring „Zuletzt verwendet“ stehen, werden nicht übernommen. Dienst `yuvomi.sync_bring` gleicht sofort ab, der Diagnosesensor „Bring-Abgleich“ zeigt das letzte Ergebnis.

## Dienste und Ereignisse

- Generisch: `yuvomi.create`, `yuvomi.get`, `yuvomi.update` (führt mit dem aktuellen Stand zusammen), `yuvomi.delete` für jede Ressource der API.
- Komfortdienste: Aufgaben anlegen/erledigen, Artikel auf die Liste, Essen planen, Putzaufgabe erledigt, Abo verlängern, Kreditrate, Bonuspunkte, Erinnerung erledigen u. a. `yuvomi.refresh` erzwingt einen Abruf.
- `yuvomi.api_request` ruft beliebige `/api/v1/…`-Pfade direkt auf. **Das ist ein Notausgang:** Verbindlich ist nur, was in Yuvomis OpenAPI-Spezifikation steht – alles darüber hinaus kann mit jedem Yuvomi-Update brechen.
- Ereignis `yuvomi_changed` bei jedem neuen, geänderten oder gelöschten Objekt (`resource`, `action`, `id`, `title`, `item`, `changed`, `old_status`); Ereignis `yuvomi_webhook` für eingehende Benachrichtigungen (`data.notification` mit `title`, `body`, `url`, `tag`).

## Bekannte Grenzen

- Getestet mit einem Haushalt und einer Yuvomi-Instanz (Yuvomi 2.79, Docker). Mehrere Yuvomi-Instanzen in einem Home Assistant sollten funktionieren, sind aber nicht getestet.
- Der Webhook-Payload nennt keinen Empfänger; Benachrichtigungen an einzelne Handys zu verteilen, geht nur über eigene Automationen.

## Lizenz

MIT – siehe [LICENSE](LICENSE). Yuvomi selbst ist ein eigenständiges Projekt mit eigener Lizenz.
