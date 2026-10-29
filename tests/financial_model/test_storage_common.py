"""Shared SQLite connection limits, transaction rollback, and cleanup."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from financial_model import storage_common
from financial_model.storage_common import connect


def test_connect_requires_existing_database_and_closes_connection(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing.sqlite"
    with pytest.raises(sqlite3.OperationalError), connect(str(path)):
        pytest.fail("A missing database must not be created implicitly.")
    assert not path.exists()


def test_connect_enforces_foreign_keys_and_closes_connection(database_url: str) -> None:
    with connect(database_url) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA trusted_schema").fetchone()[0] == 0
        assert connection.execute("PRAGMA application_id").fetchone()[0] == 1179471692
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_connect_rejects_growth_beyond_upload_limit(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with storage_common.connect(database_url) as connection:
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    limit = Path(database_url).stat().st_size + page_size
    monkeypatch.setattr(storage_common, "MAX_DATABASE_BYTES", limit)
    with (
        pytest.raises(sqlite3.OperationalError, match="full"),
        storage_common.connect(database_url) as connection,
    ):
        connection.execute("CREATE TABLE growth_test (data BLOB)")
        connection.execute("INSERT INTO growth_test VALUES (zeroblob(?))", (limit,))
    assert Path(database_url).stat().st_size <= limit
    with storage_common.connect(database_url) as connection:
        assert connection.execute("SELECT count(*) FROM growth_test").fetchone()[0] == 0
