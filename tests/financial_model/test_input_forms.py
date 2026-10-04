"""Shared selection controls retain values when navigation removes their widgets."""

from __future__ import annotations

from streamlit.testing.v1 import AppTest


def test_segmented_choice_retains_value_across_navigation() -> None:
    page = AppTest.from_string(
        """
import streamlit as st
from financial_model import input_forms as forms

row = st.session_state.setdefault("draft", {"status": "unknown"})
if st.checkbox("Show input", value=True, key="show"):
    forms.choice(
        row, "status", "Employment status", ("unknown", "working", "not_working"), "status",
        labels={"unknown": "Not entered", "working": "Working", "not_working": "Not working"}, segmented=True,
    )
"""
    ).run()
    assert not page.exception
    assert page.button_group[0].value == "unknown"
    assert page.button_group[0].options == ["Not entered", "Working", "Not working"]
    page.button_group[0].set_value("not_working").run()
    page.checkbox(key="show").uncheck().run()
    assert page.session_state["draft"]["status"] == "not_working"
    page.checkbox(key="show").check().run()
    assert page.button_group[0].value == "not_working"
    page.button_group[0].set_value("unknown").run()
    assert page.session_state["draft"]["status"] == "unknown"
    assert not page.exception
