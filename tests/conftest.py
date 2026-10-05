"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import rootutils

from financial_model.schemas import PersonInputs, Scenario

# Set up project root once for all tests.
rootutils.setup_root(
    search_from=__file__,
    indicator=[".project-root", "pyproject.toml"],
    pythonpath=True,
    dotenv=True,
    cwd=False,
)


@pytest.fixture
def scenario() -> Scenario:
    """A synthetic joint household, unrelated to the family's private workbook."""
    return Scenario(
        name="Synthetic baseline",
        tax_year=2025,
        state="CA",
        adults=(
            PersonInputs(name="Adult A", age=40, cash_pay=100000),
            PersonInputs(name="Adult B", age=38, cash_pay=100000),
        ),
        assumptions_confirmed=True,
    )


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    """Create a private SQLite file with the canonical application schema."""
    path = tmp_path / "financial-model.sqlite"
    schema = Path(__file__).parents[1] / "src/financial_model/schema.sql"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(schema.read_text())
    finally:
        connection.close()
    return str(path)
