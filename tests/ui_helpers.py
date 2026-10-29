"""Shared setup for UI tests without production-only testing hooks."""

from __future__ import annotations

from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest

from financial_model import app
from financial_model.schemas import Scenario


def open_annual_draft(page: AppTest, scenario: Scenario) -> AppTest:
    """Initialize an unsaved draft inside the app's normal Streamlit context."""
    main = app.main

    def initialize() -> None:
        app._set_editor(scenario)
        st.session_state["workspace_target"] = "annual"
        st.session_state["pending_category"] = "Plans"
        main()

    with patch.object(app, "main", initialize):
        return page.run()
