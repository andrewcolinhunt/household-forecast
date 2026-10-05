"""Portable forecast validation and immutable SQLite run persistence."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

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
)
from financial_model.planning import (
    PlanningScenario,
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


@pytest.mark.parametrize("version", [1, 2, 3, 4])
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
    with pytest.raises(sqlite3.IntegrityError):
        create_forecast(database_url, replacement)
    assert list_forecasts(database_url) == (saved_forecast,)


@pytest.mark.integration
def test_forecast_storage_does_not_create_tables(database_url) -> None:
    from financial_model.storage import _connect

    with _connect(database_url) as connection:
        connection.execute("DROP TABLE forecast_runs")
    with pytest.raises(sqlite3.OperationalError):
        list_forecasts(database_url)


@pytest.mark.integration
def test_forecast_schema_reapply_preserves_current_runs(
    database_url: str, saved_forecast: SavedForecast
) -> None:
    create_forecast(database_url, saved_forecast)
    schema = (Path(__file__).parents[2] / "src/financial_model/schema.sql").read_text()
    connection = sqlite3.connect(database_url)
    try:
        connection.executescript(schema)
        connection.executescript(schema)
    finally:
        connection.close()
    assert list_forecasts(database_url) == (saved_forecast,)


@pytest.mark.integration
@pytest.mark.parametrize(
    "change",
    [
        {"id": "different"},
        {"schema_version": 4},
        {"schema_version": "5"},
        {"document_type": "planning_document"},
        {"created_at": "2000-01-01T00:00:00Z"},
        {"created_at": None},
    ],
)
def test_forecast_database_rejects_invalid_envelopes(
    database_url: str, saved_forecast: SavedForecast, change: dict
) -> None:
    from financial_model.storage import _connect

    raw = saved_forecast.model_dump(mode="json") | change
    with pytest.raises(sqlite3.IntegrityError), _connect(database_url) as connection:
        connection.execute(
            "INSERT INTO forecast_runs VALUES (?, ?, ?)",
            (
                str(saved_forecast.id),
                json.dumps(raw),
                saved_forecast.created_at.isoformat(),
            ),
        )
    assert not list_forecasts(database_url)
