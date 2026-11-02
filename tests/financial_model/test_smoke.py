"""The package exposes its installed distribution version."""

from __future__ import annotations

from importlib.metadata import version

import financial_model


def test_package_version_matches_installed_metadata() -> None:
    assert financial_model.__version__ == version("household-forecast")
