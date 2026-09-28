"""Streamlit editor for shared annual household scenarios."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import psycopg
import streamlit as st
from pydantic import ValidationError
from streamlit.delta_generator import DeltaGenerator

from financial_model.allocations import fund_cash_goals
from financial_model.comparison import (
    Period,
    _number,
    cash_flow_html,
    cash_surplus,
    equity_html,
    goal_breakdown_html,
    period_divisor,
    remaining_cash,
    tax_html,
)
from financial_model.model import evaluate_scenario
from financial_model.scenario_display import (
    STATE_NAMES,
    ScenarioDisplay,
    scenario_displays,
)
from financial_model.schemas import (
    MODEL_REVISION,
    AllocationPlan,
    Goal,
    SavedScenario,
    Scenario,
    ScenarioResult,
    default_allocation_plan,
    dollars,
)
from financial_model.storage import (
    RevisionConflictError,
    create_scenario,
    delete_scenario,
    import_backup,
    list_goals,
    list_scenarios,
    load_household,
    resolve_goal_conflicts,
    update_scenario,
)
from financial_model.taxes import CORE_VERSION, CORRECTION_VERSION, ENGINE_VERSION

PAGE_DIR = Path(__file__).with_name("pages")
SCENARIOS_PAGE = PAGE_DIR / "scenarios.py"
EDITOR_PAGE = PAGE_DIR / "editor.py"
COMPARE_PAGE = PAGE_DIR / "compare.py"
HOUSEHOLD_PAGE = PAGE_DIR / "household.py"
GOALS_PAGE = PAGE_DIR / "goals.py"
WEALTH_PAGE = PAGE_DIR / "wealth.py"
WEALTH_EDITOR_PAGE = PAGE_DIR / "wealth_editor.py"
DRAFT_FIELDS = ("draft_data", "draft_ages", "loaded_record", "document", "draft_id")


def _database_url() -> str | None:
    """Read server-held configuration without displaying credentials."""
    if configured := os.environ.get("FINANCIAL_MODEL_DATABASE_URL"):
        return configured
    try:
        return str(st.secrets["database_url"])
    except (KeyError, FileNotFoundError):
        return None


def _read_store(dsn: str | None) -> tuple[SavedScenario, ...]:
    """Keep the editor usable if the shared store is temporarily unavailable."""
    st.session_state["store_available"] = bool(dsn)
    if not dsn:
        return ()
    try:
        records = list_scenarios(dsn)
        st.session_state["last_records"] = records
        return records
    except (psycopg.Error, ValidationError, ValueError):
        st.session_state["store_available"] = False
        st.error(
            "Saved scenarios could not be read. Your draft is still available; try Refresh."
        )
        return tuple(st.session_state.get("last_records", ()))


def _set_editor(inputs: Scenario | None, record: SavedScenario | None = None) -> None:
    """Replace the active editing buffer with validated inputs or a blank draft."""
    st.session_state["editor_epoch"] = st.session_state.get("editor_epoch", 0) + 1
    data = inputs.model_dump(mode="json") if inputs else _household_defaults()
    st.session_state["draft_data"] = data
    st.session_state["draft_ages"] = ", ".join(
        str(child["age"]) for child in data.get("dependents", ())
    )
    st.session_state["loaded_record"] = record
    st.session_state["document"] = record
    st.session_state["draft_id"] = record.id if record else uuid4()
    _remember_draft()


def _household_defaults() -> dict[str, Any]:
    """Copy shared family facts only when starting a new scenario."""
    dsn = _database_url()
    try:
        household = (
            load_household(dsn) if dsn else st.session_state.get("local_household")
        )
    except (psycopg.Error, ValueError):
        st.warning(
            "Household defaults could not be loaded. Enter the family details for this draft."
        )
        return {}
    return household.scenario_defaults() if household else {}


def _remember_draft() -> None:
    """Retain raw edits separately from widgets that disappear on page changes."""
    drafts = st.session_state.setdefault("drafts", {})
    drafts[str(st.session_state["draft_id"])] = {
        key: st.session_state[key] for key in DRAFT_FIELDS
    }


def _open_editor(
    record: SavedScenario | None = None, *, duplicate: bool = False
) -> None:
    """Open a saved scenario's existing draft or start a distinct new draft."""
    cached = (
        st.session_state.get("drafts", {}).get(str(record.id))
        if record and not duplicate
        else None
    )
    if cached and record and cached["loaded_record"].revision != record.revision:
        try:
            if (
                _parse_inputs(cached["draft_data"], cached["draft_ages"])
                == cached["loaded_record"].inputs
            ):
                cached = None
        except ValueError:
            pass  # Invalid edits must survive updates to the saved scenario too.
    if cached:
        st.session_state.update(cached)
        st.session_state["editor_epoch"] = st.session_state.get("editor_epoch", 0) + 1
    else:
        inputs = record.inputs if record else None
        _set_editor(inputs, None if duplicate else record)
    st.switch_page(str(EDITOR_PAGE))


def _key(name: str) -> str:
    """Scope widget values to the current loaded draft."""
    return f"{name}_{st.session_state['editor_epoch']}"


def _money_input(
    label: str, value: Any, key: str, *, help_text: str | None = None
) -> float:
    """Render a dollar input with explicit units and stable widget identity."""
    return float(
        st.number_input(
            label,
            min_value=0.0,
            value=float(value or 0),
            step=100.0,
            format="%.2f",
            key=_key(key),
            help=help_text,
        )
    )


