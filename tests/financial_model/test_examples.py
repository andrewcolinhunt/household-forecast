"""Exercise the public example through storage and the real forecast engine."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing
from decimal import Decimal

import pytest

from financial_model.database import SessionDatabase, validate_database
from financial_model.examples import example_database
from financial_model.forecast import calculate_forecast, forecast_issues
from financial_model.forecast_models import ForecastRequest, ForecastSettings
from financial_model.forecast_storage import create_forecast, list_forecasts
from financial_model.planning import PlanningScenario, missing_details
from financial_model.planning_storage import (
    list_planning_documents,
    update_planning_document,
)
from financial_model.storage import (
    list_scenarios,
    list_wealth_snapshots,
    load_household,
)


@pytest.fixture(scope="module")
def example_bytes() -> bytes:
    """Build once while retaining independent writable copies for each test."""
    return example_database()


@pytest.fixture
def example(example_bytes: bytes) -> Iterator[SessionDatabase]:
    """Open an isolated copy of the generated public example."""
    with closing(SessionDatabase(example_bytes)) as database:
        yield database


def test_example_database_contains_complete_comparable_inputs(
    example: SessionDatabase,
) -> None:
    validate_database(example.path)
    household = load_household(example.path)
    assert household is not None
    assert household.tax_year == 2026
    assert [adult.name for adult in household.adults] == ["Alex", "Sam"]
    wealth = list_wealth_snapshots(example.path)
    assert len(wealth) == 1
    assert sum(account.balance for account in wealth[0].snapshot.accounts) == 120000
    assert (
        wealth[0].snapshot.properties[0].mortgage_id == wealth[0].snapshot.debts[0].id
    )
    plans = tuple(
        document.inputs for document in list_planning_documents(example.path, "plan")
    )
    assert all(isinstance(plan, PlanningScenario) for plan in plans)
    assert [plan.name for plan in plans] == ["Current spending", "Lower spending"]
    request = ForecastRequest(plans=plans)
    assert forecast_issues(request) == ()
    for kind in (
        "household",
        "work",
        "spending",
        "retirement",
        "investments",
        "economy",
        "plan",
    ):
        for document in list_planning_documents(example.path, kind):
            assert missing_details(document.inputs) == ()
    for plan in plans:
        assert plan.wealth == wealth[0]
        for selection in plan.components:
            sources = list_planning_documents(example.path, selection.inputs.kind)
            source = next(item for item in sources if item.id == selection.source_id)
            assert source.inputs == selection.inputs
            assert source.revision == selection.source_revision


def test_example_database_annual_comparison_keeps_savings_difference(
    example: SessionDatabase,
) -> None:
    baseline, lower = list_scenarios(example.path)
    assert baseline.result.gross_cash == lower.result.gross_cash == 95000
    assert baseline.result.living_cost == 57000
    assert lower.result.living_cost == 54000
    assert lower.result.cash_added - baseline.result.cash_added == Decimal(3000)
    assert baseline.result.taxes.total > 0
    assert baseline.result.employee_401k == 5000
    assert baseline.result.employer_retirement == 2850


def test_example_database_edits_survive_download_without_changing_template(
    example: SessionDatabase,
    example_bytes: bytes,
) -> None:
    original = list_planning_documents(example.path, "plan")[0]
    edited = original.model_copy(
        update={"inputs": original.inputs.model_copy(update={"name": "My edited plan"})}
    )
    update_planning_document(example.path, edited)
    with closing(SessionDatabase(example.export())) as downloaded:
        restored = next(
            item
            for item in list_planning_documents(downloaded.path, "plan")
            if item.id == original.id
        )
        assert restored.inputs.name == "My edited plan"
        assert restored.revision == 2
    with closing(SessionDatabase(example_bytes)) as fresh:
        assert original in list_planning_documents(fresh.path, "plan")


def test_example_database_forecasts_through_retirement_and_saves(
    example: SessionDatabase,
) -> None:
    plans = tuple(
        document.inputs for document in list_planning_documents(example.path, "plan")
    )
    result = calculate_forecast(
        ForecastRequest(
            plans=plans,
            settings=ForecastSettings(mode="fixed", count=1),
        )
    )
    baseline, lower = (result.inspect(index, 0) for index in (0, 1))
    assert baseline[0]["year"] == 2026
    assert baseline[-1]["year"] == 2078
    assert baseline[0]["cash_pay"] == lower[0]["cash_pay"] == 95000
    assert baseline[0]["spending_planned"] - lower[0]["spending_planned"] == 3000
    assert lower[0]["net_worth"] - baseline[0]["net_worth"] == pytest.approx(3000)
    assert all(row["cash_pay"] == 0 for row in baseline if row["year"] >= 2053)
    assert any(row["social_security"] > 0 for row in baseline)
    assert baseline[0]["debt_payment"] == 18000
    assert baseline[-1]["debt"] == 0
    assert lower[-1]["net_worth"] > baseline[-1]["net_worth"]
    assert all(summary.paths == 1 for summary in result.document.summaries)
    create_forecast(example.path, result.document)
    with closing(SessionDatabase(example.export())) as downloaded:
        assert list_forecasts(downloaded.path) == (result.document,)
