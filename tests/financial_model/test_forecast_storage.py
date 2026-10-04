"""Portable forecast validation and immutable PostgreSQL run persistence."""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from financial_model.forecast_models import (
    ForecastPoint,
    ForecastRequest,
    ForecastSummary,
    SavedForecast,
)
from financial_model.forecast_storage import (
    MAX_FORECAST_BYTES,
    create_forecast,
    import_forecast_backup,
    list_forecasts,
    list_historical_forecasts,
)
from financial_model.planning import (
    EconomicAssumptions,
    PlanningScenario,
    SavedPlanningDocument,
)
from financial_model.wealth import SavedWealthSnapshot, WealthSnapshot


@pytest.fixture
def saved_forecast() -> SavedForecast:
    plan = PlanningScenario(
        name="Portable example",
        household={"reference_year": 2026},
        wealth=SavedWealthSnapshot(snapshot=WealthSnapshot(as_of=date(2026, 1, 1))),
    )
    point = ForecastPoint(
        year=2026,
        lower=90,
        median=100,
        upper=110,
        accessible_median=50,
        cash_median=10,
        debt_median=0,
        shortfall_probability=0,
        reserve_breach_probability=0.1,
        median_difference=0,
        lower_difference=0,
        upper_difference=0,
    )
    return SavedForecast(
        numpy_version="test",
        tax_engine_version="test",
        request=ForecastRequest(plans=(plan,)),
        summaries=(
            ForecastSummary(
                name=plan.name,
                money_year=2026,
                retirement_years=(2040, 2041),
                points=(point,),
                funded_fraction=1,
                first_shortfall_year=None,
                mean_unfunded_total=0,
                mean_unfunded_years=0,
                paths=10,
                example=({"year": 2026, "ending_assets": 100},),
            ),
        ),
    )


def test_forecast_backup_preserves_exact_inputs_and_identity(saved_forecast) -> None:
    imported = import_forecast_backup(saved_forecast.model_dump_json().encode())
    assert imported == saved_forecast
    assert imported.id == saved_forecast.id


@pytest.mark.parametrize(
    "change",
    [
        {"document_type": "planning_document"},
        {"schema_version": True},
        {"schema_version": 1},
        {"schema_version": 2},
        {"model_version": "unknown"},
        {"summaries": []},
    ],
)
def test_forecast_backup_rejects_wrong_envelopes(saved_forecast, change) -> None:
    raw = saved_forecast.model_dump(mode="json") | change
    with pytest.raises(ValueError):
        import_forecast_backup(json.dumps(raw).encode())


@pytest.mark.parametrize("data", [b"invalid", b"[]", b" " * (MAX_FORECAST_BYTES + 1)])
def test_forecast_backup_rejects_invalid_json_and_size(data: bytes) -> None:
    with pytest.raises(ValueError):
        import_forecast_backup(data)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_forecast_backup_explains_unsupported_version(saved_forecast, version) -> None:
    raw = saved_forecast.model_dump(mode="json") | {"schema_version": version}
    with pytest.raises(
        ValueError, match=f"Unsupported forecast schema version {version}"
    ):
        import_forecast_backup(json.dumps(raw).encode())


@pytest.mark.integration
def test_forecast_storage_round_trip_and_duplicate_cannot_replace(
    database_url, saved_forecast
) -> None:
    assert create_forecast(database_url, saved_forecast) == saved_forecast
    assert list_forecasts(database_url) == (saved_forecast,)
    replacement = saved_forecast.model_copy(update={"numpy_version": "changed"})
    with pytest.raises(psycopg.errors.UniqueViolation):
        create_forecast(database_url, replacement)
    assert list_forecasts(database_url) == (saved_forecast,)


