"""Portable SQLite scenarios with atomic revision checks."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from financial_model.database import MAX_DATABASE_BYTES
from financial_model.migration import convert_legacy_inputs
from financial_model.schemas import (
    AllocationPlan,
    Goal,
    Household,
    SavedScenario,
    Scenario,
)
from financial_model.wealth import SavedWealthSnapshot, WealthSnapshot

MAX_BACKUP_BYTES = 1_000_000


class RevisionConflictError(Exception):
    """The record changed or was deleted after this session loaded it."""


@contextmanager
def _connect(dsn: str) -> Iterator[sqlite3.Connection]:
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


def list_scenarios(dsn: str) -> tuple[SavedScenario, ...]:
    """Load annual plans in stable, case-insensitive name order."""
    with _connect(dsn) as connection:
        rows = connection.execute(
            "SELECT document FROM scenarios ORDER BY lower(document -> 'inputs' ->> 'name'), id"
        ).fetchall()
    return tuple(SavedScenario.model_validate_json(row["document"]) for row in rows)


def load_scenario(dsn: str, scenario_id: UUID) -> SavedScenario | None:
    """Read one shared scenario, returning None when it no longer exists."""
    with _connect(dsn) as connection:
        row = connection.execute(
            "SELECT document FROM scenarios WHERE id = ?", (str(scenario_id),)
        ).fetchone()
    return SavedScenario.model_validate_json(row["document"]) if row else None


def create_scenario(dsn: str, document: SavedScenario) -> SavedScenario:
    """Insert a new scenario; transaction completion precedes success reporting."""
    if document.revision != 1:
        message = "A new scenario must start at revision 1."
        raise ValueError(message)
    with _connect(dsn) as connection:
        _register_goals(connection, document)
        connection.execute(
            "INSERT INTO scenarios (id, revision, document, updated_at) VALUES (?, ?, ?, ?)",
            (
                str(document.id),
                document.revision,
                document.model_dump_json(),
                document.updated_at.astimezone(UTC).isoformat(),
            ),
        )
        _save_goal_references(connection, document)
    return document


def update_scenario(dsn: str, document: SavedScenario) -> SavedScenario:
    """Save a replacement only if its loaded revision is still current.

    Args:
        dsn: Path of the active SQLite database; never shown in UI errors.
        document: Matching inputs/result with the ID and revision previously loaded.

    Returns:
        The saved document with its incremented revision and timestamp.

    Raises:
        RevisionConflictError: When another session already changed the record.
    """
    updated = SavedScenario(
        id=document.id,
        revision=document.revision + 1,
        updated_at=datetime.now(UTC),
        inputs=document.inputs,
        result=document.result,
    )
    with _connect(dsn) as connection:
        _register_goals(connection, document)
        row = connection.execute(
            "UPDATE scenarios SET revision = ?, document = ?, updated_at = ? "
            "WHERE id = ? AND revision = ? RETURNING id",
            (
                updated.revision,
                updated.model_dump_json(),
                updated.updated_at.astimezone(UTC).isoformat(),
                str(document.id),
                document.revision,
            ),
        ).fetchone()
        if row is None:
            message = "This scenario changed or was deleted. Reload or save a new copy."
            raise RevisionConflictError(message)
        _save_goal_references(connection, updated)
    return updated


def delete_scenario(dsn: str, scenario_id: UUID, revision: int) -> None:
    """Delete only the revision the user reviewed in the confirmation dialog.

    Args:
        dsn: Path of the active SQLite database.
        scenario_id: Identity of the scenario to remove.
        revision: Revision shown when deletion was requested.

    Raises:
        RevisionConflictError: If the scenario changed or was already deleted.
    """
    with _connect(dsn) as connection:
        row = connection.execute(
            "DELETE FROM scenarios WHERE id = ? AND revision = ? RETURNING id",
            (str(scenario_id), revision),
        ).fetchone()
        if row is None:
            message = (
                "This scenario changed or was deleted. Refresh the list and try again."
            )
            raise RevisionConflictError(message)


def import_backup(
    data: bytes, *, legacy_destinations: AllocationPlan | None = None
) -> Scenario:
    """Validate a backup and return only its inputs for a fresh calculation.

    The imported result and record identity never become current or overwrite a
    database record. The caller must calculate and explicitly save a new copy.
    """
    if len(data) > MAX_BACKUP_BYTES:
        message = "Scenario backup exceeds the 1 MB limit."
        raise ValueError(message)
    if legacy_destinations is not None:
        return convert_legacy_inputs(json.loads(data), destinations=legacy_destinations)
    return SavedScenario.model_validate_json(data).inputs


def list_goals(dsn: str) -> tuple[Goal, ...]:
    """Read the household's shared goal catalogue."""
    with _connect(dsn) as connection:
        rows = connection.execute(
            "SELECT id, name, revision FROM goals ORDER BY name, id"
        ).fetchall()
    return tuple(Goal.model_validate(dict(row)) for row in rows)


