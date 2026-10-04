"""Back up, preview, and atomically migrate planning inputs to the current schema."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from psycopg import sql
from psycopg.types.json import Jsonb
from rich.console import Console

from financial_model.planning_migration import (
    TIMELINE_CONVERSION_NOTICE,
    _convert_version_one,
    convert_planning_documents,
    timeline_conversion_review,
    upgrade_forecast_constraint,
)
from financial_model.storage import _connect


def preview(dsn: str, directory: Path, settings: dict) -> None:
    """Back up originals and validated proposed records in a private directory."""
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    with _connect(dsn) as connection:
        originals = [
            r["document"]
            for r in connection.execute(
                "SELECT document FROM planning_documents ORDER BY id"
            ).fetchall()
        ]
    for name, value in (
        ("originals", originals),
        ("settings", settings),
    ):
        path = directory / f"{name}.json"
        path.write_text(json.dumps(value, indent=2))
        path.chmod(0o600)
    proposals = convert_planning_documents(originals, **settings)
    old = originals
    if originals and originals[0]["schema_version"] == 1:
        old = list(
            _convert_version_one(
                originals,
                **{
                    key: value
                    for key, value in settings.items()
                    if key != "profile_contexts"
                },
            )
        )
    review = timeline_conversion_review(old, proposals)
    for name, value in (
        ("proposals", [p.model_dump(mode="json") for p in proposals]),
        ("annual-review", review),
    ):
        path = directory / f"{name}.json"
        path.write_text(json.dumps(value, indent=2))
        path.chmod(0o600)
    Console().print(
        f"Prepared {len(proposals)} documents. Originals and proposals: {directory}"
    )
    Console().print(TIMELINE_CONVERSION_NOTICE)


def apply(dsn: str, directory: Path) -> None:
    """Validate the exact preview and migrate documents and constraints together."""
    originals = json.loads((directory / "originals.json").read_text())
    settings = json.loads((directory / "settings.json").read_text())
    from financial_model.planning import SavedPlanningDocument

    proposals = [
        SavedPlanningDocument.model_validate(p)
        for p in json.loads((directory / "proposals.json").read_text())
    ]
    expected = convert_planning_documents(originals, **settings)
    if {p.id: p.inputs for p in proposals} != {p.id: p.inputs for p in expected} or {
        p.id: p.revision for p in proposals
    } != {p.id: p.revision for p in expected}:
        msg = "Proposals differ from the reviewed conversion. Create a new preview."
        raise ValueError(msg)
    with _connect(dsn) as connection:
        connection.execute("LOCK TABLE planning_documents IN ACCESS EXCLUSIVE MODE")
        current = [
            r["document"]
            for r in connection.execute(
                "SELECT document FROM planning_documents ORDER BY id"
            ).fetchall()
        ]
        if current != originals:
            msg = "Planning inputs changed since preview. Create a new preview."
            raise ValueError(msg)
        constraints = connection.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'planning_documents'::regclass AND contype = 'c' AND (pg_get_constraintdef(oid) LIKE '%%schema_version%%' OR pg_get_constraintdef(oid) LIKE '%%kind%%')"
        ).fetchall()
        for row in constraints:
            connection.execute(
                sql.SQL("ALTER TABLE planning_documents DROP CONSTRAINT {}").format(
                    sql.Identifier(row["conname"])
                )
            )
        for proposal in proposals:
            connection.execute(
                "INSERT INTO planning_documents (id, revision, document, updated_at) VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET revision = EXCLUDED.revision, document = EXCLUDED.document, updated_at = EXCLUDED.updated_at",
                (
                    proposal.id,
                    proposal.revision,
                    Jsonb(proposal.model_dump(mode="json")),
                    proposal.updated_at,
                ),
            )
        connection.execute(
            "ALTER TABLE planning_documents ADD CONSTRAINT planning_schema_version CHECK ((document ->> 'schema_version') IS NOT DISTINCT FROM '4'), ADD CONSTRAINT planning_kind CHECK ((document -> 'inputs' ->> 'kind' IN ('work', 'spending', 'retirement', 'investments', 'economy', 'plan')) IS TRUE)"
        )
        upgrade_forecast_constraint(connection)
    Console().print(f"Migrated {len(proposals)} planning documents.")


def main() -> None:
    """Require an explicit operation; credentials stay in the environment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["preview", "apply"])
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--settings",
        type=Path,
        help="JSON with reviewed profile_contexts, and optional v1 retirement_rows/money_year",
    )
    args = parser.parse_args()
    dsn = os.environ["FINANCIAL_MODEL_DATABASE_URL"]
    if args.action == "preview":
        settings = json.loads(args.settings.read_text()) if args.settings else {}
        preview(dsn, args.directory, settings)
    else:
        apply(dsn, args.directory)


if __name__ == "__main__":
    main()
