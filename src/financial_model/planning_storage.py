"""Input-only planning documents with atomic PostgreSQL revision checks."""

from __future__ import annotations

import json
from uuid import UUID

from psycopg.types.json import Jsonb

from financial_model.planning import Kind, PlanningInputs, SavedPlanningDocument
from financial_model.storage import MAX_BACKUP_BYTES, RevisionConflictError, _connect


def list_planning_documents(dsn: str, kind: Kind) -> tuple[SavedPlanningDocument, ...]:
    """Load one category without reading unrelated financial stores."""
    with _connect(dsn) as connection:
        rows = connection.execute(
            "SELECT document FROM planning_documents WHERE document -> 'inputs' ->> 'kind' = %s ORDER BY updated_at DESC, id",
            (kind,),
        ).fetchall()
    return tuple(SavedPlanningDocument.model_validate(row["document"]) for row in rows)


def load_planning_document(dsn: str, document_id: UUID) -> SavedPlanningDocument | None:
    """Read a latest input document, without replacing a caller's draft."""
    with _connect(dsn) as connection:
        row = connection.execute(
            "SELECT document FROM planning_documents WHERE id = %s", (document_id,)
        ).fetchone()
    return SavedPlanningDocument.model_validate(row["document"]) if row else None


def create_planning_document(
    dsn: str, document: SavedPlanningDocument
) -> SavedPlanningDocument:
    """Persist validated inputs without registering embedded source or goal records."""
    if document.revision != 1:
        msg = "A new planning document must start at revision 1."
        raise ValueError(msg)
    with _connect(dsn) as connection:
        connection.execute(
            "INSERT INTO planning_documents (id, revision, document, updated_at) VALUES (%s, %s, %s, %s)",
            (
                document.id,
                document.revision,
                Jsonb(document.model_dump(mode="json")),
                document.updated_at,
            ),
        )
    return document


def update_planning_document(
    dsn: str, document: SavedPlanningDocument
) -> SavedPlanningDocument:
    """Replace only the loaded revision and category; deleted records stay deleted.

    Args:
        dsn: Server-held PostgreSQL connection string.
        document: Current inputs with their reviewed identity and revision.

    Returns:
        The committed document with a new timestamp and incremented revision.

    Raises:
        RevisionConflictError: If the revision or category no longer matches.
    """
    updated = SavedPlanningDocument(
        id=document.id, revision=document.revision + 1, inputs=document.inputs
    )
    with _connect(dsn) as connection:
        row = connection.execute(
            "UPDATE planning_documents SET revision = %s, document = %s, updated_at = %s WHERE id = %s AND revision = %s AND document -> 'inputs' ->> 'kind' = %s RETURNING id",
            (
                updated.revision,
                Jsonb(updated.model_dump(mode="json")),
                updated.updated_at,
                document.id,
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
    with _connect(dsn) as connection:
        row = connection.execute(
            "DELETE FROM planning_documents WHERE id = %s AND revision = %s RETURNING id",
            (document_id, revision),
        ).fetchone()
        if row is None:
            msg = "This input document changed or was deleted. Refresh and try again."
            raise RevisionConflictError(msg)


def import_planning_backup(data: bytes) -> PlanningInputs:
    """Validate type, version and size, discarding outer identity for a new draft."""
    if len(data) > MAX_BACKUP_BYTES:
        msg = "Planning backup exceeds the 1 MB limit."
        raise ValueError(msg)
    raw = json.loads(data)
    if (
        not isinstance(raw, dict)
        or raw.get("document_type") != "planning_document"
        or type(raw.get("schema_version")) is not int
    ):
        message = "A planning backup must declare its document type and schema version."
        raise ValueError(message)
    return SavedPlanningDocument.model_validate(raw).inputs
