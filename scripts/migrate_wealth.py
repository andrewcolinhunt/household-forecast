"""Back up, preview, and atomically simplify Wealth and its saved Plan copies."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from psycopg import Connection, sql
from psycopg.types.json import Jsonb
from rich.console import Console

from financial_model.planning import SavedPlanningDocument
from financial_model.planning_migration import (
    TIMELINE_CONVERSION_NOTICE,
    timeline_conversion_review,
    upgrade_forecast_constraint,
)
from financial_model.storage import _connect
from financial_model.wealth_migration import (
    _convert_wealth_and_planning_stage,
    convert_wealth_and_planning,
)

TABLES = {"wealth_snapshots": "2", "planning_documents": "4"}


def _read(connection: Connection) -> dict:
    """Read deterministic source documents from both affected stores."""
    return {
        table: [
            row["document"]
            for row in connection.execute(
                sql.SQL("SELECT document FROM {} ORDER BY id").format(
                    sql.Identifier(table)
                )
            ).fetchall()
        ]
        for table in TABLES
    }


def preview(dsn: str, directory: Path, settings: dict) -> None:
    """Keep a private backup and exact proposed documents before any mutation."""
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    with _connect(dsn) as connection:
        originals = _read(connection)
    for name, value in (
        ("originals", originals),
        ("settings", settings),
    ):
        path = directory / f"{name}.json"
        path.write_text(json.dumps(value, indent=2))
        path.chmod(0o600)
    proposals = convert_wealth_and_planning(
        originals["wealth_snapshots"], originals["planning_documents"], **settings
    )
    intermediate = _convert_wealth_and_planning_stage(
        originals["wealth_snapshots"],
        originals["planning_documents"],
        account_updates=settings.get("account_updates"),
    )
    review = timeline_conversion_review(
        [d for d in intermediate["planning_documents"] if d["schema_version"] == 3],
        tuple(
            SavedPlanningDocument.model_validate(d)
            for d in proposals["planning_documents"]
        ),
    )
    for name, value in (("proposals", proposals), ("annual-review", review)):
        path = directory / f"{name}.json"
        path.write_text(json.dumps(value, indent=2))
        path.chmod(0o600)
    Console().print(
        f"Prepared {sum(map(len, proposals.values()))} documents in {directory}."
    )
    Console().print(TIMELINE_CONVERSION_NOTICE)


def _without_timestamps(value: Any) -> Any:
    """Compare converted contents independently of generated save timestamps."""
    if isinstance(value, dict):
        return {
            key: _without_timestamps(item)
            for key, item in value.items()
            if key != "updated_at"
        }
    if isinstance(value, list):
        return [_without_timestamps(item) for item in value]
    return value


def apply(dsn: str, directory: Path) -> None:
    """Reject stale or altered previews and update both schemas in one transaction."""
    originals = json.loads((directory / "originals.json").read_text())
    settings = json.loads((directory / "settings.json").read_text())
    proposals = json.loads((directory / "proposals.json").read_text())
    expected = convert_wealth_and_planning(
        originals["wealth_snapshots"], originals["planning_documents"], **settings
    )
    if _without_timestamps(proposals) != _without_timestamps(expected):
        message = "Proposals differ from the reviewed conversion. Create a new preview."
        raise ValueError(message)
    from financial_model.wealth import SavedWealthSnapshot

    for document in proposals["wealth_snapshots"]:
        SavedWealthSnapshot.model_validate(document)
    for document in proposals["planning_documents"]:
        SavedPlanningDocument.model_validate(document)
    with _connect(dsn) as connection:
        connection.execute(
            "LOCK TABLE wealth_snapshots, planning_documents IN ACCESS EXCLUSIVE MODE"
        )
        if _read(connection) != originals:
            message = "Saved inputs changed since preview. Create a new preview."
            raise ValueError(message)
        for table, version in TABLES.items():
            constraints = connection.execute(
                "SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%%schema_version%%'",
                (table,),
            ).fetchall()
            for row in constraints:
                connection.execute(
                    sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}").format(
                        sql.Identifier(table), sql.Identifier(row["conname"])
                    )
                )
            for document in proposals[table]:
                connection.execute(
                    sql.SQL(
                        "INSERT INTO {} (revision, document, updated_at, id) VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET revision = EXCLUDED.revision, document = EXCLUDED.document, updated_at = EXCLUDED.updated_at"
                    ).format(sql.Identifier(table)),
                    (
                        document["revision"],
                        Jsonb(document),
                        document["updated_at"],
                        document["id"],
                    ),
                )
            connection.execute(
                sql.SQL(
                    "ALTER TABLE {} ADD CONSTRAINT {} CHECK ((document ->> 'schema_version') IS NOT DISTINCT FROM {})"
                ).format(
                    sql.Identifier(table),
                    sql.Identifier(table + "_schema_version"),
                    sql.Literal(version),
                )
            )
        upgrade_forecast_constraint(connection)
    Console().print("Wealth and Planning migration complete.")


def main() -> None:
    """Require explicit preview/apply operations with credentials in the environment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["preview", "apply"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--settings", type=Path)
    args = parser.parse_args()
    dsn = os.environ["FINANCIAL_MODEL_DATABASE_URL"]
    if args.action == "preview":
        preview(
            dsn,
            args.directory,
            json.loads(args.settings.read_text()) if args.settings else {},
        )
    else:
        apply(dsn, args.directory)


if __name__ == "__main__":
    main()
