"""Private, temporary SQLite copies of portable household databases."""

from __future__ import annotations

import sqlite3
import weakref
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from financial_model.forecast_models import SavedForecast
from financial_model.planning import SavedPlanningDocument
from financial_model.schemas import Goal, Household, SavedScenario
from financial_model.wealth import SavedWealthSnapshot

MAX_DATABASE_BYTES = 50 * 1024 * 1024
APPLICATION_ID = 1179471692
DATABASE_VERSION = 1


def _schema() -> str:
    return Path(__file__).with_name("schema.sql").read_text()


def _schema_objects(
    connection: sqlite3.Connection,
) -> list[tuple[str, str, str, str | None]]:
    return connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"
    ).fetchall()


def validate_database(path: str) -> None:
    """Require our schema, valid relationships, and supported financial documents.

    Uploaded schemas are compared before querying any financial records. Extra
    triggers, views, and tables are rejected rather than executed or retained.

    Args:
        path: Local SQLite file to inspect without modifying it.

    Raises:
        ValueError: If the file is incompatible or contains invalid records.
        sqlite3.Error: If SQLite cannot read the file.
    """
    if Path(path).stat().st_size > MAX_DATABASE_BYTES:
        message = "The database exceeds the 50 MB upload limit."
        raise ValueError(message)
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("PRAGMA query_only = ON")
        metadata = (
            connection.execute("PRAGMA application_id").fetchone()[0],
            connection.execute("PRAGMA user_version").fetchone()[0],
        )
        with closing(sqlite3.connect(":memory:")) as expected:
            expected.executescript(_schema())
            valid_schema = _schema_objects(connection) == _schema_objects(expected)
        if metadata != (APPLICATION_ID, DATABASE_VERSION) or not valid_schema:
            message = "This is not a supported financial-model database."
            raise ValueError(message)
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            message = "The database is damaged. Open another backup."
            raise ValueError(message)
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            message = "The database contains broken record references."
            raise ValueError(message)
        _validate_documents(connection)


def _validate_documents(connection: sqlite3.Connection) -> None:
    expected_references: set[tuple[str, str]] = set()
    for table, model in (
        ("scenarios", SavedScenario),
        ("household", Household),
        ("wealth_snapshots", SavedWealthSnapshot),
        ("planning_documents", SavedPlanningDocument),
        ("forecast_runs", SavedForecast),
    ):
        for (document,) in connection.execute(f"SELECT document FROM {table}"):
            record = model.model_validate_json(document)
            if isinstance(record, SavedScenario):
                expected_references.update(
                    (str(record.id), str(row.goal.id))
                    for row in record.inputs.allocation_plan.rows
                )
    for identity, name, revision in connection.execute(
        "SELECT id, name, revision FROM goals"
    ):
        Goal(id=identity, name=name, revision=revision)
    actual_references = set(
        connection.execute("SELECT scenario_id, goal_id FROM scenario_goals")
    )
    if actual_references != expected_references:
        message = "The database goal references do not match its saved scenarios."
        raise ValueError(message)


class SessionDatabase:
    """Own one private database directory until its browser session releases it."""

    def __init__(self, data: bytes | None = None) -> None:
        """Create an empty store or validate an uploaded copy before using it."""
        if data is not None and (
            len(data) > MAX_DATABASE_BYTES or not data.startswith(b"SQLite format 3\0")
        ):
            message = "Upload a financial-model SQLite database, up to 50 MB."
            raise ValueError(message)
        directory = TemporaryDirectory(prefix="financial-model-")
        self._cleanup = weakref.finalize(self, directory.cleanup)
        self.path = str(Path(directory.name) / "financial-model.sqlite")
        try:
            if data is None:
                with closing(sqlite3.connect(self.path)) as connection:
                    connection.executescript(_schema())
            else:
                Path(self.path).write_bytes(data)
                validate_database(self.path)
            Path(self.path).chmod(0o600)
        except (OSError, sqlite3.Error, ValueError):
            self.close()
            raise

    def export(self) -> bytes:
        """Return a consistent standalone snapshot, including committed changes."""
        uri = Path(self.path).as_uri() + "?mode=ro"
        with (
            closing(sqlite3.connect(uri, uri=True)) as source,
            closing(sqlite3.connect(":memory:")) as snapshot,
        ):
            source.backup(snapshot)
            return snapshot.serialize()

    def close(self) -> None:
        """Remove this session's temporary files, leaving uploaded originals alone."""
        self._cleanup()
