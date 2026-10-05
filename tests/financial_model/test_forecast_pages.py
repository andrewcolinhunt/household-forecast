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
    ForecastExpense,
    ForecastPoint,
    ForecastSettings,
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
    retirement = profiles[-1].model_dump(mode="json")
    for index, adult in enumerate(retirement["adults"]):
        adult.update(
            retirement={"central": 2040 + index},
            age={"central": 90},
            social_security=0,
            pension=0,
        )
    profiles[-1] = RetirementAssumptions.model_validate(retirement)
    return SavedPlanningDocument(
        inputs=PlanningScenario(
            name="Compare choices",
            household={
                "reference_year": 2026,
                "adults": [
                    {"name": "Adult 1", "age": 40},
                    {"name": "Adult 2", "age": 40},
                ],
            },
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
                select_profile(SavedPlanningDocument(inputs=profile)).model_copy(
                    update={
                        "adult_mapping": (1, 2)
                        if profile.kind in {"work", "retirement"}
                        else None
                    }
                )
                for profile in profiles
            ),
        )
    )


def synthetic_ledger(plan, scenario: int = 0, path: int = 0):
    """Return complete synthetic rows with distinct Plan and future amounts."""
    opening = 3000.0
    rows = []
    for offset, year in enumerate((2026, 2027)):
        income = 500.0 + 100 * scenario + 10 * path
        assets = opening + income + 100 - 300 - 50 - 10
        debt = 90.0 - 10 * offset
        row = {
            "year": year,
            "opening_assets": opening,
            "income": income,
            "cash_pay": income,
            "rsu_income": 0.0,
            "rsu_tax_withholding": 0.0,
            "investment_gain": 100.0,
            "property_value": 0.0,
            "property_gain": 0.0,
            "rental_income": 0.0,
            "property_expenses": 0.0,
            "spending_planned": 300.0,
            "spending_paid": 300.0,
            "taxes": 50.0,
            "federal_income_tax": 40.0,
            "state_income_tax": 5.0,
            "employee_payroll_tax": 5.0,
            "tax_paid": 50.0,
            "unpaid_tax_liability": 0.0,
            "debt_interest": 0.0,
            "debt_payment": 10.0,
            "employee_contributions": 0.0,
            "employer_contributions": 0.0,
            "pensions": 0.0,
            "social_security": 0.0,
            "required_distributions": 0.0,
            "withdrawals": 0.0,
            "realized_gains": 0.0,
            "assets": assets,
            "debt": debt,
            "net_worth": assets - debt,
            "unfunded": 0.0,
            "unfunded_living": 0.0,
            "unfunded_debt_payment": 0.0,
            "new_unfunded_tax": 0.0,
            "price_index": 1.02 ** (offset + 1),
            "equity_return": 0.04 + path * 0.01,
            "bond_return": 0.02,
            "cash_return": 0.01,
            "inflation": 0.02,
            "taxable_cost_basis": 1000.0,
            "capital_loss_carryforward": 0.0,
            "ca_capital_loss_carryforward": 0.0,
            "wa_capital_loss_carryforward": 0.0,
            "ca_hsa_income": 0.0,
            "ca_hsa_gains": 0.0,
            "accessible": assets - 2000,
            "cash": assets - 3000,
            "reserve_breach": 0.0,
            "Forecast cash": assets - 3000,
            "Forecast investments": 0.0,
            "Adult 1 contributions": 0.0,
            "Adult 2 contributions": 0.0,
        }
        for account in plan.wealth.snapshot.accounts:
            row[f"Account: {account.label} ({str(account.id)[:8]})"] = float(
                account.balance
            )
        rows.append(row)
        opening = assets
    return rows


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
                    points=(point, point.model_copy(update={"year": 2027})),
                    funded_fraction=1,
                    first_shortfall_year=None,
                    mean_unfunded_total=0,
                    mean_unfunded_years=0,
                    paths=1 if request.settings.mode in {"fixed", "stress"} else 2,
                    example=tuple(synthetic_ledger(plan, scenario)),
                )
                for scenario, plan in enumerate(request.plans)
            ),
        )
        return SimpleNamespace(
            document=document,
            inspect=lambda scenario, path: synthetic_ledger(
                request.plans[scenario], scenario, path
            ),
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


def result_tab(page, name: str):
    """Activate a lazy tab through its documented Streamlit state key."""
    document = page.session_state["forecast_document"]
    page.session_state[f"forecast_result_tab_{document.id}"] = name
    page.run()
    assert not page.exception
    return page


def detail_value(page, tab: str, kind: str, label: str, value):
    """Supply tab state omitted by AppTest's unsupported tab-container widget."""
    field(page, kind, label).set_value(value)
    return result_tab(page, tab)


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
    assert [tab.label for tab in page.tabs] == [
        "Overview",
        "Cash flow",
        "Simulated future",
        "Sensitivity",
    ]
    assert field(page, "button", "Save forecast").disabled
    assert page.get("download_button")
    result_tab(page, "Simulated future")
    assert len(page.get("vega_lite_chart")) == 3
    assert any(
        "Wealth, accessible money, and accounts" in item.value for item in page.markdown
    )
    detail_value(page, "Simulated future", "number_input", "Future number", 2)
    assert not page.exception
    assert any(
        "cash_pay" in table.value and table.value.iloc[0]["cash_pay"] == 510
        for table in page.dataframe
    )
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


def test_forecast_setup_offers_only_fixed_and_simulated_returns(
    plan_record, calculation_calls
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    assert not page.exception
    assert page.button_group(key="mode_0").options == [
        "Fixed assumptions",
        "Simulated returns",
    ]
    assert [widget.label for widget in page.get("file_uploader")] == ["Forecast JSON"]
    assert not page.text_area
    assert not any(widget.label == "Adverse scenario" for widget in page.selectbox)
    assert not field(page, "button", "Run forecast").disabled
    assert not calculation_calls


@pytest.mark.parametrize("mode", ["historical", "bootstrap", "stress"])
def test_forecast_older_saved_modes_open_unchanged_and_rerun_exact_inputs(
    monkeypatch, plan_record, calculation_calls, mode
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    field(page, "button", "Run forecast").click().run()
    document = page.session_state["forecast_document"]
    settings = ForecastSettings(
        mode=mode,
        count=37,
        seed=128,
        history=((0.04, 0.02, 0.01, 0.02),) * 3,
        history_source="Synthetic saved history",
        history_start_year=1980,
        block_years=2,
        stress="persistent_inflation",
    )
    original = document.model_copy(
        update={
            "request": document.request.model_copy(update={"settings": settings}),
            "summaries": tuple(
                summary.model_copy(update={"paths": 1 if mode == "stress" else 2})
                for summary in document.summaries
            ),
        }
    )
    monkeypatch.setattr(app, "_database_url", lambda: "test")
    monkeypatch.setattr(forecast_pages, "list_forecasts", lambda dsn: (original,))
    monkeypatch.setattr(
        forecast_pages, "list_planning_documents", lambda dsn, kind: (plan_record,)
    )
    page.session_state["planning_records"] = {}
    page.run()
    field(page, "button", "Open saved run").click().run()
    assert not page.exception
    assert page.session_state["forecast_document"] == original
    assert "forecast_calculation" not in page.session_state
    assert len(calculation_calls) == 1
    method = page.button_group(key="mode_1")
    assert method.value == "fixed"
    assert method.options == ["Fixed assumptions", "Simulated returns"]
    assert [widget.label for widget in page.get("file_uploader")] == ["Forecast JSON"]
    assert not page.text_area
    labels = {widget.label for widget in (*page.selectbox, *page.number_input)}
    assert "Adverse scenario" not in labels
    assert "Consecutive years per block" not in labels

    method.set_value("monte_carlo").run()
    assert not page.exception
    assert field(page, "number_input", "Simulated futures").value == 37
    assert field(page, "number_input", "Random seed").value == 128
    assert page.session_state["forecast_document"] == original
    assert len(calculation_calls) == 1
    field(page, "button", "Rerun these inputs").click().run()
    assert not page.exception
    assert len(calculation_calls) == 2
    assert calculation_calls[-1] == original.request
    rerun = page.session_state["forecast_document"]
    assert rerun.request == original.request
    assert rerun.id != original.id


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


def test_forecast_opened_run_does_not_restore_deleted_plan_options(
    monkeypatch, plan_record, calculation_calls
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    field(page, "button", "Run forecast").click().run()
    original = page.session_state["forecast_document"]
    replacement = plan_record.model_copy(
        update={
            "id": uuid4(),
            "inputs": plan_record.inputs.model_copy(update={"name": "Current Plan"}),
        }
    )
    monkeypatch.setattr(app, "_database_url", lambda: "test")
    monkeypatch.setattr(forecast_pages, "list_forecasts", lambda dsn: (original,))
    monkeypatch.setattr(
        forecast_pages, "list_planning_documents", lambda dsn, kind: (replacement,)
    )
    page.session_state["forecast_loaded_request"] = original.request
    page.run()
    assert not page.exception
    assert page.multiselect[0].options == ["Current Plan"]
    assert page.multiselect[0].value == []
    page.multiselect[0].select(f"{replacement.id}:0").run()
    field(page, "button", "Open saved run").click().run()
    assert not page.exception
    assert page.multiselect[0].options == ["Current Plan"]
    assert page.multiselect[0].value == [f"{replacement.id}:0"]
    assert page.session_state["forecast_document"] == original
    assert len(calculation_calls) == 1
    field(page, "button", "Rerun these inputs").click().run()
    assert calculation_calls[-1] == original.request


@pytest.mark.parametrize("error_type", [psycopg.OperationalError, ValueError])
def test_forecast_plan_read_failure_does_not_offer_cached_plans(
    monkeypatch, plan_record, calculation_calls, error_type
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    field(page, "button", "Run forecast").click().run()
    original = page.session_state["forecast_document"]

    def unavailable(dsn, kind):
        message = "Saved Plan read failed"
        raise error_type(message)

    monkeypatch.setattr(app, "_database_url", lambda: "test")
    monkeypatch.setattr(forecast_pages, "list_forecasts", lambda dsn: (original,))
    monkeypatch.setattr(forecast_pages, "list_planning_documents", unavailable)
    page.run()
    assert not page.exception
    assert not page.multiselect
    assert any("Saved Plans could not be read" in item.value for item in page.error)
    assert page.session_state["forecast_document"] == original
    field(page, "button", "Rerun these inputs").click().run()
    assert calculation_calls[-1] == original.request


def test_forecast_saved_run_controls_omit_historical_inventory(
    monkeypatch, plan_record, calculation_calls
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    field(page, "button", "Run forecast").click().run()
    original = page.session_state["forecast_document"]
    monkeypatch.setattr(app, "_database_url", lambda: "test")
    monkeypatch.setattr(forecast_pages, "list_forecasts", lambda dsn: ())
    monkeypatch.setattr(forecast_pages, "list_planning_documents", lambda dsn, kind: ())
    page.run()
    assert not page.exception
    assert not any(widget.label == "Historical run" for widget in page.selectbox)
    assert not any(
        button.label == "Download historical forecast"
        for button in page.get("download_button")
    )
    assert not any(button.label == "Open saved run" for button in page.button)
    assert page.session_state["forecast_document"] == original
    assert len(calculation_calls) == 1


def test_forecast_cash_flow_inspects_named_expenses_and_category_slice(
    plan_record, calculation_calls
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    field(page, "button", "Run forecast").click().run()
    original = page.session_state["forecast_document"]
    summary = original.summaries[0]
    living, health = uuid4(), uuid4()
    expenses = tuple(
        ForecastExpense(
            year=year,
            item_id=item,
            label=label,
            category=category,
            frequency="recurring",
            base_amount=amount,
            real_growth=0,
            real_amount=amount,
        )
        for year in (2026, 2027)
        for item, label, category, amount in (
            (living, "Ordinary living", "ordinary", 200),
            (health, "Coverage", "healthcare", 100),
        )
    )
    summary = summary.model_copy(
        update={
            "expenses": expenses,
            "example": tuple(
                row | {f"expense_{living}": 200, f"expense_{health}": 100}
                for row in summary.example
            ),
        }
    )
    page.session_state["forecast_document"] = original.model_copy(
        update={"summaries": (summary,)}
    )
    del page.session_state["forecast_calculation"]
    result_tab(page, "Cash flow")
    detail_value(page, "Cash flow", "selectbox", "Inspect year", 2026)
    table = next(table.value for table in page.dataframe if "Expense" in table.value)
    assert table["Expense"].tolist() == ["Ordinary living", "Coverage"]
    assert table["Planned amount"].sum() == 300
    detail_value(page, "Cash flow", "selectbox", "Expense category", "Healthcare")
    table = next(table.value for table in page.dataframe if "Expense" in table.value)
    assert table["Expense"].tolist() == ["Coverage"]
    assert table["Planned amount"].tolist() == [100]
    assert any(
        "shortfalls are household totals" in caption.value for caption in page.caption
    )
    assert len(calculation_calls) == 1


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


@pytest.mark.parametrize("mode", ["fixed", "monte_carlo"])
def test_forecast_duplicate_names_have_distinct_plot_series(
    monkeypatch, plan_record, calculation_calls, mode
) -> None:
    specs = []
    monkeypatch.setattr(
        forecast_pages.st, "vega_lite_chart", lambda spec, **kwargs: specs.append(spec)
    )
    name = "Example household — reduced working hours · one child · rental property"
    plan_record = plan_record.model_copy(
        update={"inputs": plan_record.inputs.model_copy(update={"name": name})}
    )
    page = open_page(plan_record)
    duplicate = plan_record.model_copy(update={"id": uuid4()})
    page.session_state["planning_records"] = {"plan": (plan_record, duplicate)}
    page.run()
    page.multiselect[0].select(f"{plan_record.id}:0").select(f"{duplicate.id}:0").run()
    page.button_group(key="mode_0").set_value(mode).run()
    field(page, "button", "Run forecast").click().run()
    assert not page.exception
    assert len(specs) == 1
    for spec in specs:
        rows = spec["layer"][0]["data"]["values"]
        assert {row["scenario"] for row in rows} == {
            f"1. {name}",
            f"2. {name}",
        }
        hover = spec["layer"][-1]
        assert hover["layer"][0]["mark"]["type"] == "rule"
        tooltip = hover["layer"][1]["encoding"]["tooltip"]
        assert {field["title"] for field in tooltip} >= {
            "Year",
            "Adult 1 age",
            "Adult 2 age",
            "Plan 1 · Median",
            "Plan 2 · Median",
        }
        measures = {"median": ("Median", "$100.00")}
        if mode == "monte_carlo":
            measures |= {
                "lower": ("10th percentile", "$90.00"),
                "upper": ("90th percentile", "$110.00"),
            }
        metric_fields = [
            field for field in tooltip if field["field"].startswith("plan_")
        ]
        assert {field["field"]: field["title"] for field in metric_fields} == {
            f"plan_{index}_{measure}": f"Plan {index + 1} · {label}"
            for index in range(2)
            for measure, (label, _) in measures.items()
        }
        assert len({field["title"] for field in metric_fields}) == len(metric_fields)
        for row in hover["data"]["values"]:
            assert {
                key: value for key, value in row.items() if key.startswith("plan_")
            } == {
                f"plan_{index}_{measure}": value
                for index in range(2)
                for measure, (_, value) in measures.items()
            }
    result_tab(page, "Simulated future")
    detail_value(page, "Simulated future", "selectbox", "Plan to inspect", 1)
    assert any(
        "cash_pay" in table.value and table.value.iloc[0]["cash_pay"] == 600
        for table in page.dataframe
    )
    detail_value(page, "Simulated future", "selectbox", "Plan to inspect", 0)
    assert any(
        "cash_pay" in table.value and table.value.iloc[0]["cash_pay"] == 500
        for table in page.dataframe
    )


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


@pytest.mark.parametrize("mode", ["fixed", "monte_carlo"])
def test_forecast_overview_omits_lifetime_funding_summary(
    plan_record, calculation_calls, mode
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    page.button_group(key="mode_0").set_value(mode).run()
    field(page, "button", "Run forecast").click().run()
    assert not page.exception
    assert not any("Lifetime funding" in item.value for item in page.markdown)
    assert not any(
        "Expenses funded through lifetime" in table.value
        or "Fully funded futures (%)" in table.value
        for table in page.dataframe
    )
    assert not any("Fully funded means" in item.value for item in page.caption)
    assert len(page.get("vega_lite_chart")) == 1
    if mode == "fixed":
        assert any("one projected future" in item.value for item in page.caption)
        assert not any("shading covers" in item.value for item in page.caption)
    assert len(calculation_calls) == 1


def test_forecast_detail_tabs_share_future_and_do_not_calculate(
    plan_record, calculation_calls
) -> None:
    page = open_page(plan_record)
    duplicate = plan_record.model_copy(update={"id": uuid4()})
    page.session_state["planning_records"] = {"plan": (plan_record, duplicate)}
    page.run()
    page.multiselect[0].select(f"{plan_record.id}:0").select(f"{duplicate.id}:0").run()
    field(page, "button", "Run forecast").click().run()
    original = page.session_state["forecast_document"]

    result_tab(page, "Cash flow")
    detail_value(page, "Cash flow", "number_input", "Future number", 2)
    detail_value(page, "Cash flow", "selectbox", "Plan to inspect", 1)
    detail_value(page, "Cash flow", "selectbox", "Inspect year", 2027)
    detail_value(page, "Cash flow", "button_group", "Dollar display", False)
    assert field(page, "number_input", "Future number").value == 2
    for name in ("Simulated future", "Overview", "Cash flow"):
        result_tab(page, name)
        if name != "Overview":
            assert field(page, "number_input", "Future number").value == 2
            assert field(page, "selectbox", "Plan to inspect").value == 1
            assert field(page, "button_group", "Dollar display").value is False
        if name == "Cash flow":
            assert field(page, "selectbox", "Inspect year").value == 2027
        elif name == "Simulated future":
            assert not any(w.label == "Inspect year" for w in page.selectbox)
        assert len(calculation_calls) == 1
        assert page.session_state["forecast_document"] == original

    result_tab(page, "Simulated future")
    assert any(
        "cash_pay" in table.value and table.value.iloc[0]["cash_pay"] == 610
        for table in page.dataframe
    )
    page.switch_page("src/financial_model/pages/compare.py").run()
    page.switch_page(PAGE).run()
    assert not page.exception
    result_tab(page, "Simulated future")
    assert field(page, "number_input", "Future number").value == 2
    assert field(page, "selectbox", "Plan to inspect").value == 1
    assert not any(w.label == "Inspect year" for w in page.selectbox)
    assert field(page, "button_group", "Dollar display").value is False
    result_tab(page, "Cash flow")
    assert field(page, "selectbox", "Inspect year").value == 2027
    assert len(calculation_calls) == 1


def test_forecast_saved_details_use_example_until_explicit_rerun(
    plan_record, calculation_calls
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    field(page, "button", "Run forecast").click().run()
    original = page.session_state["forecast_document"]
    del page.session_state["forecast_calculation"]

    for name in ("Cash flow", "Simulated future"):
        result_tab(page, name)
        assert page.get("vega_lite_chart")
        assert len(calculation_calls) == 1
    assert any(
        "cash_pay" in table.value and table.value.iloc[0]["cash_pay"] == 500
        for table in page.dataframe
    )

    detail_value(page, "Simulated future", "number_input", "Future number", 2)
    assert not page.exception
    assert any("rerun" in item.value.lower() for item in page.info)
    assert not page.get("vega_lite_chart")
    assert not page.dataframe
    assert len(calculation_calls) == 1
    assert page.session_state["forecast_document"] == original
    assert page.get("download_button")

    field(page, "button", "Rerun these inputs").click().run()
    assert not page.exception
    assert len(calculation_calls) == 2
    assert calculation_calls[-1] == original.request
    result_tab(page, "Simulated future")
    detail_value(page, "Simulated future", "number_input", "Future number", 2)
    assert page.get("vega_lite_chart")
    assert len(calculation_calls) == 2


def test_forecast_sparse_saved_example_keeps_overview_and_download(
    plan_record, calculation_calls
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    field(page, "button", "Run forecast").click().run()
    document = page.session_state["forecast_document"]
    sparse = document.summaries[0].model_copy(
        update={"example": ({"year": 2026, "ending_assets": 100},)}
    )
    page.session_state["forecast_document"] = document.model_copy(
        update={"summaries": (sparse,)}
    )
    del page.session_state["forecast_calculation"]
    for name in ("Cash flow", "Simulated future"):
        result_tab(page, name)
        assert page.info
        assert page.get("download_button")
        assert len(calculation_calls) == 1
    result_tab(page, "Overview")
    assert page.get("vega_lite_chart")
    assert page.dataframe


def test_forecast_unrelated_in_memory_calculation_does_not_supply_saved_paths(
    plan_record, calculation_calls
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    field(page, "button", "Run forecast").click().run()
    original = page.session_state["forecast_document"]
    field(page, "button", "Rerun these inputs").click().run()
    page.session_state["forecast_document"] = original
    result_tab(page, "Simulated future")
    detail_value(page, "Simulated future", "number_input", "Future number", 2)
    assert not page.exception
    assert any("rerun" in item.value.lower() for item in page.info)
    assert not page.get("vega_lite_chart")
    assert len(calculation_calls) == 2


@pytest.mark.parametrize("history_start_year", [None, 1980])
def test_forecast_historical_details_accept_unknown_calendar_start(
    plan_record, calculation_calls, history_start_year
) -> None:
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    field(page, "button", "Run forecast").click().run()
    document = page.session_state["forecast_document"]
    settings = ForecastSettings(
        mode="historical",
        count=10000,
        history=((0.04, 0.02, 0.01, 0.02),) * 3,
        history_source="Synthetic history for a UI test",
        history_start_year=history_start_year,
    )
    page.session_state["forecast_document"] = document.model_copy(
        update={"request": document.request.model_copy(update={"settings": settings})}
    )
    del page.session_state["forecast_calculation"]
    result_tab(page, "Simulated future")
    assert page.get("vega_lite_chart")
    assert field(page, "number_input", "Future number").proto.max == 2
    captions = [item.value for item in page.caption]
    if history_start_year is not None:
        assert any("1980" in caption for caption in captions)
    else:
        assert not any("observed year" in caption for caption in captions)
    assert len(calculation_calls) == 1


def test_forecast_sensitivity_requires_action_and_uses_frozen_plan(
    monkeypatch, plan_record, calculation_calls
) -> None:
    from financial_model import forecast_views
    from financial_model.forecast_sensitivity import (
        SensitivityCase,
        SensitivityDriver,
        SensitivityPoint,
        SensitivityResult,
    )

    sensitivity_calls = []

    def calculate(plan_json, _progress):
        plan = PlanningScenario.model_validate_json(plan_json)
        sensitivity_calls.append(plan)

        def case(label, amount):
            return SensitivityCase(
                label=label,
                status="ok",
                annual=tuple(
                    SensitivityPoint(
                        year=year, wealth_real=amount, accessible_real=amount / 2
                    )
                    for year in (2026, 2027)
                ),
            )

        return SensitivityResult(
            plan_name=plan.name,
            money_year=2026,
            baseline=case("Baseline", 3000),
            drivers=(
                SensitivityDriver(
                    key="spending",
                    label="Annual spending",
                    range_label="-10% / +10%",
                    description="Synthetic spending sensitivity",
                    low=case("Lower spending", 3200),
                    high=case("Higher spending", 2800),
                ),
            ),
        )

    monkeypatch.setattr(forecast_views, "_calculate_sensitivity", calculate)
    page = open_page(plan_record)
    page.multiselect[0].select(f"{plan_record.id}:0").run()
    field(page, "button", "Run forecast").click().run()
    original = page.session_state["forecast_document"]
    changed = plan_record.inputs.model_copy(update={"name": "Changed in library"})
    page.session_state["planning_records"] = {
        "plan": (plan_record.model_copy(update={"inputs": changed}),)
    }

    result_tab(page, "Sensitivity")
    assert not sensitivity_calls
    assert not page.get("vega_lite_chart")
    assert len(calculation_calls) == 1
    field(page, "button", "Run sensitivity analysis").click()
    result_tab(page, "Sensitivity")
    assert not page.exception
    assert sensitivity_calls == [original.request.plans[0]]
    assert page.get("vega_lite_chart")
    assert page.session_state["forecast_document"] == original
    assert len(calculation_calls) == 1

    detail_value(page, "Sensitivity", "selectbox", "Inspect year", 2026)
    result_tab(page, "Overview")
    result_tab(page, "Sensitivity")
    assert page.get("vega_lite_chart")
    assert len(sensitivity_calls) == 1
    assert field(page, "selectbox", "Inspect year").value == 2026
    assert page.session_state["forecast_document"] == original
