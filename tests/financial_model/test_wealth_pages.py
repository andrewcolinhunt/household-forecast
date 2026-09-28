"""Wealth collection and shared editing through Streamlit's real page runner."""

from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from financial_model import app, wealth_pages
from financial_model.schemas import Household
from financial_model.storage import (
    create_wealth_snapshot,
    delete_wealth_snapshot,
    list_wealth_snapshots,
    load_wealth_snapshot,
    update_wealth_snapshot,
)
from financial_model.wealth import AccountPosition, SavedWealthSnapshot, WealthSnapshot

ENTRY = Path(__file__).parents[2] / "streamlit_app.py"
PAGES = "src/financial_model/pages/"


@pytest.fixture(autouse=True)
def local_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: None)


def library() -> AppTest:
    page = AppTest.from_file(str(ENTRY), default_timeout=60).run()
    page.switch_page(PAGES + "wealth.py").run()
    assert not page.exception
    return page


def press(page: AppTest, label: str) -> AppTest:
    next(b for b in page.button if b.label == label).click().run()
    assert not page.exception
    # AppTest needs the destination pinned after the app's switch_page call.
    target = "wealth" if page.title[0].value == "Wealth" else "wealth_editor"
    page.switch_page(PAGES + target + ".py")
    return page


def field(page: AppTest, kind: str, label: str):
    return next(w for w in getattr(page, kind) if w.label == label)


def raw(page: AppTest) -> dict:
    return page.session_state["wealth_drafts"][page.session_state["wealth_active"]][
        "data"
    ]


def saved_example(database_url: str) -> SavedWealthSnapshot:
    return create_wealth_snapshot(
        database_url,
        SavedWealthSnapshot(
            snapshot=WealthSnapshot(
                as_of=date(2026, 9, 1),
                name="Synthetic position",
                accounts=(AccountPosition(label="Brokerage", reported_value=100),),
            )
        ),
    )


def test_wealth_local_collection_retains_invalid_edits_and_separate_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_calculation(*args, **kwargs):
        pytest.fail("Wealth must not evaluate an annual scenario")

    monkeypatch.setattr(app, "evaluate_scenario", no_calculation)
    page = press(library(), "New snapshot")
    assert page.title[0].value == "New wealth snapshot"
    assert any("No balances entered" in v.value for v in page.info)
    assert next(b for b in page.button if b.label == "Save snapshot").disabled
    press(page, "Add account")
    field(page, "text_input", "Account name").set_value("Brokerage").run()
    field(page, "text_input", "Reported account value ($)").set_value("100.01").run()
    field(page, "selectbox", "Account type").select("brokerage").run()
    field(page, "selectbox", "Tax treatment").select("taxable").run()
    press(page, "Add holding")
    field(page, "text_input", "Asset name").set_value("Fund").run()
    field(page, "selectbox", "Holding type").select("etf").run()
    field(page, "selectbox", "Asset class").select("fixed_income").run()
    field(page, "text_input", "Asset value ($)").set_value("80.00").run()
    assert not page.error
    assert any(m.value == "$100.01" for m in page.metric)
    assert raw(page)["accounts"][0]["holdings"][0]["asset_class"] == "fixed_income"
    field(page, "text_input", "Asset value ($)").set_value("bad amount").run()
    assert page.error
    page.switch_page(PAGES + "scenarios.py").run()
    page.switch_page(PAGES + "wealth.py").run()
    resume = next(b.label for b in page.button if b.label.startswith("Resume"))
    press(page, resume)
    assert field(page, "text_input", "Asset value ($)").value == "bad amount"
    assert raw(page)["accounts"][0]["reported_value"] == "100.01"
    assert not page.exception


