"""Streamlit editor for shared annual household scenarios."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import streamlit as st
from pydantic import ValidationError

from financial_model.comparison import (
    cash_flow_html,
    compensation_html,
    overview_html,
    saving_html,
)
from financial_model.model import evaluate_scenario
from financial_model.scenario_display import (
    STATE_NAMES,
    ScenarioDisplay,
    scenario_displays,
)
from financial_model.schemas import (
    MODEL_REVISION,
    SavedScenario,
    Scenario,
    ScenarioResult,
)
from financial_model.storage import (
    RevisionConflictError,
    create_scenario,
    delete_scenario,
    import_backup,
    list_scenarios,
    update_scenario,
)
from financial_model.taxes import CORE_VERSION, CORRECTION_VERSION, ENGINE_VERSION

PAGE_DIR = Path(__file__).with_name("pages")
SCENARIOS_PAGE = PAGE_DIR / "scenarios.py"
EDITOR_PAGE = PAGE_DIR / "editor.py"
COMPARE_PAGE = PAGE_DIR / "compare.py"
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
    st.session_state["draft_data"] = inputs.model_dump(mode="json") if inputs else {}
    st.session_state["draft_ages"] = (
        ", ".join(str(child.age) for child in inputs.dependents) if inputs else ""
    )
    st.session_state["loaded_record"] = record
    st.session_state["document"] = record
    st.session_state["draft_id"] = record.id if record else uuid4()
    _remember_draft()


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
            help_text=(
                "Assumes shares are delivered at vesting and enough can be sold to pay vesting taxes. The remaining shares stay separate from cash."
                if field == "rsu_vesting"
                else None
            ),
        )
        for field, label in [
            ("cash_pay", "Annual cash salary + bonus ($)"),
            ("rsu_vesting", "Annual RSU vesting value ($)"),
            ("employee_401k", "Annual pretax 401(k) contribution ($)"),
        ]
    }
    rates = {}
    for field, label, default in [
        ("savings_rate", "Cash-funded saving goal (% of salary + RSUs)", "0.10"),
        ("employer_retirement_rate", "Employer retirement (% of cash pay)", "0"),
    ]:
        percent = st.number_input(
            label,
            min_value=0.0,
            max_value=100.0,
            value=float(Decimal(data.get(field, default)) * 100),
            step=1.0,
            key=_key(f"{prefix}_{field}"),
            help=(
                "Funded from salary. Your 401(k) counts toward this goal; retained RSUs and employer contributions are additional."
                if field == "savings_rate"
                else "Employer contribution as a percentage of cash salary and bonus."
            ),
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
            "Strike price ($/share)",
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
    st.subheader("Living costs")
    columns = st.columns(3)
    amounts = {}
    for column, field, label in zip(
        columns,
        ["rent", "childcare", "other"],
        ["Monthly rent ($)", "Monthly childcare ($)", "Other monthly costs ($)"],
        strict=True,
    ):
        with column:
            amounts[field] = _money_input(label, data.get(field), f"living_{field}")
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


def _form(dsn: str | None) -> None:
    """Retain edits on each rerun; calculate and save only on explicit actions."""
    data = st.session_state["draft_data"]
    with st.container():
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
        st.subheader("Compensation and saving")
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
    data = {
        "name": name,
        "tax_year": year,
        "state": state,
        "adults": adults,
        "living_costs": living,
        "notes": notes,
        "assumptions_confirmed": confirmed,
    }
    st.session_state["draft_data"] = data
    st.session_state["draft_ages"] = ages
    _remember_draft()
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
    if action != "calculate" and dsn:
        _save(dsn, document, new=action == "copy" or loaded is None)


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
        "Cash compensation": result.gross_cash,
        "Gross vested RSUs": result.gross_rsus,
        "Total taxes": result.taxes.total,
        "Taxes funded by RSUs": result.rsu_tax_funding,
        "Taxes funded by cash": result.cash_funded_tax,
        "Employee 401(k)": result.employee_401k,
        "Additional personal savings": result.extra_savings,
        "Employer retirement": result.employer_retirement,
        "Living costs": result.living_cost,
        "ISO purchase cost": result.option_cost,
        "Remaining cash": result.annual_remaining_cash,
        "Net cash added (saving + remaining cash)": result.cash_added,
        "Retained RSUs": result.retained_rsus,
        "Purchased ISO shares (at exercise FMV)": result.option_value,
        "Cash + contributions + shares acquired": result.total_savings_and_assets,
        "Less option exercise spread": result.option_cost - result.option_value,
        "Net saved from income": result.net_saved_from_income,
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
    _cash_shortfalls((document,), displays)
    _financial_views((document,), displays)
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
    with st.expander("Annual calculation details"):
        st.table(
            [
                {"Item": label, "Annual dollars": f"${value:,.2f}"}
                for label, value in _annual_rows(result).items()
            ]
        )
        st.caption(
            "Net saving combines cash, retirement, and estimated RSU value. It is not spendable cash. Option award values and exercise spread are excluded."
        )
        _tax_detail(result)
        st.json(document.inputs.model_dump(mode="json"), expanded=False)


def _tax_detail(result: ScenarioResult) -> None:
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
            {"Tax component": label, "Annual dollars": f"${getattr(taxes, field):,.2f}"}
            for field, label in labels.items()
        ]
    )
    rate = result.effective_tax_rate
    st.caption(
        f"Effective tax rate: {rate:.1%}"
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
        selected,
        tuple(displays[key] for key in selected),
    )


def _comparison(
    documents: tuple[SavedScenario, ...],
    selected: list[str],
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
    _cash_shortfalls(documents, displays)
    _financial_views(documents, displays, selected)


def _cash_shortfalls(
    documents: tuple[SavedScenario, ...], displays: tuple[ScenarioDisplay, ...]
) -> None:
    """Keep unfunded cash needs visible even when retained equity is substantial."""
    for document, display in zip(documents, displays, strict=True):
        if document.result.annual_remaining_cash < 0:
            st.warning(
                f"{display.label}: ${-document.result.monthly_remaining_cash:,.0f}/month cash shortfall. "
                "Planned saving is not reduced and no extra shares are sold automatically."
            )


def _financial_views(
    documents: tuple[SavedScenario, ...],
    displays: tuple[ScenarioDisplay, ...],
    selected: list[str] | None = None,
) -> None:
    """Use the same definitions for an individual result and all comparisons."""
    reference = _comparison_reference(selected, displays) if selected else None
    tabs = st.tabs(["Overview", "Cash flow", "Compensation", "Saving & equity"])
    for tab, render in zip(
        tabs,
        (overview_html, cash_flow_html, compensation_html, saving_html),
        strict=True,
    ):
        with tab:
            st.html(render(documents, displays, reference))


def _comparison_reference(
    selected: list[str], displays: tuple[ScenarioDisplay, ...]
) -> int | None:
    """Keep the reference choice when differences are hidden or pages change."""
    if st.session_state.get("comparison_reference") not in selected:
        st.session_state["comparison_reference"] = selected[0]
    toggle, selector = st.columns([1, 2], vertical_alignment="center")
    enabled = toggle.toggle(
        "Show differences",
        key="comparison_show_differences",
        help="Changes appear below each amount, in the same units as its row. "
        "For cash outflows, a more negative change means more cash going out.",
    )
    if not enabled:
        return None
    reference_names = dict(
        zip(selected, (display.label for display in displays), strict=True)
    )
    reference = selector.selectbox(
        "Reference scenario",
        selected,
        key="comparison_reference",
        format_func=reference_names.__getitem__,
    )
    return selected.index(reference)


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
        if st.button("Load backup as new draft", disabled=upload is None) and upload:
            try:
                inputs = import_backup(upload.getvalue())
            except (ValidationError, ValueError) as error:
                _input_error(error) if isinstance(error, ValidationError) else st.error(
                    str(error)
                )
                return
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
            "from cash; 401(k) contributions count toward that goal. Option purchases use cash and may create AMT. "
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
    inputs, preview = st.columns([2, 1], gap="large")
    with inputs:
        _form(_database_url())
        _assumptions()
        loaded = st.session_state.get("loaded_record")
        latest = next(
            (
                record
                for record in st.session_state["records"]
                if loaded and record.id == loaded.id
            ),
            None,
        )
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
    unsaved, stale = _draft_status()
    with status:
        st.caption("Unsaved changes" if unsaved else "Saved")
        if loaded and latest and loaded.revision != latest.revision:
            st.warning(
                "This scenario has a newer saved version. Save your edits as a new "
                "scenario, or reload the saved version."
            )
    with preview:
        document = st.session_state.get("document")
        if document:
            if stale:
                st.info("Inputs changed. Calculate to update these results.")
            _results(document)
        else:
            st.subheader("Results")
            st.caption("Calculate to see income, taxes, and savings.")
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
    if st.session_state.get("comparison_reference") == str(record.id):
        st.session_state.pop("comparison_reference", None)


def _scenario_card(record: SavedScenario, display: ScenarioDisplay) -> None:
    """Show a saved scenario's outcomes and its edit, copy, and delete actions."""
    with st.container(border=True):
        name, cash, assets = st.columns([2, 1, 1], vertical_alignment="center")
        name.html(f'<div class="fm-scenario-card">{display.html(heading=True)}</div>')
        remaining = record.result.monthly_remaining_cash
        cash.metric(
            "Monthly cash shortfall" if remaining < 0 else "Monthly cash surplus",
            f"${abs(remaining):,.0f}",
            help="After taxes, living costs, planned saving, and option exercise costs.",
        )
        assets.metric(
            "Net saved from income / year",
            f"${record.result.net_saved_from_income:,.0f}",
            help="Combined cash, retirement, and estimated RSU value, not spendable cash. Includes cash shortfalls; excludes option award values, exercise spread, and investment growth.",
        )
        if remaining < 0:
            st.caption(
                "Cash shortfall: planned spending and saving need additional cash."
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
    if records:
        st.caption(
            "Net saving combines cash, retirement, and estimated RSU value; it is not all spendable. Options are excluded."
        )
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
            st.Page(str(SCENARIOS_PAGE), title="Scenarios", default=True),
            st.Page(str(COMPARE_PAGE), title="Compare", url_path="compare"),
            st.Page(
                str(EDITOR_PAGE),
                title="Scenario editor",
                url_path="editor",
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
    if notice := st.session_state.pop("notice", None):
        st.success(notice)
    if imported := st.session_state.pop("pending_inputs", None):
        _set_editor(imported)
        st.switch_page(str(EDITOR_PAGE))
    # Page widgets are transient; persist comparison choices between visits.
    for key in (
        "comparison_selection",
        "comparison_reference",
        "comparison_show_differences",
    ):
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]
    st.html(Path(__file__).with_name("comparison.css"))
    page.run()
