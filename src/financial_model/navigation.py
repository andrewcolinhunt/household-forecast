"""Shared page locations and navigation for the Scenarios workspace."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

PAGE_DIR = Path(__file__).with_name("pages")


def navigate(
    category: str, *, target: str | None = None, view: str | None = None
) -> None:
    """Request a category on the next rerun without changing any draft contents."""
    st.session_state["pending_category"] = category
    if target is not None:
        st.session_state["workspace_target"] = target
    if view is not None:
        st.session_state.setdefault("workspace_views", {})[category] = view
    st.rerun()


def category_link(
    label: str,
    category: str,
    *,
    target: str | None = None,
    view: str | None = None,
    key: str | None = None,
) -> None:
    """Render an explicit navigation action within the current editing context."""
    if st.button(label, key=key):
        navigate(category, target=target, view=view)
