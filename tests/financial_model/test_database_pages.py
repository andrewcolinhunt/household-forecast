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
        assert list_planning_documents(one.path, "plan") == ()
        assert list_planning_documents(two.path, "plan") == ()
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


def test_example_can_be_cancelled_without_losing_saved_data_or_drafts() -> None:
    page = AppTest.from_function(_controls_app, default_timeout=60).run()
    original = page.session_state["session_database"]
    record = create_planning_document(
        original.path,
        SavedPlanningDocument(inputs=SpendingAssumptions(name="My saved spending")),
    )
    try:
        page.text_input(key="draft").set_value("Keep my draft").run()
        page.button(key="load_example_database").click().run()
        assert page.session_state["session_database"] is original
        assert list_planning_documents(original.path, "spending") == (record,)
        page.button(key="cancel_replace_database").click().run()
        assert not page.exception
        assert page.session_state["session_database"] is original
        assert page.text_input(key="draft").value == "Keep my draft"
        assert list_planning_documents(original.path, "spending") == (record,)
        assert "pending_database_replacement" not in page.session_state
    finally:
        original.close()


def test_example_loads_in_one_session_and_new_database_starts_empty() -> None:
    first = AppTest.from_file(str(ENTRY), default_timeout=60).run()
    second = AppTest.from_file(str(ENTRY), default_timeout=60).run()
    original = first.session_state["session_database"]
    other = second.session_state["session_database"]
    try:
        first.button(key="load_example_database").click().run()
        assert first.session_state["session_database"] is original
        first.button(key="confirm_replace_database").click().run()
        assert not first.exception
        example = first.session_state["session_database"]
        assert not Path(original.path).exists()
        assert len(list_planning_documents(example.path, "plan")) == 2
        assert list_planning_documents(other.path, "plan") == ()
        record = create_planning_document(
            example.path,
            SavedPlanningDocument(inputs=SpendingAssumptions(name="Edited example")),
        )
        first.run()
        assert first.session_state["session_database"] is example
        assert record in list_planning_documents(example.path, "spending")
        first.button(key="new_database").click().run()
        first.button(key="confirm_replace_database").click().run()
        assert not first.exception
        assert not Path(example.path).exists()
        assert (
            list_planning_documents(
                first.session_state["session_database"].path, "plan"
            )
            == ()
        )
    finally:
        first.session_state["session_database"].close()
        other.close()
