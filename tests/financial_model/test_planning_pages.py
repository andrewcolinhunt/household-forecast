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
    list_planning_documents,
    load_planning_document,
    update_planning_document,
)
from financial_model.scenario_pages import CATEGORIES
from financial_model.storage import create_wealth_snapshot
from financial_model.wealth import (
    AccountPosition,
    AssetPosition,
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
    for category in ("Work", "Spending", "Investments", "Economy", "Longevity"):
        tab(page, category)
        press(page, f"New {category} assumptions")
        assert page.get("download_button")
        assert next(b for b in page.button if b.label == "Save inputs").disabled
        assert not any(b.label == "Calculate" for b in page.button)
        assert any("Needs input" in c.value for c in page.caption)
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
    field(page, "text_input", "Central Equity return (%)").set_value("not a number")
    tab(page, "Longevity")
    assert raw(page, "economy")["equities"]["central"] == "not a number"
    page.switch_page("src/financial_model/pages/compare.py").run()
    page.switch_page("src/financial_model/pages/scenarios.py").run()
    assert page.session_state["scenario_category"] == "Longevity"
    tab(page, "Economy")
    assert (
        field(page, "text_input", "Central Equity return (%)").value == "not a number"
    )
    assert page.error
    assert not page.get("download_button")
    field(page, "text_input", "Central Equity return (%)").set_value("5.5").run()
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
    field(page, "number_input", "Phase start year").set_value(2026).run()
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
    press(page, "Add replacement budget")
    field(page, "selectbox", "Replacement starts").select("both").run()
    field(page, "text_input", "Central Replacement spending ($/year)").set_value(
        "80000"
    ).run()
    press(page, "Add additional expense")
    field(page, "text_input", "Additional expense label").set_value("Childcare").run()
    field(page, "text_input", "Central Additional spending ($/year)").set_value(
        "20000"
    ).run()
    field(page, "checkbox", "Specify an end date").check().run()
    assert not page.exception
    tab(page, "Investments")
    press(page, "New Investments assumptions")
    field(page, "selectbox", "Allocation policy").select("gradual").run()
    press(page, "Add allocation checkpoint")
    field(page, "checkbox", "Specify target allocation").check().run()
    for label, value in (
        ("Equities (%)", "80"),
        ("Fixed income (%)", "20"),
        ("Cash (%)", "0"),
    ):
        field(page, "text_input", label).set_value(value).run()
    assert not page.error
    tab(page, "Spending")
    assert field(page, "text_input", "Additional expense label").value == "Childcare"
    assert field(page, "selectbox", "Replacement starts").value == "both"


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
def test_all_categories_save_partial_inputs_without_calculation(
    database_url, monkeypatch
):
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    monkeypatch.setattr(
        app, "evaluate_scenario", lambda *args: pytest.fail("No annual evaluation")
    )
    page = AppTest.from_file(str(ENTRY)).run()
    for category in ("Work", "Spending", "Investments", "Economy", "Longevity"):
        tab(page, category)
        press(page, f"New {category} assumptions")
        press(page, "Save inputs")
        (saved,) = list_planning_documents(database_url, category.lower())
        assert saved.inputs.kind == category.lower()
        assert page.success
    tab(page, "Plans")
    press(page, "New combined plan")
    for kind in ("work", "spending", "investments", "economy", "longevity"):
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
    field(page, "text_input", "Central Equity return (%)").set_value("invalid").run()
    press(page, "All Economy inputs")
    latest = update_planning_document(
        database_url,
        SavedPlanningDocument(
            id=original.id, inputs=EconomicAssumptions(name="New name")
        ),
    )
    page.run()
    press(page, "Edit")
    assert field(page, "text_input", "Central Equity return (%)").value == "invalid"
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
def test_plan_household_and_wealth_bindings_save_in_plan_only(
    database_url, monkeypatch
):
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    account = AccountPosition(
        label="Synthetic cash",
        owner="Joint",
        account_type="brokerage",
        reported_value=100,
        holdings=(
            AssetPosition(
                label="Cash", value=100, asset_class="cash", instrument_type="cash"
            ),
        ),
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
    press(page, "Edit plan Household")
    field(page, "text_input", "Name").set_value("Synthetic A").run()
    field(page, "selectbox", "Work adult alignment").select((1, 2)).run()
    press(page, "Return to Plans")
    press(page, "Map plan Wealth")
    field(page, "selectbox", "Synthetic cash · reported owner: Joint").select(
        "joint"
    ).run()
    field(page, "checkbox", "Synthetic cash").check().run()
    field(page, "text_input", "Cash reserved within selected accounts ($)").set_value(
        "10"
    )
    press(page, "Return to Plans")
    press(page, "Save inputs")
    saved = load_planning_document(database_url, plan.id)
    assert saved.inputs.household.adults[0].name == "Synthetic A"
    assert saved.inputs.work_adults == (1, 2)
    assert saved.inputs.owners[0].owner == "joint"
    assert saved.inputs.portfolio_accounts == (account.id,)
    assert saved.inputs.reserved_cash == 10
    assert saved.inputs.wealth == wealth
    assert load_planning_document(database_url, work.id) == work
    press(page, "Replace Wealth")
    assert not raw(page, "plan")["owners"]
    assert not raw(page, "plan")["portfolio_accounts"]
    assert raw(page, "plan")["reserved_cash"] is None
