"""Real workspace workflows, raw draft preservation and input-only persistence."""

from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from financial_model import app, planning_pages
from financial_model.planning import (
    EconomicAssumptions,
    PlanningScenario,
    SavedPlanningDocument,
    SpendingAssumptions,
    WorkAssumptions,
    select_profile,
)
from financial_model.planning_storage import (
    create_planning_document,
    delete_planning_document,
    import_planning_backup,
    list_planning_documents,
    load_planning_document,
    update_planning_document,
)
from financial_model.scenario_pages import CATEGORIES
from financial_model.schemas import AllocationPlan
from financial_model.storage import create_wealth_snapshot
from financial_model.wealth import (
    AccountPosition,
    SavedWealthSnapshot,
    WealthSnapshot,
)

ENTRY = Path(__file__).parents[2] / "streamlit_app.py"


@pytest.fixture(autouse=True)
def preview(monkeypatch):
    monkeypatch.setattr(app, "_database_url", lambda: None)


def tab(page, category):
    page.session_state["scenario_category"] = category
    page.switch_page("src/financial_model/pages/scenarios.py").run()
    assert not page.exception
    return page


def press(page, label):
    next(b for b in page.button if b.label == label).click().run()
    assert not page.exception
    return page


def field(page, kind, label):
    return next(w for w in getattr(page, kind) if w.label == label)


def raw(page, kind):
    return page.session_state["planning_drafts"][
        page.session_state["planning_active"][kind]
    ]["data"]


