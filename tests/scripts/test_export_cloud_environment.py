"""Deployment pins must come from the Linux lock, without private machine paths."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from scripts.export_cloud_environment import render_environment

ROOT = Path(__file__).parents[2]


def test_render_environment_matches_locked_linux_packages() -> None:
    lock = yaml.safe_load((ROOT / "pixi.lock").read_text())
    rendered = render_environment(lock)
    assert rendered == (ROOT / "environment.yml").read_text()
    result = yaml.safe_load(rendered)
    pins = result["dependencies"][-1]["pip"]
    assert "-e ." in pins
    assert "policyengine-us==2.15.1" in pins
    assert "policyengine-core==3.32.7" in pins
    refs = lock["environments"]["default"]["packages"]["linux-64"]
    assert len(pins) + len(result["dependencies"]) - 1 == len(refs)
    assert "/Users/" not in rendered
    assert "osx" not in rendered
    assert any(pin.startswith("python=3.12.") for pin in result["dependencies"][:-1])


def test_render_environment_accepts_versionless_editable_record() -> None:
    lock = yaml.safe_load((ROOT / "pixi.lock").read_text())
    editable = next(p for p in lock["packages"] if p.get("pypi") == "./")
    assert "version" not in editable
    assert "sha256" not in editable
    assert "-e ." in yaml.safe_load(render_environment(lock))["dependencies"][-1]["pip"]


@pytest.mark.parametrize("version", [6, 8])
def test_render_environment_rejects_other_lock_versions(version: int) -> None:
    lock = yaml.safe_load((ROOT / "pixi.lock").read_text())
    lock["version"] = version
    with pytest.raises(ValueError, match="Expected Pixi lock format 7"):
        render_environment(lock)


@pytest.mark.parametrize("change", ["source", "reference"])
def test_render_environment_rejects_unsupported_lock(change: str) -> None:
    lock = deepcopy(yaml.safe_load((ROOT / "pixi.lock").read_text()))
    if change == "reference":
        lock["environments"]["default"]["packages"]["linux-64"].append(
            {"other": "package"}
        )
    else:
        next(p for p in lock["packages"] if p.get("pypi") == "./")["name"] = (
            "unknown-local"
        )
    with pytest.raises(ValueError):
        render_environment(lock)
