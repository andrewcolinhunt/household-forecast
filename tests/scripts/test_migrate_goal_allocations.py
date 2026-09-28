"""Reviewed conversion preserves financial inputs and commits atomically."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from financial_model.migration import convert_legacy_inputs, verify_legacy_totals
from financial_model.model import evaluate_scenario
from financial_model.schemas import SavedScenario, default_allocation_plan
from financial_model.storage import import_backup, list_goals, list_scenarios

SCRIPT = Path(__file__).parents[2] / "scripts/migrate_goal_allocations.py"
spec = importlib.util.spec_from_file_location("goal_migration", SCRIPT)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.fixture
def legacy(scenario):
    result = evaluate_scenario(scenario)
    raw = SavedScenario(inputs=scenario, result=result).model_dump(mode="json")
    raw["schema_version"] = 1
    del raw["inputs"]["allocation_plan"]
    for person in raw["inputs"]["adults"]:
        person["savings_rate"] = "0.10"
    del raw["result"]["allocations"]
    del raw["result"]["cash_added"]
    raw["result"].update(
        extra_savings="20000",
        annual_remaining_cash=str(result.cash_added - 20000),
        monthly_remaining_cash=str((result.cash_added - 20000) / 12),
        total_savings_and_assets=str(result.net_saved_from_income),
    )
    return raw


def test_legacy_conversion_requires_destinations_and_keeps_cash_target(legacy):
    inputs = convert_legacy_inputs(legacy, destinations=default_allocation_plan())
    assert inputs.allocation_plan.fixed_cash == 20000
    assert all("savings_rate" not in adult for adult in inputs.model_dump()["adults"])
    result = evaluate_scenario(inputs)
    verify_legacy_totals(legacy["result"], result)
    assert (
        import_backup(
            json.dumps(legacy).encode(), legacy_destinations=default_allocation_plan()
        )
        == inputs
    )
    with pytest.raises(ValueError, match="schema_version"):
        import_backup(json.dumps(legacy).encode())
    changed = dict(legacy["result"], gross_cash="1")
    with pytest.raises(ValueError, match="gross_cash"):
        verify_legacy_totals(changed, result)


def insert_legacy(dsn, legacy):
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "ALTER TABLE scenarios DROP CONSTRAINT scenarios_schema_version"
        )
        connection.execute(
            "ALTER TABLE scenarios ADD CONSTRAINT scenarios_schema_version CHECK (document ->> 'schema_version' = '1')"
        )
        connection.execute(
            "INSERT INTO scenarios (id, revision, updated_at, document) VALUES (%s, %s, %s, %s)",
            (legacy["id"], legacy["revision"], legacy["updated_at"], Jsonb(legacy)),
        )


@pytest.mark.integration
def test_migration_preview_and_apply_preserve_identity(database_url, legacy, tmp_path):
    insert_legacy(database_url, legacy)
    folder = tmp_path / "review"
    migration.preview(database_url, folder)
    assert json.loads((folder / "originals.json").read_text()) == [legacy]
    migration.apply(database_url, folder)
    (saved,) = list_scenarios(database_url)
    assert str(saved.id) == legacy["id"]
    assert saved.revision == legacy["revision"] + 1
    assert saved.inputs.allocation_plan.fixed_cash == 20000
    verify_legacy_totals(legacy["result"], saved.result)
    assert len(list_goals(database_url)) == 2
    with pytest.raises(ValueError, match="changed since preview"):
        migration.apply(database_url, folder)


@pytest.mark.integration
def test_migration_rejects_changed_inputs_without_writing(
    database_url, legacy, tmp_path
):
    insert_legacy(database_url, legacy)
    folder = tmp_path / "review"
    migration.preview(database_url, folder)
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "UPDATE scenarios SET updated_at = now(), document = jsonb_set(document, '{inputs,name}', '\"Changed elsewhere\"')"
        )
    with pytest.raises(ValueError, match="changed since preview"):
        migration.apply(database_url, folder)
    assert list_goals(database_url) == ()
    with psycopg.connect(database_url) as connection:
        assert (
            connection.execute(
                "SELECT document ->> 'schema_version' FROM scenarios"
            ).fetchone()[0]
            == "1"
        )
