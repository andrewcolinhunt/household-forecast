"""Scenarios workspace routing and category-owned annual input editing."""

from __future__ import annotations

from typing import Any, cast

import streamlit as st

from financial_model import input_forms as forms
from financial_model.planning import Kind
from financial_model.schemas import default_allocation_plan

CATEGORIES = (
    "Plans",
    "Household",
    "Wealth",
    "Work",
    "Spending",
    "Retirement",
    "Investments",
    "Economy",
    "Goals",
)


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


def _annual_buffer() -> dict[str, Any]:
    """Ensure an incomplete annual draft has its existing zero/default sections."""
    from financial_model import app

    if "editor_epoch" not in st.session_state:
        app._set_editor(None)
    data = cast(dict[str, Any], st.session_state["draft_data"])
    data.setdefault("adults", [{}, {}])
    data.setdefault("state", "WA")
    data.setdefault("tax_year", 2026)
    data.setdefault("living_costs", {})
    data.setdefault(
        "allocation_plan", default_allocation_plan().model_dump(mode="json")
    )
    return data


def _annual_category(category: str, dsn: str | None) -> None:
    """Edit only the active concern in one retained annual Scenario buffer."""
    from financial_model import app

    data = _annual_buffer()
    app._read_goal_catalogue(dsn)
    catalogue = {str(g.id): g for g in st.session_state.get("goals", ())}
    for row in data["allocation_plan"]["rows"]:
        if row["goal"]["id"] in catalogue:
            row["goal"] = catalogue[row["goal"]["id"]].model_dump(mode="json")
    st.caption(f"Editing annual plan: {data.get('name') or 'New annual plan'}")
    loaded = st.session_state.get("loaded_record")
    latest = next(
        (r for r in st.session_state["records"] if loaded and r.id == loaded.id), None
    )
    if loaded and st.session_state["store_available"] and latest is None:
        st.warning("This annual plan was deleted. Save a new copy to keep this draft.")
    elif loaded and latest and loaded.revision != latest.revision:
        st.warning(
            "This annual plan has a newer saved version. Save a new copy or reload."
        )
    with st.container(horizontal=True):
        if category != "Plans":
            category_link("Return to Plans", "Plans")
        category_link(
            "All annual plans"
            if category == "Plans"
            else f"All {category} assumptions",
            category,
            target="library",
            view="library",
            key="leave_annual_context",
        )
    scope = f"annual_{st.session_state['editor_epoch']}"
    if category == "Plans":
        _annual_assembly(data, scope, dsn)
    elif category == "Household":
        ages = st.session_state.setdefault("annual_children_buffer", {})
        ages["text"] = st.session_state["draft_ages"]
        forms.annual_household(data, ages, scope)
        st.session_state["draft_ages"] = ages["text"]
    elif category == "Work":
        st.subheader("Compensation")
        st.caption("Enter each person's pay and contributions for this tax year.")
        forms.rsu_funding(data, scope, annual=True)
        for i, column in enumerate(st.columns(2)):
            with column:
                adult = data["adults"][i]
                st.subheader(adult.get("name") or f"Adult {i + 1}")
                forms.compensation(adult, f"adult_{i}_{scope}", annual=True)
                forms.annual_options(adult, f"adult_{i}_{scope}")
    elif category == "Spending":
        st.subheader("Living expenses")
        st.caption(
            "Enter your monthly budget, then the childcare and renter details used for tax credits."
        )
        forms.living_costs(data["living_costs"], scope)
    elif category == "Investments":
        st.subheader("Saving instructions")
        changed = forms.saving_instructions(
            data["allocation_plan"], scope, st.session_state.get("goals", ())
        )
        category_link("Manage household goals", "Goals")
        if changed:
            st.session_state["editor_epoch"] += 1
            app._remember_draft()
            st.rerun()
    app._remember_draft()