def test_wealth_direct_assets_debts_and_removal() -> None:
    page = press(library(), "New snapshot")
    press(page, "Add direct asset")
    field(page, "text_input", "Asset value ($)").set_value("200").run()
    press(page, "Add debt")
    field(page, "text_input", "Amount owed ($)").set_value("50").run()
    assert (
        next(m.value for m in page.metric if m.label == "Net recorded position")
        == "$150.00"
    )
    press(page, "Remove direct asset")
    assert not raw(page)["direct_assets"]
    assert (
        next(m.value for m in page.metric if m.label == "Net recorded position")
        == "$-50.00"
    )
    press(page, "Remove debt")
    assert not raw(page)["debts"]
    assert any("No balances entered" in v.value for v in page.info)


def test_wealth_uses_household_names_as_copied_owner_labels() -> None:
    page = library()
    page.session_state["local_household"] = Household(
        tax_year=2026, adults=({"name": "Alex", "age": 40}, {"name": "Sam", "age": 38})
    )
    press(page, "New snapshot")
    press(page, "Add account")
    field(page, "selectbox", "Owner / borrower").select("Alex").run()
    page.session_state["local_household"] = Household(
        tax_year=2026,
        adults=({"name": "New name", "age": 40}, {"name": "Sam", "age": 38}),
    )
    page.run()
    assert field(page, "selectbox", "Owner / borrower").value == "Alex"
    assert raw(page)["accounts"][0]["owner"] == "Alex"


def test_wealth_backup_import_is_new_draft_and_invalid_upload_keeps_edits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = SavedWealthSnapshot(
        snapshot=WealthSnapshot(
            as_of=date(2026, 2, 1), name="Imported", kind="hypothetical"
        )
    )
    page = library()
    monkeypatch.setattr(
        wealth_pages.st,
        "file_uploader",
        lambda *args, **kwargs: BytesIO(document.model_dump_json().encode()),
    )
    page.run()
    press(page, "Load wealth backup as new draft")
    assert raw(page)["kind"] == "hypothetical"
    assert page.session_state["wealth_active"] != str(document.id)
    assert (
        page.session_state["wealth_drafts"][page.session_state["wealth_active"]][
            "loaded"
        ]
        is None
    )
    assert page.get("download_button")
    field(page, "text_input", "Snapshot name (optional)").set_value("My edits").run()
    monkeypatch.setattr(
        wealth_pages.st, "file_uploader", lambda *args, **kwargs: BytesIO(b"invalid")
    )
    page.run()
    assert page.error
    assert next(
        b for b in page.button if b.label == "Load wealth backup as new draft"
    ).disabled
    assert raw(page)["name"] == "My edits"


