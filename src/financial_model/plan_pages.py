"""Combined Plan selections, timelines, and household context."""

from __future__ import annotations

from functools import partial
from typing import Any

import streamlit as st

from financial_model import planning_forms, planning_library
from financial_model.navigation import category_link, navigate
from financial_model.planning import (
    INPUT_ADAPTER,
    PROFILE_KINDS,
    PlanningScenario,
    SavedPlanningDocument,
    TimeAnchor,
    plan_years,
    replace_household,
    replace_profile,
    replace_wealth,
    select_profile,
)
from financial_model.setup_pages import _setup_error


def _timeline_rows(
    data: dict[str, Any],
    components: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve a chronological review without changing the underlying inputs."""
    try:
        plan = PlanningScenario.model_validate(
            {
                "name": "Timeline",
                "household": data["household"],
                "components": [
                    row for row in components if row["inputs"]["kind"] == "retirement"
                ],
            }
        )
        retirements, ends = plan_years(plan)
        household = plan.household
    except ValueError:
        retirements = ends = (None, None)
        household = None
    rows: list[dict[str, Any]] = []

    def append(
        category: str, label: str, anchor: dict[str, Any], amount: Any = ""
    ) -> None:
        parsed = TimeAnchor.model_validate(anchor)
        year = parsed.resolve(retirements, household, ends)
        rows.append(
            {
                "Year": year,
                "Timing": planning_forms.time_label(
                    parsed.model_dump(mode="json"), household=data["household"]
                ),
                "Category": category,
                "Item": label,
                "Amount": str(amount) if amount is not None else "Unknown",
            }
        )

    for component in components:
        inputs = component["inputs"]
        kind = inputs["kind"]
        if kind == "spending":
            for expense in inputs["expenses"]:
                for level in expense["levels"]:
                    append(
                        expense["category"],
                        expense["label"]
                        + (f" · {level['label']}" if level["label"] else ""),
                        level["when"],
                        level["amount"]["central"],
                    )
                if expense["end"]:
                    append(
                        expense["category"],
                        f"{expense['label']} stops",
                        expense["end"],
                        "0",
                    )
        elif kind == "work":
            for adult in inputs["adults"]:
                for phase in adult["phases"]:
                    append(
                        "work",
                        f"{adult['name']} · {phase.get('employer') or phase['status']}",
                        {"year": phase["start"]},
                        phase["cash_pay"],
                    )
                    if phase["end"]:
                        append(
                            "work",
                            f"{adult['name']} · work phase ends",
                            {"year": phase["end"]},
                        )
                for vesting in adult["vesting"]:
                    append(
                        "work",
                        f"{adult['name']} · vesting ({vesting['basis']})",
                        {"year": vesting["start"]},
                        vesting["amount"],
                    )
                    if vesting["end"]:
                        append(
                            "work",
                            f"{adult['name']} · vesting ends",
                            {"year": vesting["end"]},
                        )
                for option in adult["options"]:
                    append(
                        "work",
                        f"{adult['name']} · option event",
                        {"year": option["year"]},
                    )
        elif kind == "retirement":
            for i, adult in enumerate(inputs["adults"]):
                append(
                    "retirement",
                    f"{adult['name']} retires",
                    {"year": adult["retirement"]["central"]},
                )
                mapping = component.get("adult_mapping")
                person = f"adult_{mapping[i]}" if mapping else None
                for field, age_field, label in (
                    ("social_security", "claiming_age", "Social Security"),
                    ("pension", "pension_start_age", "pension"),
                ):
                    if adult[field] is not None:
                        append(
                            "retirement",
                            f"{adult['name']} · {label}",
                            {"kind": "age", "person": person, "age": adult[age_field]},
                            adult[field],
                        )
                append(
                    "retirement",
                    f"{adult['name']} · final planning year",
                    {"kind": "age", "person": person, "age": adult["age"]["central"]},
                )
        elif kind == "investments":
            for checkpoint in inputs["checkpoints"]:
                append("investments", "Allocation checkpoint", checkpoint["when"])
    for residence in data["household"]["residence"]:
        append(
            "residence",
            residence["location"] or "Residence",
            {"year": residence["year"]},
        )
    return sorted(
        rows, key=lambda row: (row["Year"] is None, row["Year"] or 0, row["Item"])
    )


def _timeline_choice_label(choices: dict[str, dict[str, Any]], key: str) -> str:
    """Name the chosen Plan copy without consulting its source library."""
    return str(choices[key]["inputs"]["name"])


def _plan_timeline(data: dict[str, Any], scope: str, dsn: str) -> None:
    """Review one combination and edit its saved Plan copies through shared editors."""
    st.caption(
        "Edits here change this Plan's copies. Save the Plan to retain them. Source libraries keep their own inputs."
    )
    components = []
    for kind in PROFILE_KINDS:
        choices = {
            row["source_id"]: row
            for row in data["components"]
            if row["inputs"]["kind"] == kind
        }
        if not choices:
            continue
        selected = next(iter(choices))
        if len(choices) > 1:
            selected = st.selectbox(
                f"Timeline {planning_library.LABELS[kind]} alternative",
                tuple(choices),
                format_func=partial(_timeline_choice_label, choices),
                key=f"timeline_choice_{kind}_{scope.rsplit('_', 1)[0]}",
            )
        components.append(choices[selected])
    categories = {
        "all": "All events",
        "work": "Work",
        **planning_forms.EXPENSE_CATEGORIES,
        "retirement": "Retirement",
        "investments": "Investments",
        "residence": "Residence",
    }
    category = st.selectbox(
        "Timeline category",
        tuple(categories),
        format_func=categories.__getitem__,
        key=f"timeline_filter_{scope.rsplit('_', 1)[0]}",
    )
    try:
        rows = _timeline_rows(data, components)
    except ValueError:
        rows = []
        st.warning("Correct the timing fields below to resolve this timeline.")
    visible = [row for row in rows if category == "all" or row["Category"] == category]
    if visible:
        st.table(
            [
                {
                    **row,
                    "Year": row["Year"] or "Unresolved",
                    "Category": categories[row["Category"]],
                }
                for row in visible
            ]
        )
        st.caption(
            "Amounts are annual or one-time inputs in each profile's money year. Equity events retain their entered basis. These are planned amounts, not paid or funded totals."
        )
    else:
        st.caption(
            "No events in this view. Choose inputs in Selections or add expenses below."
        )
    editors = {
        row["inputs"]["kind"]: row
        for row in components
        if row["inputs"]["kind"] != "economy"
    }
    labels = {**planning_library.LABELS, "residence": "Household and residence"}
    available = (*editors, "residence")
    requested = (
        "spending" if category in planning_forms.EXPENSE_CATEGORIES else category
    )
    selected = (
        requested
        if requested in available
        else st.selectbox(
            "Edit inputs",
            available,
            format_func=labels.__getitem__,
            key=f"timeline_editor_{scope.rsplit('_', 1)[0]}",
        )
    )
    if selected == "residence":
        planning_forms.household_context(data, f"timeline_household_{scope}")
        return
    selection = editors[selected]
    key = f"timeline_{selection['source_id']}_{scope}"
    inputs = selection["inputs"]
    st.subheader(inputs["name"])
    if selected == "spending":
        planning_forms.spending(
            inputs,
            key,
            data["household"],
            category if category in planning_forms.EXPENSE_CATEGORIES else None,
        )
    elif selected == "work":
        planning_forms.work(inputs, key)
    elif selected == "retirement":
        planning_forms.retirement(inputs, key)
    elif selected == "investments":
        planning_forms.investments(inputs, key, dsn)


def assembly(data: dict[str, Any], scope: str, dsn: str) -> None:
    """Choose and explicitly replace self-contained source copies in a Plan."""
    from financial_model import wealth_pages
    from financial_model.wealth import SavedWealthSnapshot

    view = st.segmented_control(
        "Plan view",
        ("Selections", "Timeline"),
        default="Selections",
        required=True,
        key=f"plan_view_{scope.rsplit('_', 1)[0]}",
    )
    if view == "Timeline":
        _plan_timeline(data, scope, dsn)
        return

    st.caption(
        "Choose saved assumptions for each category. Add alternatives to compare combinations. Household and starting Wealth apply to every combination."
    )
    st.caption(
        "This plan keeps copies of your selections. After editing a source, update its copy here and save the plan."
    )
    _household_selection(data, scope, dsn)
    category_link("Edit plan Household", "Household", target="plan")
    _child_expense_warning(data)
    for kind in PROFILE_KINDS:
        records = planning_library.read_records(dsn, kind)
        selected = [c for c in data["components"] if c["inputs"]["kind"] == kind]
        with st.container(border=True):
            st.subheader(planning_library.LABELS[kind])
            current_column, choose_column = st.columns((3, 2))
            with current_column:
                st.caption("In this plan")
                if not selected:
                    st.caption("No assumptions selected")
                elif len(selected) > 1:
                    st.caption(f"Compare {len(selected)} alternatives")
                for selection in selected:
                    try:
                        inputs = INPUT_ADAPTER.validate_python(selection["inputs"])
                    except ValueError:
                        inputs = None
                    st.text(selection["inputs"]["name"])
                    st.caption(
                        planning_library.input_summary(inputs).replace("$", r"\$")
                        if inputs
                        else "Draft inputs need review in Timeline."
                    )
                    current = next(
                        (r for r in records if str(r.id) == selection["source_id"]),
                        None,
                    )
                    if st.session_state["planning_available"][kind]:
                        if current is None:
                            st.caption(
                                "Source no longer in library. This plan keeps its copy."
                            )
                        elif current.revision != selection["source_revision"]:
                            st.warning(
                                "Saved source has changed. Update this copy to use it."
                            )
                            if st.button(
                                "Update this copy",
                                key=f"refresh_{selection['source_id']}_{scope}",
                            ):
                                _refresh_profile_copy(data, current)
                    with st.container(horizontal=True):
                        if current and st.button(
                            "Edit source",
                            key=f"source_{selection['source_id']}_{scope}",
                        ):
                            planning_library.open_record(current)
                        if st.button(
                            "Remove",
                            key=f"remove_selection_{selection['source_id']}_{scope}",
                        ):
                            data["components"].remove(selection)
                            planning_library.changed_rows()
                    if inputs is not None:
                        planning_library.review_inputs(inputs, "Review copied inputs")
            with choose_column:
                choices = {str(r.id): r for r in records}
                if choices:
                    chosen = st.selectbox(
                        f"Choose {planning_library.LABELS[kind]} from library",
                        list(choices),
                        index=list(choices).index(selected[0]["source_id"])
                        if selected and selected[0]["source_id"] in choices
                        else 0,
                        format_func=partial(_profile_label, choices),
                        key=f"select_{kind}_{scope}",
                    )
                    st.caption(
                        planning_library.input_summary(choices[chosen].inputs).replace(
                            "$", r"\$"
                        )
                    )
                    with st.container(horizontal=True):
                        if st.button(
                            "Replace selection"
                            if selected
                            else "Use these assumptions",
                            key=f"choose_{kind}_{scope}",
                            disabled=not st.session_state["planning_available"][kind],
                        ):
                            _apply_replacement(data, choices[chosen])
                        if selected and st.button(
                            "Add alternative",
                            key=f"alternative_{kind}_{scope}",
                            disabled=not st.session_state["planning_available"][kind]
                            or any(c["source_id"] == chosen for c in selected),
                        ):
                            data["components"].append(
                                select_profile(choices[chosen]).model_dump(mode="json")
                            )
                            planning_library.changed_rows()
                    if len(selected) > 1:
                        st.caption(
                            "Replace selection removes the other alternatives in this category."
                        )
                else:
                    st.caption(
                        f"Save {planning_library.LABELS[kind]} assumptions to choose them here."
                    )
                    category_link(
                        f"Create {planning_library.LABELS[kind]} assumptions",
                        planning_library.LABELS[kind],
                        target="library",
                        view="library",
                    )
    wealth = wealth_pages._read(dsn)
    with st.container(border=True):
        st.subheader("Starting Wealth")
        current_column, choose_column = st.columns((3, 2))
        with current_column:
            if data["wealth"]:
                selected_wealth = SavedWealthSnapshot.model_validate(data["wealth"])
                st.text(
                    f"{selected_wealth.snapshot.title} · {selected_wealth.snapshot.as_of}"
                )
                st.text(
                    f"Net recorded wealth: ${selected_wealth.snapshot.net_recorded:,.2f}"
                    if selected_wealth.snapshot.has_balances
                    else "No balances entered"
                )
                current_wealth = next(
                    (r for r in wealth if r.id == selected_wealth.id), None
                )
                if st.session_state["wealth_store_available"] and (
                    current_wealth is None
                    or current_wealth.revision != selected_wealth.revision
                ):
                    st.caption(
                        "Source no longer in library. This plan keeps its copy."
                        if current_wealth is None
                        else "Saved source has changed. Update this copy to use it."
                    )
                    if current_wealth and st.button(
                        "Update Wealth copy", key=f"refresh_wealth_{scope}"
                    ):
                        _apply_wealth(data, current_wealth)
                if st.button("Remove Wealth selection", key=f"remove_wealth_{scope}"):
                    _apply_wealth(data, None)
            else:
                st.caption("No starting Wealth selected")
        with choose_column:
            wealth_choices = {str(r.id): r for r in wealth}
            if wealth_choices:
                chosen_wealth = st.selectbox(
                    "Choose Wealth from library",
                    list(wealth_choices),
                    index=list(wealth_choices).index(data["wealth"]["id"])
                    if data["wealth"] and data["wealth"]["id"] in wealth_choices
                    else 0,
                    format_func=lambda key: f"{wealth_choices[key].snapshot.title} · {wealth_choices[key].snapshot.as_of} · {key[:8]}",
                    key=f"select_wealth_{scope}",
                )
                candidate = wealth_choices[chosen_wealth].snapshot
                st.caption(
                    f"Net recorded wealth: ${candidate.net_recorded:,.2f}".replace(
                        "$", r"\$"
                    )
                    if candidate.has_balances
                    else "No balances entered"
                )
                if st.button(
                    "Replace Wealth" if data["wealth"] else "Choose Wealth",
                    disabled=not st.session_state["wealth_store_available"],
                ):
                    _apply_wealth(data, wealth_choices[chosen_wealth])
            else:
                st.caption("Save a Wealth snapshot to choose it here.")
                category_link(
                    "Create Wealth snapshot", "Wealth", target="library", view="library"
                )

    try:
        plan = PlanningScenario(name="Preview", components=data["components"])
    except ValueError:
        st.caption("Correct draft inputs in Timeline before previewing combinations.")
        return
    if plan.combination_count:
        st.info(
            f"{plan.combination_count} {'combination' if plan.combination_count == 1 else 'combinations'} selected. Save this plan, then select it in Forecast."
        )
        if plan.combination_count > 1:
            import itertools

            st.table(
                [
                    {
                        planning_library.LABELS[c.inputs.kind]: c.inputs.name
                        for c in case.components
                    }
                    for case in itertools.islice(plan.combinations(), 20)
                ]
            )
            if plan.combination_count > 20:
                st.caption("Showing the first 20 combinations.")


def _profile_label(choices: dict[str, SavedPlanningDocument], key: str) -> str:
    """Disambiguate same-name records by their stable identity and reviewed revision."""
    record = choices[key]
    return (
        record.inputs.name
        if sum(r.inputs.name == record.inputs.name for r in choices.values()) == 1
        else f"{record.inputs.name} · {key[:8]}"
    )


def _apply_replacement(data: dict[str, Any], document: SavedPlanningDocument) -> None:
    """Replace the selected component while retaining unrelated invalid draft fields."""
    # Validate only the structural selection operation; unrelated draft text stays raw.
    updated = replace_profile(PlanningScenario(name="Selection"), document)
    data["components"] = [
        *[
            row
            for row in data["components"]
            if row["inputs"]["kind"] != document.inputs.kind
        ],
        *updated.model_dump(mode="json")["components"],
    ]
    planning_library.changed_rows()


def _refresh_profile_copy(
    data: dict[str, Any], document: SavedPlanningDocument
) -> None:
    """Refresh one saved source while retaining alternatives and unchanged people."""
    index = next(
        i
        for i, component in enumerate(data["components"])
        if component["source_id"] == str(document.id)
    )
    previous = data["components"][index]
    replacement = select_profile(document).model_dump(mode="json")
    old_names = tuple(a["name"] for a in previous["inputs"].get("adults", ()))
    new_names = tuple(a["name"] for a in replacement["inputs"].get("adults", ()))
    if old_names and old_names == new_names:
        replacement["adult_mapping"] = previous["adult_mapping"]
    data["components"][index] = replacement
    planning_library.changed_rows()


def _apply_wealth(data: dict[str, Any], document: Any) -> None:
    """Replace starting balances without changing the selected Investments."""
    updated = replace_wealth(PlanningScenario(name="Selection"), document)
    data["wealth"] = updated.model_dump(mode="json")["wealth"]
    planning_library.changed_rows()


def _apply_household(data: dict[str, Any], document: SavedPlanningDocument) -> None:
    """Replace family facts without changing the Plan's other inputs."""
    try:
        updated = replace_household(PlanningScenario.model_validate(data), document)
    except ValueError as error:
        _setup_error(error)
        return
    copied = updated.model_dump(mode="json")
    for field in ("household", "household_source"):
        data[field] = copied[field]
    planning_library.changed_rows()


def _household_selection(data: dict[str, Any], scope: str, dsn: str) -> None:
    """Choose a reusable family and explicitly refresh its self-contained copy."""
    records = planning_library.read_records(dsn, "household")
    source = data.get("household_source")
    choices = {str(record.id): record for record in records}
    current = choices.get(source["source_id"]) if source else None
    with st.container(border=True):
        st.subheader("Household")
        household = data["household"]
        count = len(household["children"])
        st.text(" · ".join(a["name"] for a in household["adults"]))
        st.caption(f"{count} {'child' if count == 1 else 'children'} in this Plan")
        if source:
            st.caption(f"Copied from: {source['source_name']}")
            if st.session_state["planning_available"]["household"]:
                if current is None:
                    st.caption("Source no longer in library. This Plan keeps its copy.")
                elif current.revision != source["source_revision"]:
                    st.warning(
                        "Saved Household has changed. Update its copy to use it."
                    )
                    if st.button(
                        "Update household copy", key=f"household_update_{scope}"
                    ):
                        _apply_household(data, current)
            if current and st.button("Edit source", key=f"household_source_{scope}"):
                planning_library.open_record(current)
        else:
            st.caption(
                "Family details entered in this Plan; no Household source selected."
            )
        if choices:
            chosen = st.selectbox(
                "Choose Household from library",
                list(choices),
                index=list(choices).index(str(current.id)) if current else 0,
                format_func=partial(_profile_label, choices),
                key=f"select_household_{scope}",
            )
            st.caption(planning_library.input_summary(choices[chosen].inputs))
            if st.button(
                "Use this Household",
                key=f"choose_household_{scope}",
                disabled=not st.session_state["planning_available"]["household"],
            ):
                _apply_household(data, choices[chosen])
        else:
            st.caption(
                "Save Household assumptions in the library to reuse family details."
            )
        st.caption(
            "Choosing a Household preserves this Plan's residence and financial inputs."
        )


def _child_expense_warning(data: dict[str, Any]) -> None:
    """Flag children without age-linked spending while allowing aggregate budgets."""
    for selection in data["components"]:
        spending = selection["inputs"]
        if spending["kind"] != "spending":
            continue
        anchors = [
            anchor
            for expense in spending["expenses"]
            for anchor in [
                *(level["when"] for level in expense["levels"]),
                expense.get("end"),
            ]
            if anchor and anchor["kind"] == "age"
        ]
        referenced = {anchor.get("person") for anchor in anchors}
        missing = [
            child["name"]
            for child in data["household"]["children"]
            if child["id"] not in referenced
        ]
        if missing:
            st.warning(
                f"{spending['name']}: No age-linked expense refers to {', '.join(missing)}. Confirm their costs are included in this budget, or add child expenses."
            )


def plan_context(dsn: str) -> None:
    """Edit family facts and person mappings in the selected Plan."""
    if "plan" not in st.session_state.get("planning_active", {}):
        navigate("Plans", target="library", view="library")
    data = planning_library.active_draft("plan")["data"]
    scope = planning_library.draft_scope("plan")
    st.info(f"Editing combined plan: {data['name']}")
    with st.container(horizontal=True):
        category_link("Return to Plans", "Plans", target="plan", view="editor")
        category_link(
            "Household library", "Household", target="library", view="library"
        )
    _household_selection(data, scope, dsn)
    planning_forms.household_context(data, scope)
    _child_expense_warning(data)
    st.caption(
        "These edits belong to this combined plan. Return to Plans to save them."
    )
