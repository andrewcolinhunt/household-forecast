"""Browser-session workflows with real calculations and isolated shared storage."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from financial_model import app
from financial_model.model import evaluate_scenario
from financial_model.schemas import SavedScenario, Scenario
from financial_model.storage import (
    create_scenario,
    delete_scenario,
    list_scenarios,
    load_scenario,
)

ENTRY = Path(__file__).parents[2] / "streamlit_app.py"


def editor(scenario: Scenario | None = None) -> AppTest:
    """Start a distinct browser session, optionally loading imported input facts."""
    page = AppTest.from_file(str(ENTRY), default_timeout=60)
    if scenario:
        page.session_state["pending_inputs"] = scenario
    page.run()
    if not scenario:
        press(page, "New scenario")
    page.switch_page("src/financial_model/pages/editor.py")
    assert not page.exception
    return page


def press(page: AppTest, label: str) -> AppTest:
    next(button for button in page.button if button.label == label).click().run()
    assert not page.exception
    # AppTest does not retain the destination of an in-app st.switch_page call.
    target = {"Scenarios": "scenarios", "Compare": "compare"}.get(
        page.title[0].value, "editor"
    )
    page.switch_page(f"src/financial_model/pages/{target}.py")
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
    assert next(button for button in page.button if button.label == "Save").disabled


def test_main_calculation_and_session_comparison(scenario: Scenario) -> None:
    page = editor(scenario)
    press(page, "Calculate")
    original = page.session_state["document"]
    assert original.inputs == scenario
    press(page, "Keep snapshot")
    widget(page, "number_input", "adult_0_cash_pay").set_value(120000.0)
    press(page, "Calculate")
    page.switch_page("src/financial_model/pages/compare.py").run()
    page.multiselect(key="comparison_selection").set_value(["snapshot", "draft"]).run()
    page.selectbox(key="comparison_reference").select("snapshot").run()
    assert not page.exception
    assert not page.error
    assert page.session_state["document"].inputs.adults[0].cash_pay == 120000
    assert [tab.label for tab in page.tabs] == [
        "Overview",
        "Cash flow",
        "Component differences",
    ]
    rendered = "\n".join(element.proto.body for element in page.get("html"))
    assert "Synthetic baseline (1)" in rendered
    assert "Synthetic baseline (2)" in rendered
    difference = next(
        element.proto.body
        for element in page.get("html")
        if "fm-components" in element.proto.body
    )
    assert "$20,000 more income" in difference
    assert "Cash left over" in rendered
    assert not page.dataframe
    assert page.session_state["session_snapshot"] == original


@pytest.mark.parametrize("outdated", [False, True])
def test_main_comparison_rejects_mismatched_snapshots(
    scenario: Scenario, outdated: bool
) -> None:
    page = editor(scenario)
    press(page, "Calculate")
    press(page, "Keep snapshot")
    if outdated:
        saved = page.session_state["session_snapshot"]
        page.session_state["session_snapshot"] = saved.model_copy(
            update={"result": saved.result.model_copy(update={"model_revision": "old"})}
        )
    else:
        widget(page, "selectbox", "year_").select(2026)
        press(page, "Calculate")
    page.switch_page("src/financial_model/pages/compare.py").run()
    page.multiselect(key="comparison_selection").set_value(["snapshot", "draft"]).run()
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
    press(page, "Save")
    saved = list_scenarios(database_url)[0]
    assert saved.inputs.adults[0].cash_pay == 110000
    for revision, cash in [(2, 120000), (3, 130000)]:
        widget(page, "number_input", "adult_0_cash_pay").set_value(float(cash))
        press(page, "Save")
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
    press(page, "Save")
    original = list_scenarios(database_url)[0]
    page.switch_page("src/financial_model/pages/scenarios.py").run()
    press(page, "Duplicate")
    assert len(list_scenarios(database_url)) == 1
    press(page, "Save")
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
        page.switch_page("src/financial_model/pages/scenarios.py").run()
        page.button(key=f"edit_{saved.id}").click().run()
        page.switch_page("src/financial_model/pages/editor.py")
    widget(first, "number_input", "adult_0_cash_pay").set_value(120000.0)
    press(first, "Save")
    widget(second, "number_input", "adult_0_cash_pay").set_value(130000.0)
    press(second, "Save")
    assert any("changed or was deleted" in warning.value for warning in second.warning)
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
    press(page, "Save")
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
    page.switch_page("src/financial_model/pages/scenarios.py").run()
    page.button(key=f"edit_{saved.id}").click().run()
    page.switch_page("src/financial_model/pages/editor.py")
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


def test_main_starts_with_library() -> None:
    page = AppTest.from_file(str(ENTRY)).run()
    assert not page.exception
    assert page.title[0].value == "Scenarios"
    assert not page.number_input
    page.switch_page("src/financial_model/pages/compare.py").run()
    assert page.title[0].value == "Compare"
    assert not page.number_input


def test_editor_preserves_unsubmitted_inputs_across_pages(scenario: Scenario) -> None:
    page = editor(scenario)
    press(page, "Calculate")
    original = page.session_state["document"]
    widget(page, "number_input", "adult_0_cash_pay").set_value(130000.0).run()
    widget(page, "text_input", "children_").set_value("not an age").run()
    assert any("Inputs changed" in item.value for item in page.info)
    for target in ("compare", "scenarios", "editor"):
        page.switch_page(f"src/financial_model/pages/{target}.py").run()
        assert not page.exception
    assert widget(page, "number_input", "adult_0_cash_pay").value == 130000
    assert widget(page, "text_input", "children_").value == "not an age"
    assert page.session_state["document"] == original
    press(page, "Calculate")
    assert page.error
    assert page.session_state["document"] == original


@pytest.mark.integration
def test_compare_saved_scenarios_without_opening_editor(
    scenario: Scenario, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    first = create_scenario(
        database_url, SavedScenario(inputs=scenario, result=evaluate_scenario(scenario))
    )
    inputs = Scenario.model_validate(scenario.model_dump() | {"name": "Second place"})
    second = create_scenario(
        database_url, SavedScenario(inputs=inputs, result=evaluate_scenario(inputs))
    )
    page = AppTest.from_file(str(ENTRY), default_timeout=60).run()
    page.switch_page("src/financial_model/pages/compare.py").run()
    page.multiselect(key="comparison_selection").set_value(
        [str(first.id), str(second.id)]
    ).run()
    assert not page.exception
    assert [tab.label for tab in page.tabs] == [
        "Overview",
        "Cash flow",
        "Component differences",
    ]
    page.switch_page("src/financial_model/pages/scenarios.py").run()
    page.switch_page("src/financial_model/pages/compare.py").run()
    assert page.multiselect(key="comparison_selection").value == [
        str(first.id),
        str(second.id),
    ]
    delete_scenario(database_url, second.id, second.revision)
    page.run()
    assert not page.exception
    assert page.multiselect(key="comparison_selection").value == [str(first.id)]
    assert not page.tabs


@pytest.mark.integration
def test_editor_keeps_separate_drafts_for_saved_scenarios(
    scenario: Scenario, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    first = create_scenario(
        database_url, SavedScenario(inputs=scenario, result=evaluate_scenario(scenario))
    )
    second = create_scenario(
        database_url, SavedScenario(inputs=scenario, result=evaluate_scenario(scenario))
    )
    page = AppTest.from_file(str(ENTRY), default_timeout=60).run()
    page.button(key=f"edit_{first.id}").click().run()
    page.switch_page("src/financial_model/pages/editor.py")
    widget(page, "number_input", "adult_0_cash_pay").set_value(150000.0).run()
    page.switch_page("src/financial_model/pages/scenarios.py").run()
    page.button(key=f"edit_{second.id}").click().run()
    page.switch_page("src/financial_model/pages/editor.py")
    assert widget(page, "number_input", "adult_0_cash_pay").value == 100000
    page.switch_page("src/financial_model/pages/scenarios.py").run()
    page.button(key=f"edit_{first.id}").click().run()
    page.switch_page("src/financial_model/pages/editor.py")
    assert widget(page, "number_input", "adult_0_cash_pay").value == 150000
    assert load_scenario(database_url, first.id).inputs.adults[0].cash_pay == 100000


@pytest.mark.integration
def test_delete_requires_confirmation_and_cleans_session(
    scenario: Scenario, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    page = editor(scenario)
    press(page, "Save")
    saved = list_scenarios(database_url)[0]
    press(page, "Keep snapshot")
    page.session_state["comparison_selection"] = [str(saved.id)]
    page.switch_page("src/financial_model/pages/scenarios.py").run()
    press(page, "Delete")
    assert load_scenario(database_url, saved.id) is not None
    press(page, "Cancel")
    assert load_scenario(database_url, saved.id) is not None
    press(page, "Delete")
    press(page, "Delete scenario")
    assert not list_scenarios(database_url)
    assert "document" not in page.session_state
    assert "session_snapshot" not in page.session_state
    assert not page.session_state["drafts"]
    assert page.session_state["comparison_selection"] == []
    assert page.success


@pytest.mark.integration
def test_delete_conflict_and_database_failure_preserve_record(
    scenario: Scenario, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from financial_model.storage import update_scenario

    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    saved = create_scenario(
        database_url, SavedScenario(inputs=scenario, result=evaluate_scenario(scenario))
    )
    page = AppTest.from_file(str(ENTRY), default_timeout=60).run()
    press(page, "Delete")
    changed = update_scenario(database_url, saved)
    press(page, "Delete scenario")
    assert page.warning
    assert load_scenario(database_url, saved.id) == changed
    press(page, "Cancel")
    press(page, "Delete")

    def unavailable(*args, **kwargs):
        message = "private connection details"
        raise psycopg.OperationalError(message)

    monkeypatch.setattr(app, "delete_scenario", unavailable)
    press(page, "Delete scenario")
    assert any("Delete failed" in error.value for error in page.error)
    assert all("private connection" not in error.value for error in page.error)
    assert load_scenario(database_url, saved.id) == changed


@pytest.mark.integration
def test_deleted_scenario_cannot_be_saved_back_by_another_session(
    scenario: Scenario, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    page = editor(scenario)
    press(page, "Save")
    saved = list_scenarios(database_url)[0]
    widget(page, "number_input", "adult_0_cash_pay").set_value(140000.0).run()
    delete_scenario(database_url, saved.id, saved.revision)
    page.run()
    assert any("was deleted" in warning.value for warning in page.warning)
    assert next(button for button in page.button if button.label == "Save").disabled
    press(page, "Save as new scenario")
    assert list_scenarios(database_url)[0].id != saved.id
    assert list_scenarios(database_url)[0].inputs.adults[0].cash_pay == 140000


@pytest.mark.parametrize("count", [3, 4])
def test_compare_multiple_scenarios_retains_selection_and_reference(
    scenario: Scenario,
    monkeypatch: pytest.MonkeyPatch,
    count: int,
) -> None:
    records = tuple(
        SavedScenario(
            inputs=(
                inputs := Scenario.model_validate(
                    scenario.model_dump() | {"name": f"Option {index}"}
                )
            ),
            result=evaluate_scenario(inputs),
        )
        for index in range(5)
    )
    monkeypatch.setattr(app, "_database_url", lambda: "synthetic-store")
    monkeypatch.setattr(app, "list_scenarios", lambda dsn: records)
    page = AppTest.from_file(str(ENTRY), default_timeout=60).run()
    page.switch_page("src/financial_model/pages/compare.py").run()
    ids = [str(record.id) for record in records[:count]]
    page.multiselect(key="comparison_selection").set_value(ids).run()
    assert not page.exception
    assert page.multiselect[0].proto.max_selections == 4
    assert [tab.label for tab in page.tabs] == [
        "Overview",
        "Cash flow",
        "Component differences",
    ]
    page.selectbox(key="comparison_reference").select(ids[-1]).run()
    rendered = "\n".join(element.proto.body for element in page.get("html"))
    assert f"Annual differences from Option {count - 1}" in rendered
    for target in ("scenarios", "compare"):
        page.switch_page(f"src/financial_model/pages/{target}.py").run()
    assert page.multiselect[0].value == ids
    assert page.selectbox(key="comparison_reference").value == ids[-1]
    page.multiselect[0].set_value(ids[:-1]).run()
    assert page.selectbox(key="comparison_reference").value == ids[0]
    assert len(records) == 5


def test_compare_results_adds_draft_to_selection(scenario: Scenario) -> None:
    page = editor(scenario)
    press(page, "Calculate")
    press(page, "Keep snapshot")
    widget(page, "number_input", "adult_0_cash_pay").set_value(150000).run()
    press(page, "Calculate")
    press(page, "Compare results")
    assert page.title[0].value == "Compare"
    assert page.multiselect[0].value == ["draft", "snapshot"]
    assert page.tabs