@pytest.mark.integration
def test_wealth_saves_incomplete_contents_and_rejects_overcounting(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    page = press(library(), "New snapshot")
    press(page, "Add account")
    field(page, "text_input", "Reported account value ($)").set_value("100").run()
    press(page, "Save snapshot")
    saved = list_wealth_snapshots(database_url)[0]
    assert saved.snapshot.accounts[0].reported_value == 100
    assert saved.snapshot.accounts[0].owner is None
    assert field(page, "date_input", "Financial as-of date").disabled
    press(page, "Add holding")
    field(page, "text_input", "Asset value ($)").set_value("101").run()
    assert any("holdings exceed" in e.value for e in page.error)
    assert next(b for b in page.button if b.label == "Save snapshot").disabled
    assert load_wealth_snapshot(database_url, saved.id) == saved
    field(page, "text_input", "Asset value ($)").set_value("80").run()
    press(page, "Save snapshot")
    current = load_wealth_snapshot(database_url, saved.id)
    assert current.revision == 2
    assert current.snapshot.accounts[0].unitemized == 20
    assert page.success


@pytest.mark.integration
def test_wealth_duplicate_new_date_keeps_original(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    original = saved_example(database_url)
    page = press(library(), "Duplicate")
    assert len(list_wealth_snapshots(database_url)) == 1
    assert not field(page, "date_input", "Financial as-of date").disabled
    field(page, "date_input", "Financial as-of date").set_value(date(2026, 10, 1)).run()
    field(page, "selectbox", "Snapshot kind").select("hypothetical").run()
    field(page, "text_input", "Reported account value ($)").set_value("200").run()
    press(page, "Save snapshot")
    records = list_wealth_snapshots(database_url)
    assert len(records) == 2
    assert records[0].snapshot.as_of == date(2026, 10, 1)
    assert records[0].snapshot.kind == "hypothetical"
    assert records[0].id != original.id
    assert load_wealth_snapshot(database_url, original.id) == original


@pytest.mark.integration
def test_wealth_concurrent_save_preserves_edits_and_allows_copy(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    saved = saved_example(database_url)
    first, second = press(library(), "Edit"), press(library(), "Edit")
    field(first, "text_input", "Reported account value ($)").set_value("110").run()
    press(first, "Save snapshot")
    field(second, "text_input", "Reported account value ($)").set_value("120").run()
    press(second, "Save snapshot")
    assert any("changed or was deleted" in e.value for e in second.error)
    assert raw(second)["accounts"][0]["reported_value"] == "120"
    assert load_wealth_snapshot(database_url, saved.id).snapshot.assets == 110
    press(second, "Save as new snapshot")
    assert len(list_wealth_snapshots(database_url)) == 2


@pytest.mark.integration
@pytest.mark.parametrize("edited", [False, True])
def test_wealth_reopen_refreshes_only_unchanged_drafts(
    database_url: str, monkeypatch: pytest.MonkeyPatch, edited: bool
) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    saved = saved_example(database_url)
    page = press(library(), "Edit")
    if edited:
        field(page, "text_input", "Reported account value ($)").set_value(
            "invalid"
        ).run()
    page.switch_page(PAGES + "wealth.py").run()
    changed = WealthSnapshot.model_validate(
        saved.snapshot.model_dump() | {"name": "Renamed elsewhere"}
    )
    update_wealth_snapshot(
        database_url, SavedWealthSnapshot(id=saved.id, snapshot=changed)
    )
    page.run()
    press(page, "Edit")
    if edited:
        assert raw(page)["accounts"][0]["reported_value"] == "invalid"
        assert raw(page)["name"] == "Synthetic position"
        press(page, "Discard edits and reload wealth")
    assert raw(page)["name"] == "Renamed elsewhere"
    assert raw(page)["accounts"][0]["reported_value"] == "100"


@pytest.mark.integration
def test_wealth_failed_save_keeps_draft_and_hides_credentials(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    page = press(library(), "New snapshot")
    field(page, "text_input", "Snapshot name (optional)").set_value("Keep this").run()

    def unavailable(*args, **kwargs):
        message = "private connection details"
        raise psycopg.OperationalError(message)

    monkeypatch.setattr(wealth_pages, "create_wealth_snapshot", unavailable)
    press(page, "Save snapshot")
    assert raw(page)["name"] == "Keep this"
    assert page.error
    assert all("private connection" not in e.value for e in page.error)
    assert not list_wealth_snapshots(database_url)


@pytest.mark.integration
def test_wealth_delete_confirmation_and_stale_revision(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    original = saved_example(database_url)
    page = press(library(), "Edit")
    page.switch_page(PAGES + "wealth.py").run()
    press(page, "Delete")
    press(page, "Cancel")
    assert load_wealth_snapshot(database_url, original.id) == original
    press(page, "Delete")
    updated = update_wealth_snapshot(database_url, original)
    press(page, "Delete wealth snapshot")
    assert page.error
    assert load_wealth_snapshot(database_url, original.id) == updated
    press(page, "Cancel")
    press(page, "Delete")
    press(page, "Delete wealth snapshot")
    assert not list_wealth_snapshots(database_url)
    assert not page.session_state["wealth_drafts"]
    assert "wealth_active" not in page.session_state


@pytest.mark.integration
def test_wealth_deleted_open_record_can_only_be_saved_as_new(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    saved = saved_example(database_url)
    page = press(library(), "Edit")
    delete_wealth_snapshot(database_url, saved.id, saved.revision)
    press(page, "Save snapshot")
    assert page.error
    assert not list_wealth_snapshots(database_url)
    press(page, "Save as new snapshot")
    assert list_wealth_snapshots(database_url)[0].id != saved.id
