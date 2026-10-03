"""Forecast setup, retained edits, and explicit run/save actions in the app."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import uuid4

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
    module.forecast_issues = (
        lambda request: ()
        if request.details.assumptions_confirmed
        else ("Review assumptions before running.",)
    )
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
    assert field(page, "button", "Run forecast").disabled
    assert field(page, "text_input", "Taxable cost basis ($)").value == ""
    assert field(page, "selectbox", "Account owner").value is None
    assert field(page, "text_input", "Annual debt payment ($)").value == ""
    assert not calculation_calls
    field(
        page,
        "checkbox",
        "I have reviewed the tax, annual timing, benefit, and withdrawal assumptions",
    ).check().run()
    field(page, "button", "Run forecast").click().run()
    assert not page.exception
    assert len(calculation_calls) == 1
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
    field(page, "text_input", "Taxable cost basis ($)").set_value("unfinished")
    page.switch_page("src/financial_model/pages/compare.py").run()
    page.switch_page(PAGE).run()
    assert not page.exception
    assert page.multiselect[0].value == [f"{plan_record.id}:0"]
    assert field(page, "text_input", "Taxable cost basis ($)").value == "unfinished"
    assert page.error
    assert field(page, "button", "Run forecast").disabled
    assert not calculation_calls


def test_forecast_history_requires_explicit_provenance(
    plan_record, calculation_calls
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    field(page, "selectbox", "Forecast method").select("historical").run()
    assert not page.exception
    assert any("history source" in message.value for message in page.error)
    assert field(page, "button", "Run forecast").disabled
    assert not calculation_calls


def test_forecast_saved_run_reuses_frozen_plans_without_library(
    plan_record, calculation_calls
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    field(
        page,
        "checkbox",
        "I have reviewed the tax, annual timing, benefit, and withdrawal assumptions",
    ).check().run()
    field(page, "button", "Run forecast").click().run()
    original = page.session_state["forecast_document"]
    page.session_state["planning_records"] = {}
    page.run()
    field(page, "button", "Rerun saved inputs").click().run()
    assert not page.exception
    assert len(calculation_calls) == 2
    assert calculation_calls[1] == original.request
    assert page.session_state["forecast_document"].id != original.id


def test_forecast_failed_rerun_preserves_previous_result(
    monkeypatch, plan_record, calculation_calls
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    field(
        page,
        "checkbox",
        "I have reviewed the tax, annual timing, benefit, and withdrawal assumptions",
    ).check().run()
    field(page, "button", "Run forecast").click().run()
    original = page.session_state["forecast_document"]

    def failed(request, progress=None):
        message = "Synthetic accounting mismatch"
        raise RuntimeError(message)

    monkeypatch.setattr(
        sys.modules["financial_model.forecast"], "calculate_forecast", failed
    )
    field(page, "button", "Rerun saved inputs").click().run()
    assert not page.exception
    assert any("Synthetic accounting mismatch" in item.value for item in page.error)
    assert page.session_state["forecast_document"] == original
    assert page.get("download_button")


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
    field(
        page,
        "checkbox",
        "I have reviewed the tax, annual timing, benefit, and withdrawal assumptions",
    ).check().run()
    field(page, "button", "Run forecast").click().run()
    assert not page.exception
    assert len(specs) == 2
    for spec in specs:
        rows = spec["layer"][0]["data"]["values"]
        assert {row["scenario"] for row in rows} == {
            "1. Compare choices",
            "2. Compare choices",
        }


def test_forecast_hsa_requires_explicit_eligible_health_and_ca_basis(
    plan_record, calculation_calls
) -> None:
    data = plan_record.model_dump(mode="json")
    data["inputs"]["wealth"]["snapshot"]["accounts"].append(
        {
            "label": "HSA",
            "balance": 500,
            "tax_treatment": "hsa",
            "invested_in": "stocks",
        }
    )
    data["inputs"]["household"]["residence"] = [{"year": 2026, "location": "CA"}]
    record = SavedPlanningDocument.model_validate(data)
    page = open_page(record)
    page.multiselect[0].select(f"{record.id}:0").run()
    assert not page.exception
    assert field(page, "text_input", "California HSA cost basis ($)").value == ""
    eligible = [item for item in page.text_input if "HSA-eligible" in item.label]
    assert len(eligible) == 4
    assert all(item.value == "" for item in eligible)
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
