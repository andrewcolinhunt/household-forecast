"""Portable database snapshots, upload validation, and session ownership."""

from __future__ import annotations

import gc
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from financial_model.database import SessionDatabase, validate_database
from financial_model.model import evaluate_scenario
from financial_model.planning import SavedPlanningDocument, SpendingAssumptions
from financial_model.planning_storage import (
    create_planning_document,
    list_planning_documents,
    update_planning_document,
)
from financial_model.schemas import Goal, SavedScenario
from financial_model.storage import (
    create_goal,
    create_scenario,
    delete_goal,
    list_goals,
    list_scenarios,
    update_goal,
)


@pytest.fixture
def database():
    database = SessionDatabase()
    yield database
    database.close()


@pytest.fixture
def saved_scenario(database, scenario):
    return create_scenario(
        database.path,
        SavedScenario(inputs=scenario, result=evaluate_scenario(scenario)),
    )


def test_download_upload_edit_round_trip_and_independent_copies(database):
    original = create_planning_document(
        database.path,
        SavedPlanningDocument(inputs=SpendingAssumptions(name="First plan")),
    )
    upload = database.export()
    first, second = SessionDatabase(upload), SessionDatabase(upload)
    try:
        assert first.path != second.path != database.path
        changed = original.model_copy(
            update={"inputs": original.inputs.model_copy(update={"name": "Changed"})}
        )
        saved = update_planning_document(first.path, changed)
        assert list_planning_documents(second.path, "spending") == (original,)
        assert list_planning_documents(database.path, "spending") == (original,)
        reopened = SessionDatabase(first.export())
        try:
            assert list_planning_documents(reopened.path, "spending") == (saved,)
            assert saved.revision == original.revision + 1
        finally:
            reopened.close()
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize(
    "statement",
    [
        "PRAGMA application_id = 0",
        "PRAGMA user_version = 999",
        "CREATE TABLE extra (secret TEXT)",
        "CREATE TABLE sqliteXautoindex_hidden (secret TEXT)",
        "CREATE VIEW injected AS SELECT * FROM goals",
        "CREATE TRIGGER injected AFTER INSERT ON goals BEGIN DELETE FROM scenarios; END",
    ],
)
def test_upload_rejects_unknown_schema_before_using_records(database, statement):
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute(statement)
    with pytest.raises(ValueError, match="supported"):
        SessionDatabase(database.export())


@pytest.mark.parametrize("data", [b"", b"not a database", b"SQLite format 3\0broken"])
def test_upload_rejects_invalid_files(data):
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        SessionDatabase(data)


def test_upload_rejects_invalid_financial_documents(database):
    create_planning_document(
        database.path, SavedPlanningDocument(inputs=SpendingAssumptions(name="Valid"))
    )
    with closing(sqlite3.connect(database.path)) as connection, connection:
        connection.execute(
            "UPDATE planning_documents SET document = json_set(document, '$.inputs.name', '')"
        )
    with pytest.raises(ValueError):
        SessionDatabase(database.export())


def test_upload_rejects_broken_goal_references(database):
    with closing(sqlite3.connect(database.path)) as connection, connection:
        connection.execute("INSERT INTO scenario_goals VALUES ('missing', 'missing')")
    with pytest.raises(ValueError, match="references"):
        SessionDatabase(database.export())


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_upload_rejects_goal_references_that_disagree_with_scenarios(
    database, saved_scenario, change
):
    unused = create_goal(database.path, Goal(name="Unused"))
    with closing(sqlite3.connect(database.path)) as connection, connection:
        if change == "missing":
            connection.execute("DELETE FROM scenario_goals")
        else:
            connection.execute(
                "INSERT INTO scenario_goals VALUES (?, ?)",
                (str(saved_scenario.id), str(unused.id)),
            )
    with pytest.raises(ValueError, match="goal references"):
        SessionDatabase(database.export())


def test_upload_accepts_renamed_goals_and_protects_saved_references(
    database, saved_scenario
):
    goal = list_goals(database.path)[0]
    renamed = update_goal(database.path, goal.model_copy(update={"name": "Renamed"}))
    reopened = SessionDatabase(database.export())
    try:
        assert renamed in list_goals(reopened.path)
        assert list_scenarios(reopened.path) == (saved_scenario,)
        with pytest.raises(ValueError, match="Reassign this goal"):
            delete_goal(reopened.path, renamed)
    finally:
        reopened.close()


def test_upload_and_validation_enforce_size_limit(database, monkeypatch):
    from financial_model import database as database_module

    data = database.export()
    monkeypatch.setattr(database_module, "MAX_DATABASE_BYTES", len(data) - 1)
    with pytest.raises(ValueError, match="50 MB"):
        SessionDatabase(data)
    with pytest.raises(ValueError, match="50 MB"):
        validate_database(database.path)


def test_export_includes_committed_wal_records(database):
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        record = create_planning_document(
            database.path,
            SavedPlanningDocument(inputs=SpendingAssumptions(name="In journal")),
        )
        reopened = SessionDatabase(database.export())
        try:
            assert list_planning_documents(reopened.path, "spending") == (record,)
        finally:
            reopened.close()


def test_temporary_files_are_private_and_removed_on_close_or_collection():
    database = SessionDatabase()
    path = Path(database.path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    database.close()
    database.close()
    assert not path.parent.exists()
    database = SessionDatabase()
    path = Path(database.path)
    del database
    gc.collect()
    assert not path.parent.exists()
