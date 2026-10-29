"""Dated Wealth snapshots with atomic SQLite revision checks."""

from __future__ import annotations

from datetime import UTC
from uuid import UUID

from financial_model.storage_common import (
    MAX_BACKUP_BYTES,
    RevisionConflictError,
    connect,
)
from financial_model.wealth import SavedWealthSnapshot, WealthSnapshot


def list_wealth_snapshots(dsn: str) -> tuple[SavedWealthSnapshot, ...]:
    """Read dated wealth documents, newest financial date first."""
    with connect(dsn) as connection:
        rows = connection.execute(
            "SELECT document FROM wealth_snapshots "
            "ORDER BY document -> 'snapshot' ->> 'as_of' DESC, updated_at DESC, id"
        ).fetchall()
    return tuple(
        SavedWealthSnapshot.model_validate_json(row["document"]) for row in rows
    )


def load_wealth_snapshot(dsn: str, snapshot_id: UUID) -> SavedWealthSnapshot | None:
    """Read one wealth document without substituting newer data into an open draft."""
    with connect(dsn) as connection:
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
    with connect(dsn) as connection:
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
    with connect(dsn) as connection:
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
    with connect(dsn) as connection:
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
