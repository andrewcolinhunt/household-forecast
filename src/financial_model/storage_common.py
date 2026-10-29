"""Shared SQLite transactions, revision conflicts, and backup limits."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from financial_model.database import MAX_DATABASE_BYTES

MAX_BACKUP_BYTES = 1_000_000


class RevisionConflictError(Exception):
    """The record changed or was deleted after this session loaded it."""


@contextmanager
def connect(dsn: str) -> Iterator[sqlite3.Connection]:
    """Open an existing database and commit or roll back before closing it."""
    connection = sqlite3.connect(Path(dsn).resolve().as_uri() + "?mode=rw", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        connection.execute(f"PRAGMA max_page_count = {MAX_DATABASE_BYTES // page_size}")
        with connection:
            yield connection
    finally:
        connection.close()
