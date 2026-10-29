"""Input-only planning documents with atomic SQLite revision checks."""

from __future__ import annotations

import json
from datetime import UTC
from typing import Any
from uuid import UUID

from financial_model.planning import Kind, PlanningInputs, SavedPlanningDocument
from financial_model.storage_common import (
    MAX_BACKUP_BYTES,
    RevisionConflictError,
    connect,
)


def list_planning_documents(dsn: str, kind: Kind) -> tuple[SavedPlanningDocument, ...]:
    """Load Plans by name and other categories by latest update."""
    order = (
        "lower(document -> 'inputs' ->> 'name'), id"
        if kind == "plan"
        else "updated_at DESC, id"
    )
    with connect(dsn) as connection:
        rows = connection.execute(
            f"SELECT document FROM planning_documents WHERE document -> 'inputs' ->> 'kind' = ? ORDER BY {order}",
            (kind,),
        ).fetchall()
    return tuple(_read_document(json.loads(row["document"])) for row in rows)


def load_planning_document(dsn: str, document_id: UUID) -> SavedPlanningDocument | None:
    """Read a latest input document, without replacing a caller's draft."""
    with connect(dsn) as connection:
        row = connection.execute(
            "SELECT document FROM planning_documents WHERE id = ?", (str(document_id),)
        ).fetchone()
    return _read_document(json.loads(row["document"])) if row else None


def create_planning_document(
    dsn: str, document: SavedPlanningDocument
) -> SavedPlanningDocument:
    """Persist validated inputs without registering embedded source or goal records."""
    if document.revision != 1:
        msg = "A new planning document must start at revision 1."
        raise ValueError(msg)
    with connect(dsn) as connection:
        connection.execute(
            "INSERT INTO planning_documents (id, revision, document, updated_at) VALUES (?, ?, ?, ?)",
            (
                str(document.id),
                document.revision,
                document.model_dump_json(),
                document.updated_at.astimezone(UTC).isoformat(),
            ),
        )
    return document


def update_planning_document(
    dsn: str, document: SavedPlanningDocument
) -> SavedPlanningDocument:
    """Replace only the loaded revision and category; deleted records stay deleted.

    Args:
        dsn: Path of the active SQLite database.
        document: Current inputs with their reviewed identity and revision.

    Returns:
        The committed document with a new timestamp and incremented revision.

    Raises:
        RevisionConflictError: If the revision or category no longer matches.
    """
    updated = SavedPlanningDocument(
        id=document.id, revision=document.revision + 1, inputs=document.inputs
    )
    with connect(dsn) as connection:
        row = connection.execute(
            "UPDATE planning_documents SET revision = ?, document = ?, updated_at = ? WHERE id = ? AND revision = ? AND document -> 'inputs' ->> 'kind' = ? RETURNING id",
            (
                updated.revision,
                updated.model_dump_json(),
                updated.updated_at.astimezone(UTC).isoformat(),
                str(document.id),
                document.revision,
                document.inputs.kind,
            ),
        ).fetchone()
        if row is None:
            msg = "This input document changed or was deleted, or its category changed. Reload or save a new copy."
            raise RevisionConflictError(msg)
    return updated


def delete_planning_document(dsn: str, document_id: UUID, revision: int) -> None:
    """Delete only the revision reviewed by the user; embedded Plan copies survive."""
    with connect(dsn) as connection:
        row = connection.execute(
            "DELETE FROM planning_documents WHERE id = ? AND revision = ? RETURNING id",
            (str(document_id), revision),
        ).fetchone()
        if row is None:
            msg = "This input document changed or was deleted. Refresh and try again."
            raise RevisionConflictError(msg)


def import_planning_backup(data: bytes) -> PlanningInputs:
    """Validate type, version and size, discarding outer identity for a new draft."""
    if len(data) > MAX_BACKUP_BYTES:
        msg = "Planning backup exceeds the 1 MB limit."
        raise ValueError(msg)
    return _read_document(json.loads(data)).inputs


def _read_document(raw: Any) -> SavedPlanningDocument:
    """Require explicit conversion instead of silently upgrading stored assumptions."""
    if (
        not isinstance(raw, dict)
        or raw.get("document_type") != "planning_document"
        or type(raw.get("schema_version")) is not int
    ):
        message = "A planning backup must declare its document type and schema version."
        raise ValueError(message)
    current = SavedPlanningDocument.model_fields["schema_version"].default
    if raw["schema_version"] != current:
        message = (
            f"Unsupported planning schema version {raw['schema_version']}; "
            f"this app reads version {current}. The original is unchanged. "
            "This backup needs explicit conversion before it can be opened."
        )
        raise ValueError(message)
    return SavedPlanningDocument.model_validate(raw)
