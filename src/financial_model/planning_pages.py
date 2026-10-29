"""Planning library pages, category editors, and readiness checks."""

from __future__ import annotations

import streamlit as st

from financial_model import input_forms as forms
from financial_model import plan_pages, planning_forms, planning_library
from financial_model.navigation import category_link, navigate
from financial_model.planning import (
    INPUT_ADAPTER,
    Kind,
    PlanningInputs,
    PlanningScenario,
    SavedPlanningDocument,
    SpendingAssumptions,
    copy_annual_inputs,
    missing_details,
)
from financial_model.schemas import SavedScenario
from financial_model.setup_pages import _setup_error


def _timing_needs_plan(inputs: PlanningInputs, details: tuple[str, ...]) -> bool:
    """Identify Spending whose remaining review concerns its timing context."""
    return (
        isinstance(inputs, SpendingAssumptions)
        and bool(details)
        and all(detail.startswith("Spending timing unresolved:") for detail in details)
    )


def _status(inputs: PlanningInputs) -> None:
    """Show missing capture inputs and bounded Plan forecast readiness."""
    details = missing_details(inputs)
    st.caption(
        "Review timing in Plan"
        if _timing_needs_plan(inputs, details)
        else (
            f"{len(details)} items need input"
            if len(details) != 1
            else "1 item needs input"
        )
        if details
        else "Plan selections complete"
        if isinstance(inputs, PlanningScenario)
        else "Inputs entered"
    )
    if details:
        for detail in details:
            st.text(detail)
    elif isinstance(inputs, PlanningScenario):
        _forecast_readiness(inputs)


def _forecast_readiness(plan: PlanningScenario) -> None:
    """Check completed inputs without generating paths or calculating balances."""
    from financial_model.forecast import forecast_issues
    from financial_model.forecast_models import ForecastRequest, ForecastSettings

    if plan.combination_count > 4:
        st.caption(
            "Forecast readiness is checked when you select combinations in Forecast. You can compare up to four per run."
        )
        return
    request = ForecastRequest(
        plans=tuple(plan.combinations()), settings=ForecastSettings(mode="fixed")
    )
    issues = forecast_issues(request)
    if issues:
        st.caption(
            f"Forecast: {len(issues)} {'item needs' if len(issues) == 1 else 'items need'} input"
        )
        with st.container(border=True):
            for issue in issues:
                st.text(issue)
            st.caption(
                "Edit this Plan's copies in Timeline, or update them from saved library assumptions, then save the Plan. Edit family details in Household."
            )
    else:
        st.success("Ready for a fixed forecast")
    simulation_request = request.model_copy(
        update={"settings": ForecastSettings(mode="monte_carlo")}
    )
    simulation = tuple(
        issue for issue in forecast_issues(simulation_request) if issue not in issues
    )
    if simulation:
        with st.expander("For Monte Carlo simulation"):
            for detail in simulation:
                st.text(detail)
    elif not issues:
        st.caption("Monte Carlo assumptions are also complete.")


def category_page(kind: Kind, dsn: str) -> None:
    """Display a library or its active retained editor without touching other drafts."""
    records = planning_library.read_records(dsn, kind)
    if st.session_state.get("workspace_views", {}).get(
        planning_library.LABELS[kind]
    ) == "editor" and kind in st.session_state.get("planning_active", {}):
        _editor(kind, dsn, records)
        return
    st.subheader(
        "Combined plans"
        if kind == "plan"
        else f"{planning_library.LABELS[kind]} assumptions"
    )
    st.caption(
        {
            "household": "Reusable family details, including planned children. Choose a Household in a Plan, then select Spending for its costs. Residence belongs to each Plan.",
            "plan": "Combine saved Work, Spending, Retirement, Investments, and Economy assumptions with starting Wealth. Save a plan, then open Forecast to project it through retirement.",
            "work": "Pay, salary growth, contributions, and equity compensation. Retirement dates belong in Retirement.",
            "spending": "Household expenses through every stage of life, including retirement.",
            "retirement": "When each of you retires, retirement income, and how long to plan for.",
            "investments": "How you save and how your mix of stocks, bonds, and cash changes over time.",
            "economy": "Inflation and average investment returns, after fees and before tax.",
        }[kind]
    )
    with st.container(horizontal=True):
        if st.button(
            "New combined plan"
            if kind == "plan"
            else f"New {planning_library.LABELS[kind]} assumptions",
            key=f"new_{kind}",
            type="primary",
        ):
            planning_library.new_draft(planning_library.initial_inputs(kind, dsn))
            navigate(
                planning_library.LABELS[kind],
                target="plan" if kind == "plan" else "library",
                view="editor",
            )
        st.button(f"Refresh {planning_library.LABELS[kind]}", key=f"refresh_{kind}")
    if not records:
        st.info(f"No saved {planning_library.LABELS[kind]} inputs yet.")
    if records:
        forms.table_header(
            ("Plan" if kind == "plan" else "Name", "Summary", "Inputs", ""),
            (2.5, 4, 1.4, 1.5),
        )
    for record in records:
        name, summary, status, actions = st.columns(
            (2.5, 4, 1.4, 1.5), vertical_alignment="center"
        )
        name.text(record.inputs.name)
        summary.caption(
            planning_library.input_summary(record.inputs).replace("$", r"\$")
        )
        details = missing_details(record.inputs)
        with status:
            if _timing_needs_plan(record.inputs, details):
                st.caption("Review timing in Plan")
            elif details:
                st.caption(f"{len(details)} missing · Edit to complete")
            else:
                st.caption("Selections made" if kind == "plan" else "Inputs entered")
        with actions, st.container(horizontal=True, gap="small"):
            if forms.row_action("Edit", "edit", f"planning_edit_{record.id}"):
                planning_library.open_record(record)
            if forms.row_action(
                "Duplicate", "content_copy", f"planning_duplicate_{record.id}"
            ):
                planning_library.open_record(record, duplicate=True)
            if forms.row_action(
                "Delete",
                "delete",
                f"planning_delete_{record.id}",
                disabled=not st.session_state["planning_available"][kind],
            ):
                st.session_state["planning_delete"] = record
    drafts = [
        (key, draft)
        for key, draft in st.session_state.get("planning_drafts", {}).items()
        if draft["data"]["kind"] == kind and planning_library.has_unsaved_edits(draft)
    ]
    if drafts:
        with st.container(border=True):
            st.markdown("**Continue editing**")
            for draft_id, draft in drafts:
                if st.button(
                    f"Resume {draft['data'].get('name') or 'Unnamed'} · {draft_id[:8]}",
                    key=f"planning_resume_{draft_id}",
                ):
                    planning_library.activate_draft(draft_id)
                    navigate(
                        planning_library.LABELS[kind],
                        target="plan" if kind == "plan" else "library",
                        view="editor",
                    )
    planning_library.backups(kind)
    if pending := st.session_state.get("planning_delete"):
        planning_library.confirm_delete(dsn, pending)


