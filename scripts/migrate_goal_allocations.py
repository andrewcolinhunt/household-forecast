"""Preview and atomically apply an explicit version-one goal-allocation migration."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from psycopg import sql
from rich.console import Console

from financial_model.migration import convert_legacy_inputs, verify_legacy_totals
from financial_model.model import evaluate_scenario
from financial_model.schemas import SavedScenario, default_allocation_plan
from financial_model.storage import _connect, _register_goals, _save_goal_references


def preview(dsn: str, directory: Path) -> None:
    """Write unchanged originals and reviewed proposed documents outside the repo."""
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    with _connect(dsn) as connection:
        originals = [
            row["document"]
            for row in connection.execute(
                "SELECT document FROM scenarios ORDER BY id"
            ).fetchall()
        ]
    (directory / "originals.json").write_text(json.dumps(originals, indent=2))
    proposals = []
    for old in originals:
        inputs = convert_legacy_inputs(old, destinations=default_allocation_plan())
        result = evaluate_scenario(inputs)
        verify_legacy_totals(old["result"], result)
        proposal = SavedScenario(
            id=old["id"], revision=old["revision"] + 1, inputs=inputs, result=result
        )
        proposals.append(proposal.model_dump(mode="json"))
        Console().print(
            f"{inputs.name}: Retirement receives contributions, RSUs, option purchases, and ${inputs.allocation_plan.fixed_cash:,.2f} cash/year; Available to spend receives remaining cash. Net additions ${result.net_saved_from_income:,.2f}; taxes unchanged."
        )
    (directory / "proposals.json").write_text(json.dumps(proposals, indent=2))


def apply(dsn: str, directory: Path) -> None:
    """Apply only the exact reviewed originals, with a rollback on any conflict."""
    originals = json.loads((directory / "originals.json").read_text())
    proposals = [
        SavedScenario.model_validate(raw)
        for raw in json.loads((directory / "proposals.json").read_text())
    ]
    before = {raw["id"]: raw for raw in originals}
    if {str(p.id) for p in proposals} != set(before):
        msg = "Migration originals and proposals must contain the same scenarios."
        raise ValueError(msg)
    with _connect(dsn) as connection:
        current = connection.execute(
            "SELECT id, document FROM scenarios FOR UPDATE"
        ).fetchall()
        if {str(row["id"]): row["document"] for row in current} != before:
            msg = "Scenarios changed since preview. Create a new preview."
            raise ValueError(msg)
        for proposal in proposals:
            old = before[str(proposal.id)]
            if proposal.revision != old["revision"] + 1:
                msg = "Migration must increment each scenario revision once."
                raise ValueError(msg)
            expected = convert_legacy_inputs(
                old, destinations=default_allocation_plan()
            )
            if proposal.inputs != expected:
                message = "Proposed inputs differ from the reviewed conversion."
                raise ValueError(message)
            verify_legacy_totals(old["result"], proposal.result)
        constraints = connection.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'scenarios'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%%schema_version%%'"
        ).fetchall()
        for row in constraints:
            connection.execute(
                sql.SQL("ALTER TABLE scenarios DROP CONSTRAINT {}").format(
                    sql.Identifier(row["conname"])
                )
            )
        connection.execute((Path(__file__).parent / "schema.sql").read_text())
        from psycopg.types.json import Jsonb

        for proposal in proposals:
            _register_goals(connection, proposal)
            connection.execute(
                "UPDATE scenarios SET revision = %s, document = %s, updated_at = %s WHERE id = %s",
                (
                    proposal.revision,
                    Jsonb(proposal.model_dump(mode="json")),
                    proposal.updated_at,
                    proposal.id,
                ),
            )
            _save_goal_references(connection, proposal)
        connection.execute(
            "ALTER TABLE scenarios ADD CONSTRAINT scenarios_schema_version CHECK (document ->> 'schema_version' = '2')"
        )
    Console().print(
        f"Migrated {len(proposals)} scenarios. Original backups: {directory / 'originals.json'}"
    )


def main() -> None:
    """Require an explicit preview or apply operation and private output directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["preview", "apply"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--dsn-env", default="FINANCIAL_MODEL_DATABASE_URL")
    args = parser.parse_args()
    operation = preview if args.action == "preview" else apply
    operation(os.environ[args.dsn_env], args.directory)


if __name__ == "__main__":
    main()