def create_goal(dsn: str, goal: Goal) -> Goal:
    """Create a user-named purpose without assigning any assets."""
    with _connect(dsn) as connection:
        connection.execute(
            "INSERT INTO goals (id, name, revision) VALUES (?, ?, 1)",
            (str(goal.id), goal.name),
        )
    return goal.model_copy(update={"revision": 1})


def update_goal(dsn: str, goal: Goal) -> Goal:
    """Rename only the reviewed goal revision, preserving all assignments."""
    with _connect(dsn) as connection:
        row = connection.execute(
            "UPDATE goals SET name = ?, revision = revision + 1 WHERE id = ? AND revision = ? RETURNING id, name, revision",
            (goal.name, str(goal.id), goal.revision),
        ).fetchone()
        if row is None:
            msg = "This goal changed or was deleted. Refresh and try again."
            raise RevisionConflictError(msg)
    return Goal.model_validate(dict(row))


def delete_goal(dsn: str, goal: Goal) -> None:
    """Remove an unused goal; foreign keys protect all saved scenario references."""
    try:
        with _connect(dsn) as connection:
            row = connection.execute(
                "DELETE FROM goals WHERE id = ? AND revision = ? RETURNING id",
                (str(goal.id), goal.revision),
            ).fetchone()
            if row is None:
                msg = "This goal changed or was deleted. Refresh and try again."
                raise RevisionConflictError(msg)
    except sqlite3.IntegrityError as error:
        if error.sqlite_errorcode != sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY:
            raise
        msg = "Reassign this goal in saved scenarios before deleting it."
        raise ValueError(msg) from error


def _register_goals(connection: sqlite3.Connection, document: SavedScenario) -> None:
    """Register goal identities in the write transaction; never merge by name."""
    for allocation in sorted(
        document.inputs.allocation_plan.rows, key=lambda row: str(row.goal.id)
    ):
        goal = allocation.goal
        connection.execute(
            "INSERT INTO goals (id, name, revision) VALUES (?, ?, ?) ON CONFLICT (id) DO NOTHING",
            (str(goal.id), goal.name, goal.revision),
        )
        current = connection.execute(
            "SELECT name FROM goals WHERE id = ?", (str(goal.id),)
        ).fetchone()
        if current is None or current["name"] != goal.name:
            msg = "A goal name differs from the shared catalogue. Review its destination and calculate again."
            raise ValueError(msg)


def _save_goal_references(
    connection: sqlite3.Connection, document: SavedScenario
) -> None:
    """Replace the scenario's goal references in the same transaction as its data."""
    connection.execute(
        "DELETE FROM scenario_goals WHERE scenario_id = ?", (str(document.id),)
    )
    for allocation in document.inputs.allocation_plan.rows:
        connection.execute(
            "INSERT INTO scenario_goals (scenario_id, goal_id) VALUES (?, ?)",
            (str(document.id), str(allocation.goal.id)),
        )


def resolve_goal_conflicts(
    inputs: Scenario, replacements: dict[UUID, Goal]
) -> Scenario:
    """Apply explicitly chosen imported goal identities, then validate destinations."""
    data = inputs.model_dump(mode="json")
    plan = data["allocation_plan"]
    for row in plan["rows"]:
        replacement = replacements.get(UUID(row["goal"]["id"]))
        if replacement:
            row["goal"] = replacement.model_dump(mode="json")
    for field in ("retirement_goal_id", "option_goal_id"):
        if replacement := replacements.get(UUID(plan[field])):
            plan[field] = str(replacement.id)
    return Scenario.model_validate(data)


def load_household(dsn: str) -> Household | None:
    """Read the shared family defaults, if they have been configured."""
    with _connect(dsn) as connection:
        row = connection.execute(
            "SELECT document FROM household WHERE id = TRUE"
        ).fetchone()
    return Household.model_validate_json(row["document"]) if row else None