def _annual_assembly(data: dict[str, Any], scope: str, dsn: str | None) -> None:
    """Assemble an annual case and preserve its explicit calculate/save/result actions."""
    from financial_model import app, planning_pages

    st.subheader("Annual plan")
    st.caption(
        "Compare one tax year of income, taxes, spending, and savings. Use a combined plan to forecast through retirement."
    )
    name, year = st.columns((3, 1))
    with name:
        forms.text(data, "name", "Annual plan name (optional)", f"name_{scope}")
    with year:
        forms.choice(
            data,
            "tax_year",
            "Tax year",
            [2025, 2026],
            f"year_{scope}",
            labels={2025: "2025", 2026: "2026"},
            segmented=True,
        )
    st.markdown("**Plan inputs**")
    for sections in (
        (
            ("Household", "People, ages, and state of residence"),
            ("Work", "Pay, equity compensation, and 401(k) contributions"),
        ),
        (
            ("Spending", "Monthly expenses and tax-credit eligibility"),
            ("Investments", "How cash and retained shares are assigned to goals"),
        ),
    ):
        for column, (category, description) in zip(
            st.columns(2), sections, strict=True
        ):
            with column, st.container(border=True):
                st.markdown(f"**{category}**")
                st.caption(description)
                category_link(f"Edit {category}", category)
    forms.text(data, "notes", "Notes (optional)", f"notes_{scope}", multiline=True)
    forms.check(
        data,
        "assumptions_confirmed",
        "My household fits the supported assumptions",
        f"confirmed_{scope}",
    )
    app._assumptions()
    app._remember_draft()
    loaded = st.session_state.get("loaded_record")
    latest = next(
        (r for r in st.session_state["records"] if loaded and r.id == loaded.id), None
    )
    unsaved, stale = app._draft_status()
    st.caption("Unsaved changes" if unsaved else "Saved")
    with st.container(horizontal=True):
        calculate = st.button("Calculate")
        save = st.button(
            "Save",
            type="primary",
            disabled=not st.session_state["store_available"]
            or bool(loaded and not latest),
        )
        copy = (
            st.button(
                "Save as new annual plan",
                disabled=not st.session_state["store_available"],
            )
            if loaded
            else False
        )
    if calculate or save or copy:
        app._submit(
            data,
            st.session_state["draft_ages"],
            "copy" if copy else "save" if save else "calculate",
            dsn,
        )
    if latest:
        with st.expander("Reload saved version"):
            if st.button(
                "Discard edits and reload",
                disabled=not st.session_state["store_available"],
            ):
                app._set_editor(latest.inputs, latest)
                st.rerun()
    document = st.session_state.get("document")
    if document:
        if stale:
            st.info("Inputs changed. Calculate to update these results.")
        app._results(document)
        planning_pages.copy_from_annual(document, stale=stale)
    else:
        st.caption("Calculate to see one-year results.")
    app._backups(document)


def workspace() -> None:
    """Render only the open category, retaining the category and target across Compare."""
    from financial_model import app, planning_pages, setup_pages, wealth_pages

    forms.compact_styles()

    # Children's raw text also needs capture when its widget changes with navigation.
    if (
        st.session_state.get("workspace_target") == "annual"
        and "annual_children_buffer" in st.session_state
    ):
        st.session_state["draft_ages"] = st.session_state["annual_children_buffer"][
            "text"
        ]
    if pending := st.session_state.pop("pending_category", None):
        st.session_state["scenario_category"] = pending
    tabs = st.tabs(CATEGORIES, key="scenario_category", on_change="rerun")
    category = next(
        name for name, tab in zip(CATEGORIES, tabs, strict=True) if tab.open
    )
    dsn = app._database_url()
    target = st.session_state.get("workspace_target", "library")
    with tabs[CATEGORIES.index(category)]:
        if target == "annual" and category in {
            "Plans",
            "Household",
            "Work",
            "Spending",
            "Investments",
        }:
            st.session_state["records"] = app._read_store(dsn)
            _annual_category(category, dsn)
        elif target == "plan" and category == "Household":
            planning_pages.plan_context(category, dsn)
        elif category == "Household":
            st.session_state["records"] = app._read_store(dsn)
            setup_pages.household_page(dsn, tuple(st.session_state["records"]))
        elif category == "Goals":
            app._read_goal_catalogue(dsn)
            st.session_state["records"] = app._read_store(dsn)
            setup_pages.goals_page(dsn, tuple(st.session_state["records"]))
        elif category == "Wealth":
            if st.session_state.get("workspace_views", {}).get("Wealth") == "editor":
                wealth_pages.wealth_editor_page(dsn)
            else:
                wealth_pages.wealth_page(dsn)
        else:
            planning_pages.category_page(
                cast(Kind, "plan" if category == "Plans" else category.lower()), dsn
            )
            if (
                category == "Plans"
                and st.session_state.get("workspace_views", {}).get("Plans") != "editor"
            ):
                st.divider()
                st.session_state["records"] = app._read_store(dsn)
                app._read_goal_catalogue(dsn)
                app.scenarios_page()
