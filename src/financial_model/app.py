"""Streamlit editor for shared annual household scenarios."""

from __future__ import annotations

import json
import re
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import streamlit as st
from pydantic import ValidationError

from financial_model import database_pages
from financial_model import input_forms as forms
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
from financial_model.database_pages import (
    database_controls,
    database_download,
)
from financial_model.model import evaluate_scenario
from financial_model.navigation import PAGE_DIR, navigate
from financial_model.scenario_display import (
    ScenarioDisplay,
    scenario_displays,
)
from financial_model.schemas import (
    MODEL_REVISION,
    Goal,
    SavedScenario,
    Scenario,
    ScenarioResult,
    default_allocation_plan,
)
from financial_model.setup_pages import read_goal_catalogue
from financial_model.storage import (
    create_scenario,
    delete_scenario,
    import_backup,
    list_scenarios,
    load_household,
    resolve_goal_conflicts,
    update_scenario,
)
from financial_model.storage_common import RevisionConflictError
from financial_model.taxes import CORE_VERSION, CORRECTION_VERSION, ENGINE_VERSION

SCENARIOS_PAGE = PAGE_DIR / "scenarios.py"
COMPARE_PAGE = PAGE_DIR / "compare.py"
DRAFT_FIELDS = (
    "draft_data",
    "draft_ages",
    "loaded_record",
    "document",
    "draft_id",
    "annual_children_buffer",
)


def _read_store(dsn: str) -> tuple[SavedScenario, ...]:
    """Keep the editor usable if the shared store is temporarily unavailable."""
    st.session_state["store_available"] = True
    try:
        return list_scenarios(dsn)
    except (sqlite3.Error, ValidationError, ValueError):
        st.session_state["store_available"] = False
        st.error(
            "Saved annual plans could not be read. Your draft is still available; try Refresh annual plans."
        )
        return ()


def _set_editor(inputs: Scenario | None, record: SavedScenario | None = None) -> None:
    """Replace the active editing buffer with validated inputs or a blank draft."""
    st.session_state["editor_epoch"] = st.session_state.get("editor_epoch", 0) + 1
    data = inputs.model_dump(mode="json") if inputs else _household_defaults()
    st.session_state["draft_data"] = data
    st.session_state["draft_ages"] = ", ".join(
        str(child["age"]) for child in data.get("dependents", ())
    )
    st.session_state["annual_children_buffer"] = {
        "text": st.session_state["draft_ages"]
    }
    st.session_state["loaded_record"] = record
    st.session_state["document"] = record
    st.session_state["draft_id"] = record.id if record else uuid4()
    _remember_draft()


def _household_defaults() -> dict[str, Any]:
    """Copy shared family facts only when starting a new scenario."""
    dsn = database_pages.database_path()
    try:
        household = load_household(dsn)
    except (sqlite3.Error, ValueError):
        st.warning(
            "Household defaults could not be loaded. Enter the family details for this draft."
        )
        return {}
    return household.scenario_defaults() if household else {}


def _remember_draft() -> None:
    """Retain raw edits separately from widgets that disappear on page changes."""
    if "annual_children_buffer" in st.session_state:
        st.session_state["draft_ages"] = st.session_state["annual_children_buffer"][
            "text"
        ]
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

    navigate("Plans", target="annual")


def _submit(data: dict[str, Any], ages: str, action: str, dsn: str) -> None:
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
    if action != "calculate":
        _save(dsn, document, new=action == "copy" or loaded is None)
    else:
        st.rerun()


