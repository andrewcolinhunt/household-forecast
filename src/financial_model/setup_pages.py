"""Household-wide setup, separate from scenario assumptions and allocations."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
import streamlit as st
from pydantic import ValidationError

from financial_model import input_forms as forms
from financial_model.schemas import Goal, Household, SavedScenario
from financial_model.storage import (
    RevisionConflictError,
    create_goal,
    delete_goal,
    load_household,
    save_household,
    update_goal,
)


def _setup_error(error: Exception) -> None:
    """Report validation and concurrency errors without exposing connection details."""
    if isinstance(error, ValidationError):
        for item in error.errors(
            include_input=False, include_context=False, include_url=False
        ):
            st.error(item["msg"])
    elif isinstance(error, (RevisionConflictError, ValueError)):
        st.error(str(error))
    else:
        st.error("Could not save this change. Your entries are still here; try again.")


def household_page(dsn: str | None, records: tuple[SavedScenario, ...]) -> None:
    """Edit the family defaults that new scenarios copy, retaining an editing buffer."""
    st.title("Annual household defaults")
    st.caption(
        "Starting details for new annual plans and planning drafts. Saved Household assumptions and existing Plans keep their own copies."
    )
    if "household_draft" not in st.session_state:
        try:
            saved = (
                load_household(dsn) if dsn else st.session_state.get("local_household")
            )
        except (psycopg.Error, ValueError):
            st.error("Household defaults could not be loaded. Try again.")
            return
        st.session_state["household_draft"] = (
            saved.model_dump(mode="json")
            if saved
            else {
                "revision": 0,
                "tax_year": 2026,
                "adults": [{}, {}],
                "dependents": [],
            }
        )
    raw = st.session_state["household_draft"]
    if raw["revision"] == 0 and records:
        choices = {str(r.id): r for r in records}
        selected = st.selectbox(
            "Copy family details from",
            list(choices),
            format_func=lambda key: choices[key].inputs.name
            or choices[key].inputs.state,
        )
        if st.button("Copy family details"):
            source = choices[selected].inputs
            st.session_state["household_draft"] = {
                "revision": 0,
                "tax_year": source.tax_year,
                "adults": [{"name": a.name, "age": a.age} for a in source.adults],
                "dependents": [d.model_dump() for d in source.dependents],
            }
            st.session_state["household_epoch"] = (
                st.session_state.get("household_epoch", 0) + 1
            )
            st.rerun()
    epoch = st.session_state.get("household_epoch", 0)
    forms.choice(
        raw,
        "tax_year",
        "Ages at year end",
        [2025, 2026],
        f"household_year_{epoch}",
        labels={2025: "2025", 2026: "2026"},
        segmented=True,
    )
    forms.table_header(("Person", "Age at year end"), (3, 1))
    for index, person in enumerate(raw["adults"]):
        name, age = st.columns((3, 1))
        with name:
            forms.text(
                person, "name", "Name", f"household_name_{index}_{epoch}", compact=True
            )
        with age:
            forms.number(
                person,
                "age",
                "Age at year end",
                f"household_age_{index}_{epoch}",
                integer=True,
                minimum=18,
                maximum=64,
                optional=True,
                compact=True,
            )
    raw.setdefault("children_text", ", ".join(str(d["age"]) for d in raw["dependents"]))
    forms.text(
        raw,
        "children_text",
        "Qualifying children's ages",
        f"household_children_{epoch}",
        help_text="Ages at year end, separated by commas. Leave blank for none.",
    )
    st.caption("Filing status: married filing jointly.")
    save, reload = st.columns(2)
    if save.button("Save household", type="primary"):
        _save_household_inputs(dsn, raw)
    if reload.button("Reload saved household"):
        st.session_state.pop("household_draft", None)
        st.session_state["household_epoch"] = epoch + 1
        st.rerun()


def _save_household_inputs(dsn: str | None, raw: dict[str, Any]) -> None:
    """Validate family details and preserve pending edits when a save conflicts."""
    try:
        data = {key: value for key, value in raw.items() if key != "children_text"}
        data["dependents"] = [
            {"age": int(value.strip())}
            for value in raw["children_text"].split(",")
            if value.strip()
        ]
        inputs = Household.model_validate(data)
        saved = (
            save_household(dsn, inputs)
            if dsn
            else inputs.model_copy(update={"revision": inputs.revision + 1})
        )
    except (ValueError, RevisionConflictError, psycopg.Error) as error:
        _setup_error(error)
        return
    st.session_state["household_draft"] = saved.model_dump(mode="json")
    if not dsn:
        st.session_state["local_household"] = saved
    st.session_state["notice"] = (
        "Household defaults saved. Existing plans keep their own details."
    )
    st.rerun()


def goals_page(dsn: str | None, records: tuple[SavedScenario, ...]) -> None:
    """Manage shared goal definitions without changing scenario allocations."""
    st.title("Goals")
    st.caption(
        "Shared savings goals. Set amounts and funding priority in Investments → Saving instructions."
    )
    with st.form("new_goal", clear_on_submit=True, border=False):
        name_column, add_column = st.columns((5, 1), vertical_alignment="bottom")
        name = name_column.text_input("New goal name", max_chars=80)
        if add_column.form_submit_button("Add goal"):
            try:
                goal = Goal(name=name)
                if dsn:
                    goal = create_goal(dsn, goal)
                st.session_state["goals"] = (*st.session_state.get("goals", ()), goal)
                st.session_state["notice"] = (
                    "Goal added. Choose it in Investments to assign savings."
                )
                st.rerun()
            except (ValueError, psycopg.Error) as error:
                _setup_error(error)
    spending_ids = {
        row.goal.id
        for record in records
        for row in record.inputs.allocation_plan.rows
        if row.cash_amount is None
    }
    spending_ids.update(
        UUID(row["goal"]["id"])
        for draft in st.session_state.get("drafts", {}).values()
        for row in draft["draft_data"].get("allocation_plan", {}).get("rows", ())
        if row["cash_amount"] is None
    )
    forms.table_header(("Goal", "Used in annual plans", ""), (3, 2, 1))
    for goal in st.session_state.get("goals", ()):
        if goal.id not in spending_ids:
            _goal_row(dsn, goal, records)
    st.subheader("Spending")
    st.caption(
        "Leftover cash after funding goals. A negative amount is an expense deficit."
    )


def _goal_row(dsn: str | None, goal: Goal, records: tuple[SavedScenario, ...]) -> None:
    """Rename one shared goal or delete it when no saved scenario references it."""
    used = sum(
        any(row.goal.id == goal.id for row in r.inputs.allocation_plan.rows)
        for r in records
    )
    draft_used = any(
        any(
            row["goal"]["id"] == str(goal.id)
            for row in draft["draft_data"].get("allocation_plan", {}).get("rows", ())
        )
        for draft in st.session_state.get("drafts", {}).values()
    )
    with st.container():
        label, usage, actions = st.columns((3, 2, 1), vertical_alignment="center")
        name = label.text_input(
            "Goal name",
            value=goal.name,
            max_chars=80,
            key=f"goal_name_{goal.id}_{goal.revision}",
            label_visibility="collapsed",
        )
        usage.caption(f"{used} saved plan{'s' if used != 1 else ''}")
        with actions, st.container(horizontal=True, gap="small"):
            change = forms.row_action("Rename", "check", f"rename_goal_{goal.id}")
            remove = forms.row_action(
                "Delete unused goal",
                "delete",
                f"delete_goal_{goal.id}",
                disabled=bool(used or draft_used),
                help_text="Remove the goal from saved annual plans and open drafts before deleting it.",
            )
        if not (change or remove):
            return
        try:
            if remove:
                if dsn:
                    delete_goal(dsn, goal)
                st.session_state["goals"] = tuple(
                    g for g in st.session_state["goals"] if g.id != goal.id
                )
            else:
                updated = Goal.model_validate(goal.model_dump() | {"name": name})
                updated = (
                    update_goal(dsn, updated)
                    if dsn
                    else updated.model_copy(update={"revision": goal.revision + 1})
                )
                st.session_state["goals"] = tuple(
                    updated if g.id == goal.id else g for g in st.session_state["goals"]
                )
            st.session_state["notice"] = (
                "Goal deleted." if remove else "Goal renamed across annual plans."
            )
            st.rerun()
        except (ValueError, RevisionConflictError, psycopg.Error) as error:
            _setup_error(error)
