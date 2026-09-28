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
    update_scenario,
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
    target = {
        "Annual plans": "scenarios",
        "Compare annual plans": "compare",
        "Household": "household",
        "Goals": "goals",
    }.get(page.title[0].value, "editor")
    page.switch_page(f"src/financial_model/pages/{target}.py")
    return page


def widget(page: AppTest, kind: str, prefix: str):
    return next(
        item for item in getattr(page, kind) if (item.key or "").startswith(prefix)
    )


def select_comparison(page: AppTest, keys: list[str]) -> None:
    """Choose columns through the checklist in the requested order."""
    for key in list(page.session_state["comparison_selection"]):
        page.checkbox(key=f"compare_check_{key}").uncheck().run()
    for key in keys:
        page.checkbox(key=f"compare_check_{key}").check().run()
    assert not page.exception


@pytest.fixture(autouse=True)
def local_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: None)


@pytest.mark.integration
def test_rsu_tax_toggle_preserves_drafts_saves_and_comparison(
    scenario: Scenario, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from financial_model.storage import import_backup

    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    data = scenario.model_dump(mode="json")
    data["adults"][0]["rsu_vesting"] = 30000
    page = editor(Scenario.model_validate(data))
    assert widget(page, "toggle", "rsu_taxes_from_shares").value
    press(page, "Save")
    original = list_scenarios(database_url)[0]
    assert original.result.rsu_tax_funding > 0
    widget(page, "toggle", "rsu_taxes_from_shares").set_value(False).run()
    for target in ("household", "editor"):
        page.switch_page(f"src/financial_model/pages/{target}.py").run()
    assert not widget(page, "toggle", "rsu_taxes_from_shares").value
    assert page.session_state["document"] == original
    assert load_scenario(database_url, original.id) == original
    press(page, "Calculate")
    calculated = page.session_state["document"]
    assert not calculated.inputs.rsu_taxes_from_shares
    assert calculated.result.rsu_tax_funding == 0
    assert calculated.result.retained_rsus == 30000
    assert calculated.result.cash_funded_tax == calculated.result.taxes.total
    assert calculated.result.taxes == original.result.taxes
    assert calculated.result.effective_tax_rate == original.result.effective_tax_rate
    assert (
        original.result.cash_added - calculated.result.cash_added
        == calculated.result.retained_rsus - original.result.retained_rsus
        == original.result.rsu_tax_funding
    )
    press(page, "Save")
    saved = load_scenario(database_url, original.id)
    assert not saved.inputs.rsu_taxes_from_shares
    assert not import_backup(saved.model_dump_json().encode()).rsu_taxes_from_shares

    second = AppTest.from_file(str(ENTRY), default_timeout=60).run()
    press(second, "Edit")
    assert not widget(second, "toggle", "rsu_taxes_from_shares").value
    second.switch_page("src/financial_model/pages/scenarios.py").run()
    press(second, "Duplicate")
    assert not widget(second, "toggle", "rsu_taxes_from_shares").value
    widget(second, "toggle", "rsu_taxes_from_shares").set_value(True).run()
    press(second, "Save")
    assert len(list_scenarios(database_url)) == 2
    assert load_scenario(database_url, original.id) == saved
    second.switch_page("src/financial_model/pages/compare.py").run()
    assert not second.exception
    assert any(
        "RSU taxes paid from: Cash" in choice.label for choice in second.checkbox
    )
    assert any(
        "RSU taxes paid from: Vested shares" in choice.label
        for choice in second.checkbox
    )


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
    select_comparison(page, ["snapshot", "draft"])
    assert not page.exception
    assert not page.error
    assert page.session_state["document"].inputs.adults[0].cash_pay == 120000
    assert [tab.label for tab in page.tabs] == [
        "Cash overview",
        "Equity overview",
        "Tax breakdown",
        "Goal breakdown",
    ]
    rendered = "\n".join(element.proto.body for element in page.get("html"))
    assert '<span class="fm-scenario-name">Synthetic baseline</span>' in rendered
    assert '<span class="fm-scenario-status">Last calculated draft</span>' in rendered
    assert '<span class="fm-scenario-status">Kept snapshot</span>' in rendered
    assert "$18,333</span>" in rendered
    assert 'class="fm-delta"' not in rendered
    assert "Goal allocations" in rendered
    assert "Net additions to assets" not in rendered
    assert "Total allocated to goals" not in rendered
    assert "Effective tax rate" in rendered
    assert not page.dataframe
    assert page.session_state["session_snapshot"] == original
    calculated = page.session_state["document"]
    page.radio(key="display_period").set_value("Annual").run()
    rendered = "\n".join(element.proto.body for element in page.get("html"))
    assert "$220,000</span>" in rendered
    assert "+$1,667</span>" not in rendered
    assert page.session_state["document"] == calculated
    page.switch_page("src/financial_model/pages/household.py").run()
    page.switch_page("src/financial_model/pages/compare.py").run()
    assert page.radio(key="display_period").value == "Annual"
    assert not page.exception


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
    select_comparison(page, ["snapshot", "draft"])
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
        record.id != original.id and record.inputs.name == original.inputs.name
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
@pytest.mark.parametrize("edit", [None, "employer", "invalid"])
def test_reopening_updated_scenario_refreshes_only_unchanged_drafts(
    scenario: Scenario,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    edit: str | None,
) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    saved = create_scenario(
        database_url, SavedScenario(inputs=scenario, result=evaluate_scenario(scenario))
    )
    page = AppTest.from_file(str(ENTRY), default_timeout=60).run()
    press(page, "Edit")
    if edit == "employer":
        widget(page, "text_input", "adult_0_employer").set_value("New employer").run()
    elif edit == "invalid":
        widget(page, "text_input", "children_").set_value("not an age").run()
    page.switch_page("src/financial_model/pages/scenarios.py").run()
    inputs = Scenario.model_validate(scenario.model_dump() | {"name": "New label"})
    latest = update_scenario(
        database_url,
        SavedScenario(
            id=saved.id,
            revision=saved.revision,
            inputs=inputs,
            result=evaluate_scenario(inputs),
        ),
    )
    press(page, "Edit")
    if edit is not None:
        assert page.session_state["loaded_record"] == saved
        key = "adult_0_employer" if edit == "employer" else "children_"
        value = "New employer" if edit == "employer" else "not an age"
        assert widget(page, "text_input", key).value == value
        page.run()
        assert any("newer saved version" in item.value for item in page.warning)
        assert load_scenario(database_url, saved.id) == latest
    else:
        assert widget(page, "text_input", "name_").value == "New label"
        assert page.session_state["loaded_record"] == latest
        widget(page, "text_input", "adult_0_employer").set_value("New employer")
        press(page, "Save")
        assert not page.warning
        updated = load_scenario(database_url, saved.id)
        assert updated.inputs.name == "New label"
        assert updated.inputs.adults[0].employer == "New employer"
        page.switch_page("src/financial_model/pages/scenarios.py").run()
        assert "Adult A: New employer" in "\n".join(
            e.proto.body for e in page.get("html")
        )


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
    assert next(
        b for b in page.button if b.label == "Load backup as new draft"
    ).disabled
    assert page.error
    assert page.session_state["document"] == original


def test_main_starts_with_library() -> None:
    page = AppTest.from_file(str(ENTRY)).run()
    assert not page.exception
    assert page.title[0].value == "Annual plans"
    assert not page.number_input
    page.switch_page("src/financial_model/pages/compare.py").run()
    assert page.title[0].value == "Compare annual plans"
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
    select_comparison(page, [str(first.id), str(second.id)])
    assert not page.exception
    assert [tab.label for tab in page.tabs] == [
        "Cash overview",
        "Equity overview",
        "Tax breakdown",
        "Goal breakdown",
    ]
    page.switch_page("src/financial_model/pages/scenarios.py").run()
    page.switch_page("src/financial_model/pages/compare.py").run()
    assert page.session_state["comparison_selection"] == [
        str(first.id),
        str(second.id),
    ]
    delete_scenario(database_url, second.id, second.revision)
    page.run()
    assert not page.exception
    assert page.session_state["comparison_selection"] == [str(first.id)]
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


@pytest.mark.parametrize("count", [2, 3, 4])
def test_compare_multiple_scenarios_retains_selection(
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
    select_comparison(page, ids)
    assert not page.exception
    assert not page.multiselect
    assert page.checkbox[-1].disabled == (count == 4)
    assert [tab.label for tab in page.tabs] == [
        "Cash overview",
        "Equity overview",
        "Tax breakdown",
        "Goal breakdown",
    ]
    assert not page.toggle
    assert not page.selectbox
    rendered = "\n".join(element.proto.body for element in page.get("html"))
    assert f'<span class="fm-scenario-name">Option {count - 1}</span>' in rendered
    assert 'class="fm-reference"' not in rendered
    assert 'class="fm-delta"' not in rendered
    checklist_order = [checkbox.key for checkbox in page.checkbox]
    assert not any(heading.value == "Column order" for heading in page.subheader)
    assert (
        len([b for b in page.button if b.key.startswith("compare_move_")]) == 2 * count
    )
    assert page.button(key=f"compare_move_{ids[0]}_-1").disabled
    assert page.button(key=f"compare_move_{ids[-1]}_1").disabled
    page.button(key=f"compare_move_{ids[-1]}_-1").click().run()
    ids[-2], ids[-1] = ids[-1], ids[-2]
    assert page.session_state["comparison_selection"] == ids
    for index, key in enumerate(ids, 1):
        assert f"Column {index}" in page.checkbox(key=f"compare_check_{key}").label
    tables = [e.proto.body for e in page.get("html") if "<table" in e.proto.body]
    names = {str(record.id): record.inputs.name for record in records}
    assert len(tables) == 4
    for table in tables:
        positions = [table.index(f">{names[key]}</span>") for key in ids]
        assert positions == sorted(positions)
    page.button(key=f"compare_move_{ids[0]}_1").click().run()
    ids[0], ids[1] = ids[1], ids[0]
    assert page.session_state["comparison_selection"] == ids
    assert [checkbox.key for checkbox in page.checkbox] == checklist_order
    for target in ("scenarios", "compare"):
        page.switch_page(f"src/financial_model/pages/{target}.py").run()
    assert page.session_state["comparison_selection"] == ids
    assert not page.toggle
    assert not page.selectbox
    page.checkbox(key=f"compare_check_{ids[-1]}").uncheck().run()
    assert page.session_state["comparison_selection"] == ids[:-1]
    assert all(not checkbox.disabled for checkbox in page.checkbox)
    page.checkbox(key=f"compare_check_{ids[-1]}").check().run()
    page.checkbox(key=f"compare_check_{ids[0]}").uncheck().run()
    page.checkbox(key=f"compare_check_{ids[0]}").check().run()
    assert page.session_state["comparison_selection"] == ids[1:] + ids[:1]
    assert [checkbox.key for checkbox in page.checkbox] == checklist_order
    select_comparison(page, [])
    assert not page.tabs
    assert not any(b.key.startswith("compare_move_") for b in page.button)
    assert any("Select at least two" in info.value for info in page.info)
    page.switch_page("src/financial_model/pages/scenarios.py").run()
    page.switch_page("src/financial_model/pages/compare.py").run()
    assert page.session_state["comparison_selection"] == []
    assert all(not checkbox.value for checkbox in page.checkbox)
    assert len(records) == 5


def test_compare_pending_scenario_respects_order_and_limit(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    documents = [
        SavedScenario(inputs=scenario, result=evaluate_scenario(scenario))
        for _ in range(5)
    ]
    monkeypatch.setattr(app, "_database_url", lambda: "synthetic-store")
    monkeypatch.setattr(app, "list_scenarios", lambda dsn: tuple(documents))
    page = AppTest.from_file(str(ENTRY), default_timeout=60).run()
    ids = [str(document.id) for document in documents]
    page.session_state["comparison_selection"] = ids[:4][::-1]
    page.session_state["pending_comparison"] = ids[4]
    page.switch_page("src/financial_model/pages/compare.py").run()
    assert not page.exception
    assert page.session_state["comparison_selection"] == ids[:4][::-1]
    assert page.checkbox(key=f"compare_check_{ids[4]}").disabled
    assert any("Remove one scenario" in info.value for info in page.info)
    page.checkbox(key=f"compare_check_{ids[1]}").uncheck().run()
    page.session_state["pending_comparison"] = ids[4]
    page.run()
    assert page.session_state["comparison_selection"] == [
        ids[3],
        ids[2],
        ids[0],
        ids[4],
    ]
    assert page.checkbox(key=f"compare_check_{ids[4]}").value


def test_compare_results_adds_draft_to_selection(scenario: Scenario) -> None:
    page = editor(scenario)
    press(page, "Calculate")
    press(page, "Keep snapshot")
    widget(page, "number_input", "adult_0_cash_pay").set_value(150000).run()
    press(page, "Calculate")
    press(page, "Compare results")
    assert page.title[0].value == "Compare annual plans"
    assert page.session_state["comparison_selection"] == ["draft", "snapshot"]
    assert page.tabs


@pytest.mark.integration
def test_option_award_save_preserves_taxes_and_survives_reload(
    database_url: str, scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    from financial_model.storage import import_backup

    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    baseline = evaluate_scenario(scenario)
    page = editor(scenario)
    widget(page, "checkbox", "adult_0_award_enabled").check().run()
    widget(page, "selectbox", "adult_0_award_kind").select("NSO").run()
    widget(page, "number_input", "adult_0_award_shares").set_value(10000)
    widget(page, "number_input", "adult_0_award_strike").set_value(2.0)
    press(page, "Calculate")
    assert "Not estimated" in "\n".join(e.proto.body for e in page.get("html"))
    widget(page, "number_input", "adult_0_award_value").set_value(5.0)
    press(page, "Save")
    saved = list_scenarios(database_url)[0]
    assert saved.inputs.adults[0].option_award.exercise_spread == 30000
    assert saved.result.taxes == baseline.taxes
    assert saved.result.net_saved_from_income == baseline.net_saved_from_income
    assert saved.result.cash_added == baseline.cash_added
    assert import_backup(saved.model_dump_json().encode()) == saved.inputs
    second = AppTest.from_file(str(ENTRY), default_timeout=60).run()
    press(second, "Edit")
    assert widget(second, "selectbox", "adult_0_award_kind").value == "NSO"
    assert widget(second, "number_input", "adult_0_award_shares").value == 10000
    assert widget(second, "number_input", "adult_0_award_value").value == 5.0
    assert not any("Unsaved changes" in c.value for c in second.caption)


def test_cash_shortfall_visible_in_library_results_and_comparison(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = scenario.model_dump(mode="json")
    data["living_costs"]["rent"] = 20000
    inputs = Scenario.model_validate(data)
    shortfall = SavedScenario(inputs=inputs, result=evaluate_scenario(inputs))
    other = SavedScenario(inputs=scenario, result=evaluate_scenario(scenario))
    monkeypatch.setattr(app, "_database_url", lambda: "synthetic-store")
    monkeypatch.setattr(app, "list_scenarios", lambda dsn: (shortfall, other))
    page = AppTest.from_file(str(ENTRY), default_timeout=60).run()
    assert page.metric[0].label == "Cash surplus / deficit per year"
    assert (
        page.metric[0].value
        == f"\N{MINUS SIGN}${abs(app.cash_surplus(shortfall.result)):,.0f}"
    )
    assert page.metric[1].label == "Spending per year"
    assert page.metric[1].value == app._number(app.remaining_cash(shortfall))
    assert any("Funding shortfall" in caption.value for caption in page.caption)
    press(page, "Edit")
    assert not page.warning
    rendered = "\n".join(e.proto.body for e in page.get("html"))
    assert "Spending" in rendered
    assert 'class="fm-deficit"' in rendered
    assert [tab.label for tab in page.tabs] == [
        "Assumptions",
        "Savings goals",
        "Results",
        "Cash overview",
        "Equity overview",
        "Tax breakdown",
        "Goal breakdown",
    ]
    page.switch_page("src/financial_model/pages/compare.py").run()
    assert not page.warning
    rendered = "\n".join(e.proto.body for e in page.get("html"))
    assert "Spending" in rendered
    assert 'class="fm-deficit"' in rendered


@pytest.mark.integration
def test_employer_metadata_survives_navigation_save_reload_and_copy(
    scenario: Scenario, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from financial_model.storage import import_backup

    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    baseline = evaluate_scenario(scenario)
    page = editor(scenario)
    widget(page, "text_input", "adult_0_employer").set_value("  Example & Co  ").run()
    widget(page, "checkbox", "adult_1_not_working").check().run()
    assert widget(page, "text_input", "adult_1_employer").disabled
    for target in ("scenarios", "editor"):
        page.switch_page(f"src/financial_model/pages/{target}.py").run()
    assert (
        widget(page, "text_input", "adult_0_employer").value.strip() == "Example & Co"
    )
    assert widget(page, "checkbox", "adult_1_not_working").value
    press(page, "Save")
    saved = list_scenarios(database_url)[0]
    assert saved.inputs.adults[0].employer == "Example & Co"
    assert saved.inputs.adults[1].not_working
    assert saved.inputs.adults[1].employer is None
    ignored = {"input_digest", "calculated_at"}
    assert saved.result.model_dump(exclude=ignored) == baseline.model_dump(
        exclude=ignored
    )
    assert import_backup(saved.model_dump_json().encode()) == saved.inputs
    second = AppTest.from_file(str(ENTRY), default_timeout=60).run()
    rendered = "\n".join(e.proto.body for e in second.get("html"))
    assert "Adult A: Example &amp; Co" in rendered
    assert "Adult B: Not working" in rendered
    assert '<h3 class="fm-scenario-name">Synthetic baseline</h3>' in rendered
    press(second, "Edit")
    assert widget(second, "text_input", "adult_0_employer").value == "Example & Co"
    assert widget(second, "checkbox", "adult_1_not_working").value
    assert not any("Unsaved changes" in c.value for c in second.caption)
    second.switch_page("src/financial_model/pages/scenarios.py").run()
    press(second, "Duplicate")
    assert widget(second, "text_input", "name_").value == "Synthetic baseline"
    assert widget(second, "text_input", "adult_0_employer").value == "Example & Co"
    press(second, "Save")
    assert len(list_scenarios(database_url)) == 2
    assert load_scenario(database_url, saved.id) == saved


def test_employer_changes_refresh_labels_without_changing_selection(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = []
    for employer in ("Acme", "Example"):
        data = scenario.model_dump(mode="json")
        data["adults"][0]["employer"] = employer
        inputs = Scenario.model_validate(data)
        records.append(SavedScenario(inputs=inputs, result=evaluate_scenario(inputs)))
    monkeypatch.setattr(app, "_database_url", lambda: "synthetic-store")
    monkeypatch.setattr(app, "list_scenarios", lambda dsn: tuple(records))
    page = AppTest.from_file(str(ENTRY), default_timeout=60).run()
    page.switch_page("src/financial_model/pages/compare.py").run()
    ids = [str(record.id) for record in records]
    assert page.session_state["comparison_selection"] == ids
    assert "Acme" in page.checkbox[0].label
    assert "Example" in page.checkbox[1].label
    data = records[1].inputs.model_dump(mode="json")
    data["adults"][0]["employer"] = "New employer"
    changed = Scenario.model_validate(data)
    records[1] = records[1].model_copy(
        update={"inputs": changed, "result": evaluate_scenario(changed)}
    )
    page.run()
    assert not page.exception
    assert page.session_state["comparison_selection"] == ids
    assert "New employer" in page.checkbox[1].label
    rendered = "\n".join(e.proto.body for e in page.get("html"))
    assert '<span class="fm-scenario-name">Synthetic baseline</span>' in rendered
    assert "Adult A: New employer" in rendered
    assert 'class="fm-scenario-duplicate"' not in rendered


def test_not_working_can_be_cleared_and_employer_reentered(scenario: Scenario) -> None:
    data = scenario.model_dump(mode="json")
    data["adults"][0]["employer"] = "Initial employer"
    page = editor(Scenario.model_validate(data))
    widget(page, "checkbox", "adult_0_not_working").check().run()
    press(page, "Calculate")
    adult = page.session_state["document"].inputs.adults[0]
    assert adult.not_working
    assert adult.employer is None
    assert adult.cash_pay == 100000
    widget(page, "checkbox", "adult_0_not_working").uncheck().run()
    widget(page, "text_input", "adult_0_employer").set_value("New employer").run()
    press(page, "Calculate")
    adult = page.session_state["document"].inputs.adults[0]
    assert not adult.not_working
    assert adult.employer == "New employer"
    assert page.session_state["document"].inputs.name == scenario.name


@pytest.mark.integration
def test_automatic_title_save_copy_compare_and_clear_label(
    scenario: Scenario, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    data = scenario.model_dump(mode="json")
    data["name"] = ""
    data["adults"][0]["employer"] = "Acme"
    data["adults"][1]["employer"] = "Example"
    page = editor(Scenario.model_validate(data))
    assert widget(page, "text_input", "name_").label == "Custom label (optional)"
    press(page, "Save")
    original = list_scenarios(database_url)[0]
    assert original.inputs.name == ""
    page.switch_page("src/financial_model/pages/scenarios.py").run()
    html = "\n".join(e.proto.body for e in page.get("html"))
    assert '<h3 class="fm-scenario-name">California</h3>' in html
    press(page, "Duplicate")
    assert widget(page, "text_input", "name_").value == ""
    widget(page, "number_input", "living_rent").set_value(4000.0)
    press(page, "Save")
    assert len(list_scenarios(database_url)) == 2
    page.switch_page("src/financial_model/pages/compare.py").run()
    assert "Rent: $4,000/mo" in page.checkbox[0].label
    assert "Rent: $0/mo" in page.checkbox[1].label
    selected = page.session_state["comparison_selection"]
    page.switch_page("src/financial_model/pages/editor.py").run()
    widget(page, "text_input", "name_").set_value("Higher rent")
    press(page, "Save")
    widget(page, "selectbox", "state_").select("WA").run()
    press(page, "Calculate")
    assert page.session_state["document"].inputs.name == "Higher rent"
    widget(page, "text_input", "name_").set_value("").run()
    press(page, "Save")
    html = "\n".join(e.proto.body for e in page.get("html"))
    assert '<span class="fm-scenario-name">Washington</span>' in html
    press(page, "Save as new scenario")
    assert len(list_scenarios(database_url)) == 3
    assert page.session_state["document"].inputs.name == ""
    page.switch_page("src/financial_model/pages/compare.py").run()
    assert page.session_state["comparison_selection"] == selected
    assert any("Same assumptions" in checkbox.label for checkbox in page.checkbox)
    assert load_scenario(database_url, original.id) == original


def test_allocation_units_preserve_cents_and_edits_across_navigation(scenario):
    raw = scenario.model_dump(mode="json")
    raw["allocation_plan"]["rows"][0]["cash_amount"] = "100.01"
    page = editor(Scenario.model_validate(raw))
    press(page, "Calculate")
    assert (
        str(page.session_state["document"].inputs.allocation_plan.fixed_cash)
        == "100.01"
    )
    widget(page, "radio", "allocation_unit").set_value("Annual").run()
    widget(page, "number_input", "allocation_cash_").set_value(1200.01).run()
    widget(page, "radio", "allocation_unit").set_value("Monthly").run()
    widget(page, "radio", "allocation_unit").set_value("Annual").run()
    assert widget(page, "number_input", "allocation_cash_").value == 1200.01
    press(page, "Calculate")
    assert (
        str(page.session_state["document"].inputs.allocation_plan.fixed_cash)
        == "1200.01"
    )
    page.switch_page("src/financial_model/pages/compare.py").run()
    page.switch_page("src/financial_model/pages/editor.py").run()
    assert (
        str(
            page.session_state["draft_data"]["allocation_plan"]["rows"][0][
                "cash_amount"
            ]
        )
        == "1200.01"
    )


@pytest.mark.integration
def test_goals_add_rename_save_and_reload(database_url, monkeypatch, scenario):
    from financial_model.storage import list_goals

    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    page = editor(scenario)
    widget(page, "number_input", "adult_0_cash_pay").set_value(120000).run()
    page.switch_page("src/financial_model/pages/goals.py").run()
    next(w for w in page.text_input if w.label == "New goal name").set_value("House")
    press(page, "Add goal")
    house = str(list_goals(database_url)[0].id)
    page.switch_page("src/financial_model/pages/editor.py").run()
    assert widget(page, "number_input", "adult_0_cash_pay").value == 120000
    assert not any(b.label == "Edit goals" for b in page.button)
    press(page, "Add goal to scenario")
    widget(page, "number_input", f"allocation_cash_{house}").set_value(500).run()
    press(page, "Save")
    (saved,) = list_scenarios(database_url)
    assert (
        next(
            r for r in saved.inputs.allocation_plan.rows if str(r.goal.id) == house
        ).cash_amount
        == 6000
    )
    page.switch_page("src/financial_model/pages/goals.py").run()
    widget(page, "text_input", f"goal_name_{house}").set_value("Home deposit")
    next(b for b in page.button if b.key == f"rename_goal_{house}").click().run()
    assert not page.error
    # Goal names change globally; the saved snapshot and financial facts do not.
    assert list_scenarios(database_url) == (saved,)
    page.switch_page("src/financial_model/pages/editor.py").run()
    assert (
        next(
            r
            for r in page.session_state["draft_data"]["allocation_plan"]["rows"]
            if r["goal"]["id"] == house
        )["goal"]["name"]
        == "Home deposit"
    )
    press(page, "Save")
    (updated,) = list_scenarios(database_url)
    assert (
        next(
            r for r in updated.inputs.allocation_plan.rows if str(r.goal.id) == house
        ).cash_amount
        == 6000
    )
    fresh = AppTest.from_file(str(ENTRY), default_timeout=60).run()
    press(fresh, "Edit")
    assert (
        next(
            r
            for r in fresh.session_state["draft_data"]["allocation_plan"]["rows"]
            if r["goal"]["id"] == house
        )["goal"]["name"]
        == "Home deposit"
    )


@pytest.mark.integration
def test_household_defaults_apply_only_to_new_scenarios(
    database_url, monkeypatch, scenario
):
    from financial_model.schemas import Household
    from financial_model.storage import load_household, save_household

    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    original = create_scenario(
        database_url, SavedScenario(inputs=scenario, result=evaluate_scenario(scenario))
    )
    profile = save_household(
        database_url,
        Household(
            tax_year=2026,
            adults=({"name": "Alex", "age": 32}, {"name": "Sam", "age": 37}),
        ),
    )
    page = editor()
    assert widget(page, "text_input", "adult_0_name").value == "Alex"
    widget(page, "number_input", "adult_0_cash_pay").set_value(123456).run()
    page.switch_page("src/financial_model/pages/household.py").run()
    widget(page, "text_input", "household_name_0").set_value("Alexandra").run()
    press(page, "Save household")
    assert load_household(database_url).revision == profile.revision + 1
    assert load_scenario(database_url, original.id) == original
    page.switch_page("src/financial_model/pages/editor.py").run()
    assert widget(page, "text_input", "adult_0_name").value == "Alex"
    assert widget(page, "number_input", "adult_0_cash_pay").value == 123456
    page.switch_page("src/financial_model/pages/scenarios.py").run()
    press(page, "New scenario")
    assert widget(page, "text_input", "adult_0_name").value == "Alexandra"
    assert widget(page, "selectbox", "year_").value == 2026


def test_editor_sections_keep_drafts_and_switch_to_results(scenario):
    page = editor(scenario)
    assert [t.label for t in page.tabs] == ["Assumptions", "Savings goals", "Results"]
    widget(page, "number_input", "adult_0_cash_pay").set_value(123456).run()
    # Tracking the section must not reset inputs in the other tab.
    page.session_state[f"editor_section_{page.session_state['editor_epoch']}"] = (
        "Savings goals"
    )
    page.run()
    widget(page, "number_input", "allocation_cash_").set_value(500).run()
    press(page, "Calculate")
    assert page.session_state["document"].inputs.adults[0].cash_pay == 123456
    assert page.session_state["document"].inputs.allocation_plan.fixed_cash == 6000
    assert (
        page.session_state[f"editor_section_{page.session_state['editor_epoch']}"]
        == "Results"
    )


@pytest.mark.integration
def test_removing_allocation_does_not_delete_shared_goal(
    database_url, monkeypatch, scenario
):
    from financial_model.schemas import Goal
    from financial_model.storage import create_goal, list_goals

    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    goal = create_goal(database_url, Goal(name="Travel"))
    page = editor(scenario)
    press(page, "Add goal to scenario")
    assert len(page.session_state["draft_data"]["allocation_plan"]["rows"]) == 3
    widget(page, "number_input", f"allocation_cash_{goal.id}").set_value(50).run()
    assert widget(page, "button", f"remove_allocation_{goal.id}").disabled
    widget(page, "number_input", f"allocation_cash_{goal.id}").set_value(0).run()
    widget(page, "button", f"remove_allocation_{goal.id}").click().run()
    assert len(page.session_state["draft_data"]["allocation_plan"]["rows"]) == 2
    assert list_goals(database_url) == (goal,)


def test_household_draft_survives_page_changes_and_reports_invalid_ages():
    page = editor()
    page.switch_page("src/financial_model/pages/household.py").run()
    widget(page, "text_input", "household_name_0").set_value("Alex").run()
    widget(page, "number_input", "household_age_0").set_value(32).run()
    page.switch_page("src/financial_model/pages/scenarios.py").run()
    page.switch_page("src/financial_model/pages/household.py").run()
    assert widget(page, "text_input", "household_name_0").value == "Alex"
    assert widget(page, "number_input", "household_age_0").value == 32
    press(page, "Save household")
    assert page.error


def test_goal_priority_edit_preserves_amounts_and_survives_navigation(scenario):
    from decimal import Decimal

    from financial_model.allocations import fund_cash_goals
    from financial_model.schemas import Goal, GoalAllocation
    from financial_model.storage import import_backup

    base = scenario.allocation_plan
    travel = Goal(name="Travel")
    rows = (
        base.rows[0].model_copy(update={"cash_amount": Decimal(120000)}),
        GoalAllocation(goal=travel, cash_amount=120000),
        base.rows[1],
    )
    inputs = scenario.model_copy(
        update={"allocation_plan": base.model_copy(update={"rows": rows})}
    )
    page = editor(inputs)
    assert not any(
        select.label == "Remaining cash designated for" for select in page.selectbox
    )
    widget(page, "number_input", f"allocation_cash_{travel.id}").set_value(11000).run()
    widget(page, "button", f"priority_up_{travel.id}").click().run()
    updated = page.session_state["draft_data"]["allocation_plan"]["rows"]
    assert updated[0]["goal"]["id"] == str(travel.id)
    assert Decimal(updated[0]["cash_amount"]) == 132000
    assert updated[-1]["cash_amount"] is None
    page.switch_page("src/financial_model/pages/compare.py").run()
    page.switch_page("src/financial_model/pages/editor.py").run()
    press(page, "Calculate")
    document = page.session_state["document"]
    assert document.inputs.allocation_plan.rows[0].goal.id == travel.id
    funding = fund_cash_goals(
        document.inputs.allocation_plan, document.result.cash_added
    )
    assert funding.rows[0].funded == 132000
    assert funding.rows[1].shortfall > 0
    restored = import_backup(document.model_dump_json().encode())
    assert restored.allocation_plan == document.inputs.allocation_plan


@pytest.mark.integration
def test_cash_priority_saves_and_loads_in_another_session(
    scenario, database_url, monkeypatch
):
    from decimal import Decimal

    from financial_model.allocations import fund_cash_goals
    from financial_model.schemas import Goal, GoalAllocation

    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    base = scenario.allocation_plan
    travel = Goal(name="Travel")
    rows = (
        base.rows[0].model_copy(update={"cash_amount": Decimal(120000)}),
        GoalAllocation(goal=travel, cash_amount=120000),
        base.rows[1],
    )
    inputs = scenario.model_copy(
        update={"allocation_plan": base.model_copy(update={"rows": rows})}
    )
    page = editor(inputs)
    widget(page, "button", f"priority_up_{travel.id}").click().run()
    press(page, "Save")
    (saved,) = list_scenarios(database_url)
    assert saved.inputs.allocation_plan.rows[0].goal.id == travel.id
    funding = fund_cash_goals(saved.inputs.allocation_plan, saved.result.cash_added)
    assert funding.rows[0].funded == 120000
    assert funding.rows[1].shortfall > 0
    fresh = AppTest.from_file(str(ENTRY), default_timeout=60).run()
    press(fresh, "Edit")
    assert widget(fresh, "button", f"priority_up_{travel.id}").disabled
    assert not widget(fresh, "button", f"priority_up_{base.rows[0].goal.id}").disabled
    fresh.switch_page("src/financial_model/pages/goals.py").run()
    assert not any(
        (item.key or "").startswith(f"goal_name_{base.rows[1].goal.id}")
        for item in fresh.text_input
    )
    assert any(header.value == "Spending" for header in fresh.subheader)
