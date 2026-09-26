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

from financial_model.comparison import cash_flow_html, difference_html, overview_html
from financial_model.model import evaluate_scenario
from financial_model.schemas import (
    MODEL_REVISION,
    SavedScenario,
    Scenario,
    ScenarioResult,
)
from financial_model.storage import (
    RevisionConflictError,
    create_scenario,
    import_backup,
    list_scenarios,
    update_scenario,
)
from financial_model.taxes import CORE_VERSION, CORRECTION_VERSION, ENGINE_VERSION

NEW_SCENARIO = "__new__"


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
    """Replace the editing buffer and reset widget identities when loading inputs."""
    st.session_state["editor_epoch"] = st.session_state.get("editor_epoch", 0) + 1
    st.session_state["draft_data"] = inputs.model_dump(mode="json") if inputs else {}
    st.session_state["loaded_record"] = record
    st.session_state["document"] = record
    st.session_state["draft_id"] = record.id if record else uuid4()


def _scenario_picker(records: tuple[SavedScenario, ...]) -> None:
    """Load explicitly selected records while retaining drafts on ordinary reruns."""
    by_id = {str(record.id): record for record in records}
    if "editor_epoch" not in st.session_state:
        _set_editor(None)
    if pending := st.session_state.pop("pending_selection", None):
        st.session_state["scenario_choice"] = pending
    if imported := st.session_state.pop("pending_inputs", None):
        _set_editor(imported)
        st.session_state["loaded_selection"] = NEW_SCENARIO
    choices = [NEW_SCENARIO, *by_id]
    selected = st.selectbox(
        "Scenario",
        choices,
        key="scenario_choice",
        format_func=lambda key: "New scenario"
        if key == NEW_SCENARIO
        else (
            f"{by_id[key].inputs.name} · {by_id[key].inputs.state} {by_id[key].inputs.tax_year}"
        ),
    )
    if selected != st.session_state.get("loaded_selection", NEW_SCENARIO):
        record = by_id.get(selected)
        _set_editor(record.inputs if record else None, record)
    st.session_state["loaded_selection"] = selected
    reload_column, refresh_column = st.columns(2)
    if reload_column.button("Reload saved", disabled=selected == NEW_SCENARIO):
        record = by_id[selected]
        _set_editor(record.inputs, record)
    refresh_column.button("Refresh saved list")
    st.caption("Calculate to update results. Save before switching scenarios.")


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
    age = st.number_input(
        "Age at year end",
        min_value=18,
        max_value=64,
        value=data.get("age"),
        key=_key(f"{prefix}_age"),
    )
    amounts = {
        field: _money_input(label, data.get(field), f"{prefix}_{field}")
        for field, label in [
            ("cash_pay", "Annual cash salary + bonus ($)"),
            ("rsu_vesting", "Annual RSU vesting value ($)"),
            ("employee_401k", "Annual pretax 401(k) contribution ($)"),
        ]
    }
    rates = {}
    for field, label, default in [
        ("savings_rate", "Personal savings goal (% of cash + RSUs)", "0.10"),
        ("employer_retirement_rate", "Employer retirement (% of cash pay)", "0"),
    ]:
        percent = st.number_input(
            label,
            min_value=0.0,
            max_value=100.0,
            value=float(Decimal(data.get(field, default)) * 100),
            step=1.0,
            key=_key(f"{prefix}_{field}"),
        )
        rates[field] = str(Decimal(str(percent)) / 100)
    exempt = st.checkbox(
        "Exempt from WA Cares",
        value=data.get("wa_cares_exempt", False),
        key=_key(f"{prefix}_wa_cares_exempt"),
    )
    return {
        "name": name,
        "age": age,
        **amounts,
        **rates,
        "wa_cares_exempt": exempt if state == "WA" else False,
        "options": _option_inputs(prefix, data.get("options", {})),
    }


def _option_inputs(prefix: str, data: dict[str, Any]) -> dict[str, Any]:
    """Collect only the supported ISO exercise-and-hold event."""
    with st.expander("ISO exercise and hold"):
        st.caption(
            "Qualifying ISOs held through year end. No share sales or AMT credit carryforwards."
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
    """Submit all visible inputs together for calculation or persistence."""
    data = st.session_state["draft_data"]
    with st.form(_key("scenario_form")):
        name = st.text_input(
            "Scenario name", value=data.get("name", "New scenario"), key=_key("name")
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
        )
        ages = st.text_input(
            "Qualifying children's ages",
            value=", ".join(str(child["age"]) for child in data.get("dependents", [])),
            key=_key("children"),
            help="Ages at year end, separated by commas. Leave blank for none. Supported ages: 0-17.",
        )
        st.subheader("Income and savings")
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
        calculate = st.form_submit_button("Calculate", type="primary")
        save = st.form_submit_button(
            "Calculate and save", disabled=not st.session_state["store_available"]
        )
        copy = st.form_submit_button(
            "Save as new scenario", disabled=not st.session_state["store_available"]
        )
    if calculate or save or copy:
        _submit(
            {
                "name": name,
                "tax_year": year,
                "state": state,
                "adults": adults,
                "living_costs": living,
                "notes": notes,
                "assumptions_confirmed": confirmed,
            },
            ages,
            "copy" if copy else "save" if save else "calculate",
            dsn,
        )


def _submit(data: dict[str, Any], ages: str, action: str, dsn: str | None) -> None:
    """Keep results and their inputs together through validation and failed saves."""
    loaded = st.session_state.get("loaded_record")
    if action == "copy" and loaded and data["name"] == loaded.inputs.name:
        data["name"] = data["name"][:73] + " (copy)"
    try:
        data["dependents"] = [
            {"age": int(age.strip())} for age in ages.split(",") if age.strip()
        ]
        inputs = Scenario.model_validate(data)
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
    st.session_state["draft_data"] = inputs.model_dump(mode="json")
    st.session_state["document"] = document
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
    _set_editor(saved.inputs, saved)
    st.session_state["loaded_selection"] = str(saved.id)
    st.session_state["pending_selection"] = str(saved.id)
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
        "Retained RSUs": result.retained_rsus,
        "Purchased ISO shares (at exercise FMV)": result.option_value,
        "Total savings and assets added": result.total_savings_and_assets,
    }


