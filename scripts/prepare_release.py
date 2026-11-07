"""Prepare a release: bump the version, date the changelog, write the release notes.

Usage: python scripts/prepare_release.py 0.5.3 [notes-file]
Fails if the version is not newer than the current one or "## Unreleased" is empty.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "custom_components/yuvomi/manifest.json"
CONST = ROOT / "custom_components/yuvomi/const.py"
CHANGELOG = ROOT / "CHANGELOG.md"


def ver(v: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", v):
        sys.exit(f"Version must look like 1.2.3, got {v!r}")
    return tuple(int(x) for x in v.split("."))


def main() -> None:
    new = sys.argv[1].strip().lstrip("v")
    notes_file = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "release-notes.md"

    manifest = json.loads(MANIFEST.read_text())
    old = manifest["version"]
    if ver(new) <= ver(old):
        sys.exit(f"New version {new} must be higher than the current {old}")

    log = CHANGELOG.read_text()
    m = re.search(r"^## Unreleased[^\n]*\n(.*?)(?=^## |\Z)", log, re.M | re.S)
    if not m or not m.group(1).strip():
        sys.exit("CHANGELOG.md has no entries under '## Unreleased'")
    body = m.group(1).strip()

    today = datetime.date.today().isoformat()
    log = log[: m.start()] + f"## Unreleased\n\n## {new} – {today}\n\n{body}\n\n" + log[m.end():]
    CHANGELOG.write_text(log)

    manifest["version"] = new
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    const = CONST.read_text()
    const, n = re.subn(r'^VERSION = "[^"]*"', f'VERSION = "{new}"', const, flags=re.M)
    if n != 1:
        sys.exit("VERSION not found in const.py")
    CONST.write_text(const)

    notes = body + "\n\nFull changelog: [CHANGELOG.md](https://github.com/Thomas-1904/ha-yuvomi/blob/main/CHANGELOG.md)\n"
    notes_file.write_text(notes)
    print(f"{old} -> {new}")


if __name__ == "__main__":
    main()