def test_active_categories_and_new_inputs_never_calculate(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Input collection must not run annual evaluation")

    monkeypatch.setattr(app, "evaluate_scenario", forbidden)
    page = AppTest.from_file(str(ENTRY)).run()
    assert [t.label for t in page.tabs] == list(CATEGORIES)
    for category in ("Work", "Spending", "Investments", "Economy", "Retirement"):
        tab(page, category)
        press(page, f"New {category} assumptions")
        assert page.get("download_button")
        assert next(b for b in page.button if b.label == "Save inputs").disabled
        assert not any(b.label == "Calculate" for b in page.button)
        assert any("need input" in c.value for c in page.caption)
    tab(page, "Plans")
    press(page, "New combined plan")
    assert "plan" in page.session_state["planning_active"]
    tab(page, "Household")
    assert any("Editing combined plan" in i.value for i in page.info)
    assert not any(b.label == "Save household" for b in page.button)


def test_invalid_text_survives_tab_change_in_same_rerun_and_compare():
    page = AppTest.from_file(str(ENTRY)).run()
    tab(page, "Economy")
    press(page, "New Economy assumptions")
    field(page, "text_input", "Stock return (%)").set_value("not a number")
    tab(page, "Retirement")
    assert raw(page, "economy")["equities"]["central"] == "not a number"
    page.switch_page("src/financial_model/pages/compare.py").run()
    page.switch_page("src/financial_model/pages/scenarios.py").run()
    assert page.session_state["scenario_category"] == "Retirement"
    tab(page, "Economy")
    assert field(page, "text_input", "Stock return (%)").value == "not a number"
    assert page.error
    assert not page.get("download_button")
    field(page, "text_input", "Stock return (%)").set_value("5.5").run()
    assert raw(page, "economy")["equities"]["central"] == "0.055"
    assert not page.error


def test_work_shares_compensation_without_demographics_and_collects_dates():
    page = AppTest.from_file(str(ENTRY)).run()
    tab(page, "Work")
    press(page, "New Work assumptions")
    press(page, "Add work phase")
    assert not any(
        w.label in {"Age at year end", "Full-year state", "Monthly rent ($)"}
        for kind in ("number_input", "selectbox", "text_input")
        for w in getattr(page, kind)
    )
    field(page, "selectbox", "Employment status").select("working").run()
    field(page, "number_input", "Start year").set_value(2026).run()
    field(page, "text_input", "Annual salary and bonus ($)").set_value(
        "123456.78"
    ).run()
    assert raw(page, "work")["adults"][0]["phases"][0]["real_growth"]["central"] == "0"
    tab(page, "Spending")
    tab(page, "Work")
    assert field(page, "text_input", "Annual salary and bonus ($)").value == "123456.78"
    assert not page.error


def test_spending_and_investment_rows_retain_inputs():
    page = AppTest.from_file(str(ENTRY)).run()
    tab(page, "Spending")
    press(page, "New Spending assumptions")
    press(page, "Change regular living costs")
    field(page, "selectbox", "Change starts").select("both").run()
    field(page, "text_input", "New regular living costs ($/year)").set_value(
        "80000"
    ).run()
    press(page, "Add an expense")
    field(page, "text_input", "Expense name").set_value("Childcare").run()
    field(page, "text_input", "Extra annual cost ($/year)").set_value("20000").run()
    field(page, "checkbox", "Specify an end date").check().run()
    assert not page.exception
    tab(page, "Investments")
    press(page, "New Investments assumptions")
    field(page, "selectbox", "Allocation policy").select("gradual").run()
    press(page, "Add allocation checkpoint")
    field(page, "checkbox", "Specify target allocation").check().run()
    for label, value in (
        ("Stocks (%)", "80"),
        ("Bonds (%)", "20"),
        ("Cash (%)", "0"),
    ):
        field(page, "text_input", label).set_value(value).run()
    assert not page.error
    tab(page, "Spending")
    assert field(page, "text_input", "Expense name").value == "Childcare"
    assert field(page, "selectbox", "Change starts").value == "both"


def test_import_is_independent_and_bad_upload_keeps_draft(monkeypatch):
    record = SavedPlanningDocument(inputs=EconomicAssumptions(name="Imported"))
    page = AppTest.from_file(str(ENTRY)).run()
    tab(page, "Economy")
    monkeypatch.setattr(
        planning_pages.st,
        "file_uploader",
        lambda *args, **kwargs: BytesIO(record.model_dump_json().encode()),
    )
    page.run()
    press(page, "Load input backup as new draft")
    assert raw(page, "economy")["name"] == "Imported"
    assert page.session_state["planning_active"]["economy"] != str(record.id)
    field(page, "text_input", "Name").set_value("Edited").run()
    monkeypatch.setattr(
        planning_pages.st, "file_uploader", lambda *args, **kwargs: BytesIO(b"invalid")
    )
    page.run()
    assert page.error
    assert raw(page, "economy")["name"] == "Edited"
    assert next(
        b for b in page.button if b.label == "Load input backup as new draft"
    ).disabled


@pytest.mark.integration
def test_investment_surplus_destination_survives_save_and_reopen(
    database_url, monkeypatch
):
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    page = AppTest.from_file(str(ENTRY)).run()
    tab(page, "Investments")
    press(page, "New Investments assumptions")
    field(page, "checkbox", "Include saving instructions").check().run()
    saving = AllocationPlan.model_validate(raw(page, "investments")["saving"])
    retirement = str(saving.retirement_goal_id)
    old_remainder = saving.spending_goal_id
    field(page, "selectbox", "Surplus goes to").select(retirement).run()
    assert not page.exception
    assert not page.error
    assert any(c.value == "All surplus" for c in page.caption)
    press(page, "Save inputs")
    (saved,) = list_planning_documents(database_url, "investments")
    assert saved.inputs.saving.spending_goal_id == saving.retirement_goal_id
    assert (
        next(
            r for r in saved.inputs.saving.rows if r.goal.id == old_remainder
        ).cash_amount
        == 0
    )
    assert sum(r.rsu_rate for r in saved.inputs.saving.rows) == 1
    tab(page, "Spending")
    tab(page, "Investments")
    assert field(page, "selectbox", "Surplus goes to").value == retirement
    field(page, "selectbox", "Surplus goes to").select(str(old_remainder)).run()
    assert not page.exception
    reverted = AllocationPlan.model_validate(raw(page, "investments")["saving"])
    assert reverted.spending_goal_id == old_remainder


@pytest.mark.integration
@pytest.mark.parametrize(
    "shape", ["linear_then_hold", "log_then_hold", "exponential_then_hold"]
)
def test_glide_shape_survives_save_backup_and_plan_selection(
    database_url, monkeypatch, shape
):
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    page = AppTest.from_file(str(ENTRY)).run()
    tab(page, "Investments")
    press(page, "New Investments assumptions")
    assert not any(w.label == "Glide-path shape" for w in page.selectbox)
    field(page, "selectbox", "Allocation policy").select("gradual").run()
    assert field(page, "selectbox", "Glide-path shape").value == "linear_then_hold"
    field(page, "selectbox", "Glide-path shape").select(shape).run()
    press(page, "Save inputs")
    (saved,) = list_planning_documents(database_url, "investments")
    assert saved.inputs.interpolation == shape
    assert import_planning_backup(saved.model_dump_json().encode()) == saved.inputs
    plan = PlanningScenario(name="Plan", components=(select_profile(saved),))
    assert plan.components[0].inputs.interpolation == shape
    press(page, "All Investments inputs")
    assert any(planning_pages.GLIDE_SHAPES[shape] in t.value for t in page.caption)
    press(page, "Edit")
    assert field(page, "selectbox", "Glide-path shape").value == shape
    field(page, "selectbox", "Allocation policy").select("constant").run()
    assert not any(w.label == "Glide-path shape" for w in page.selectbox)
    field(page, "selectbox", "Allocation policy").select("gradual").run()
    assert field(page, "selectbox", "Glide-path shape").value == shape
    assert not page.exception


@pytest.mark.integration
def test_all_categories_save_partial_inputs_without_calculation(
    database_url, monkeypatch
):
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    monkeypatch.setattr(
        app, "evaluate_scenario", lambda *args: pytest.fail("No annual evaluation")
    )
    page = AppTest.from_file(str(ENTRY)).run()
    for category in ("Work", "Spending", "Investments", "Economy", "Retirement"):
        tab(page, category)
        press(page, f"New {category} assumptions")
        press(page, "Save inputs")
        (saved,) = list_planning_documents(database_url, category.lower())
        assert saved.inputs.kind == category.lower()
        assert page.success
    tab(page, "Plans")
    press(page, "New combined plan")
    for kind in ("work", "spending", "investments", "economy", "retirement"):
        next(
            b for b in page.button if (b.key or "").startswith(f"choose_{kind}_")
        ).click().run()
        assert not page.exception
    press(page, "Save inputs")
    (saved,) = list_planning_documents(database_url, "plan")
    assert len(saved.inputs.components) == 5
    assert page.success


@pytest.mark.integration
def test_conflicts_reload_duplicate_and_same_name_drafts(database_url, monkeypatch):
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    original = create_planning_document(
        database_url,
        SavedPlanningDocument(inputs=EconomicAssumptions(name="Same name")),
    )
    page = AppTest.from_file(str(ENTRY)).run()
    tab(page, "Economy")
    press(page, "Edit")
    field(page, "text_input", "Stock return (%)").set_value("invalid").run()
    press(page, "All Economy inputs")
    latest = update_planning_document(
        database_url,
        SavedPlanningDocument(
            id=original.id, inputs=EconomicAssumptions(name="New name")
        ),
    )
    page.run()
    press(page, "Edit")
    assert field(page, "text_input", "Stock return (%)").value == "invalid"
    press(page, "Discard edits and reload inputs")
    assert raw(page, "economy")["name"] == "New name"
    delete_planning_document(database_url, latest.id, latest.revision)
    press(page, "Save inputs")
    assert page.error
    press(page, "Save as new inputs")
    (saved,) = list_planning_documents(database_url, "economy")
    assert saved.id != original.id
    press(page, "All Economy inputs")
    press(page, "Duplicate")
    draft_id = page.session_state["planning_active"]["economy"]
    assert draft_id != str(saved.id)
    assert raw(page, "economy")["name"] == saved.inputs.name
    press(page, "Save inputs")
    assert len(list_planning_documents(database_url, "economy")) == 2
    press(page, "All Economy inputs")
    page.button(key=f"planning_delete_{saved.id}").click().run()
    press(page, "Cancel")
    assert load_planning_document(database_url, saved.id) == saved
    page.button(key=f"planning_delete_{saved.id}").click().run()
    press(page, "Delete inputs")
    assert load_planning_document(database_url, saved.id) is None


@pytest.mark.integration
def test_two_saved_plans_keep_copies_after_source_changes(database_url, monkeypatch):
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    source = create_planning_document(
        database_url, SavedPlanningDocument(inputs=WorkAssumptions(name="Shared work"))
    )
    plans = []
    for amount in (60000, 80000):
        spending = create_planning_document(
            database_url,
            SavedPlanningDocument(
                inputs=SpendingAssumptions(
                    name=f"Budget {amount}", baseline={"amount": {"central": amount}}
                )
            ),
        )
        plans.append(
            create_planning_document(
                database_url,
                SavedPlanningDocument(
                    inputs=PlanningScenario(
                        name=f"Plan {amount}",
                        components=(select_profile(source), select_profile(spending)),
                    )
                ),
            )
        )
    page = AppTest.from_file(str(ENTRY)).run()
    page.button(key=f"planning_edit_{plans[0].id}").click().run()
    update_planning_document(database_url, source)
    page.run()
    assert any("Newer version available" in c.value for c in page.caption)
    assert all(load_planning_document(database_url, p.id) == p for p in plans)
    delete_planning_document(database_url, source.id, 2)
    page.run()
    assert any("Source no longer in library" in c.value for c in page.caption)
    press(page, "Save inputs")
    assert load_planning_document(database_url, plans[0].id).inputs == plans[0].inputs


def test_annual_ownership_and_unchanged_round_trip(scenario):
    page = AppTest.from_file(str(ENTRY)).run()
    page.session_state["pending_inputs"] = scenario
    page.run()
    assert not any(w.label == "Annual salary and bonus ($)" for w in page.number_input)
    for category in ("Household", "Work", "Spending", "Investments", "Plans"):
        tab(page, category)
        assert any("Editing annual plan" in i.value for i in page.info)
        labels = [
            w.label
            for kind in ("number_input", "text_input", "selectbox")
            for w in getattr(page, kind)
        ]
        assert ("Annual salary and bonus ($)" in labels) == (category == "Work")
        assert ("Age at year end" in labels) == (category == "Household")
        assert ("Monthly rent ($)" in labels) == (category == "Spending")
    restored = app._parse_inputs(
        page.session_state["draft_data"], page.session_state["draft_ages"]
    )
    assert restored == scenario
    assert restored.digest() == scenario.digest()
    tab(page, "Household")
    field(page, "text_input", "Qualifying children's ages").set_value("invalid age")
    tab(page, "Work")
    assert page.session_state["draft_ages"] == "invalid age"
    press(page, "All Work assumptions")
    assert not any("Editing annual plan" in i.value for i in page.info)
    assert page.session_state["draft_ages"] == "invalid age"


@pytest.mark.integration
def test_plan_household_and_wealth_are_copied_without_account_bindings(
    database_url, monkeypatch
):
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    account = AccountPosition(
        label="Synthetic cash", balance=100, invested_in="cash", tax_treatment="taxable"
    )
    wealth = create_wealth_snapshot(
        database_url,
        SavedWealthSnapshot(
            snapshot=WealthSnapshot(as_of=date(2026, 9, 27), accounts=(account,))
        ),
    )
    work = create_planning_document(
        database_url, SavedPlanningDocument(inputs=WorkAssumptions(name="Work"))
    )
    plan = create_planning_document(
        database_url,
        SavedPlanningDocument(
            inputs=PlanningScenario(
                name="Plan", wealth=wealth, components=(select_profile(work),)
            )
        ),
    )
    page = AppTest.from_file(str(ENTRY)).run()
    page.button(key=f"planning_edit_{plan.id}").click().run()
    assert not any(b.label == "Map plan Wealth" for b in page.button)
    press(page, "Edit plan Household")
    field(page, "text_input", "Name").set_value("Synthetic A").run()
    field(page, "selectbox", "Match people · Work").select((1, 2)).run()
    press(page, "Return to Plans")
    press(page, "Save inputs")
    saved = load_planning_document(database_url, plan.id)
    assert saved.inputs.household.adults[0].name == "Synthetic A"
    assert saved.inputs.components[0].adult_mapping == (1, 2)
    assert saved.inputs.wealth == wealth
    assert load_planning_document(database_url, work.id) == work
    press(page, "Replace Wealth")
    assert raw(page, "plan")["wealth"] == wealth.model_dump(mode="json")
    assert (
        not {"owners", "portfolio_accounts", "reserved_cash"} & raw(page, "plan").keys()
    )


def test_cash_reserve_lives_in_investments_and_survives_navigation():
    page = AppTest.from_file(str(ENTRY)).run()
    tab(page, "Investments")
    press(page, "New Investments assumptions")
    field(page, "text_input", "Cash reserve ($)").set_value("12345.67").run()
    tab(page, "Wealth")
    tab(page, "Investments")
    assert raw(page, "investments")["reserved_cash"] == "12345.67"
    assert not any(w.label == "Employer shares" for w in page.selectbox)


def test_retirement_fields_are_owned_by_retirement_and_ranges_survive_navigation():
    page = AppTest.from_file(str(ENTRY)).run()
    tab(page, "Retirement")
    press(page, "New Retirement assumptions")
    field(page, "number_input", "Adult 1 retires").set_value(2040).run()
    field(page, "number_input", "Plan for Adult 1 through age").set_value(95).run()
    age_bounds = [w for w in page.number_input if "age_0_" in (w.key or "")]
    next(w for w in age_bounds if w.label == "Lower").set_value(90).run()
    next(
        w for w in page.number_input if "age_0_" in (w.key or "") and w.label == "Upper"
    ).set_value(100).run()
    field(page, "selectbox", "Only Adult 1 retired").select("continue").run()
    field(page, "text_input", "Both retired ($/year)").set_value("80000").run()
    tab(page, "Work")
    press(page, "New Work assumptions")
    assert not any("retire" in w.label.lower() for w in page.number_input)
    tab(page, "Spending")
    press(page, "New Spending assumptions")
    assert not any("survivor" in w.label.lower() for w in page.checkbox)
    tab(page, "Retirement")
    assert raw(page, "retirement")["adults"][0]["age"] == {
        "central": 95,
        "lower": 90,
        "upper": 100,
    }
    assert raw(page, "retirement")["both_retired"]["amount"]["central"] == "80000"
    assert raw(page, "retirement")["only_adult_1"] == {
        "mode": "continue",
        "budget": None,
    }
    assert any("Age: 90 to 100" in c.value for c in page.caption)
    assert not page.error


def test_retirement_benefits_preserve_inputs_without_separate_health_controls():
    page = AppTest.from_file(str(ENTRY)).run()
    tab(page, "Retirement")
    press(page, "New Retirement assumptions")
    field(page, "text_input", "Adult 1 Social Security ($/year)").set_value(
        "32000"
    ).run()
    field(page, "number_input", "Social Security claiming age").set_value(70).run()
    field(page, "selectbox", "Adult 1 pension").select("pension").run()
    field(page, "text_input", "Pension ($/year)").set_value("12000").run()
    field(page, "number_input", "Pension starts at age").set_value(60).run()
    field(page, "text_input", "Pension paid to survivor (%)").set_value("50").run()
    field(page, "selectbox", "Adult 2 pension").select("none").run()
    assert not any("health" in w.label.lower() for w in page.text_input)
    assert not any("HSA" in w.label for w in page.expander)
    assert not any(s.value == "Retirement health costs" for s in page.subheader)
    tab(page, "Economy")
    tab(page, "Retirement")
    data = raw(page, "retirement")
    assert data["adults"][0]["social_security"] == "32000"
    assert data["adults"][0]["claiming_age"] == 70
    assert data["adults"][0]["pension_start_age"] == 60
    assert data["adults"][0]["pension_survivor_fraction"] == "0.5"
    assert data["adults"][1]["pension"] == "0"
    assert (
        len([w for w in page.number_input if w.label == "Pension starts at age"]) == 1
    )
    field(page, "selectbox", "Adult 1 pension").select("none").run()
    assert not any(w.label == "Pension starts at age" for w in page.number_input)
    assert raw(page, "retirement")["adults"][0]["pension"] == "0"
    assert not page.error


def test_economy_yields_are_separate_saved_income_portions_of_returns():
    page = AppTest.from_file(str(ENTRY)).run()
    tab(page, "Economy")
    press(page, "New Economy assumptions")
    field(page, "text_input", "Stock return (%)").set_value("7").run()
    field(page, "text_input", "Stock dividend yield (%)").set_value("2").run()
    field(page, "text_input", "Bond interest yield (%)").set_value("4").run()
    tab(page, "Retirement")
    tab(page, "Economy")
    data = raw(page, "economy")
    assert data["equities"]["central"] == "0.07"
    assert data["stock_dividend_yield"] == "0.02"
    assert data["bond_interest_yield"] == "0.04"
    assert not page.error


@pytest.mark.parametrize("rename_person", [False, True])
def test_refresh_one_profile_preserves_alternatives_and_reviews_person_changes(
    monkeypatch, rename_person
):
    source = SavedPlanningDocument(inputs=WorkAssumptions(name="Work"))
    alternative = SavedPlanningDocument(inputs=WorkAssumptions(name="Other work"))
    selection = select_profile(source).model_copy(update={"adult_mapping": (2, 1)})
    plan = PlanningScenario(
        name="Plan", components=(selection, select_profile(alternative))
    )
    data = plan.model_dump(mode="json")
    updated = source.model_dump(mode="json")
    updated["revision"] = 2
    updated["inputs"]["notes"] = "Updated assumptions"
    if rename_person:
        updated["inputs"]["adults"][0]["name"] = "Different person"
    monkeypatch.setattr(planning_pages, "_changed_rows", lambda: None)
    planning_pages._refresh_profile_copy(
        data, SavedPlanningDocument.model_validate(updated)
    )
    assert data["components"][0]["source_revision"] == 2
    assert data["components"][0]["inputs"]["notes"] == "Updated assumptions"
    assert data["components"][0]["adult_mapping"] == (None if rename_person else [2, 1])
    assert data["components"][1] == plan.model_dump(mode="json")["components"][1]


def _readiness_app(plan_json):
    from financial_model.planning import PlanningScenario
    from financial_model.planning_pages import _status

    _status(PlanningScenario.model_validate_json(plan_json))


def test_plan_readiness_distinguishes_fixed_and_simulated_requirements(monkeypatch):
    import runpy

    from financial_model import forecast

    helper = runpy.run_path(str(Path(__file__).with_name("test_forecast.py")))
    plan = helper["forecast_request"]().plans[0]
    monkeypatch.setattr(
        forecast,
        "calculate_forecast",
        lambda *args, **kwargs: pytest.fail("Readiness must not calculate"),
    )
    page = AppTest.from_function(_readiness_app, args=(plan.model_dump_json(),)).run()
    assert not page.exception
    assert any(s.value == "Ready for a fixed forecast" for s in page.success)
    assert any(
        "Monte Carlo assumptions are also complete" in c.value for c in page.caption
    )
    data = plan.model_dump(mode="json")
    economy = next(
        c["inputs"] for c in data["components"] if c["inputs"]["kind"] == "economy"
    )
    economy["volatility"] = [None] * 4
    page = AppTest.from_function(
        _readiness_app, args=(PlanningScenario.model_validate(data).model_dump_json(),)
    ).run()
    assert not page.exception
    assert page.success
    assert any("all four economic volatilities" in t.value for t in page.text)
    retirement = next(
        c["inputs"] for c in data["components"] if c["inputs"]["kind"] == "retirement"
    )
    retirement["adults"][0].update(pension=None, pension_start_age=60)
    page = AppTest.from_function(
        _readiness_app, args=(PlanningScenario.model_validate(data).model_dump_json(),)
    ).run()
    assert not page.exception
    assert not page.success
    assert any(
        "Retirement:" in t.value and "pension income" in t.value for t in page.text
    )


def test_large_plan_defers_forecast_readiness_until_combinations_are_selected(
    monkeypatch,
):
    import runpy
    from uuid import uuid4

    from financial_model import forecast

    helper = runpy.run_path(str(Path(__file__).with_name("test_forecast.py")))
    plan = helper["forecast_request"]().plans[0]
    spending = next(c for c in plan.components if c.inputs.kind == "spending")
    alternatives = tuple(
        spending.model_copy(update={"source_id": uuid4()}) for _ in range(4)
    )
    plan = plan.model_copy(update={"components": plan.components + alternatives})
    monkeypatch.setattr(
        forecast,
        "forecast_issues",
        lambda *args: pytest.fail("Do not expand all forecast combinations"),
    )
    page = AppTest.from_function(_readiness_app, args=(plan.model_dump_json(),)).run()
    assert not page.exception
    assert any("select combinations in Forecast" in c.value for c in page.caption)
    assert not page.success


def test_economy_hidden_ranges_preserve_invalid_edits_and_clear_explicitly():
    page = AppTest.from_file(str(ENTRY)).run()
    tab(page, "Economy")
    press(page, "New Economy assumptions")
    field(page, "text_input", "Stock return (%)").set_value("6.25").run()
    field(page, "toggle", "Edit ranges").set_value(True).run()
    lower = next(
        w for w in page.text_input if (w.key or "").startswith("lower_equities_")
    )
    lower.set_value("invalid").run()
    next(
        w for w in page.text_input if (w.key or "").startswith("upper_equities_")
    ).set_value("8").run()
    field(page, "toggle", "Edit ranges").set_value(False).run()
    assert raw(page, "economy")["equities"] == {
        "central": "0.0625",
        "lower": "invalid",
        "upper": "0.08",
    }
    assert page.error
    tab(page, "Work")
    tab(page, "Economy")
    field(page, "toggle", "Edit ranges").set_value(True).run()
    assert (
        next(
            w for w in page.text_input if (w.key or "").startswith("lower_equities_")
        ).value
        == "invalid"
    )
    next(
        b for b in page.button if (b.key or "").startswith("clear_range_equities_")
    ).click().run()
    assert raw(page, "economy")["equities"] == {
        "central": "0.0625",
        "lower": None,
        "upper": None,
    }
    assert not page.error


@pytest.mark.integration
def test_plan_explicit_copy_updates_preserve_other_alternatives(
    database_url, monkeypatch
):
    from financial_model.storage import update_wealth_snapshot

    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    source = create_planning_document(
        database_url, SavedPlanningDocument(inputs=WorkAssumptions(name="Work"))
    )
    alternative = create_planning_document(
        database_url, SavedPlanningDocument(inputs=WorkAssumptions(name="Other work"))
    )
    wealth = create_wealth_snapshot(
        database_url,
        SavedWealthSnapshot(
            snapshot=WealthSnapshot(
                as_of=date(2026, 1, 1),
                accounts=(
                    AccountPosition(
                        label="Cash",
                        balance=100,
                        invested_in="cash",
                        tax_treatment="taxable",
                    ),
                ),
            )
        ),
    )
    plan = create_planning_document(
        database_url,
        SavedPlanningDocument(
            inputs=PlanningScenario(
                name="Plan",
                wealth=wealth,
                components=(
                    select_profile(source).model_copy(update={"adult_mapping": (2, 1)}),
                    select_profile(alternative),
                ),
            )
        ),
    )
    page = AppTest.from_file(str(ENTRY)).run()
    page.button(key=f"planning_edit_{plan.id}").click().run()
    newer = update_planning_document(
        database_url,
        source.model_copy(
            update={
                "inputs": source.inputs.model_copy(
                    update={"notes": "New benefit inputs"}
                )
            }
        ),
    )
    newer_wealth = update_wealth_snapshot(
        database_url,
        wealth.model_copy(
            update={
                "snapshot": wealth.snapshot.model_copy(
                    update={"notes": "Reviewed account basis"}
                )
            }
        ),
    )
    page.run()
    assert not page.exception
    press(page, "Update this copy")
    press(page, "Update Wealth copy")
    assert load_planning_document(database_url, plan.id) == plan
    press(page, "Save inputs")
    saved = load_planning_document(database_url, plan.id)
    assert saved.inputs.components[0].source_revision == newer.revision
    assert saved.inputs.components[0].adult_mapping == (2, 1)
    assert saved.inputs.components[1] == plan.inputs.components[1]
    assert saved.inputs.wealth == newer_wealth


@pytest.mark.integration
def test_plan_alternatives_save_combinations_and_survive_source_deletion(
    database_url, monkeypatch
):
    from financial_model.planning import InvestmentAssumptions, RetirementAssumptions

    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    choices = [
        create_planning_document(database_url, SavedPlanningDocument(inputs=m(name=n)))
        for m, n in (
            (WorkAssumptions, "Job A"),
            (WorkAssumptions, "Job B"),
            (SpendingAssumptions, "Budget A"),
            (SpendingAssumptions, "Budget B"),
            (RetirementAssumptions, "Retire"),
            (InvestmentAssumptions, "Invest"),
            (EconomicAssumptions, "Economy"),
        )
    ]
    plan = create_planning_document(
        database_url,
        SavedPlanningDocument(
            inputs=PlanningScenario(
                name="Compare",
                components=tuple(select_profile(choices[i]) for i in (0, 2, 4, 5, 6)),
            )
        ),
    )
    page = AppTest.from_file(str(ENTRY)).run()
    page.button(key=f"planning_edit_{plan.id}").click().run()
    for kind, index in (("work", 1), ("spending", 3)):
        field(page, "selectbox", f"Choose {kind.capitalize()} from library").select(
            str(choices[index].id)
        ).run()
        next(
            b for b in page.button if (b.key or "").startswith(f"alternative_{kind}_")
        ).click().run()
        assert not page.exception
    assert any("4 combinations selected" in i.value for i in page.info)
    press(page, "Save inputs")
    saved = load_planning_document(database_url, plan.id)
    assert saved.inputs.combination_count == 4
    delete_planning_document(database_url, choices[1].id, choices[1].revision)
    page.run()
    assert any("Source no longer in library" in c.value for c in page.caption)
    assert load_planning_document(database_url, plan.id) == saved
    field(page, "selectbox", "Choose Work from library").select(
        str(choices[0].id)
    ).run()
    next(
        b for b in page.button if (b.key or "").startswith("choose_work_")
    ).click().run()
    press(page, "Save inputs")
    assert load_planning_document(database_url, plan.id).inputs.combination_count == 2


@pytest.mark.integration
def test_compact_editors_save_populated_profiles_without_changing_values(
    database_url, monkeypatch
):
    from financial_model.planning import InvestmentAssumptions, RetirementAssumptions

    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    budget = {
        "amount": {"central": "78901.23", "lower": "60000", "upper": "90000"},
        "essential": "40000",
        "notes": "Included costs",
    }
    work = WorkAssumptions(name="Work").model_dump(mode="json")
    work["money_year"] = 2026
    work["adults"][0]["phases"] = [
        {
            "start": 2026,
            "status": "working",
            "employer": "Example",
            "cash_pay": "123456.78",
            "real_growth": {"central": "0.01", "lower": "0", "upper": "0.03"},
            "employee_401k": "23000.12",
            "employer_retirement_rate": "0.045",
        }
    ]
    work["adults"][0]["vesting"] = [
        {
            "start": 2026,
            "end": 2030,
            "amount": "999.123456",
            "basis": "shares",
            "notes": "Share schedule",
        }
    ]
    work["adults"][0]["options"] = [
        {
            "year": 2028,
            "award": {
                "kind": "ISO",
                "vesting_shares": 500,
                "strike": "1.123456",
                "assumed_share_value": "2.654321",
            },
        }
    ]
    profiles = [
        WorkAssumptions.model_validate(work),
        SpendingAssumptions(
            name="Spending",
            money_year=2026,
            baseline=budget,
            replacements=[
                {
                    "label": "Move",
                    "when": {"kind": "both", "offset": 1},
                    "budget": budget,
                }
            ],
            adjustments=[
                {
                    "label": "Extra",
                    "amount": {"central": "1000.01", "lower": "500", "upper": "2000"},
                    "start": {"kind": "year", "year": 2027},
                    "end": {"kind": "year", "year": 2030},
                }
            ],
        ),
        RetirementAssumptions(
            name="Retirement",
            money_year=2026,
            both_retired=budget,
            survivor=budget,
            only_adult_1={"mode": "continue"},
            only_adult_2={"mode": "replace", "budget": budget},
        ),
        InvestmentAssumptions(
            name="Investments",
            money_year=2026,
            policy="constant",
            reserved_cash="12345.67",
            checkpoints=[
                {
                    "when": {"kind": "adult_1", "offset": -5},
                    "mix": {"equities": "0.7", "fixed_income": "0.2", "cash": "0.1"},
                }
            ],
        ),
        EconomicAssumptions(
            name="Economy",
            equities={"central": "0.06789", "lower": "0.03", "upper": "0.09"},
            volatility=("0.15", "0.04", "0.01", "0.01"),
            notes="Keep this note",
        ),
    ]
    saved = [
        create_planning_document(database_url, SavedPlanningDocument(inputs=p))
        for p in profiles
    ]
    page = AppTest.from_file(str(ENTRY)).run()
    for record in saved:
        tab(page, record.inputs.kind.capitalize())
        press(page, "Edit")
        assert not page.error
        press(page, "Save inputs")
        assert load_planning_document(database_url, record.id).inputs == record.inputs
        assert page.success