def save_household(dsn: str, household: Household) -> Household:
    """Save defaults with a revision check, without modifying any scenario."""
    updated = household.model_copy(update={"revision": household.revision + 1})
    with _connect(dsn) as connection:
        if household.revision == 0:
            row = connection.execute(
                "INSERT INTO household (id, revision, document) VALUES (TRUE, ?, ?) ON CONFLICT (id) DO NOTHING RETURNING id",
                (updated.revision, updated.model_dump_json()),
            ).fetchone()
        else:
            row = connection.execute(
                "UPDATE household SET revision = ?, document = ? WHERE id = TRUE AND revision = ? RETURNING id",
                (
                    updated.revision,
                    updated.model_dump_json(),
                    household.revision,
                ),
            ).fetchone()
        if row is None:
            message = (
                "Household defaults changed in another session. Reload before saving."
            )
            raise RevisionConflictError(message)
    return updated


def list_wealth_snapshots(dsn: str) -> tuple[SavedWealthSnapshot, ...]:
    """Read dated wealth documents, newest financial date first."""
    with _connect(dsn) as connection:
        rows = connection.execute(
            "SELECT document FROM wealth_snapshots "
            "ORDER BY document -> 'snapshot' ->> 'as_of' DESC, updated_at DESC, id"
        ).fetchall()
    return tuple(
        SavedWealthSnapshot.model_validate_json(row["document"]) for row in rows
    )


def load_wealth_snapshot(dsn: str, snapshot_id: UUID) -> SavedWealthSnapshot | None:
    """Read one wealth document without substituting newer data into an open draft."""
    with _connect(dsn) as connection:
        row = connection.execute(
            "SELECT document FROM wealth_snapshots WHERE id = ?", (str(snapshot_id),)
        ).fetchone()
    return SavedWealthSnapshot.model_validate_json(row["document"]) if row else None


def create_wealth_snapshot(
    dsn: str, document: SavedWealthSnapshot
) -> SavedWealthSnapshot:
    """Save a new independent inventory atomically, without evaluating a scenario."""
    if document.revision != 1:
        message = "A new wealth snapshot must start at revision 1."
        raise ValueError(message)
    with _connect(dsn) as connection:
        connection.execute(
            "INSERT INTO wealth_snapshots (id, revision, document, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (
                str(document.id),
                document.revision,
                document.model_dump_json(),
                document.updated_at.astimezone(UTC).isoformat(),
            ),
        )
    return document


def update_wealth_snapshot(
    dsn: str, document: SavedWealthSnapshot
) -> SavedWealthSnapshot:
    """Replace the reviewed revision; a new financial date requires a new record.

    Args:
        dsn: Path of the active SQLite database.
        document: Inventory with the ID and revision previously loaded.

    Returns:
        The committed inventory with incremented revision and save timestamp.

    Raises:
        RevisionConflictError: If the revision or financial date no longer matches.
    """
    updated = SavedWealthSnapshot(
        id=document.id, revision=document.revision + 1, snapshot=document.snapshot
    )
    with _connect(dsn) as connection:
        row = connection.execute(
            "UPDATE wealth_snapshots SET revision = ?, document = ?, updated_at = ? "
            "WHERE id = ? AND revision = ? "
            "AND document -> 'snapshot' ->> 'as_of' = ? RETURNING id",
            (
                updated.revision,
                updated.model_dump_json(),
                updated.updated_at.astimezone(UTC).isoformat(),
                str(document.id),
                document.revision,
                document.snapshot.as_of.isoformat(),
            ),
        ).fetchone()
        if row is None:
            message = (
                "This wealth snapshot changed or was deleted, or its financial date changed. "
                "Reload or save a new copy."
            )
            raise RevisionConflictError(message)
    return updated


def delete_wealth_snapshot(dsn: str, snapshot_id: UUID, revision: int) -> None:
    """Delete only the saved wealth revision reviewed in the confirmation dialog."""
    with _connect(dsn) as connection:
        row = connection.execute(
            "DELETE FROM wealth_snapshots WHERE id = ? AND revision = ? RETURNING id",
            (str(snapshot_id), revision),
        ).fetchone()
        if row is None:
            message = (
                "This wealth snapshot changed or was deleted. Refresh and try again."
            )
            raise RevisionConflictError(message)


def import_wealth_backup(data: bytes) -> WealthSnapshot:
    """Validate a wealth backup and discard its database identity for a new draft."""
    if len(data) > MAX_BACKUP_BYTES:
        message = "Wealth backup exceeds the 1 MB limit."
        raise ValueError(message)
    return SavedWealthSnapshot.model_validate_json(data).snapshot
