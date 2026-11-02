# Yuvomi for Home Assistant

*Unofficial community integration – not affiliated with the Yuvomi project.* · [Deutsch](README.de.md)

Connects the self-hosted family planner [Yuvomi](https://github.com/ulsklyc/yuvomi) to Home Assistant. Yuvomi stays the source of truth: everything Home Assistant creates, changes or deletes is written to Yuvomi immediately, and changes made in Yuvomi show up in Home Assistant as entity states and events.

The integration talks to Yuvomi **only through the public REST API (`/api/v1`)** with an API token and the scopes listed below – the interface the Yuvomi maintainer commits to keeping stable. Every endpoint it uses is part of Yuvomi's OpenAPI specification; internal endpoints, the database or files are never touched. Questions about this integration go to this repository, not to the Yuvomi project.

## Requirements

- Yuvomi **2.79 or newer**. **2.84+ recommended**: from 2.84 Yuvomi reports its API generation (`client_api` in `/api/v1/version`), which the integration checks during setup and on every start. Older versions work but show a repair notice.
- Home Assistant 2025.1 or newer.

## Installation

**HACS (custom repository):** HACS → ⋮ → Custom repositories → add `https://github.com/Thomas-1904/ha-yuvomi` (type *Integration*) → install *Yuvomi* → restart Home Assistant.

**Manual:** download the ZIP from the latest [release](https://github.com/Thomas-1904/ha-yuvomi/releases), copy `custom_components/yuvomi` to `/config/custom_components/yuvomi`, restart Home Assistant.

Then: Settings → Devices & services → Add integration → **Yuvomi**, enter the URL (e.g. `http://192.168.1.10:3005`) and an API token, and pick the modules you want.

## API token and scopes

Create the token in Yuvomi under *Settings → API tokens*. Give it only the scopes for the modules you enable (`write` includes `read`). Modules without permission simply stay empty.

| Integration module | Scope | Notes |
|---|---|---|
| Always (members, overview) | `family:read`, `dashboard:read` | |
| Tasks | `tasks:write` | `tasks:read` for display only |
| Shopping lists | `shopping:write` | also needed for the Bring sync |
| Family calendar, Reminders, Birthdays | `calendar:write` | Yuvomi groups these three under `calendar` |
| Meal plan, Recipes | `meals:write` | |
| Pantry | `pantry:write` | |
| Inventory & maintenance | `inventory:write` | |
| Cleaning plan & housekeeper | `housekeeping:write` | |
| Waste collection | `waste:read` | |
| Budget, accounts, subscriptions, loans, shared expenses | `budget:write` | |
| Rewards & pocket money | `rewards:write` | |
| Notes / Contacts / Documents / Shift schedule / Health | `notes` / `contacts` / `documents` / `schedule` / `health` | `:read` or `:write` |
| **Instant updates (notification channel)** | unscoped token of an **administrator** | notification channels are admin-only and not covered by scopes; with a scoped token, leave the option off and set up the channel by hand (see below) |

## Modules

Tested against real data: tasks, shopping lists, pantry, family calendar, meal plan, recipes, cleaning plan, birthdays, reminders. Inventory: endpoints verified, but only with an empty inventory so far.

**Experimental** (built from the OpenAPI specification, not yet run against real data – feedback welcome): waste collection, budget/accounts/subscriptions, loans, shared expenses, rewards, notes, contacts, documents, shift schedule, health. They are marked *(experimental)* in the module list and are off by default.

| Module | Entities |
|---|---|
| Tasks | To-do list (create, complete with "done by", due date/time, description, recurrence), sensors open/overdue/done today, per-member and statistics sensors, overdue binary sensor |
| Shopping lists | One to-do list per list (`Milk (2 l)` sets the quantity), open-items sensor |
| Family calendar | Calendar (read, create, delete incl. series), upcoming-events sensor |
| Meal plan | Calendar, "meal today" sensor (week as attribute) |
| Recipes | Sensor with all recipes |
| Pantry | To-do list (due = best-before, completing = used up), sensors items/low/expiring, expired binary sensor |
| Inventory & maintenance | Sensor per item (next service), "done" button, deadlines calendar |
| Cleaning plan & housekeeper | Due-tasks sensor (all tasks with area, interval, status as attributes), button per task, housekeeper-present binary sensor |
| Birthdays / Reminders | Next-birthday sensor; due reminders sensor + binary sensor |
| Experimental modules | see above |

## Keeping Home Assistant up to date

- **Polling** of all enabled modules (default every 60 s, configurable 15–3600 s).
- **Change counters**: every 15 s the integration reads `/api/v1/shopping/versions` (one tiny request) and refreshes immediately when a list changed.
- **Notification channel (recommended):** with the option *Instant updates* the integration creates a Yuvomi notification channel of type *webhook* named "Home Assistant". Every Yuvomi notification is forwarded to Home Assistant as the event `yuvomi_webhook` and triggers an immediate refresh. With working webhooks you can set the polling interval more generously.
  - Yuvomi refuses channel URLs in private networks unless `NOTIFICATION_ALLOW_PRIVATE_NETWORK=true` is set for the Yuvomi container. Without it the integration falls back to Home Assistant's external URL (e.g. Nabu Casa) if one is configured.
  - Note: the channel carries *notifications* (reminders, due tasks …), not every data change. Polling remains the backstop.
- **Rate limit:** Yuvomi allows 300 requests per minute **per IP address**. Behind NAT or a reverse proxy Home Assistant may share this budget with the family's phones. The integration therefore throttles itself to 120 requests per minute and pauses automatically when Yuvomi answers `429`. A full refresh of all default modules needs about a dozen requests.

## Bring sync (optional)

In the options you can pick any Home Assistant to-do list (e.g. a Bring list from the Bring integration) to keep in sync with a Yuvomi shopping list in both directions: new items, checked/unchecked, deleted items and quantity (Yuvomi quantity ↔ Bring specification). A stored snapshot of the last sync decides which side changed. Items that are only in Bring's "recently used" list are not imported. Service `yuvomi.sync_bring` runs it immediately; the diagnostic sensor "Bring sync" shows the last result.

## Services and events

- Generic: `yuvomi.create`, `yuvomi.get`, `yuvomi.update` (merges with the current record), `yuvomi.delete` for every resource in the API (tasks, list items, events, meals, recipes, pantry, inventory, waste, budget, subscriptions, loans, rewards, notes, contacts, shifts, health …).
- Convenience services: add/complete tasks, add list items, plan meals, complete cleaning tasks, renew subscriptions, loan payments, bonus points, dismiss reminders, and more. `yuvomi.refresh` forces a refresh.
- `yuvomi.api_request` calls any `/api/v1/…` path directly. **This is an escape hatch:** only what is part of Yuvomi's OpenAPI specification is a supported contract – anything beyond it may break with any Yuvomi update.
- Event `yuvomi_changed` for every created, updated or deleted object (`resource`, `action`, `id`, `title`, `item`, `changed`, `old_status`); event `yuvomi_webhook` for incoming notifications (`data.notification` with `title`, `body`, `url`, `tag`).

## Known limitations

- Tested with one household and one Yuvomi instance (Yuvomi 2.79, Docker). Several Yuvomi instances in one Home Assistant should work but are untested.
- The webhook payload does not name the recipient; routing notifications to individual phones has to be done in your own automations.

## License

MIT – see [LICENSE](LICENSE). Yuvomi itself is a separate project with its own license.