def _adult_inputs(index: int, data: dict[str, Any], state: str) -> dict[str, Any]:
    """Collect compensation and saving inputs for one of the two adults."""
    prefix = f"adult_{index}"
    name = st.text_input(
        "Name", value=data.get("name", f"Adult {index + 1}"), key=_key(f"{prefix}_name")
    )
    not_working = st.checkbox(
        "Not working in this scenario",
        value=data.get("not_working", False),
        key=_key(f"{prefix}_not_working"),
        help="Does not change compensation amounts. Set those separately below.",
    )
    employer = st.text_input(
        "Employer",
        value=data.get("employer") or "",
        max_chars=80,
        disabled=not_working,
        placeholder="Employer name",
        key=_key(f"{prefix}_employer"),
        help="Use a short name for scenario headings. Leave blank if unknown.",
    )
    age = st.number_input(
        "Age at year end",
        min_value=18,
        max_value=64,
        value=data.get("age"),
        key=_key(f"{prefix}_age"),
    )
    amounts = {
        field: _money_input(
            label,
            data.get(field),
            f"{prefix}_{field}",
            help_text={
                "rsu_vesting": "Assumes shares are delivered at vesting and enough can be sold to pay vesting taxes. The remaining shares stay separate from cash.",
                "employee_401k": "Traditional pretax contributions. Roth contributions are not modeled.",
            }.get(field),
        )
        for field, label in [
            ("cash_pay", "Annual salary and bonus ($)"),
            ("rsu_vesting", "Annual RSU value at vesting ($)"),
            ("employee_401k", "Annual employee 401(k) contributions ($)"),
        ]
    }
    rates = {}
    for field, label, default in [
        (
            "employer_retirement_rate",
            "Employer 401(k) contributions (% of salary and bonus)",
            "0",
        ),
    ]:
        percent = st.number_input(
            label,
            min_value=0.0,
            max_value=100.0,
            value=float(Decimal(data.get(field, default)) * 100),
            step=1.0,
            key=_key(f"{prefix}_{field}"),
            help="Employer contribution as a percentage of cash salary and bonus.",
        )
        rates[field] = str(Decimal(str(percent)) / 100)
    exempt = st.checkbox(
        "Exempt from WA Cares",
        value=data.get("wa_cares_exempt", False),
        key=_key(f"{prefix}_wa_cares_exempt"),
    )
    return {
        "name": name,
        "employer": None if not_working else employer.strip() or None,
        "not_working": not_working,
        "age": age,
        **amounts,
        **rates,
        "wa_cares_exempt": exempt if state == "WA" else False,
        "options": _option_inputs(prefix, data.get("options", {})),
        "option_award": _option_award_inputs(prefix, data.get("option_award")),
    }


