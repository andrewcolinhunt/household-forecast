"""Shared household defaults page."""

from __future__ import annotations

import streamlit as st

from financial_model.app import _database_url
from financial_model.setup_pages import household_page

household_page(_database_url(), tuple(st.session_state["records"]))
