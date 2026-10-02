"""Wealth collection and shared editing through Streamlit's real page runner."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from financial_model import app, wealth_pages
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


def go(page: AppTest, target: str) -> AppTest:
    page.run()
    page.session_state["scenario_category"] = (
        "Plans" if target == "scenarios" else "Wealth"
    )
    page.session_state["workspace_target"] = "library"
    page.session_state["workspace_views"] = {"Wealth": "library", "Plans": "library"}
    return page.switch_page(PAGES + "scenarios.py").run()


def library() -> AppTest:
    page = AppTest.from_file(str(ENTRY), default_timeout=60).run()
    go(page, "wealth")
    assert not page.exception
    return page


def press(page: AppTest, label: str) -> AppTest:
    next(b for b in page.button if b.label == label).click().run()
    assert not page.exception
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
                accounts=(AccountPosition(label="Brokerage", balance=100),),
            )
        ),
    )


def test_wealth_compact_accounts_and_mixed_edits_survive_navigation(monkeypatch):
    def no_calculation(*args, **kwargs):
        pytest.fail("Wealth must not evaluate an annual scenario")

    monkeypatch.setattr(app, "evaluate_scenario", no_calculation)
    page = press(library(), "New snapshot")
    assert any("No balances entered" in v.value for v in page.info)
    assert next(b for b in page.button if b.label == "Save snapshot").disabled
    press(page, "Add account")
    field(page, "text_input", "Account").set_value("Brokerage").run()
    field(page, "text_input", "Balance ($)").set_value("100.01").run()
    field(page, "selectbox", "Tax treatment").select("taxable").run()
    field(page, "selectbox", "Invested in").select("mixed").run()
    for label, value in (("Stocks (%)", "80"), ("Bonds (%)", "15"), ("Cash (%)", "5")):
        field(page, "text_input", label).set_value(value).run()
    assert not page.error
    assert raw(page)["accounts"][0]["mix"] == {
        "equities": "0.8",
        "fixed_income": "0.15",
        "cash": "0.05",
    }
    assert any(m.value == "$100.01" for m in page.metric)
    field(page, "text_input", "Stocks (%)").set_value("bad amount").run()
    assert page.error
    go(page, "scenarios")
    go(page, "wealth")
    press(page, next(b.label for b in page.button if b.label.startswith("Resume")))
    assert field(page, "text_input", "Stocks (%)").value == "bad amount"
    field(page, "selectbox", "Invested in").select("stocks").run()
    assert raw(page)["accounts"][0]["mix"] is None
    assert not any(w.label == "Stocks (%)" for w in page.text_input)
    assert not page.error
    field(page, "selectbox", "Invested in").select("mixed").run()
    assert field(page, "text_input", "Stocks (%)").value == ""


def test_wealth_account_debt_totals_and_removal():
    page = press(library(), "New snapshot")
    press(page, "Add account")
    field(page, "text_input", "Balance ($)").set_value("200").run()
    press(page, "Add debt")
    field(page, "text_input", "Amount owed ($)").set_value("50").run()
    assert (
        next(m.value for m in page.metric if m.label == "Net recorded wealth")
        == "$150.00"
    )
    row = raw(page)["accounts"][0]
    next(
        b for b in page.button if (b.key or "").endswith(f"{row['id']}_remove")
    ).click().run()
    assert not raw(page)["accounts"]
    assert (
        next(m.value for m in page.metric if m.label == "Net recorded wealth")
        == "$-50.00"
    )
    next(b for b in page.button if b.label == "Remove New debt").click().run()
    assert not raw(page)["debts"]
    assert any("No balances entered" in v.value for v in page.info)


def test_wealth_has_only_snapshot_and_projection_fields():
    page = press(library(), "New snapshot")
    press(page, "Add account")
    press(page, "Add debt")
    labels = {
        w.label
        for kind in ("selectbox", "text_input", "date_input", "checkbox")
        for w in getattr(page, kind)
    }
    assert not labels & {
        "Owner / borrower",
        "Account type",
        "Holding type",
        "Asset class",
        "Institution (optional)",
        "Ticker / security identifier (optional)",
        "Snapshot kind",
        "Household coverage",
    }
    assert len(page.date_input) == 1
    assert set(raw(page)["accounts"][0]) == {
        "id",
        "label",
        "balance",
        "tax_treatment",
        "invested_in",
        "mix",
    }


def test_wealth_backup_import_is_new_draft_and_invalid_upload_keeps_edits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = SavedWealthSnapshot(
        snapshot=WealthSnapshot(as_of=date(2026, 2, 1), name="Imported")
    )
    page = library()
    monkeypatch.setattr(
        wealth_pages.st,
        "file_uploader",
        lambda *args, **kwargs: BytesIO(document.model_dump_json().encode()),
    )
    page.run()
    press(page, "Load wealth backup as new draft")
    assert raw(page)["name"] == "Imported"
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
def test_wealth_saves_incomplete_inputs_and_rejects_invalid_mixed_allocation(
    database_url, monkeypatch
):
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    page = press(library(), "New snapshot")
    press(page, "Add account")
    field(page, "text_input", "Balance ($)").set_value("100").run()
    press(page, "Save snapshot")
    saved = list_wealth_snapshots(database_url)[0]
    assert saved.snapshot.accounts[0].balance == 100
    assert saved.snapshot.accounts[0].invested_in == "unknown"
    assert field(page, "date_input", "Snapshot date").disabled
    field(page, "selectbox", "Invested in").select("mixed").run()
    for label, value in (("Stocks (%)", "80"), ("Bonds (%)", "30"), ("Cash (%)", "0")):
        field(page, "text_input", label).set_value(value).run()
    assert any("100%" in e.value for e in page.error)
    assert next(b for b in page.button if b.label == "Save snapshot").disabled
    assert load_wealth_snapshot(database_url, saved.id) == saved
    field(page, "text_input", "Bonds (%)").set_value("20").run()
    press(page, "Save snapshot")
    current = load_wealth_snapshot(database_url, saved.id)
    assert current.revision == 2
    assert current.snapshot.accounts[0].mix.equities == Decimal(".8")


@pytest.mark.integration
def test_wealth_duplicate_new_date_keeps_original(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    original = saved_example(database_url)
    page = press(library(), "Duplicate")
    assert len(list_wealth_snapshots(database_url)) == 1
    assert not field(page, "date_input", "Snapshot date").disabled
    field(page, "date_input", "Snapshot date").set_value(date(2026, 10, 1)).run()
    field(page, "text_input", "Balance ($)").set_value("200").run()
    press(page, "Save snapshot")
    records = list_wealth_snapshots(database_url)
    assert len(records) == 2
    assert records[0].snapshot.as_of == date(2026, 10, 1)
    assert records[0].id != original.id
    assert load_wealth_snapshot(database_url, original.id) == original


@pytest.mark.integration
def test_wealth_concurrent_save_preserves_edits_and_allows_copy(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_database_url", lambda: database_url)
    saved = saved_example(database_url)
    first, second = press(library(), "Edit"), press(library(), "Edit")
    field(first, "text_input", "Balance ($)").set_value("110").run()
    press(first, "Save snapshot")
    field(second, "text_input", "Balance ($)").set_value("120").run()
    press(second, "Save snapshot")
    assert any("changed or was deleted" in e.value for e in second.error)
    assert raw(second)["accounts"][0]["balance"] == "120"
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
        field(page, "text_input", "Balance ($)").set_value("invalid").run()
    go(page, "wealth")
    changed = WealthSnapshot.model_validate(
        saved.snapshot.model_dump() | {"name": "Renamed elsewhere"}
    )
    update_wealth_snapshot(
        database_url, SavedWealthSnapshot(id=saved.id, snapshot=changed)
    )
    page.run()
    press(page, "Edit")
    if edited:
        assert raw(page)["accounts"][0]["balance"] == "invalid"
        assert raw(page)["name"] == "Synthetic position"
        press(page, "Discard edits and reload wealth")
    assert raw(page)["name"] == "Renamed elsewhere"
    assert raw(page)["accounts"][0]["balance"] == "100"


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
    go(page, "wealth")
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