def _results(document: SavedScenario, records: tuple[SavedScenario, ...]) -> None:
    """Display the last calculated or loaded document, never unsubmitted edits."""
    result = document.result
    st.subheader("Results")
    st.caption(
        f"{document.inputs.name} · {result.state} · {result.tax_year} · Last calculated inputs"
    )
    if not _current(result):
        st.warning(
            "Saved results use older calculation rules. Select Calculate to update this draft."
        )
    if result.taxes.rules_status == "projected_ca_parameters":
        st.warning(
            "2026 California income taxes use projected parameters in the pinned tax engine."
        )
    if result.annual_remaining_cash < 0:
        st.warning(
            "This scenario has a cash shortfall. It does not automatically borrow or sell more shares."
        )
    if not _comparison(document, records):
        columns = st.columns(3)
        columns[0].metric(
            "Average monthly remaining cash", f"${result.monthly_remaining_cash:,.2f}"
        )
        columns[1].metric("Annual retained RSUs", f"${result.retained_rsus:,.2f}")
        columns[2].metric(
            "Annual retirement contributions",
            f"${result.employee_401k + result.employer_retirement:,.2f}",
        )
    with st.expander("Annual calculation details"):
        st.table(
            [
                {"Item": label, "Annual dollars": f"${value:,.2f}"}
                for label, value in _annual_rows(result).items()
            ]
        )
        st.caption(
            "Total assets added includes remaining cash, personal saving, employer retirement, retained RSUs, and purchased shares. Purchased shares are not spendable cash."
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


def _comparison(document: SavedScenario, records: tuple[SavedScenario, ...]) -> bool:
    """Render three named comparison views when snapshots have matching rules."""
    if st.button(
        "Keep these results for comparison",
        disabled=not _current(document.result),
    ):
        st.session_state["session_baseline"] = document
    baselines = {str(record.id): record for record in records}
    if baseline := st.session_state.get("session_baseline"):
        baselines["session"] = baseline
    selected = st.selectbox(
        "Compare with",
        ["none", *baselines],
        format_func=lambda key: "Choose a scenario"
        if key == "none"
        else (
            f"{'Current session: ' if key == 'session' else ''}{baselines[key].inputs.name}"
        ),
        key="baseline_choice",
    )
    if selected == "none":
        return False
    baseline = baselines[selected]
    if baseline.inputs.tax_year != document.inputs.tax_year:
        st.info(
            "Choose scenarios from the same tax year for a like-for-like comparison."
        )
        return False
    if not _current(baseline.result) or not _current(document.result):
        st.info("Recalculate scenarios with the current rules before comparing them.")
        return False
    if (
        baseline.result.taxes.rules_status == "projected_ca_parameters"
        and document.result.taxes.rules_status != "projected_ca_parameters"
    ):
        st.warning("The comparison scenario uses projected 2026 California parameters.")
    st.html(Path(__file__).with_name("comparison.css"))
    for tab, render in zip(
        st.tabs(["Overview", "Cash flow", "Difference"]),
        (overview_html, cash_flow_html, difference_html),
        strict=True,
    ):
        with tab:
            st.html(render(baseline, document))
    return True


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
            st.session_state["pending_inputs"] = inputs
            st.session_state["pending_selection"] = NEW_SCENARIO
            st.rerun()
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


def main() -> None:
    """Run the household editor with either shared persistence or local preview."""
    st.set_page_config(
        page_title="Family financial model", page_icon="🏡", layout="wide"
    )
    st.title("Family financial model")
    st.write("Compare income, living costs, and what you can save in a year.")
    dsn = _database_url()
    if not dsn:
        st.info(
            "Local preview: drafts last only for this session. Download a backup to keep your work. Shared saving needs a database connection."
        )
    records = _read_store(dsn)
    if notice := st.session_state.pop("notice", None):
        st.success(notice)
    _scenario_picker(records)
    _assumptions()
    _form(dsn)
    document = st.session_state.get("document")
    if document:
        _results(document, records)
    _backups(document)
