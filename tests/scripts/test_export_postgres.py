"""One-time export preserves private records without changing the source."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from copy import deepcopy
from datetime import UTC, date, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from financial_model.forecast_models import SavedForecast
from financial_model.model import evaluate_scenario
from financial_model.planning import PlanningScenario, SavedPlanningDocument
from financial_model.schemas import Household, SavedScenario
from financial_model.wealth import SavedWealthSnapshot, WealthSnapshot

SCRIPT = Path(__file__).parents[2] / "scripts/export_postgres.py"
spec = importlib.util.spec_from_file_location("export_postgres", SCRIPT)
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class Source:
    """Expose synthetic PostgreSQL rows and record every source operation."""

    def __init__(self, records):
        self.records = records
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, statement):
        self.statements.append(statement)
        if statement.startswith("SET TRANSACTION "):
            return None
        assert statement.startswith("SELECT ")
        rows = self.records[statement.split(" FROM ")[1]]
        return SimpleNamespace(fetchall=lambda: deepcopy(rows))


@pytest.fixture
def records(scenario):
    annual = SavedScenario(
        revision=7, inputs=scenario, result=evaluate_scenario(scenario)
    )
    wealth = SavedWealthSnapshot(
        revision=3, snapshot=WealthSnapshot(as_of=date(2026, 1, 1))
    )
    plan = PlanningScenario(
        name="Saved comparison", household={"reference_year": 2026}, wealth=wealth
    )
    planning = SavedPlanningDocument(revision=4, inputs=plan)
    forecast = SavedForecast(
        numpy_version="frozen-version",
        tax_engine_version="frozen-version",
        request={"plans": [plan]},
        summaries=[
            {
                "name": plan.name,
                "money_year": 2026,
                "retirement_years": [2040, 2041],
                "points": [
                    {
                        "year": 2026,
                        "lower": 10,
                        "median": 20,
                        "upper": 30,
                        "accessible_median": 10,
                        "cash_median": 10,
                        "debt_median": 0,
                        "shortfall_probability": 0,
                        "reserve_breach_probability": 0,
                        "median_difference": None,
                        "lower_difference": None,
                        "upper_difference": None,
                    }
                ],
                "funded_fraction": 1,
                "first_shortfall_year": None,
                "mean_unfunded_total": 0,
                "mean_unfunded_years": 0,
                "paths": 10,
                "example": [{"year": 2026, "ending_assets": 20}],
            }
        ],
    )
    household = Household(
        revision=2,
        tax_year=2025,
        adults=({"name": "Adult A", "age": 40}, {"name": "Adult B", "age": 38}),
    )
    result = {
        "goals": [
            {"id": row.goal.id, "name": row.goal.name, "revision": 5}
            for row in scenario.allocation_plan.rows
        ],
        "scenario_goals": [
            {"scenario_id": annual.id, "goal_id": row.goal.id}
            for row in scenario.allocation_plan.rows
        ],
        "household": [
            {
                "id": True,
                "revision": household.revision,
                "document": household.model_dump(mode="json"),
            }
        ],
        "forecast_runs": [
            {
                "id": forecast.id,
                "document": forecast.model_dump(mode="json"),
                "created_at": forecast.created_at.astimezone(
                    timezone(timedelta(hours=5))
                ),
            }
        ],
    }
    for table, saved in (
        ("scenarios", annual),
        ("wealth_snapshots", wealth),
        ("planning_documents", planning),
    ):
        result[table] = [
            {
                "id": saved.id,
                "revision": saved.revision,
                "document": saved.model_dump(mode="json"),
                "updated_at": saved.updated_at,
            }
        ]
    return result


def test_export_preserves_all_records_from_read_only_snapshot(
    monkeypatch, records, tmp_path
):
    source = Source(records)
    before = deepcopy(records)
    monkeypatch.setattr(exporter, "_connect_postgres", lambda _dsn: source)
    destination = tmp_path / "household.sqlite"
    exporter.export_database("private-connection-string", destination)
    assert source.statements[0] == (
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    )
    assert len(source.statements) == 8
    assert records == before
    assert destination.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(destination) as connection:
        connection.row_factory = sqlite3.Row
        for table, originals in records.items():
            rows = connection.execute(f"SELECT * FROM {table}").fetchall()
            assert len(rows) == len(originals)
            for row, original in zip(rows, originals, strict=True):
                if "document" in original:
                    assert json.loads(row["document"]) == original["document"]
                if "revision" in original:
                    assert row["revision"] == original["revision"]
                if "id" in original and table != "household":
                    assert row["id"] == str(original["id"])
                for timestamp in ("updated_at", "created_at"):
                    if timestamp in original:
                        assert row[timestamp] == (
                            original[timestamp].astimezone(UTC).isoformat()
                        )
        references = connection.execute("SELECT * FROM scenario_goals").fetchall()
        assert {tuple(row) for row in references} == {
            (str(row["scenario_id"]), str(row["goal_id"]))
            for row in records["scenario_goals"]
        }


@pytest.mark.parametrize("invalid", ["schema_version", "inputs"])
def test_export_rejects_unsupported_or_invalid_documents(
    monkeypatch, records, tmp_path, invalid
):
    records["scenarios"][0]["document"][invalid] = (
        1 if invalid == "schema_version" else {}
    )
    source = Source(records)
    monkeypatch.setattr(exporter, "_connect_postgres", lambda _dsn: source)
    destination = tmp_path / "household.sqlite"
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        exporter.export_database("private-connection-string", destination)
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_export_does_not_replace_existing_file(monkeypatch, tmp_path):
    destination = tmp_path / "household.sqlite"
    destination.write_bytes(b"Original family database")
    monkeypatch.setattr(
        exporter,
        "_connect_postgres",
        lambda _dsn: pytest.fail("Existing output must be checked before connecting."),
    )
    with pytest.raises(FileExistsError):
        exporter.export_database("private-connection-string", destination)
    assert destination.read_bytes() == b"Original family database"


def test_cli_hides_connection_and_record_details(monkeypatch, tmp_path, capsys):
    def fail(_dsn):
        message = "secret credentials and private financial records"
        raise ValueError(message)

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(Error=RuntimeError))
    monkeypatch.setattr(exporter, "_connect_postgres", fail)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), str(tmp_path / "household.sqlite")])
    monkeypatch.setenv("FINANCIAL_MODEL_DATABASE_URL", "secret password")
    with pytest.raises(SystemExit, match="2"):
        exporter.main()
    error = capsys.readouterr().err
    assert "Export failed" in error
    assert "secret" not in error
    assert "financial records" not in error
