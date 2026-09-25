"""Smoke test: confirm the package imports and exposes a version."""

from __future__ import annotations

import financial_model


def test_package_has_version() -> None:
    assert hasattr(financial_model, "__version__")
    assert isinstance(financial_model.__version__, str)