def _option_award_inputs(
    prefix: str, data: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Record annual option vesting without treating it as cash or an exercise."""
    with st.expander("Stock option award"):
        if not st.checkbox(
            "Include an option award",
            value=data is not None,
            key=_key(f"{prefix}_award_enabled"),
        ):
            return None
        data = data or {}
        st.caption(
            "Record this year's vesting, not the full multiyear grant. Award estimates do not enter cash, tax, or saving totals."
        )
        kind = st.selectbox(
            "Option type",
            ["ISO", "NSO"],
            index=["ISO", "NSO"].index(data.get("kind", "ISO")),
            key=_key(f"{prefix}_award_kind"),
        )
        shares = st.number_input(
            "Options vesting this year",
            min_value=0,
            value=data.get("vesting_shares", 0),
            key=_key(f"{prefix}_award_shares"),
        )
        strike = st.number_input(
            "Award exercise price ($/share)",
            min_value=0.0,
            value=float(data.get("strike", 0)),
            format="%.6f",
            key=_key(f"{prefix}_award_strike"),
        )
        value = st.number_input(
            "Assumed share value ($/share)",
            min_value=0.0,
            value=float(data["assumed_share_value"])
            if data.get("assumed_share_value") is not None
            else None,
            format="%.6f",
            key=_key(f"{prefix}_award_value"),
            help="Leave blank if unknown. An estimate does not mean shares can be sold at this price.",
        )
    return {
        "kind": kind,
        "vesting_shares": shares,
        "strike": str(strike),
        "assumed_share_value": str(value) if value is not None else None,
    }


def _option_inputs(prefix: str, data: dict[str, Any]) -> dict[str, Any]:
    """Collect only the supported ISO exercise-and-hold event."""
    with st.expander("ISO exercise and hold"):
        st.caption(
            "Qualifying ISOs exercised and held through year end. NSO exercises, share sales, and AMT credit carryforwards are not supported."
        )
        shares = st.number_input(
            "Shares exercised",
            min_value=0,
            value=data.get("shares", 0),
            key=_key(f"{prefix}_shares"),
        )
        strike = st.number_input(
            "Exercise price ($/share)",
            min_value=0.0,
            value=float(data.get("strike", 0)),
            format="%.6f",
            key=_key(f"{prefix}_strike"),
        )
        value = st.number_input(
            "FMV at exercise ($/share)",
            min_value=0.0,
            value=float(data.get("fair_market_value", 0)),
            format="%.6f",
            key=_key(f"{prefix}_fmv"),
        )
    return {"shares": shares, "strike": str(strike), "fair_market_value": str(value)}


def _living_inputs(data: dict[str, Any], state: str) -> dict[str, Any]:
    """Separate monthly spending from tax-credit eligibility facts."""
    st.subheader("Living expenses")
    columns = st.columns(3)
    amounts = {}
    for column, field, label in zip(
        columns,
        ["rent", "childcare", "other"],
        [
            "Monthly rent ($)",
            "Monthly childcare ($)",
            "Other monthly living expenses ($)",
        ],
        strict=True,
    ):
        with column:
            amounts[field] = _money_input(
                label,
                data.get(field),
                f"living_{field}",
                help_text="Include insurance and other costs or payroll deductions not entered elsewhere, once. This expense input does not reduce taxable wages."
                if field == "other"
                else None,
            )
    amounts["eligible_care_expense"] = _money_input(
        "Annual qualifying work-related childcare expense ($)",
        data.get("eligible_care_expense"),
        "eligible_care",
        help_text="Enter only eligible care for children under 13. The tax module applies income and credit limits.",
    )
    eligible = st.checkbox(
        "Qualifying California renter",
        value=data.get("ca_renter_credit_eligible", False),
        key=_key("ca_renter"),
        help="Paid rent for at least six months on California property subject to property tax; not claimed as someone else's dependent or claiming a homeowner exemption.",
    )
    return {
        **amounts,
        "ca_renter_credit_eligible": eligible if state == "CA" else False,
    }


def _read_goal_catalogue(dsn: str | None) -> None:
    """Use live shared names, with local snapshots available in preview mode."""
    if dsn:
        try:
            st.session_state["goals"] = list_goals(dsn)
        except psycopg.Error:
            st.session_state.setdefault("goals", ())
    else:
        st.session_state.setdefault("goals", ())


def _allocation_inputs(
    raw: dict[str, Any] | None, adults: list[dict[str, Any]]
) -> dict[str, Any]:
    """Collect fixed cash, remainder, and share destinations without asset trading."""
    st.subheader("Savings goals")
    data = raw or default_allocation_plan().model_dump(mode="json")
    catalogue = {str(g.id): g for g in st.session_state.get("goals", ())}
    rows = sorted(data["rows"], key=lambda row: row["cash_amount"] is None)
    names = {
        r["goal"]["id"]: catalogue.get(
            r["goal"]["id"], Goal.model_validate(r["goal"])
        ).name
        for r in rows
    }
    result = st.session_state.get("document")
    unit = st.radio(
        "Savings amounts",
        ["Monthly", "Annual"],
        horizontal=True,
        key=_key("allocation_unit"),
    )
    factor = 12 if unit == "Monthly" else 1
    period_label = "month" if unit == "Monthly" else "year"
    if result and _current(result.result):
        st.caption(
            f"Last calculated, {unit.lower()}: {_number(cash_surplus(result.result) / factor)} cash surplus / deficit · {_number(result.result.retained_rsus / factor)} shares retained from RSUs".replace(
                "$", r"\$"
            )
        )
    ids = list(names)
    remainder = next(r["goal"]["id"] for r in rows if r["cash_amount"] is None)
    names[remainder] = "Spending"
    st.caption(
        "Cash goals are funded from top to bottom. Leftover cash is available for spending."
    )
    period_key = unit.lower()
    updated = []
    remove_id = None
    move_id = None
    fixed_ids = [r["goal"]["id"] for r in rows if r["cash_amount"] is not None]
    remove_controls = []
    for row in rows:
        goal_id = row["goal"]["id"]
        goal = catalogue.get(goal_id, Goal.model_validate(row["goal"]))
        label, cash, shares, remove = st.columns([2, 1, 1, 1])
        if goal_id == remainder:
            label.markdown("**Spending**")
        else:
            priority = fixed_ids.index(goal_id)
            label.markdown(f"**{priority + 1}. {goal.name}**")
            if label.button(
                "Move up", key=_key(f"priority_up_{goal_id}"), disabled=priority == 0
            ):
                move_id = goal_id
        original = Decimal(str(row["cash_amount"] or 0))
        # Changing the unit recreates the widget from exact annual draft values.
        value = cash.number_input(
            f"Additional savings / {period_label}",
            min_value=0.0,
            value=float(original / factor),
            format="%.2f",
            disabled=goal_id == remainder,
            key=_key(f"allocation_cash_{goal_id}_{period_key}"),
        )
        if goal_id == remainder:
            cash.caption("Leftover cash after funding goals")
        rate = shares.number_input(
            "Retained RSU shares (%)",
            min_value=0.0,
            max_value=100.0,
            value=float(Decimal(str(row["rsu_rate"])) * 100),
            format="%.2f",
            key=_key(f"allocation_rsu_{goal_id}"),
        )
        remove_controls.append((remove, goal_id, value, rate))
        updated.append(
            {
                "goal": goal.model_dump(mode="json"),
                "cash_amount": None
                if goal_id == remainder
                else str(
                    original
                    if value == float(original / factor)
                    else dollars(Decimal(str(value)) * factor)
                ),
                "rsu_rate": str(Decimal(str(rate)) / 100),
            }
        )
    total_rate = sum(Decimal(r["rsu_rate"]) for r in updated)
    if any(Decimal(str(a["rsu_vesting"])) for a in adults) and total_rate != 1:
        st.warning(
            f"Retained RSU shares total {total_rate:.0%}; designate 100% before calculating."
        )
    retirement = st.selectbox(
        "Retirement contributions designated for",
        ids,
        index=ids.index(data["retirement_goal_id"]),
        format_func=names.__getitem__,
        key=_key("allocation_retirement"),
    )
    options = data["option_goal_id"]
    with st.expander(
        "Savings goal for exercised options",
        expanded=any(a["options"]["shares"] for a in adults),
    ):
        options = st.selectbox(
            "Shares from option exercises designated for",
            ids,
            index=ids.index(options),
            format_func=names.__getitem__,
            key=_key("allocation_options"),
        )
    for remove, goal_id, value, rate in remove_controls:
        removable = (
            goal_id != remainder
            and not value
            and not rate
            and goal_id not in (retirement, options)
        )
        if remove.button(
            "Remove",
            key=_key(f"remove_allocation_{goal_id}"),
            disabled=not removable,
            help="Reassign this goal's amounts and destinations before removing it from this scenario.",
        ):
            remove_id = goal_id
    present = set(ids)
    unused = {
        str(goal.id): goal
        for goal in st.session_state.get("goals", ())
        if str(goal.id) not in present
    }
    if unused:
        selected = st.selectbox(
            "Goal to add",
            list(unused),
            format_func=lambda key: unused[key].name,
            key=_key("allocation_add"),
        )
        if st.button("Add goal to scenario"):
            updated.append(
                {
                    "goal": unused[selected].model_dump(mode="json"),
                    "cash_amount": "0",
                    "rsu_rate": "0",
                }
            )
            st.session_state["allocation_structure_changed"] = True
    st.page_link(
        str(GOALS_PAGE), label="Manage household goals", icon=":material/arrow_forward:"
    )
    if remove_id:
        updated = [row for row in updated if row["goal"]["id"] != remove_id]
        st.session_state["allocation_structure_changed"] = True
    updated.sort(key=lambda row: row["cash_amount"] is None)
    if move_id:
        index = next(i for i, row in enumerate(updated) if row["goal"]["id"] == move_id)
        updated[index - 1], updated[index] = updated[index], updated[index - 1]
        st.session_state["allocation_structure_changed"] = True
    updated_plan = {
        "rows": updated,
        "retirement_goal_id": retirement,
        "option_goal_id": options,
    }
    if result and _current(result.result):
        try:
            plan = AllocationPlan.model_validate(updated_plan)
        except ValueError:
            pass  # Calculate reports invalid allocations with the other input errors.
        else:
            st.caption(
                "Using last calculated income, taxes, expenses, and option exercises."
            )
            st.metric(
                f"Spending per {period_label}",
                _number(
                    fund_cash_goals(plan, result.result.cash_added).remaining / factor
                ),
                help="Cash left after funding goals in priority order, or an expense deficit.",
            )
    return updated_plan


def _form(
    dsn: str | None, assumptions: DeltaGenerator, allocations: DeltaGenerator
) -> None:
    """Retain edits on each rerun; calculate and save only on explicit actions."""
    data = st.session_state["draft_data"]
    with assumptions:
        name = st.text_input(
            "Custom label (optional)",
            value=data.get("name", ""),
            max_chars=80,
            key=_key("name"),
            placeholder="For example, New job or Higher rent",
            help="Describe the decision if useful. Leave blank to show the state. "
            "Labels do not need to be unique.",
        )
        left, right = st.columns(2)
        year = left.selectbox(
            "Tax year",
            [2025, 2026],
            index=[2025, 2026].index(data.get("tax_year", 2026)),
            key=_key("year"),
        )
        state = right.selectbox(
            "Full-year state",
            ["WA", "CA"],
            index=["WA", "CA"].index(data.get("state", "WA")),
            key=_key("state"),
            format_func=STATE_NAMES.__getitem__,
        )
        ages = st.text_input(
            "Qualifying children's ages",
            value=st.session_state["draft_ages"],
            key=_key("children"),
            help="Ages at year end, separated by commas. Leave blank for none. Supported ages: 0-17.",
        )
        st.subheader("Compensation")
        adults = []
        for index, column in enumerate(st.columns(2)):
            with column:
                st.markdown(f"**Adult {index + 1}**")
                existing = data.get("adults", [{}, {}])[index]
                adults.append(_adult_inputs(index, existing, state))
        living = _living_inputs(data.get("living_costs", {}), state)
        notes = st.text_area(
            "Scenario notes",
            value=data.get("notes", ""),
            max_chars=2000,
            key=_key("notes"),
        )
        confirmed = st.checkbox(
            "My household fits the supported assumptions",
            value=data.get("assumptions_confirmed", False),
            key=_key("confirmed"),
        )
        _assumptions()
    with allocations:
        allocation_plan = _allocation_inputs(data.get("allocation_plan"), adults)
    data = {
        "name": name,
        "tax_year": year,
        "state": state,
        "adults": adults,
        "living_costs": living,
        "allocation_plan": allocation_plan,
        "notes": notes,
        "assumptions_confirmed": confirmed,
    }
    st.session_state["draft_data"] = data
    st.session_state["draft_ages"] = ages
    _remember_draft()
    if st.session_state.pop("allocation_structure_changed", False):
        st.session_state["editor_epoch"] += 1
        st.session_state["editor_target"] = "Savings goals"
        st.rerun()
    loaded = st.session_state.get("loaded_record")
    missing = loaded is not None and all(
        record.id != loaded.id for record in st.session_state["records"]
    )
    if missing and st.session_state["store_available"]:
        st.warning(
            "This scenario was deleted. Save as new scenario to keep this draft."
        )
    with st.container(horizontal=True):
        calculate = st.button("Calculate")
        save = st.button(
            "Save",
            type="primary",
            disabled=not st.session_state["store_available"] or missing,
            help="Calculate and save the current inputs.",
        )
        copy = (
            st.button(
                "Save as new scenario", disabled=not st.session_state["store_available"]
            )
            if loaded
            else False
        )
    if calculate or save or copy:
        _submit(data, ages, "copy" if copy else "save" if save else "calculate", dsn)


def _submit(data: dict[str, Any], ages: str, action: str, dsn: str | None) -> None:
    """Keep results and their inputs together through validation and failed saves."""
    loaded = st.session_state.get("loaded_record")
    try:
        inputs = _parse_inputs(data, ages)
    except (ValidationError, ValueError) as error:
        _input_error(error)
        return
    with st.spinner("Calculating income, taxes, and savings…"):
        result = evaluate_scenario(inputs)
    document = SavedScenario(
        inputs=inputs,
        result=result,
        id=st.session_state["draft_id"],
        revision=loaded.revision if loaded else 1,
    )
    st.session_state["document"] = document
    _remember_draft()
    st.session_state["editor_target"] = "Results"
    if action != "calculate" and dsn:
        _save(dsn, document, new=action == "copy" or loaded is None)
    elif action == "calculate":
        st.rerun()


def _input_error(error: ValueError) -> None:
    """Show field errors without dumping an entire financial input object."""
    if isinstance(error, ValidationError):
        for item in error.errors(
            include_input=False, include_context=False, include_url=False
        ):
            location = " → ".join(str(part) for part in item["loc"])
            st.error(f"{location or 'Scenario'}: {item['msg']}")
    else:
        st.error("Enter children's ages as whole numbers separated by commas.")


def _save(dsn: str, document: SavedScenario, *, new: bool) -> None:
    """Report success only after the database transaction commits."""
    try:
        if new:
            saved = create_scenario(
                dsn, SavedScenario(inputs=document.inputs, result=document.result)
            )
        else:
            saved = update_scenario(dsn, document)
    except RevisionConflictError as error:
        st.warning(str(error))
        return
    except (psycopg.Error, ValueError):
        st.error(
            "Save failed. Your calculated draft is still here; retry or download a backup."
        )
        return
    if not st.session_state.get("loaded_record"):
        st.session_state["drafts"].pop(str(st.session_state["draft_id"]), None)
    _set_editor(saved.inputs, saved)
    st.session_state["notice"] = "Scenario saved."
    st.rerun()


def _current(result: ScenarioResult) -> bool:
    """Require matching financial rules before comparing saved snapshots."""
    taxes = result.taxes
    return (
        result.model_revision == MODEL_REVISION
        and taxes.engine_version == ENGINE_VERSION
        and taxes.core_version == CORE_VERSION
        and taxes.correction_version == CORRECTION_VERSION
    )


def _annual_rows(result: ScenarioResult) -> dict[str, Decimal]:
    """Name the same accounting quantities consistently in detail and comparison."""
    return {
        "Salary and bonus": result.gross_cash,
        "RSU value at vesting": result.gross_rsus,
        "Total estimated taxes": result.taxes.total,
        "Taxes funded from vested shares": result.rsu_tax_funding,
        "Income and payroll taxes paid from cash": result.cash_funded_tax,
        "Employee 401(k) contributions": result.employee_401k,
        "Employer 401(k) contributions": result.employer_retirement,
        "Living expenses": result.living_cost,
        "Cash surplus / deficit": cash_surplus(result),
        "Option exercise cost": result.option_cost,
        "Shares retained from RSUs": result.retained_rsus,
        "Share value at exercise": result.option_value,
        "Funding shortfall": result.allocations.funding_gap,
    }


def _document_display(
    document: SavedScenario, status: str | None = None
) -> ScenarioDisplay:
    """Distinguish the current result or deletion target from other saved records."""
    others = tuple(
        record
        for record in st.session_state.get("records", ())
        if record.id != document.id
    )
    return scenario_displays((*others, document), (None,) * len(others) + (status,))[-1]


def _results(document: SavedScenario) -> None:
    """Display the last calculated or loaded document, never unsubmitted edits."""
    result = document.result
    st.subheader("Results")
    display = _document_display(document, "Last calculated inputs")
    st.html(display.html())
    if not _current(result):
        st.warning(
            "Saved results use older calculation rules. Select Calculate to update this draft."
        )
    displays = (display,)
    period = _financial_views((document,), displays)
    with st.container(horizontal=True):
        if st.button("Compare results", disabled=not _current(result)):
            st.session_state["pending_comparison"] = next(
                (
                    str(record.id)
                    for record in st.session_state["records"]
                    if record == document
                ),
                "draft",
            )
            st.switch_page(str(COMPARE_PAGE))
        if st.button("Keep snapshot", disabled=not _current(result)):
            st.session_state["session_snapshot"] = document
            st.toast("Snapshot kept for comparison.")
    with st.expander("Calculation details"):
        st.table(
            [
                {
                    "Item": label,
                    f"{period} dollars": f"${value / period_divisor(period):,.2f}",
                }
                for label, value in _annual_rows(result).items()
            ]
        )
        _tax_detail(result, period)
        st.json(document.inputs.model_dump(mode="json"), expanded=False)


def _tax_detail(result: ScenarioResult, period: Period) -> None:
    """Expose tax components without double-counting AMT subtotals."""
    taxes = result.taxes
    labels = {
        "federal_income_tax": "Federal income tax (including AMT)",
        "state_income_tax": "State income tax (including AMT)",
        "social_security": "Employee Social Security",
        "medicare": "Employee Medicare",
        "additional_medicare": "Additional Medicare",
        "ca_sdi": "California SDI",
        "wa_paid_leave": "Washington paid leave",
        "wa_cares": "WA Cares",
        "federal_amt": "Federal AMT already included above",
        "state_amt": "State AMT already included above",
    }
    st.table(
        [
            {
                "Tax component": label,
                f"{period} dollars": f"${getattr(taxes, field) / period_divisor(period):,.2f}",
            }
            for field, label in labels.items()
        ]
    )
    rate = result.effective_tax_rate
    st.caption(
        f"Effective tax rate (including employee payroll taxes): {rate:.1%}"
        if rate is not None
        else "Effective tax rate: undefined with zero wages."
    )
    st.caption(
        f"Model {result.model_revision} · PolicyEngine US {taxes.engine_version} / Core {taxes.core_version} · Corrections {taxes.correction_version} · Calculated {result.calculated_at:%Y-%m-%d %H:%M UTC}"
    )


def compare_page() -> None:
    """Compare two to four saved scenarios or explicitly calculated session results."""
    st.title("Compare")
    records = st.session_state["records"]
    choices = {str(record.id): record for record in records}
    document = st.session_state.get("document")
    if document and document not in records:
        choices["draft"] = document
    if snapshot := st.session_state.get("session_snapshot"):
        choices["snapshot"] = snapshot
    status_labels = {"draft": "Last calculated draft", "snapshot": "Kept snapshot"}
    displays = dict(
        zip(
            choices,
            scenario_displays(
                tuple(choices.values()),
                tuple(status_labels.get(key) for key in choices),
            ),
            strict=True,
        )
    )
    selected = [
        key
        for key in st.session_state.get("comparison_selection", list(choices)[:2])
        if key in choices
    ]
    pending = st.session_state.pop("pending_comparison", None)
    if pending in choices and pending not in selected:
        if len(selected) < 4:
            selected.append(pending)
        else:
            st.info(f"Remove one scenario to include {displays[pending].label}.")
    st.session_state["comparison_selection"] = selected
    selected = st.multiselect(
        "Scenarios",
        list(choices),
        key="comparison_selection",
        max_selections=4,
        placeholder="Choose two to four scenarios",
        format_func=lambda key: displays[key].label,
        help="Choose up to four. Removing a name here only removes it from this comparison.",
    )
    if len(selected) < 2:
        st.info(
            "Select at least two scenarios."
            if len(choices) >= 2
            else "Create or duplicate a scenario to compare two sets of results."
        )
        return
    _comparison(
        tuple(choices[key] for key in selected),
        tuple(displays[key] for key in selected),
    )


def _comparison(
    documents: tuple[SavedScenario, ...],
    displays: tuple[ScenarioDisplay, ...],
) -> None:
    """Render the approved views only for comparable calculation snapshots."""
    if len({record.inputs.tax_year for record in documents}) != 1:
        st.info(
            "Choose scenarios from the same tax year for a like-for-like comparison."
        )
        return
    if not all(_current(record.result) for record in documents):
        st.info(
            "Recalculate and save scenarios with the current rules before comparing them."
        )
        return
    _financial_views(documents, displays)


def _financial_views(
    documents: tuple[SavedScenario, ...],
    displays: tuple[ScenarioDisplay, ...],
) -> Period:
    """Use the same definitions for an individual result and all comparisons."""
    period = cast(
        Period,
        st.radio(
            "Amounts", ["Monthly", "Annual"], horizontal=True, key="display_period"
        ),
    )
    st.caption(
        (
            "Monthly averages from a one-year plan."
            if period == "Monthly"
            else "One-year plan."
        )
        + " Investment growth and retirement projections are not included."
    )
    goals = st.session_state.get("goals", ())
    views = {
        "Cash overview": cash_flow_html(
            documents, displays, goals=goals, period=period
        ),
        "Equity overview": equity_html(documents, displays, goals=goals, period=period),
        "Tax breakdown": tax_html(documents, displays, period=period),
        "Goal breakdown": goal_breakdown_html(
            documents, displays, goals=goals, period=period
        ),
    }
    for tab, html in zip(st.tabs(list(views)), views.values(), strict=True):
        with tab:
            st.html(html)
    return period


def _backups(document: SavedScenario | None) -> None:
    """Export the calculated document; imports load inputs as a new unsaved draft."""
    with st.expander("Backups"):
        if document:
            st.download_button(
                "Download calculated scenario",
                document.model_dump_json(indent=2),
                file_name=f"{document.id}.scenario.json",
                mime="application/json",
            )
        upload = st.file_uploader(
            "Import a scenario backup", type=["json"], max_upload_size=1
        )
        inputs = None
        ready = upload is not None
        if upload:
            try:
                raw = json.loads(upload.getvalue())
                legacy = isinstance(raw, dict) and raw.get("schema_version") == 1
                destinations = None
                if legacy:
                    ready = st.checkbox(
                        "Assign old saving, contributions, RSUs and option shares to Retirement; leftover cash to Spending"
                    )
                    destinations = default_allocation_plan()
                inputs = import_backup(
                    upload.getvalue(), legacy_destinations=destinations
                )
                catalogue = {g.id: g for g in st.session_state.get("goals", ())}
                replacements = {}
                for row in inputs.allocation_plan.rows:
                    current = catalogue.get(row.goal.id)
                    if current and current.name != row.goal.name:
                        choice = st.selectbox(
                            f"Imported goal '{row.goal.name}' is now named '{current.name}'",
                            ["Use current shared goal", "Create a separate goal"],
                            index=None,
                            key=f"import_goal_{row.goal.id}",
                        )
                        ready = ready and choice is not None
                        if choice:
                            replacements[row.goal.id] = (
                                current
                                if choice == "Use current shared goal"
                                else Goal(name=row.goal.name)
                            )
                inputs = resolve_goal_conflicts(inputs, replacements)
            except (ValueError, KeyError, TypeError):
                ready = False
                st.error("This backup could not be read. Check its format and version.")
        if st.button("Load backup as new draft", disabled=not ready) and inputs:
            _set_editor(inputs)
            st.switch_page(str(EDITOR_PAGE))
        st.caption(
            "Backups contain your financial inputs. Imported results are recalculated before saving a new copy."
        )


def _assumptions() -> None:
    """Make supported facts visible before the user confirms them."""
    with st.expander("Supported household and calculation assumptions"):
        st.markdown(
            "Married filing jointly; both adults aged 18-64; full-year US residence and work in the chosen state; "
            "employment-authorized Social Security numbers. Qualifying children are aged 0-17, have no income, "
            "and live with you all year. No disability-related tax treatment.\n\n"
            "W-2 cash compensation and vested RSUs; standard deductions; traditional pretax 401(k) contributions "
            "up to the base annual limit ($23,500 in 2025; $24,500 in 2026). No catch-up contributions, "
            "Roth contributions, itemized deductions, health savings or flexible spending deductions, investment/business "
            "income, stock dispositions, or existing AMT credits.\n\n"
            "RSUs fund their incremental tax and the remaining shares are retained. Personal savings are funded "
            "through savings goals. Option exercises use cash and may create AMT. "
            "Tax amounts estimate annual liability, not paycheck withholding. Average monthly cash is annual cash divided by 12."
        )


def _parse_inputs(data: dict[str, Any], ages: str) -> Scenario:
    """Validate the raw editing buffer without changing it."""
    return Scenario.model_validate(
        data
        | {
            "dependents": [
                {"age": int(age.strip())} for age in ages.split(",") if age.strip()
            ]
        }
    )


def _draft_status() -> tuple[bool, bool]:
    """Return whether the editor differs from saved and calculated inputs."""
    try:
        inputs = _parse_inputs(
            st.session_state["draft_data"], st.session_state["draft_ages"]
        )
    except ValueError:
        return True, True
    loaded = st.session_state.get("loaded_record")
    document = st.session_state.get("document")
    return (
        loaded is None or inputs != loaded.inputs,
        document is None or inputs != document.inputs,
    )


def editor_page() -> None:
    """Edit a session draft beside its last calculated result."""
    if "editor_epoch" not in st.session_state:
        _set_editor(None)
    st.page_link(
        str(SCENARIOS_PAGE), label="All scenarios", icon=":material/arrow_back:"
    )
    st.title(
        "Edit scenario" if st.session_state.get("loaded_record") else "New scenario"
    )
    status = st.container()
    section_key = _key("editor_section")
    if target := st.session_state.pop("editor_target", None):
        st.session_state[section_key] = target
    assumptions, allocations, results = st.tabs(
        ["Assumptions", "Savings goals", "Results"], key=section_key, on_change="rerun"
    )
    _form(_database_url(), assumptions, allocations)
    loaded = st.session_state.get("loaded_record")
    latest = next(
        (
            record
            for record in st.session_state["records"]
            if loaded and record.id == loaded.id
        ),
        None,
    )
    unsaved, stale = _draft_status()
    with status:
        st.caption("Unsaved changes" if unsaved else "Saved")
        if loaded and latest and loaded.revision != latest.revision:
            st.warning(
                "This scenario has a newer saved version. Save your edits as a new scenario, or reload the saved version."
            )
    with assumptions:
        if latest:
            with st.expander("Reload saved version"):
                st.caption(
                    "Discard edits to this scenario and load its latest saved version."
                )
                if st.button(
                    "Discard edits and reload",
                    disabled=not st.session_state["store_available"],
                ):
                    _set_editor(latest.inputs, latest)
                    st.rerun()
    with results:
        document = st.session_state.get("document")
        if document:
            if stale:
                st.info("Inputs changed. Calculate to update these results.")
            _results(document)
        else:
            st.caption("Calculate to see results.")
        _backups(document)


@st.dialog("Delete scenario?", dismissible=False)
def _confirm_delete(record: SavedScenario) -> None:
    """Require confirmation of a specific saved revision before deleting it."""
    st.html(_document_display(record).html())
    st.caption(
        "This removes the scenario for both of you, including its draft in this session. It cannot be undone."
    )
    cancel, confirm = st.columns(2)
    if cancel.button("Cancel", width="stretch"):
        st.session_state.pop("pending_delete", None)
        st.rerun()
    if confirm.button("Delete scenario", type="primary", width="stretch"):
        dsn = _database_url()
        if not dsn:
            st.error("The saved scenario store is unavailable.")
            return
        try:
            delete_scenario(dsn, record.id, record.revision)
        except RevisionConflictError as error:
            st.warning(str(error))
            return
        except psycopg.Error:
            st.error("Delete failed. The scenario has not been removed; try again.")
            return
        _forget_scenario(record)
        st.session_state.pop("pending_delete", None)
        st.session_state["notice"] = "Scenario deleted."
        st.rerun()


def _forget_scenario(record: SavedScenario) -> None:
    """Remove references to a deleted record from this browser session."""
    st.session_state.get("drafts", {}).pop(str(record.id), None)
    st.session_state["last_records"] = tuple(
        item
        for item in st.session_state.get("last_records", ())
        if item.id != record.id
    )
    if st.session_state.get("draft_id") == record.id:
        for key in (*DRAFT_FIELDS, "editor_epoch"):
            st.session_state.pop(key, None)
    snapshot = st.session_state.get("session_snapshot")
    if snapshot and snapshot.id == record.id:
        st.session_state.pop("session_snapshot")
    st.session_state["comparison_selection"] = [
        key
        for key in st.session_state.get("comparison_selection", [])
        if key != str(record.id)
    ]


def _scenario_card(record: SavedScenario, display: ScenarioDisplay) -> None:
    """Show a saved scenario's outcomes and its edit, copy, and delete actions."""
    with st.container(border=True):
        name, cash, balance = st.columns([2, 1, 1], vertical_alignment="center")
        name.html(f'<div class="fm-scenario-card">{display.html(heading=True)}</div>')
        gap = record.result.allocations.funding_gap
        cash.metric(
            "Cash surplus / deficit per year",
            _number(cash_surplus(record.result)),
            help="After taxes, employee 401(k) contributions, and living expenses. Before additional savings and option exercises.",
        )
        balance.metric(
            "Spending per year",
            _number(remaining_cash(record)),
            help="Cash left after funding goals in priority order, or an expense deficit.",
        )
        if gap:
            st.caption(
                "Funding shortfall after taxes, expenses, and planned contributions."
            )
        if not _current(record.result):
            st.caption("Recalculation needed: saved results use older rules.")
        with st.container(horizontal=True):
            if st.button("Edit", key=f"edit_{record.id}"):
                _open_editor(record)
            if st.button("Duplicate", key=f"duplicate_{record.id}"):
                _open_editor(record, duplicate=True)
            if st.button(
                "Delete",
                key=f"delete_{record.id}",
                disabled=not st.session_state["store_available"],
            ):
                st.session_state["pending_delete"] = record


def scenarios_page() -> None:
    """List saved household scenarios and provide entry points to their drafts."""
    st.title("Scenarios")
    with st.container(horizontal=True):
        if st.button("New scenario", type="primary"):
            _open_editor()
        st.button("Refresh", help="Read the latest saved scenarios.")
    records = st.session_state["records"]
    if not records:
        st.info("No saved scenarios yet. Create one to get started.")
    for record, display in zip(records, scenario_displays(records), strict=True):
        _scenario_card(record, display)
    drafts = [
        draft
        for draft in st.session_state.get("drafts", {}).values()
        if draft["loaded_record"] is None
    ]
    if drafts:
        with st.expander("Unsaved drafts"):
            for draft in drafts:
                label = draft["draft_data"].get("name") or "New scenario"
                try:
                    inputs = _parse_inputs(draft["draft_data"], draft["draft_ages"])
                    label = ScenarioDisplay(inputs, "Unsaved draft").label
                except ValueError:
                    pass  # An incomplete draft still needs an entry point.
                if st.button(label, key=f"resume_{draft['draft_id']}"):
                    st.session_state.update(draft)
                    st.session_state["editor_epoch"] = (
                        st.session_state.get("editor_epoch", 0) + 1
                    )
                    st.switch_page(str(EDITOR_PAGE))
    if any(
        record.result.taxes.rules_status == "projected_ca_parameters"
        for record in records
    ):
        st.caption("2026 California tax estimates use projected parameters.")
    _backups(None)
    if pending := st.session_state.get("pending_delete"):
        _confirm_delete(pending)


def main() -> None:
    """Route the app through top navigation with an editor opened from the library."""
    st.set_page_config(
        page_title="Family financial model", page_icon="🏡", layout="wide"
    )
    page = st.navigation(
        [
            st.Page(str(HOUSEHOLD_PAGE), title="Household", url_path="household"),
            st.Page(str(GOALS_PAGE), title="Goals", url_path="goals"),
            st.Page(str(WEALTH_PAGE), title="Wealth", url_path="wealth"),
            st.Page(str(SCENARIOS_PAGE), title="Scenarios", default=True),
            st.Page(str(COMPARE_PAGE), title="Compare", url_path="compare"),
            st.Page(
                str(EDITOR_PAGE),
                title="Scenario editor",
                url_path="editor",
                visibility="hidden",
            ),
            st.Page(
                str(WEALTH_EDITOR_PAGE),
                title="Wealth snapshot editor",
                url_path="wealth-editor",
                visibility="hidden",
            ),
        ],
        position="top",
    )
    dsn = _database_url()
    if not dsn:
        st.info(
            "Local preview: drafts last for this session. Download a backup to keep them."
        )
    st.session_state["records"] = _read_store(dsn)
    _read_goal_catalogue(dsn)
    if notice := st.session_state.pop("notice", None):
        st.success(notice)
    if imported := st.session_state.pop("pending_inputs", None):
        _set_editor(imported)
        st.switch_page(str(EDITOR_PAGE))
    # Page widgets are transient; persist comparison choices between visits.
    for key in (
        "comparison_selection",
        "display_period",
    ):
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]
    st.html(Path(__file__).with_name("comparison.css"))
    page.run()