@pytest.mark.integration
def test_forecast_storage_does_not_create_tables(database_url) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute("DROP TABLE forecast_runs")
    with pytest.raises(psycopg.errors.UndefinedTable):
        list_forecasts(database_url)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("version", "constraint"),
    [
        (1, "forecast_runs_document_check"),
        (1, "forecast_runs_document_check1"),
        (2, "forecast_runs_schema_version"),
    ],
)
def test_forecast_schema_reapply_preserves_legacy_runs(
    database_url, saved_forecast, version, constraint
):
    legacy = saved_forecast.model_dump(mode="json") | {
        "schema_version": version,
        "model_version": str(version),
    }
    current = saved_forecast.model_copy(update={"id": uuid4()})
    schema = (Path(__file__).parents[2] / "scripts/schema.sql").read_text()
    with psycopg.connect(database_url) as connection:
        check = sql.SQL(
            "(document ->> 'schema_version') IS NOT DISTINCT FROM '1'"
            if version == 1
            else "(document ->> 'schema_version' IN ('1', '2')) IS TRUE"
        )
        connection.execute(
            sql.SQL(
                "ALTER TABLE forecast_runs DROP CONSTRAINT IF EXISTS {}, "
                "ADD CONSTRAINT {} CHECK ({})"
            ).format(sql.Identifier(constraint), sql.Identifier(constraint), check)
        )
        connection.execute(
            "INSERT INTO forecast_runs (id, document, created_at) VALUES (%s, %s, %s)",
            (saved_forecast.id, Jsonb(legacy), saved_forecast.created_at),
        )
        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute(
                "INSERT INTO forecast_runs (id, document, created_at) VALUES (%s, %s, %s)",
                (
                    current.id,
                    Jsonb(current.model_dump(mode="json")),
                    current.created_at,
                ),
            )
        connection.execute(schema)
        connection.execute(schema)
        assert (
            connection.execute("SELECT document FROM forecast_runs").fetchone()[0]
            == legacy
        )
    create_forecast(database_url, current)
    assert list_forecasts(database_url) == (current,)


@pytest.mark.integration
def test_historical_forecast_is_exported_without_reinterpreting_old_inputs(
    database_url, saved_forecast
):
    raw = saved_forecast.model_dump(mode="json") | {
        "schema_version": 3,
        "model_version": "3",
    }
    raw["request"]["plans"][0]["components"] = [
        {"inputs": {"kind": "spending", "baseline": {"amount": {"central": "100"}}}}
    ]
    before = json.loads(json.dumps(raw))
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "INSERT INTO forecast_runs VALUES (%s, %s, %s)",
            (saved_forecast.id, Jsonb(raw), saved_forecast.created_at),
        )
    assert list_forecasts(database_url) == ()
    (record,) = list_historical_forecasts(database_url)
    assert record.schema_version == 3
    assert record.model_version == "3"
    assert record.names == ("Portable example",)
    assert json.loads(record.to_json()) == before
    with psycopg.connect(database_url) as connection:
        assert (
            connection.execute("SELECT document FROM forecast_runs").fetchone()[0]
            == before
        )


@pytest.mark.integration
def test_planning_migration_allows_current_forecasts_and_retains_old_runs(
    database_url, tmp_path, saved_forecast
):
    spec = importlib.util.spec_from_file_location(
        "migrate_timeline",
        Path(__file__).parents[2] / "scripts/migrate_planning_retirement.py",
    )
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    old_plan = SavedPlanningDocument(
        inputs=EconomicAssumptions(name="Historical inputs")
    ).model_dump(mode="json") | {"schema_version": 3}
    old_run = saved_forecast.model_dump(mode="json") | {
        "schema_version": 3,
        "model_version": "3",
    }
    with psycopg.connect(database_url) as connection:
        checks = connection.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'planning_documents'::regclass AND pg_get_constraintdef(oid) LIKE '%%schema_version%%'"
        ).fetchall()
        for (name,) in checks:
            connection.execute(
                sql.SQL("ALTER TABLE planning_documents DROP CONSTRAINT {}").format(
                    sql.Identifier(name)
                )
            )
        connection.execute(
            "ALTER TABLE planning_documents ADD CONSTRAINT planning_schema_version CHECK ((document ->> 'schema_version') IS NOT DISTINCT FROM '3')"
        )
        connection.execute(
            "ALTER TABLE forecast_runs DROP CONSTRAINT forecast_runs_schema_version, ADD CONSTRAINT forecast_runs_schema_version CHECK ((document ->> 'schema_version' IN ('1', '2', '3')) IS TRUE)"
        )
        connection.execute(
            "INSERT INTO planning_documents VALUES (%s, %s, %s, %s)",
            (
                old_plan["id"],
                old_plan["revision"],
                Jsonb(old_plan),
                old_plan["updated_at"],
            ),
        )
        connection.execute(
            "INSERT INTO forecast_runs VALUES (%s, %s, %s)",
            (old_run["id"], Jsonb(old_run), old_run["created_at"]),
        )
    directory = tmp_path / "migration"
    script.preview(database_url, directory, {})
    script.apply(database_url, directory)
    current = saved_forecast.model_copy(update={"id": uuid4()})
    assert create_forecast(database_url, current) == current
    assert list_forecasts(database_url) == (current,)
    assert json.loads(list_historical_forecasts(database_url)[0].to_json()) == old_run
