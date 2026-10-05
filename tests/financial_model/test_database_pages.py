"""The hosted upload/edit/download workflow uses independent session stores."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from streamlit.testing.v1 import AppTest

from financial_model import database_pages
from financial_model.database import SessionDatabase
from financial_model.planning import SavedPlanningDocument, SpendingAssumptions
from financial_model.planning_storage import (
    create_planning_document,
    list_planning_documents,
)

ENTRY = Path(__file__).parents[2] / "streamlit_app.py"


def _controls_app():
    import streamlit as st

    from financial_model.database_pages import database_controls, database_download

    database_controls()
    st.text_input("Unsaved draft", key="draft")
    database_download()


def _save_app():
    import streamlit as st

    from financial_model.database_pages import (
        database_controls,
        database_download,
        session_database,
    )
    from financial_model.planning import SavedPlanningDocument, SpendingAssumptions
    from financial_model.planning_storage import create_planning_document

    database_controls()
    if st.button("Save sample"):
        create_planning_document(
            session_database().path,
            SavedPlanningDocument(inputs=SpendingAssumptions(name="Latest save")),
        )
    database_download()


def test_download_includes_save_from_same_run(monkeypatch):
    downloads = []
    original = database_pages.st.download_button

    def capture(label, data, **kwargs):
        downloads.append(data)
        return original(label, data, **kwargs)

    monkeypatch.setattr(database_pages.st, "download_button", capture)
    page = AppTest.from_function(_save_app).run()
    next(
        button for button in page.button if button.label == "Save sample"
    ).click().run()
    assert not page.exception
    downloaded = SessionDatabase(downloads[-1])
    try:
        (record,) = list_planning_documents(downloaded.path, "spending")
        assert record.inputs.name == "Latest save"
    finally:
        downloaded.close()
        page.session_state["session_database"].close()


def test_real_app_starts_with_available_private_database(monkeypatch):
    monkeypatch.setenv("FINANCIAL_MODEL_DATABASE_URL", "must-not-be-used")
    first = AppTest.from_file(str(ENTRY), default_timeout=60).run()
    second = AppTest.from_file(str(ENTRY), default_timeout=60).run()
    assert not first.exception
    assert not second.exception
    one = first.session_state["session_database"]
    two = second.session_state["session_database"]
    try:
        assert one.path != two.path
        assert first.session_state["store_available"]
        assert any(b.label == "Download database" for b in first.get("download_button"))
    finally:
        one.close()
        two.close()


def test_open_requires_confirmation_and_clears_old_drafts(monkeypatch):
    source = SessionDatabase()
    record = create_planning_document(
        source.path,
        SavedPlanningDocument(inputs=SpendingAssumptions(name="Uploaded plan")),
    )
    data = source.export()
    source.close()
    monkeypatch.setattr(
        database_pages.st, "file_uploader", lambda *args, **kwargs: BytesIO(data)
    )
    page = AppTest.from_function(_controls_app).run()
    old = page.session_state["session_database"]
    page.text_input(key="draft").set_value("Old edits").run()
    page.session_state["forecast_saved_ids"] = {"old-household"}
    page.button(key="open_database").click().run()
    assert page.session_state["session_database"] is old
    page.button(key="confirm_replace_database").click().run()
    assert not page.exception
    current = page.session_state["session_database"]
    try:
        assert not Path(old.path).exists()
        assert page.text_input(key="draft").value == ""
        assert "forecast_saved_ids" not in page.session_state
        assert list_planning_documents(current.path, "spending") == (record,)
        page.run()
        assert page.session_state["session_database"] is current
    finally:
        current.close()


def test_invalid_upload_preserves_database_and_drafts(monkeypatch):
    monkeypatch.setattr(
        database_pages.st, "file_uploader", lambda *args, **kwargs: BytesIO(b"invalid")
    )
    page = AppTest.from_function(_controls_app).run()
    original = page.session_state["session_database"]
    try:
        page.text_input(key="draft").set_value("Keep these edits").run()
        page.button(key="open_database").click().run()
        page.button(key="confirm_replace_database").click().run()
        assert not page.exception
        assert page.session_state["session_database"] is original
        assert page.session_state["draft"] == "Keep these edits"
        assert any("unchanged" in error.value for error in page.error)
    finally:
        original.close()


def test_new_database_can_be_cancelled_and_confirmed():
    page = AppTest.from_function(_controls_app).run()
    original = page.session_state["session_database"]
    page.button(key="new_database").click().run()
    page.button(key="cancel_replace_database").click().run()
    assert page.session_state["session_database"] is original
    page.button(key="new_database").click().run()
    page.button(key="confirm_replace_database").click().run()
    assert not page.exception
    current = page.session_state["session_database"]
    assert current.path != original.path
    assert not Path(original.path).exists()
    current.close()