def _editor(kind: Kind, dsn: str, records: tuple[SavedPlanningDocument, ...]) -> None:
    """Render one category and save its own validated draft only."""
    draft = planning_library.active_draft(kind)
    data, loaded = draft["data"], draft["loaded"]
    category_link(
        f"All {planning_library.LABELS[kind]} inputs",
        planning_library.LABELS[kind],
        target="library",
        view="library",
    )
    if planning_library.preserve_legacy_draft(kind, draft, records):
        return
    st.caption(
        "Combined plan"
        if kind == "plan"
        else f"{planning_library.LABELS[kind]} assumptions"
    )
    scope = planning_library.draft_scope(kind)
    planning_forms.metadata(data, scope)
    if kind == "household":
        planning_forms.household_demographics(data, scope)
    elif kind == "work":
        planning_forms.work(data, scope)
    elif kind == "spending":
        planning_forms.spending(data, scope)
    elif kind == "investments":
        planning_forms.investments(data, scope, dsn)
    elif kind == "economy":
        planning_forms.economy(data, scope)
    elif kind == "retirement":
        planning_forms.retirement(data, scope)
    else:
        plan_pages.assembly(data, scope, dsn)
    inputs = None
    try:
        inputs = INPUT_ADAPTER.validate_python(data)
    except ValueError as error:
        _setup_error(error)
    saved = loaded and inputs == loaded.inputs
    st.caption("Saved" if saved else "Unsaved changes")
    if inputs:
        _status(inputs)
    latest = next((r for r in records if loaded and r.id == loaded.id), None)
    available = st.session_state["planning_available"][kind]
    if loaded and available and (not latest or latest.revision != loaded.revision):
        st.warning(
            "These inputs changed or were deleted. Save a new copy or reload the saved version."
        )
    with st.container(horizontal=True):
        if (
            st.button(
                "Save plan" if kind == "plan" else "Save inputs",
                type="primary",
                disabled=not available or inputs is None,
            )
            and inputs
        ):
            planning_library.save_inputs(kind, dsn, inputs, new=False)
        if (
            st.button(
                "Save as new plan" if kind == "plan" else "Save as new inputs",
                disabled=not available or inputs is None,
            )
            and inputs
        ):
            planning_library.save_inputs(kind, dsn, inputs, new=True)
        if kind == "plan":
            from financial_model.navigation import PAGE_DIR

            st.page_link(str(PAGE_DIR / "forecast.py"), label="Open Forecast")
    if kind == "plan" and not saved:
        st.caption("Save this plan before selecting it in Forecast.")
    if latest:
        with st.expander("Reload saved inputs"):
            if st.button("Discard edits and reload inputs", disabled=not available):
                planning_library.new_draft(latest.inputs, latest)
                st.rerun()
    planning_library.backups(kind, inputs, loaded)


def copy_from_annual(document: SavedScenario, *, stale: bool) -> None:
    """Preview an explicit conversion and create three independent unsaved drafts."""
    with st.expander("Create assumption profiles from this annual plan"):
        st.caption(
            "Uses the last calculated inputs. Work copies one year's pay and vesting; Spending converts monthly living expenses times 12; Investments copies saving instructions. Future dates and portfolio targets still need review."
        )
        profiles = copy_annual_inputs(document)
        for inputs in profiles:
            st.text(
                f"{planning_library.LABELS[inputs.kind]}: {planning_library.input_summary(inputs)}"
            )
        if stale:
            st.info("Calculate current edits before copying these inputs.")
        if st.button("Create unsaved assumption drafts", disabled=stale):
            for inputs in profiles:
                planning_library.new_draft(inputs)
                st.session_state.setdefault("workspace_views", {})[
                    planning_library.LABELS[inputs.kind]
                ] = "editor"
            st.session_state["notice"] = (
                "Work, Spending and Investments drafts created. Review and save each separately."
            )
            navigate("Work", target="library", view="editor")
