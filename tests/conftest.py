"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
import rootutils
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

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
def database_url() -> Iterator[str]:
    """Create a per-test schema only in an explicitly named local test database."""
    base = os.environ.get("FINANCIAL_MODEL_TEST_DATABASE_URL")
    if not base:
        pytest.skip(
            "Set FINANCIAL_MODEL_TEST_DATABASE_URL for PostgreSQL integration tests."
        )
    info = conninfo_to_dict(base)
    assert info.get("dbname", "").endswith("_test"), "Use a dedicated *_test database."
    assert info.get("host") in {"127.0.0.1", "localhost", "::1"}, (
        "Tests require a local database."
    )
    schema = "test_" + uuid4().hex
    with psycopg.connect(base, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    dsn = make_conninfo(base, options=f"-csearch_path={schema}")
    try:
        with psycopg.connect(dsn) as connection:
            connection.execute(
                (Path(__file__).parents[1] / "scripts/schema.sql").read_text()
            )
        yield dsn
    finally:
        with psycopg.connect(base, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )
