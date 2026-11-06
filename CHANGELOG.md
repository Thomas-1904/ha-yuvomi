# Changelog

All notable changes to this integration. Versions follow `MAJOR.MINOR.PATCH`.

## Unreleased

### Added
- **Yuvomi update check**: an update entity shows when a newer Yuvomi version is available (installed version from the instance, latest from the Yuvomi release tags on GitHub every 6 hours; link to all changes). No install button – Yuvomi is updated where it runs. Can be switched off in the options.

### Documentation
- README: note that the integration was developed with AI assistance.

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
