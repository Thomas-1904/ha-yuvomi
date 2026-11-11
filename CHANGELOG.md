# Changelog

All notable changes to this integration. Versions follow `MAJOR.MINOR.PATCH`.

## Unreleased

### Fixed
- After updating Yuvomi, the update entity and the device's software version kept showing the old Yuvomi version until Home Assistant was restarted. The version is now read again after Yuvomi was unreachable and whenever it changes.

## 0.5.6 – 2026-11-11

### Fixed
- Changing a task from Home Assistant (e.g. moving its due date) failed with newer Yuvomi versions (400, `rotation_pool: null`). Updates no longer send back empty fields or read-only rotation/sync fields.

### Security
- **Removed the diagnostic sensor "Webhook".** It put the webhook path into an entity state, visible to every Home Assistant user and stored in the recorder history – but the webhook id is the only thing protecting that endpoint. The URL is now shown only to administrators in the integration's options dialog. Existing installations: the entity is removed automatically. If the sensor was enabled, purge its history (Developer tools → Actions → `recorder.purge_entities`) and create a new webhook URL (option *New webhook URL*), then update the channel in Yuvomi.

### Added
- Option **New webhook URL**: generates a new secret webhook id (the old URL stops working).

### Documentation
- README: installation via the Home Assistant Marketplace (2026.11+); search first, custom repository as fallback.

## 0.5.5 – 2026-11-07

### Added
- **Leaderboard and points per member**, based on Yuvomi: points from Yuvomi's reward ledger when the Rewards module is used, otherwise the points of completed tasks (credited to whoever Yuvomi records as having done them). Sensor *Leaderboard* (this week's leader; week and month ranking as attributes) and *Points this week <name>* per member.
- **Shopping lists sorted like Yuvomi**: items grouped by category in Yuvomi's category order. New items from Home Assistant, Bring and `add_shopping_item` get a category automatically – from the household's own history in Yuvomi, otherwise from a built-in list of common articles (German/English). New service `yuvomi.sort_shopping` sorts existing uncategorized items.
- **Task list per member**: one extra to-do list per family member with only their tasks; new items are assigned to that member.
- **Edit family events** from Home Assistant (title, time, description, location). Recurring events are still edited in Yuvomi.
- **Repair notice for missing permissions**: enabled modules that get no data are listed under Settings → Repairs with the scopes the token needs.
- **Button "Ingredients of the week to shopping list"** (next 7 days; to the Bring-synced list if configured).

### Documentation
- README: voice assistant (Assist) section.

## 0.5.4 – 2026-11-07

### Maintenance
- HACS validation now runs without any ignored checks (brand images ship inside the integration).

## 0.5.3 – 2026-11-07

### Added
- **Create meals and birthdays from the calendars**: new entry in the meal-plan calendar = meal (the start time picks breakfast/lunch/snack/dinner, description = note); new entry in the birthdays calendar = birthday ("Anna (1990)" sets the year of birth). Both can also be deleted there.
- **Action buttons in the notification blueprint**: task notifications get "Done" (completes the task, recorded as done by the person who tapped) and "Tomorrow" (moves it to tomorrow). Re-import the blueprint to get them.
- GitHub issue forms for bug reports (asking for versions and the diagnostics file) and feature requests.
- **Yuvomi update check**: an update entity shows when a newer Yuvomi version is available (installed version from the instance, latest from the Yuvomi release tags on GitHub every 6 hours; link to all changes). No install button – Yuvomi is updated where it runs. Can be switched off in the options.

### Documentation
- README: note that the integration was developed with AI assistance.

### Maintenance
- Automated release workflow and Dependabot updates for the GitHub Actions.

## 0.5.2 – 2026-11-02

### Added
- **Cleaning plan as a to-do list**: due chores are open items, checking one marks it done in Yuvomi; create, rename and delete chores (description `area · 7` sets area and interval).
- **Event entity "Notification"**: one event per Yuvomi notification (title, body, url, tag, priority, task id) – usable in the automation editor without YAML.
- **Diagnostics download** without family data (counts and field names only; token, URL and webhook id redacted).
- **Reconfigure**: change URL and/or token without removing the integration.
- **Blueprints**: notifications to the assigned person; appliance finished → task.
- **Tests** (pytest) for version check, Bring sync, config flow and more, run on every push.

### Changed
- New installations no longer create the Yuvomi notification channel automatically: the option *Instant updates* now starts **off**. Creating the channel needs an administrator token without scopes, so the recommended way is a scoped token plus a channel created once by hand (see README). Existing installations keep their current setting.
- English log and error messages; error messages shown in the UI are translated (English/German).
- Module names in the setup are translated; experimental modules are marked in both languages.
- Units and names built at runtime (per-member sensors, buttons, calendar entries) follow the Home Assistant language (German or English).
- Device model "Family planner".

### Documentation
- README: manual webhook channel marked as recommended, automatic setup described as a convenient alternative with a note on the admin token's full access.
- README: note on time zones under *Known limitations*.
- README: centered header with badges, highlights, maintenance note.

## 0.5.1 – 2026-11-02 – first public version

- First public release.
