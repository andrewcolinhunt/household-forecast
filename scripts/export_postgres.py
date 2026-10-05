"""Export current PostgreSQL records to a new, validated portable SQLite file.

Run with the postgres-export Pixi environment. The source database is read-only;
existing destination files are never replaced.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID

from rich.console import Console

from financial_model.database import validate_database

TABLE_COLUMNS = {
    "goals": ("id", "name", "revision"),
    "scenarios": ("id", "revision", "document", "updated_at"),
    "scenario_goals": ("scenario_id", "goal_id"),
    "household": ("id", "revision", "document"),
    "wealth_snapshots": ("id", "revision", "document", "updated_at"),
    "planning_documents": ("id", "revision", "document", "updated_at"),
    "forecast_runs": ("id", "document", "created_at"),
}


def _connect_postgres(dsn: str) -> Any:
    """Load the optional migration driver and verify remote TLS certificates."""
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from psycopg.rows import dict_row

    options = conninfo_to_dict(dsn)
    if options.get("host", "") not in {"localhost", "127.0.0.1", "::1"}:
        options["sslmode"] = "verify-full"
        options.setdefault("sslrootcert", "system")
    return psycopg.connect(
        make_conninfo("", **options), connect_timeout=5, row_factory=dict_row
    )


def _sqlite_value(column: str, value: Any) -> Any:
    """Preserve JSON contents and encode PostgreSQL scalar types for SQLite."""
    if column == "document":
        return json.dumps(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def _copy_records(source: Any, destination: sqlite3.Connection) -> None:
    """Copy every table from one consistent, explicitly read-only snapshot."""
    source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    for table, columns in TABLE_COLUMNS.items():
        names = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        rows = source.execute(f"SELECT {names} FROM {table}").fetchall()
        destination.executemany(
            f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
            [
                tuple(_sqlite_value(column, row[column]) for column in columns)
                for row in rows
            ],
        )


def export_database(dsn: str, destination: Path) -> None:
    """Write an exclusive private copy without mutating the source database.

    Args:
        dsn: PostgreSQL connection string, never displayed or written to the file.
        destination: New SQLite file to create beside an existing parent directory.

    Raises:
        FileExistsError: If the destination already exists.
        ValueError: If records do not satisfy the current application schema.
    """
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    with TemporaryDirectory(
        prefix=".financial-model-export-", dir=destination.parent
    ) as directory:
        temporary = Path(directory) / "financial-model.sqlite"
        temporary.touch(mode=0o600)
        with closing(sqlite3.connect(temporary)) as target:
            target.executescript(
                files("financial_model").joinpath("schema.sql").read_text()
            )
            with target, _connect_postgres(dsn) as source:
                _copy_records(source, target)
        validate_database(str(temporary))
        # A same-directory hard link publishes only a complete file and cannot overwrite.
        os.link(temporary, destination)


def main() -> None:
    """Read credentials from the environment and keep private data out of errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--dsn-env", default="FINANCIAL_MODEL_DATABASE_URL")
    args = parser.parse_args()
    if not os.environ.get(args.dsn_env):
        parser.error(f"Set {args.dsn_env} to the PostgreSQL connection string.")
    try:
        import psycopg
    except ImportError:
        parser.exit(
            2, "Run this script with pixi run --locked -e postgres-export python.\n"
        )
    try:
        export_database(os.environ[args.dsn_env], args.destination)
    except (OSError, ValueError, sqlite3.Error, psycopg.Error):
        parser.exit(
            2,
            "Export failed. Check the connection, current document schemas, and a new "
            "output path. The source database was not changed.\n",
        )
    Console().print(f"Exported a validated database to {args.destination}.")


if __name__ == "__main__":
    main()
