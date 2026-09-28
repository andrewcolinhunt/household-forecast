"""Shared PostgreSQL scenarios with atomic revision checks."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from financial_model.migration import convert_legacy_inputs
from financial_model.schemas import AllocationPlan, Goal, SavedScenario, Scenario

MAX_BACKUP_BYTES = 1_000_000


class RevisionConflictError(Exception):
    """The scenario changed or was deleted after this session loaded it."""


def _connect(dsn: str) -> psycopg.Connection[dict[str, Any]]:
    """Use short-lived connections and verified TLS outside loopback test servers."""
    options = conninfo_to_dict(dsn)
    host = options.get("host", "")
    if host not in {"localhost", "127.0.0.1", "::1"}:
        options["sslmode"] = "verify-full"
        options.setdefault("sslrootcert", "system")
    return psycopg.connect(
        make_conninfo("", **options), connect_timeout=5, row_factory=dict_row
    )


def list_scenarios(dsn: str) -> tuple[SavedScenario, ...]:
    """Load the latest validated shared documents without a stale query cache."""
    with _connect(dsn) as connection:
        rows = connection.execute(
            "SELECT document FROM scenarios ORDER BY updated_at DESC, id"
        ).fetchall()
    return tuple(SavedScenario.model_validate(row["document"]) for row in rows)


def load_scenario(dsn: str, scenario_id: UUID) -> SavedScenario | None:
    """Read one shared scenario, returning None when it no longer exists."""
    with _connect(dsn) as connection:
        row = connection.execute(
            "SELECT document FROM scenarios WHERE id = %s", (scenario_id,)
        ).fetchone()
    return SavedScenario.model_validate(row["document"]) if row else None


def create_scenario(dsn: str, document: SavedScenario) -> SavedScenario:
    """Insert a new scenario; transaction completion precedes success reporting."""
    if document.revision != 1:
        message = "A new scenario must start at revision 1."
        raise ValueError(message)
    with _connect(dsn) as connection:
        _register_goals(connection, document)
        connection.execute(
            "INSERT INTO scenarios (id, revision, document, updated_at) VALUES (%s, %s, %s, %s)",
            (
                document.id,
                document.revision,
                Jsonb(document.model_dump(mode="json")),
                document.updated_at,
            ),
        )
        _save_goal_references(connection, document)
    return document


def update_scenario(dsn: str, document: SavedScenario) -> SavedScenario:
    """Save a replacement only if its loaded revision is still current.

    Args:
        dsn: PostgreSQL connection string; never shown in UI errors.
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
            "UPDATE scenarios SET revision = %s, document = %s, updated_at = %s "
            "WHERE id = %s AND revision = %s RETURNING id",
            (
                updated.revision,
                Jsonb(updated.model_dump(mode="json")),
                updated.updated_at,
                document.id,
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
        dsn: PostgreSQL connection string.
        scenario_id: Identity of the scenario to remove.
        revision: Revision shown when deletion was requested.

    Raises:
        RevisionConflictError: If the scenario changed or was already deleted.
    """
    with _connect(dsn) as connection:
        row = connection.execute(
            "DELETE FROM scenarios WHERE id = %s AND revision = %s RETURNING id",
            (scenario_id, revision),
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
    return tuple(Goal.model_validate(row) for row in rows)


def create_goal(dsn: str, goal: Goal) -> Goal:
    """Create a user-named purpose without assigning any assets."""
    with _connect(dsn) as connection:
        connection.execute(
            "INSERT INTO goals (id, name, revision) VALUES (%s, %s, 1)",
            (goal.id, goal.name),
        )
    return goal.model_copy(update={"revision": 1})


def update_goal(dsn: str, goal: Goal) -> Goal:
    """Rename only the reviewed goal revision, preserving all assignments."""
    with _connect(dsn) as connection:
        row = connection.execute(
            "UPDATE goals SET name = %s, revision = revision + 1 WHERE id = %s AND revision = %s RETURNING id, name, revision",
            (goal.name, goal.id, goal.revision),
        ).fetchone()
        if row is None:
            msg = "This goal changed or was deleted. Refresh and try again."
            raise RevisionConflictError(msg)
    return Goal.model_validate(row)


def delete_goal(dsn: str, goal: Goal) -> None:
    """Remove an unused goal; foreign keys protect all saved scenario references."""
    try:
        with _connect(dsn) as connection:
            row = connection.execute(
                "DELETE FROM goals WHERE id = %s AND revision = %s RETURNING id",
                (goal.id, goal.revision),
            ).fetchone()
            if row is None:
                msg = "This goal changed or was deleted. Refresh and try again."
                raise RevisionConflictError(msg)
    except psycopg.errors.ForeignKeyViolation as error:
        msg = "Reassign this goal in saved scenarios before deleting it."
        raise ValueError(msg) from error


def _register_goals(
    connection: psycopg.Connection[dict[str, Any]], document: SavedScenario
) -> None:
    """Lock goal identities before saving references; never merge by name."""
    for allocation in sorted(
        document.inputs.allocation_plan.rows, key=lambda row: str(row.goal.id)
    ):
        goal = allocation.goal
        connection.execute(
            "INSERT INTO goals (id, name, revision) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (goal.id, goal.name, goal.revision),
        )
        current = connection.execute(
            "SELECT name FROM goals WHERE id = %s FOR SHARE", (goal.id,)
        ).fetchone()
        if current is None or current["name"] != goal.name:
            msg = "A goal name differs from the shared catalogue. Review its destination and calculate again."
            raise ValueError(msg)


def _save_goal_references(
    connection: psycopg.Connection[dict[str, Any]], document: SavedScenario
) -> None:
    """Replace the scenario's goal references in the same transaction as its data."""
    connection.execute(
        "DELETE FROM scenario_goals WHERE scenario_id = %s", (document.id,)
    )
    for allocation in document.inputs.allocation_plan.rows:
        connection.execute(
            "INSERT INTO scenario_goals (scenario_id, goal_id) VALUES (%s, %s)",
            (document.id, allocation.goal.id),
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
