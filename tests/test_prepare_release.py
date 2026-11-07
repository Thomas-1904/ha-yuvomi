"""Release helper script."""

import importlib.util
import json
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _run(tmp_path, monkeypatch, version):
    for rel in ("custom_components/yuvomi/manifest.json", "custom_components/yuvomi/const.py", "CHANGELOG.md", "scripts/prepare_release.py"):
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, dst)
    spec = importlib.util.spec_from_file_location("prep", tmp_path / "scripts/prepare_release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(sys, "argv", ["x", version, str(tmp_path / "notes.md")])
    mod.main()
    return tmp_path


def test_bump(tmp_path, monkeypatch):
    current = json.loads((ROOT / "custom_components/yuvomi/manifest.json").read_text())["version"]
    major, minor, patch = map(int, current.split("."))
    new = f"{major}.{minor}.{patch + 1}"
    import re

    m = re.search(r"^## Unreleased[^\n]*\n(.*?)(?=^## |\Z)", (ROOT / "CHANGELOG.md").read_text(), re.M | re.S)
    if not m or not m.group(1).strip():
        pytest.skip("nothing unreleased (e.g. while a release is being prepared)")
    out = _run(tmp_path, monkeypatch, new)
    assert json.loads((out / "custom_components/yuvomi/manifest.json").read_text())["version"] == new
    assert f'VERSION = "{new}"' in (out / "custom_components/yuvomi/const.py").read_text()
    log = (out / "CHANGELOG.md").read_text()
    assert log.index("## Unreleased") < log.index(f"## {new} – ")
    assert (out / "notes.md").read_text().strip()


def test_refuses_same_version(tmp_path, monkeypatch):
    current = json.loads((ROOT / "custom_components/yuvomi/manifest.json").read_text())["version"]
    with pytest.raises(SystemExit):
        _run(tmp_path, monkeypatch, current)
