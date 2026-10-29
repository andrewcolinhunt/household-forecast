"""Open and download a database belonging only to the current browser session."""

from __future__ import annotations

import sqlite3
from typing import cast

import streamlit as st

from financial_model.database import MAX_DATABASE_BYTES, SessionDatabase
from financial_model.examples import example_database


def session_database() -> SessionDatabase:
    """Create a fresh empty database for each browser session on first use."""
    if "session_database" not in st.session_state:
        st.session_state["session_database"] = SessionDatabase()
    return cast(SessionDatabase, st.session_state["session_database"])


def replace_database(data: bytes | None) -> None:
    """Validate a replacement before clearing old drafts, widgets, and selections."""
    replacement = SessionDatabase(data)
    previous = st.session_state.get("session_database")
    st.session_state.clear()
    st.session_state["session_database"] = replacement
    if previous is not None:
        previous.close()


@st.dialog("Replace this database?", dismissible=False)
def _confirm_replace(data: bytes | None) -> None:
    st.write(
        "This replaces the database and all drafts in this browser session. "
        "Download any saved changes you want to keep before continuing."
    )
    if st.button("Replace database", key="confirm_replace_database", type="primary"):
        try:
            replace_database(data)
        except (ValueError, sqlite3.Error, OSError):
            st.error(
                "This database could not be opened. Check its format and version. "
                "Your current database and drafts are unchanged."
            )
        else:
            st.rerun()
    if st.button("Cancel", key="cancel_replace_database"):
        st.session_state.pop("pending_database_replacement", None)
        st.rerun()


def database_controls() -> None:
    """Offer deliberate open/reset actions without re-importing on every rerun."""
    session_database()
    with st.sidebar.expander("Database", expanded=True):
        st.caption(
            "Your database is a temporary copy for this browser session. "
            "Uploaded files and other people's copies are never changed."
        )
        uploaded = st.file_uploader(
            "Database file",
            type=["sqlite", "sqlite3", "db"],
            key="database_upload",
            max_upload_size=MAX_DATABASE_BYTES // (1024 * 1024),
        )
        if st.button("Open database", disabled=uploaded is None, key="open_database"):
            st.session_state["pending_database_replacement"] = (
                uploaded.getvalue() if uploaded is not None else None
            )
        if st.button("New database", key="new_database"):
            st.session_state["pending_database_replacement"] = None
        st.caption("Explore a fictional household with two example Plans.")
        if st.button("Load example", key="load_example_database"):
            st.session_state["pending_database_replacement"] = example_database()
    if "pending_database_replacement" in st.session_state:
        _confirm_replace(st.session_state["pending_database_replacement"])


def database_download() -> None:
    """Build the download after page actions so it includes their latest saves."""
    with st.sidebar:
        try:
            data = session_database().export()
        except (sqlite3.Error, OSError):
            st.error("The database could not be downloaded. Try again.")
            return
        st.download_button(
            "Download database",
            data,
            file_name="household-forecast.sqlite",
            mime="application/vnd.sqlite3",
            key="download_database",
            on_click="ignore",
            icon=":material/download:",
        )
        st.caption(
            "Save each edited item, then download the database before closing or "
            "refreshing this tab. Unsaved editor drafts are not included. "
            "Send the downloaded file to share your changes; copies do not sync."
        )
