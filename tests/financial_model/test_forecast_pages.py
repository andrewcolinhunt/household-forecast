"""Forecast setup, retained edits, and explicit run/save actions in the app."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from financial_model import app, forecast_pages
from financial_model.forecast_models import (
    ForecastPoint,
    ForecastSummary,
    SavedForecast,
)
from financial_model.planning import (
    EconomicAssumptions,
    InvestmentAssumptions,
    PlanningScenario,
    RetirementAssumptions,
    SavedPlanningDocument,
    SpendingAssumptions,
    WorkAssumptions,
    select_profile,
)

ENTRY = Path(__file__).parents[2] / "streamlit_app.py"
PAGE = "src/financial_model/pages/forecast.py"


@pytest.fixture
def plan_record() -> SavedPlanningDocument:
    profiles = [
        model(name=model.__name__)
        for model in (
            WorkAssumptions,
            SpendingAssumptions,
            InvestmentAssumptions,
            EconomicAssumptions,
            RetirementAssumptions,
        )
    ]
    return SavedPlanningDocument(
        inputs=PlanningScenario(
            name="Compare choices",
            household={"reference_year": 2026},
            wealth={
                "snapshot": {
                    "as_of": "2026-01-01",
                    "accounts": [
                        {
                            "label": "Taxable",
                            "balance": 1000,
                            "tax_treatment": "taxable",
                            "invested_in": "stocks",
                        },
                        {
                            "label": "IRA",
                            "balance": 2000,
                            "tax_treatment": "tax_deferred",
                            "invested_in": "bonds",
                        },
                    ],
                    "debts": [{"label": "Loan", "amount_owed": 100}],
                }
            },
            components=tuple(
                select_profile(SavedPlanningDocument(inputs=profile))
                for profile in profiles
            ),
        )
    )


@pytest.fixture
def calculation_calls(monkeypatch):
    """Isolate UI side effects; ledger accuracy is covered by engine tests."""
    calls = []
    module = ModuleType("financial_model.forecast")

    def calculate(request, progress=None):
        calls.append(request)
        if progress is not None:
            progress("Synthetic path", 1.0)
        point = ForecastPoint(
            year=2026,
            lower=90,
            median=100,
            upper=110,
            accessible_median=50,
            cash_median=10,
            debt_median=0,
            shortfall_probability=0,
            reserve_breach_probability=0,
            median_difference=0,
            lower_difference=0,
            upper_difference=0,
        )
        document = SavedForecast(
            numpy_version="test",
            tax_engine_version="test",
            request=request,
            summaries=tuple(
                ForecastSummary(
                    name=plan.name,
                    money_year=2026,
                    retirement_years=(2040, 2041),
                    points=(point,),
                    funded_fraction=1,
                    first_shortfall_year=None,
                    mean_unfunded_total=0,
                    mean_unfunded_years=0,
                    paths=2,
                    example=({"year": 2026, "ending_assets": 100},),
                )
                for plan in request.plans
            ),
        )
        return SimpleNamespace(
            document=document,
            inspect=lambda scenario, path: [
                {"year": 2026, "path": path, "scenario": scenario}
            ],
        )

    module.calculate_forecast = calculate
    module.forecast_issues = lambda request: ()
    monkeypatch.setitem(sys.modules, "financial_model.forecast", module)
    monkeypatch.setattr(app, "_database_url", lambda: None)
    return calls


def open_page(plan_record) -> AppTest:
    page = AppTest.from_file(str(ENTRY))
    page.session_state["planning_records"] = {"plan": (plan_record,)}
    page.run().switch_page(PAGE).run()
    assert not page.exception
    return page


def field(page, kind: str, label: str):
    return next(widget for widget in getattr(page, kind) if widget.label == label)


def test_forecast_expands_only_named_combinations(plan_record) -> None:
    alternative = select_profile(
        SavedPlanningDocument(inputs=WorkAssumptions(name="Retire early"))
    )
    plan = plan_record.inputs.model_copy(
        update={"components": (*plan_record.inputs.components, alternative)}
    )
    options = forecast_pages.plan_options(
        (plan_record.model_copy(update={"inputs": plan}),)
    )
    assert len(options) == 2
    assert any("Retire early" in option.name for option in options.values())
    assert all(len(option.components) == 5 for option in options.values())
    assert all(option.wealth == plan.wealth for option in options.values())


def test_forecast_does_not_calculate_until_requested(
    plan_record, calculation_calls
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    assert not page.exception
    assert not field(page, "button", "Run forecast").disabled
    assert [widget.label for widget in page.text_input] == ["Forecast name"]
    assert not page.checkbox
    assert not calculation_calls
    field(page, "button", "Run forecast").click().run()
    assert not page.exception
    assert len(calculation_calls) == 1
    assert calculation_calls[0].plans == tuple(
        forecast_pages.plan_options((plan_record,)).values()
    )
    assert "details" not in calculation_calls[0].model_dump()
    assert len(page.get("vega_lite_chart")) == 1
    assert field(page, "button", "Save forecast").disabled
    assert page.get("download_button")
    field(page, "number_input", "Future number").set_value(2).run()
    assert page.dataframe[-1].value.iloc[0]["path"] == 1
    assert len(calculation_calls) == 1


def test_forecast_invalid_edits_and_selection_survive_navigation(
    plan_record, calculation_calls
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    field(page, "text_input", "Forecast name").set_value("")
    page.switch_page("src/financial_model/pages/compare.py").run()
    page.switch_page(PAGE).run()
    assert not page.exception
    assert page.multiselect[0].value == [f"{plan_record.id}:0"]
    assert field(page, "text_input", "Forecast name").value == ""
    assert page.error
    assert field(page, "button", "Run forecast").disabled
    assert not calculation_calls


def test_forecast_history_requires_explicit_provenance(
    plan_record, calculation_calls
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    page.button_group(key="mode_0").set_value("historical").run()
    assert not page.exception
    assert any("history source" in message.value for message in page.error)
    assert field(page, "button", "Run forecast").disabled
    assert not calculation_calls


def test_forecast_saved_run_reuses_frozen_plans_without_library(
    plan_record, calculation_calls
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    field(page, "button", "Run forecast").click().run()
    original = page.session_state["forecast_document"]
    page.session_state["planning_records"] = {}
    page.run()
    field(page, "button", "Rerun these inputs").click().run()
    assert not page.exception
    assert len(calculation_calls) == 2
    assert calculation_calls[1] == original.request
    assert page.session_state["forecast_document"].id != original.id


def test_forecast_failed_rerun_preserves_previous_result(
    monkeypatch, plan_record, calculation_calls
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    field(page, "button", "Run forecast").click().run()
    original = page.session_state["forecast_document"]

    def failed(request, progress=None):
        message = "Synthetic accounting mismatch"
        raise RuntimeError(message)

    monkeypatch.setattr(
        sys.modules["financial_model.forecast"], "calculate_forecast", failed
    )
    field(page, "button", "Rerun these inputs").click().run()
    assert not page.exception
    assert any("Synthetic accounting mismatch" in item.value for item in page.error)
    assert page.session_state["forecast_document"] == original
    assert page.get("download_button")


@pytest.mark.parametrize(
    "constraint",
    [
        "forecast_runs_document_check",
        "forecast_runs_document_check1",
        "forecast_runs_schema_version",
        "forecast_runs_document_id",
    ],
)
def test_forecast_save_failure_preserves_run_and_can_retry(
    monkeypatch, plan_record, calculation_calls, constraint
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    field(page, "button", "Run forecast").click().run()
    original = page.session_state["forecast_document"]
    records = []
    monkeypatch.setattr(app, "_database_url", lambda: "test")
    monkeypatch.setattr(forecast_pages, "list_forecasts", lambda dsn: tuple(records))
    monkeypatch.setattr(
        forecast_pages, "list_planning_documents", lambda dsn, kind: (plan_record,)
    )

    def failed(dsn, document):
        message = "Private database detail"
        raise psycopg.errors.CheckViolation(
            message, info={ord("n"): constraint.encode()}
        )

    monkeypatch.setattr(forecast_pages, "create_forecast", failed)
    page.run()
    field(page, "button", "Save forecast").click().run()
    assert not page.exception
    assert page.session_state["forecast_document"] == original
    assert not page.session_state["forecast_saved_ids"]
    assert not field(page, "button", "Save forecast").disabled
    assert page.get("download_button")
    assert "Private database detail" not in page.error[0].value
    if constraint == "forecast_runs_document_id":
        assert "could not be saved" in page.error[0].value
    else:
        assert "database needs an update" in page.error[0].value

    monkeypatch.setattr(
        forecast_pages,
        "create_forecast",
        lambda dsn, document: records.append(document),
    )
    field(page, "button", "Save forecast").click().run()
    assert not page.exception
    assert records == [original]
    assert str(original.id) in page.session_state["forecast_saved_ids"]
    assert any("Forecast saved" in item.value for item in page.success)
    page.run()
    assert field(page, "button", "Save forecast").disabled
    field(page, "button", "Open saved run").click().run()
    assert not page.exception
    assert page.session_state["forecast_document"] == original
    assert len(calculation_calls) == 1


def test_forecast_duplicate_names_have_distinct_plot_series(
    monkeypatch, plan_record, calculation_calls
) -> None:
    specs = []
    monkeypatch.setattr(
        forecast_pages.st, "vega_lite_chart", lambda spec, **kwargs: specs.append(spec)
    )
    page = open_page(plan_record)
    duplicate = plan_record.model_copy(update={"id": uuid4()})
    page.session_state["planning_records"] = {"plan": (plan_record, duplicate)}
    page.run()
    page.multiselect[0].select(f"{plan_record.id}:0").select(f"{duplicate.id}:0").run()
    field(page, "button", "Run forecast").click().run()
    assert not page.exception
    assert len(specs) == 1
    for spec in specs:
        rows = spec["layer"][0]["data"]["values"]
        assert {row["scenario"] for row in rows} == {
            "1. Compare choices",
            "2. Compare choices",
        }
    inspector = next(w for w in page.button_group if w.label == "Plan to inspect")
    inspector.set_value(1).run()
    assert page.dataframe[-1].value.iloc[0]["scenario"] == 1
    page.button_group(key=inspector.key).set_value(0).run()
    assert page.dataframe[-1].value.iloc[0]["scenario"] == 0


def test_forecast_missing_inputs_link_to_scenarios(
    monkeypatch, plan_record, calculation_calls
) -> None:
    monkeypatch.setattr(
        sys.modules["financial_model.forecast"],
        "forecast_issues",
        lambda request: ("Compare choices: Wealth: taxable cost basis is missing.",),
    )
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    assert not page.exception
    assert field(page, "button", "Run forecast").disabled
    assert any("taxable cost basis" in item.value for item in page.markdown)
    assert not any("inputs need attention" in item.label for item in page.expander)
    field(page, "button", "Open Wealth").click().run()
    assert not page.exception
    assert page.session_state["scenario_category"] == "Wealth"
    page.switch_page(PAGE).run()
    assert page.multiselect[0].value == [f"{plan_record.id}:0"]
    assert not calculation_calls


def test_forecast_selection_survives_immediate_navigation(
    plan_record, calculation_calls
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0")
    page.switch_page("src/financial_model/pages/compare.py").run()
    page.switch_page(PAGE).run()
    assert not page.exception
    assert page.multiselect[0].value == [f"{plan_record.id}:0"]


def test_forecast_method_survives_immediate_navigation(
    plan_record, calculation_calls
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    page.button_group(key="mode_0").set_value("fixed")
    page.switch_page("src/financial_model/pages/compare.py").run()
    page.switch_page(PAGE).run()
    assert not page.exception
    assert page.button_group(key="mode_0").value == "fixed"
    assert not any(widget.label == "Simulated futures" for widget in page.number_input)
    assert not calculation_calls
    field(page, "button", "Run forecast").click().run()
    assert calculation_calls[0].settings.mode == "fixed"


def test_forecast_invalid_edit_identifies_previous_results(
    plan_record, calculation_calls
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    field(page, "button", "Run forecast").click().run()
    original = page.session_state["forecast_document"]
    assert not any("previous run" in message.value for message in page.info)
    field(page, "text_input", "Forecast name").set_value("").run()
    assert not page.exception
    assert field(page, "button", "Run forecast").disabled
    assert any("previous run" in message.value for message in page.info)
    assert page.session_state["forecast_document"] == original
    assert not any(widget.label == "Scenario" for widget in page.selectbox)
    assert len(calculation_calls) == 1


@pytest.mark.parametrize("funded", [True, False])
def test_forecast_single_future_shows_outcome_without_probability(
    plan_record, calculation_calls, funded
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    page.button_group(key="mode_0").set_value("fixed").run()
    field(page, "button", "Run forecast").click().run()
    document = page.session_state["forecast_document"]
    summary = document.summaries[0].model_copy(
        update={
            "paths": 1,
            "funded_fraction": int(funded),
            "first_shortfall_year": None if funded else 2026,
            "mean_unfunded_total": 0 if funded else 800,
            "mean_unfunded_years": 0 if funded else 1,
        }
    )
    page.session_state["forecast_document"] = document.model_copy(
        update={"summaries": (summary,)}
    )
    page.run()
    assert not page.exception
    outcomes = page.dataframe[0].value
    assert outcomes.iloc[0]["Expenses funded through lifetime"] == (
        "Yes" if funded else "No"
    )
    assert outcomes.iloc[0]["Unpaid expenses ($)"] == (0 if funded else 800)
    assert "Fully funded futures (%)" not in outcomes.columns
    assert any("not a probability" in caption.value for caption in page.caption)
    assert not any("shading covers" in caption.value for caption in page.caption)
    assert len(calculation_calls) == 1
