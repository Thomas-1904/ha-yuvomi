# Changelog

All notable changes to this integration. Versions follow `MAJOR.MINOR.PATCH`.

## Unreleased (planned 0.5.2)

### Changed
- New installations no longer create the Yuvomi notification channel automatically: the option *Instant updates* now starts **off**. Creating the channel needs an administrator token without scopes, so the recommended way is a scoped token plus a channel created once by hand (see README). Existing installations keep their current setting.

### Documentation
- README: manual webhook channel marked as recommended, automatic setup described as a convenient alternative with a note on the admin token's full access.
- README: note on time zones under *Known limitations*.
- README: centered header with badges, highlights, maintenance note.

## 0.5.1 – first public version

- First public release.