def _input_error(error: ValueError) -> None:
    """Show field errors without dumping an entire financial input object."""
    if isinstance(error, ValidationError):
        for item in error.errors(
            include_input=False, include_context=False, include_url=False
        ):
            location = " → ".join(str(part) for part in item["loc"])
            st.error(f"{location or 'Annual plan'}: {item['msg']}")
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
    except (sqlite3.Error, ValueError):
        st.error(
            "Save failed. Your calculated draft is still here; retry or download a backup."
        )
        return
    if not st.session_state.get("loaded_record"):
        st.session_state["drafts"].pop(str(st.session_state["draft_id"]), None)
    _set_editor(saved.inputs, saved)
    st.session_state["notice"] = "Annual plan saved."
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
            st.session_state["pending_comparison"] = (
                _matching_saved_key(document, st.session_state["records"]) or "draft"
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


def _matching_saved_key(
    document: SavedScenario, records: tuple[SavedScenario, ...]
) -> str | None:
    """Match financial inputs and results without treating timestamps as changes."""
    ignored = {"calculated_at", "app_version"}
    result = document.result.model_dump(exclude=ignored)
    return next(
        (
            str(record.id)
            for record in sorted(records, key=lambda item: item.id != document.id)
            if record.inputs == document.inputs
            and record.result.model_dump(exclude=ignored) == result
        ),
        None,
    )


def _comparison_choices(
    records: tuple[SavedScenario, ...],
) -> dict[str, SavedScenario]:
    """Offer current saved plans and distinct, explicitly calculated local results."""
    choices = {str(record.id): record for record in records}
    document = st.session_state.get("document")
    if (
        document
        and document != st.session_state.get("loaded_record")
        and not _matching_saved_key(document, records)
    ):
        choices["draft"] = document
    if snapshot := st.session_state.get("session_snapshot"):
        saved_origins = {
            draft["loaded_record"].id
            for draft in st.session_state.get("drafts", {}).values()
            if draft.get("loaded_record")
        }
        if loaded := st.session_state.get("loaded_record"):
            saved_origins.add(loaded.id)
        if (
            snapshot.id not in saved_origins or str(snapshot.id) in choices
        ) and not _matching_saved_key(snapshot, records):
            choices["snapshot"] = snapshot
    return choices


def compare_page() -> None:
    """Compare two to four saved scenarios or explicitly calculated session results."""
    st.title("Compare annual plans")
    st.caption(
        "Annual plans compare a single year. The combined plans used in Forecast are a separate list."
    )
    st.session_state["records"] = _read_store(database_pages.database_path())
    read_goal_catalogue(database_pages.database_path())
    choices = _comparison_choices(st.session_state["records"])
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
            st.info(f"Remove one annual plan to include {displays[pending].label}.")
    st.session_state["comparison_selection"] = selected
    _comparison_checklist(displays, selected)
    if len(selected) < 2:
        st.info(
            "Select at least two annual plans."
            if len(choices) >= 2
            else "Create or duplicate an annual plan in Plans, then calculate it to compare results."
        )
        return
    _comparison(
        tuple(choices[key] for key in selected),
        tuple(displays[key] for key in selected),
    )


def _toggle_comparison(key: str) -> None:
    """Append newly checked scenarios while retaining the other column positions."""
    selected = list(st.session_state["comparison_selection"])
    if st.session_state[f"compare_check_{key}"]:
        if key not in selected and len(selected) < 4:
            selected.append(key)
    elif key in selected:
        selected.remove(key)
    st.session_state["comparison_selection"] = selected


def _comparison_checklist(
    displays: dict[str, ScenarioDisplay], selected: list[str]
) -> None:
    """Keep every choice visible and in place as column selection changes."""
    st.subheader("Annual plans")
    st.caption(
        "Choose two to four. Check plans in column order, or use the arrows to reorder. "
        "Unchecking only removes a plan from this comparison."
    )
    with st.container(key="comparison_checklist", gap="small"):
        for key, display in displays.items():
            title, context = (
                re.sub(r"([\\`*_{}\[\]()<>#+.!|~-])", r"\\\1", text)
                for text in (display.title, display.label[len(display.title) + 3 :])
            )
            position = f" · Column {selected.index(key) + 1}" if key in selected else ""
            widget_key = f"compare_check_{key}"
            st.session_state[widget_key] = key in selected
            with st.container(
                key=f"comparison_row_{key}",
                horizontal=True,
                wrap=False,
                vertical_alignment="center",
                gap="xsmall",
            ):
                st.checkbox(
                    f"**{title}**{position}  \n{context}",
                    key=widget_key,
                    disabled=len(selected) == 4 and key not in selected,
                    on_change=_toggle_comparison,
                    args=(key,),
                    width="stretch",
                )
                if key in selected:
                    _comparison_arrows(key, display, selected)
    if len(selected) == 4:
        st.caption("Four selected. Uncheck an annual plan to choose another.")


def _move_comparison(key: str, direction: int) -> None:
    """Move one column without changing which scenarios are checked."""
    selected = list(st.session_state["comparison_selection"])
    index = selected.index(key)
    destination = index + direction
    if 0 <= destination < len(selected):
        selected[index], selected[destination] = selected[destination], selected[index]
        st.session_state["comparison_selection"] = selected


def _comparison_arrows(key: str, display: ScenarioDisplay, selected: list[str]) -> None:
    """Place compact, keyboard-accessible column controls on each checked row."""
    index = selected.index(key)
    for direction, label, icon in (
        (-1, "Move left", ":material/arrow_back:"),
        (1, "Move right", ":material/arrow_forward:"),
    ):
        st.button(
            f"{label}: {display.label}",
            icon=icon,
            key=f"compare_move_{key}_{direction}",
            help=f"{label}: {display.label}",
            disabled=not 0 <= index + direction < len(selected),
            on_click=_move_comparison,
            args=(key, direction),
            type="tertiary",
            width=32,
        )


def _comparison(
    documents: tuple[SavedScenario, ...],
    displays: tuple[ScenarioDisplay, ...],
) -> None:
    """Render the approved views only for comparable calculation snapshots."""
    if len({record.inputs.tax_year for record in documents}) != 1:
        st.info(
            "Choose annual plans from the same tax year for a like-for-like comparison."
        )
        return
    if not all(_current(record.result) for record in documents):
        st.info(
            "Recalculate and save annual plans with the current rules before comparing them."
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
        st.segmented_control(
            "Amounts",
            ["Monthly", "Annual"],
            default="Monthly",
            required=True,
            key="display_period",
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
                "Download calculated annual plan",
                document.model_dump_json(indent=2),
                file_name=f"{document.id}.scenario.json",
                mime="application/json",
            )
        upload = st.file_uploader(
            "Import an annual plan backup", type=["json"], max_upload_size=1
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

            navigate("Plans", target="annual")
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
            "Choose whether vested RSUs fund their incremental tax or cash pays all taxes. Retained shares stay separate from cash. Personal savings are funded "
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


@st.dialog("Delete annual plan?", dismissible=False)
def _confirm_delete(record: SavedScenario) -> None:
    """Require confirmation of a specific saved revision before deleting it."""
    st.html(_document_display(record).html())
    st.caption(
        "This removes the annual plan and its draft from this session. It cannot be undone."
    )
    cancel, confirm = st.columns(2)
    if cancel.button("Cancel", width="stretch"):
        st.session_state.pop("pending_delete", None)
        st.rerun()
    if confirm.button("Delete annual plan", type="primary", width="stretch"):
        dsn = database_pages.database_path()
        try:
            delete_scenario(dsn, record.id, record.revision)
        except RevisionConflictError as error:
            st.warning(str(error))
            return
        except sqlite3.Error:
            st.error("Delete failed. The annual plan has not been removed; try again.")
            return
        _forget_scenario(record)
        st.session_state.pop("pending_delete", None)
        st.session_state["notice"] = "Annual plan deleted."
        st.rerun()


def _forget_scenario(record: SavedScenario) -> None:
    """Remove references to a deleted record from this browser session."""
    st.session_state.get("drafts", {}).pop(str(record.id), None)
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


def _scenario_row(record: SavedScenario, display: ScenarioDisplay) -> None:
    """Align saved annual outcomes with compact edit, copy, and delete controls."""
    name, cash, balance, actions = st.columns(
        (4, 2, 2, 1.5), vertical_alignment="center"
    )
    name.html(display.html())
    cash.text(_number(cash_surplus(record.result)))
    balance.text(_number(remaining_cash(record)))
    if record.result.allocations.funding_gap:
        balance.caption("Funding shortfall")
    if not _current(record.result):
        name.caption("Recalculation needed: saved results use older rules.")
    with actions, st.container(horizontal=True, gap="small"):
        if forms.row_action("Edit", "edit", f"edit_{record.id}"):
            _open_editor(record)
        if forms.row_action("Duplicate", "content_copy", f"duplicate_{record.id}"):
            _open_editor(record, duplicate=True)
        if forms.row_action(
            "Delete",
            "delete",
            f"delete_{record.id}",
            disabled=not st.session_state["store_available"],
        ):
            st.session_state["pending_delete"] = record


def scenarios_page() -> None:
    """List saved household scenarios and provide entry points to their drafts."""
    st.subheader("Annual plans")
    with st.container(horizontal=True):
        if st.button("New annual plan", type="primary"):
            _open_editor()
        st.button("Refresh annual plans", help="Read the latest saved annual plans.")
    records = st.session_state["records"]
    if not records:
        st.info(
            "No saved annual plans yet. Create one to compare a single year's cash, equity, and taxes."
        )
    if records:
        forms.table_header(
            ("Annual plan", "Cash surplus / deficit ($/year)", "Spending ($/year)", ""),
            (4, 2, 2, 1.5),
        )
    for record, display in zip(records, scenario_displays(records), strict=True):
        _scenario_row(record, display)
    drafts = [
        draft
        for draft in st.session_state.get("drafts", {}).values()
        if draft["loaded_record"] is None
    ]
    if drafts:
        with st.container(border=True):
            st.markdown("**Continue editing annual plans**")
            for draft in drafts:
                label = draft["draft_data"].get("name") or "New annual plan"
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

                    navigate("Plans", target="annual")
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
    st.set_page_config(page_title="Household Forecast", page_icon="🏡", layout="wide")
    page = st.navigation(
        [
            st.Page(str(SCENARIOS_PAGE), title="Scenarios", default=True),
            st.Page(
                str(COMPARE_PAGE), title="Compare annual plans", url_path="compare"
            ),
            st.Page(
                str(PAGE_DIR / "forecast.py"), title="Forecast", url_path="forecast"
            ),
        ],
        position="top",
    )
    database_controls()
    if notice := st.session_state.pop("notice", None):
        st.success(notice)
    # Page widgets are transient; persist comparison choices between visits.
    for key in (
        "scenario_category",
        "comparison_selection",
        "display_period",
    ):
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]
    st.html(Path(__file__).with_name("comparison.css"))
    page.run()
    database_download()
