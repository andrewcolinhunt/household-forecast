"""Browser-session workflows with real calculations and isolated shared storage."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from financial_model import app
from financial_model.model import evaluate_scenario
from financial_model.schemas import SavedScenario, Scenario
from financial_model.storage import create_scenario, list_scenarios, load_scenario

ENTRY = Path(__file__).parents[2] / "streamlit_app.py"


def editor(scenario: Scenario | None = None) -> AppTest:
    """Start a distinct browser session, optionally loading imported input facts."""
    page = AppTest.from_file(str(ENTRY), default_timeout=60)
    if scenario:
        page.session_state["pending_inputs"] = scenario
    page.run()
    assert not page.exception
    return page


def press(page: AppTest, label: str) -> AppTest:
    next(button for button in page.button if button.label == label).click().run()
    assert not page.exception
    return page


def widget(page: AppTest, kind: str, prefix: str):
    return next(item for item in getattr(page, kind) if item.key.startswith(prefix))


@pytest.fixture(autouse=True)
def local_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: None)


def test_main_requires_household_facts() -> None:
    page = editor()
    press(page, "Calculate")
    assert page.error
    assert page.session_state["document"] is None
    assert next(
        button for button in page.button if button.label == "Calculate and save"
    ).disabled


def test_main_calculation_and_session_comparison(scenario: Scenario) -> None:
    page = editor(scenario)
    press(page, "Calculate")
    original = page.session_state["document"]
    assert original.inputs == scenario
    press(page, "Keep these results for comparison")
    widget(page, "number_input", "adult_0_cash_pay").set_value(120000.0)
    press(page, "Calculate")
    page.selectbox(key="baseline_choice").select("session").run()
    assert not page.exception
    assert not page.error
    assert page.session_state["document"].inputs.adults[0].cash_pay == 120000
    assert [tab.label for tab in page.tabs] == ["Overview", "Cash flow", "Difference"]
    rendered = "\n".join(element.proto.body for element in page.get("html"))
    assert "Synthetic baseline (comparison)" in rendered
    assert "Synthetic baseline (current)" in rendered
    difference = next(
        element.proto.body
        for element in page.get("html")
        if 'class="fm-columns"' in element.proto.body
    )
    assert "<strong>$20,000</strong>" in difference
    assert "Cash left over" in rendered
    assert not page.dataframe
    assert page.session_state["session_baseline"] == original


@pytest.mark.parametrize("outdated", [False, True])
def test_main_comparison_rejects_mismatched_snapshots(
    scenario: Scenario, outdated: bool
) -> None:
    page = editor(scenario)
    press(page, "Calculate")
    press(page, "Keep these results for comparison")
    if outdated:
        saved = page.session_state["session_baseline"]
        page.session_state["session_baseline"] = saved.model_copy(
            update={"result": saved.result.model_copy(update={"model_revision": "old"})}
        )
    else:
        widget(page, "selectbox", "year_").select(2026)
        press(page, "Calculate")
    page.selectbox(key="baseline_choice").select("session").run()
    assert not page.exception
    assert not page.tabs
    assert page.info


def test_main_state_change_and_exemption_submit_together(scenario: Scenario) -> None:
    page = editor(scenario)
    widget(page, "selectbox", "state_").select("WA")
    widget(page, "checkbox", "adult_0_wa_cares").check()
    press(page, "Calculate")
    assert not page.error
    assert page.session_state["document"].result.taxes.wa_cares == 580


def test_main_imported_inputs_require_calculation(scenario: Scenario) -> None:
    page = editor(scenario)
    assert page.session_state["document"] is None
    press(page, "Calculate")
    assert page.session_state["document"].inputs == scenario


@pytest.mark.integration
def test_main_saves_current_form_and_successive_revisions(
    scenario: Scenario, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    page = editor(scenario)
    press(page, "Calculate")
    assert not list_scenarios(database_url)
    widget(page, "number_input", "adult_0_cash_pay").set_value(110000.0)
    press(page, "Calculate and save")
    saved = list_scenarios(database_url)[0]
    assert saved.inputs.adults[0].cash_pay == 110000
    for revision, cash in [(2, 120000), (3, 130000)]:
        widget(page, "number_input", "adult_0_cash_pay").set_value(float(cash))
        press(page, "Calculate and save")
        saved = load_scenario(database_url, saved.id)
        assert saved.revision == revision
        assert saved.inputs.adults[0].cash_pay == cash
        assert not page.warning
    assert page.success


@pytest.mark.integration
def test_main_duplicate_gets_new_identity(
    scenario: Scenario, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    page = editor(scenario)
    press(page, "Calculate and save")
    original = list_scenarios(database_url)[0]
    press(page, "Save as new scenario")
    records = list_scenarios(database_url)
    assert len(records) == 2
    assert any(
        record.id != original.id and record.inputs.name.endswith(" (copy)")
        for record in records
    )
    assert load_scenario(database_url, original.id) == original


@pytest.mark.integration
def test_main_two_sessions_conflict_keeps_draft(
    scenario: Scenario, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    saved = create_scenario(
        database_url, SavedScenario(inputs=scenario, result=evaluate_scenario(scenario))
    )
    first, second = editor(), editor()
    for page in [first, second]:
        page.selectbox(key="scenario_choice").select(str(saved.id)).run()
    widget(first, "number_input", "adult_0_cash_pay").set_value(120000.0)
    press(first, "Calculate and save")
    widget(second, "number_input", "adult_0_cash_pay").set_value(130000.0)
    press(second, "Calculate and save")
    assert any("newer saved revision" in warning.value for warning in second.warning)
    assert second.session_state["document"].inputs.adults[0].cash_pay == 130000
    assert load_scenario(database_url, saved.id).inputs.adults[0].cash_pay == 120000
    assert not second.success
    press(second, "Save as new scenario")
    assert len(list_scenarios(database_url)) == 2


@pytest.mark.integration
def test_main_failed_save_preserves_result(
    scenario: Scenario, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    page = editor(scenario)

    def unavailable(*args, **kwargs):
        message = "private connection details"
        raise psycopg.OperationalError(message)

    monkeypatch.setattr(app, "create_scenario", unavailable)
    press(page, "Calculate and save")
    assert any("Save failed" in error.value for error in page.error)
    assert all("private connection" not in error.value for error in page.error)
    assert page.session_state["document"].inputs == scenario
    assert not page.success
    assert not list_scenarios(database_url)


@pytest.mark.integration
def test_main_old_result_is_labeled_and_recalculated(
    scenario: Scenario, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    result = evaluate_scenario(scenario).model_copy(update={"model_revision": "old"})
    saved = create_scenario(database_url, SavedScenario(inputs=scenario, result=result))
    page = editor()
    page.selectbox(key="scenario_choice").select(str(saved.id)).run()
    assert any("older calculation rules" in warning.value for warning in page.warning)
    press(page, "Calculate")
    assert not page.warning
    assert load_scenario(database_url, saved.id).result.model_revision == "old"


def test_main_backup_upload_loads_new_draft(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    from io import BytesIO

    document = SavedScenario(inputs=scenario, result=evaluate_scenario(scenario))
    page = editor()
    upload = BytesIO(document.model_dump_json().encode())
    monkeypatch.setattr(app.st, "file_uploader", lambda *args, **kwargs: upload)
    page.run()
    press(page, "Load backup as new draft")
    assert page.session_state["document"] is None
    assert page.session_state["loaded_record"] is None
    assert page.session_state["draft_id"] != document.id
    assert widget(page, "text_input", "name_").value == scenario.name
    press(page, "Calculate")
    assert page.session_state["document"].inputs == scenario
    assert page.session_state["document"].id != document.id


def test_main_invalid_backup_keeps_existing_results(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    from io import BytesIO

    page = editor(scenario)
    press(page, "Calculate")
    original = page.session_state["document"]
    monkeypatch.setattr(
        app.st, "file_uploader", lambda *args, **kwargs: BytesIO(b"invalid")
    )
    page.run()
    press(page, "Load backup as new draft")
    assert page.error
    assert page.session_state["document"] == original
