"""Input libraries, retained raw drafts and assembly of reviewed planning copies."""

from __future__ import annotations

from functools import partial
from typing import Any, cast
from uuid import uuid4

import psycopg
import streamlit as st

from financial_model import input_forms as forms
from financial_model.planning import (
    INPUT_ADAPTER,
    PROFILE_KINDS,
    AllocationCheckpoint,
    Budget,
    BudgetReplacement,
    EconomicAssumptions,
    InvestmentAssumptions,
    Kind,
    PlanningChild,
    PlanningInputs,
    PlanningScenario,
    Residence,
    RetirementAssumptions,
    SavedPlanningDocument,
    SpendingAdjustment,
    SpendingAssumptions,
    TimeAnchor,
    Vesting,
    WorkAssumptions,
    WorkPhase,
    copy_annual_inputs,
    household_from_defaults,
    missing_details,
    replace_profile,
    replace_wealth,
    select_profile,
)
from financial_model.planning_storage import (
    create_planning_document,
    delete_planning_document,
    import_planning_backup,
    list_planning_documents,
    update_planning_document,
)
from financial_model.scenario_pages import category_link, navigate
from financial_model.schemas import SavedScenario, default_allocation_plan
from financial_model.setup_pages import _setup_error
from financial_model.storage import (
    MAX_BACKUP_BYTES,
    RevisionConflictError,
    load_household,
)

MODELS = {
    "work": WorkAssumptions,
    "spending": SpendingAssumptions,
    "investments": InvestmentAssumptions,
    "economy": EconomicAssumptions,
    "retirement": RetirementAssumptions,
    "plan": PlanningScenario,
}
LABELS = {
    "work": "Work",
    "spending": "Spending",
    "investments": "Investments",
    "economy": "Economy",
    "retirement": "Retirement",
    "plan": "Plans",
}


def _activate(draft_id: str) -> None:
    """Select a category draft without parsing or discarding intermediate text."""
    draft = st.session_state["planning_drafts"][draft_id]
    st.session_state.setdefault("planning_active", {})[draft["data"]["kind"]] = draft_id
    st.session_state["planning_epoch"] = st.session_state.get("planning_epoch", 0) + 1


def _new_draft(
    inputs: PlanningInputs, loaded: SavedPlanningDocument | None = None
) -> str:
    """Start an independent copy or a freshly loaded revision."""
    draft_id = str(loaded.id if loaded else uuid4())
    st.session_state.setdefault("planning_drafts", {})[draft_id] = {
        "data": inputs.model_dump(mode="json"),
        "loaded": loaded,
    }
    _activate(draft_id)
    return draft_id


def _draft(kind: str) -> dict[str, Any]:
    """Get the retained raw draft for a category."""
    return cast(
        dict[str, Any],
        st.session_state["planning_drafts"][st.session_state["planning_active"][kind]],
    )


def _scope(kind: str) -> str:
    """Scope widgets to a draft identity and explicit reload epoch."""
    return f"planning_{kind}_{st.session_state['planning_active'][kind]}_{st.session_state.get('planning_epoch', 0)}"


def _changed_rows() -> None:
    """Rebuild row widget identities after an explicit structural edit."""
    st.session_state["planning_epoch"] = st.session_state.get("planning_epoch", 0) + 1
    st.rerun()


def _read(dsn: str | None, kind: Kind) -> tuple[SavedPlanningDocument, ...]:
    """Isolate category store failures and preserve the last readable library."""
    available = st.session_state.setdefault("planning_available", {})
    cache = st.session_state.setdefault("planning_records", {})
    available[kind] = bool(dsn)
    if not dsn:
        return ()
    try:
        cache[kind] = list_planning_documents(dsn, kind)
    except (ValueError, psycopg.Error):
        available[kind] = False
        st.error(
            f"Saved {LABELS[kind]} could not be read. Your drafts are still here; check database setup or try Refresh."
        )
    return tuple(cache.get(kind, ()))


def _open(record: SavedPlanningDocument, *, duplicate: bool = False) -> None:
    """Resume edits, refreshing only an untouched old revision."""
    cached = (
        st.session_state.get("planning_drafts", {}).get(str(record.id))
        if not duplicate
        else None
    )
    if cached and cached["loaded"].revision != record.revision:
        try:
            if INPUT_ADAPTER.validate_python(cached["data"]) == cached["loaded"].inputs:
                cached = None
        except ValueError:
            pass  # Invalid edits remain recoverable when another session saves.
    if cached:
        _activate(str(record.id))
    else:
        _new_draft(record.inputs, None if duplicate else record)
    navigate(
        LABELS[record.inputs.kind],
        target="plan" if record.inputs.kind == "plan" else "library",
        view="editor",
    )


def _initial(kind: Kind, dsn: str | None) -> PlanningInputs:
    """Copy shared family labels when available, without inventing financial inputs."""
    inputs: PlanningInputs = MODELS[kind](name=f"New {kind}")
    if kind not in {"work", "retirement", "plan"}:
        return inputs
    try:
        household = (
            load_household(dsn) if dsn else st.session_state.get("local_household")
        )
    except (ValueError, psycopg.Error):
        st.warning(
            "Household defaults could not be loaded; this draft keeps generic adult labels."
        )
        household = None
    if household:
        raw = inputs.model_dump(mode="json")
        if kind == "plan":
            raw["household"] = household_from_defaults(household).model_dump(
                mode="json"
            )
        else:
            for adult, source in zip(raw["adults"], household.adults, strict=True):
                adult["name"] = source.name
        inputs = INPUT_ADAPTER.validate_python(raw)
    return inputs


def _status(inputs: PlanningInputs) -> None:
    """Show missing capture inputs without claiming an engine can project them."""
    details = missing_details(inputs)
    st.caption("Some details still need input" if details else "Inputs entered")
    if details:
        with st.expander("What still needs input"):
            for detail in details:
                st.text(detail)


def _summary(inputs: PlanningInputs) -> str:
    """Show the values that distinguish saved choices."""
    if isinstance(inputs, WorkAssumptions):
        return " · ".join(
            f"{a.name}: {_money_label(a.phases[0].cash_pay) if a.phases else 'pay not entered'}"
            for a in inputs.adults
        )
    if isinstance(inputs, SpendingAssumptions):
        return f"{_money_label(inputs.baseline.amount.central)} regular living costs · Budget changes: {len(inputs.replacements)} · Extra expenses: {len(inputs.adjustments)}"
    if isinstance(inputs, InvestmentAssumptions):
        mixes = [
            f"{c.mix.equities:.0%} stocks / {c.mix.fixed_income:.0%} bonds / {c.mix.cash:.0%} cash"
            for c in inputs.checkpoints
            if c.mix
        ]
        return " → ".join(mixes) or "Portfolio allocation not entered"
    if isinstance(inputs, EconomicAssumptions):
        return " · ".join(
            f"{label}: {f'{value.central:.1%}' if value.central is not None else 'not entered'}"
            for label, value in (
                ("Inflation", inputs.inflation),
                ("Stocks", inputs.equities),
                ("Bonds", inputs.fixed_income),
                ("Cash", inputs.cash),
            )
        )
    if isinstance(inputs, RetirementAssumptions):
        return " · ".join(
            f"{a.name}: retire {a.retirement.central or '—'}, plan through {a.age.central or '—'}"
            for a in inputs.adults
        )
    return f"{inputs.combination_count} {'combination' if inputs.combination_count == 1 else 'combinations'} · Wealth: {inputs.wealth.snapshot.title if inputs.wealth else 'not selected'}"


def _money_label(value: Any) -> str:
    """Keep summaries readable even while an amount is being edited."""
    from decimal import Decimal, InvalidOperation

    if value is None or value == "":
        return "Amount not entered"
    try:
        return f"${Decimal(str(value)):,.0f}/year"
    except InvalidOperation:
        return "Amount needs review"


def _review_rows(value: Any, path: tuple[str, ...] = ()) -> list[dict[str, str]]:
    """Present all copied input values without making a second editable form."""
    labels = {
        "money_year": "USD purchasing-power year",
        "cash_pay": "Salary and bonus ($)",
        "employee_401k": "Employee contribution ($)",
        "real_growth": "Salary growth above inflation",
        "employer_retirement_rate": "Employer contribution",
        "rsu_taxes_from_shares": "Use vested shares for taxes",
        "start": "Start year (inclusive)",
        "end": "End year (exclusive)",
        "when": "Effective time",
        "essential": "Essential spending ($/year)",
        "retirement_goal_id": "Retirement contribution goal",
        "option_goal_id": "Exercised-share goal",
        "rsu_rate": "Retained RSU allocation",
        "cash_amount": "Annual cash ($; blank means remainder)",
    }
    if isinstance(value, dict):
        rows = []
        for key, item in value.items():
            if key not in {"id", "revision", "annual_source", "slot"}:
                rows.extend(
                    _review_rows(
                        item,
                        (*path, labels.get(key, key.replace("_", " ").capitalize())),
                    )
                )
        return rows
    if isinstance(value, list):
        rows = []
        for index, item in enumerate(value, 1):
            label = (
                item.get("name") or item.get("label") or str(index)
                if isinstance(item, dict)
                else str(index)
            )
            rows.extend(_review_rows(item, (*path, label)))
        return rows
    if value == "":
        return []
    shown = (
        "Unknown"
        if value is None
        else "Yes"
        if value is True
        else "No"
        if value is False
        else str(value)
    )
    return [{"Assumption": " → ".join(path), "Entered value": shown}]


def _review(inputs: PlanningInputs, label: str) -> None:
    """Keep exact selected values inspectable after their source leaves the library."""
    with st.expander(label):
        data = inputs.model_dump(mode="json")
        saving = data.get("saving")
        if saving:
            goals = {row["goal"]["id"]: row["goal"]["name"] for row in saving["rows"]}
            for field in ("retirement_goal_id", "option_goal_id"):
                saving[field] = goals[saving[field]]
        st.caption(
            "Recorded values only. Rates are decimal fractions (0.05 = 5%). Money follows the stated reference year and each amount's basis."
        )
        st.table(_review_rows(data))


def _save(kind: Kind, dsn: str, inputs: PlanningInputs, *, new: bool) -> None:
    """Save validated inputs and preserve drafts after conflicts or store failures."""
    draft = _draft(kind)
    loaded = draft["loaded"]
    document = SavedPlanningDocument(inputs=inputs)
    if loaded and not new:
        document = SavedPlanningDocument(
            id=loaded.id, revision=loaded.revision, inputs=inputs
        )
    try:
        saved = (
            update_planning_document(dsn, document)
            if loaded and not new
            else create_planning_document(dsn, document)
        )
    except (ValueError, RevisionConflictError, psycopg.Error) as error:
        _setup_error(error)
        return
    if not loaded or not new:
        st.session_state["planning_drafts"].pop(
            st.session_state["planning_active"][kind], None
        )
    _new_draft(saved.inputs, saved)
    st.session_state["notice"] = "Inputs saved."
    st.rerun()


def _backups(
    kind: Kind,
    inputs: PlanningInputs | None = None,
    loaded: SavedPlanningDocument | None = None,
) -> None:
    """Export valid edits and import an identity-free draft of the same category."""
    with st.expander(f"{LABELS[kind]} backups"):
        if inputs:
            document = (
                SavedPlanningDocument(
                    id=loaded.id, revision=loaded.revision, inputs=inputs
                )
                if loaded
                else SavedPlanningDocument(inputs=inputs)
            )
            data = document.model_dump_json(indent=2)
            if len(data.encode()) <= MAX_BACKUP_BYTES:
                st.download_button(
                    "Download current inputs",
                    data,
                    file_name=f"{kind}.planning.json",
                    mime="application/json",
                    key=f"download_{kind}",
                )
            else:
                st.error("This backup exceeds the 1 MB import limit.")
        upload = st.file_uploader(
            f"Import {LABELS[kind]} backup",
            type=["json"],
            max_upload_size=1,
            key=f"planning_upload_{kind}",
        )
        imported = None
        if upload:
            try:
                imported = import_planning_backup(upload.getvalue())
                if imported.kind != kind:
                    imported = None
                    st.error("Open the matching category to import this backup.")
            except ValueError:
                st.error(
                    "This planning backup could not be read. Check its type, format and version."
                )
        if (
            st.button(
                "Load input backup as new draft",
                disabled=imported is None,
                key=f"import_{kind}",
            )
            and imported
        ):
            _new_draft(imported)
            navigate(
                LABELS[kind],
                target="plan" if kind == "plan" else "library",
                view="editor",
            )


@st.dialog("Delete saved inputs?", dismissible=False)
def _confirm_delete(dsn: str | None, record: SavedPlanningDocument) -> None:
    """Confirm a specific revision; saved combinations retain embedded copies."""
    st.text(
        f"{record.inputs.name} · {LABELS[record.inputs.kind]} · revision {record.revision}"
    )
    st.caption(
        "This deletes the library record for both users. Copies already saved in Plans remain unchanged."
    )
    if st.button("Cancel", key="planning_cancel_delete"):
        st.session_state.pop("planning_delete", None)
        st.rerun()
    if st.button("Delete inputs", type="primary", disabled=not dsn) and dsn:
        try:
            delete_planning_document(dsn, record.id, record.revision)
        except (RevisionConflictError, psycopg.Error) as error:
            _setup_error(error)
            return
        st.session_state.get("planning_drafts", {}).pop(str(record.id), None)
        if st.session_state.get("planning_active", {}).get(record.inputs.kind) == str(
            record.id
        ):
            st.session_state["planning_active"].pop(record.inputs.kind)
        st.session_state.pop("planning_delete", None)
        st.session_state["planning_records"][record.inputs.kind] = tuple(
            r
            for r in st.session_state["planning_records"].get(record.inputs.kind, ())
            if r.id != record.id
        )
        st.session_state["notice"] = "Inputs deleted."
        st.rerun()


def category_page(kind: Kind, dsn: str | None) -> None:
    """Display a library or its active retained editor without touching other drafts."""
    records = _read(dsn, kind)
    if st.session_state.get("workspace_views", {}).get(
        LABELS[kind]
    ) == "editor" and kind in st.session_state.get("planning_active", {}):
        _editor(kind, dsn, records)
        return
    st.subheader("Combined plans" if kind == "plan" else f"{LABELS[kind]} assumptions")
    st.caption(
        {
            "plan": "Choose one scenario to hold a category fixed, or several to compare alternatives. Forecasts come next.",
            "work": "Pay, salary growth, contributions, and equity compensation. Retirement dates belong in Retirement.",
            "spending": "Regular living costs and extra expenses. Retirement living costs belong in Retirement.",
            "retirement": "When each of you retires, what you spend, and how long to plan for.",
            "investments": "How you save and how your mix of stocks, bonds, and cash changes over time.",
            "economy": "Inflation and average investment returns, after fees and before tax.",
        }[kind]
    )
    with st.container(horizontal=True):
        if st.button(
            "New combined plan"
            if kind == "plan"
            else f"New {LABELS[kind]} assumptions",
            key=f"new_{kind}",
            type="primary",
        ):
            _new_draft(_initial(kind, dsn))
            navigate(
                LABELS[kind],
                target="plan" if kind == "plan" else "library",
                view="editor",
            )
        st.button(f"Refresh {LABELS[kind]}", key=f"refresh_{kind}")
    if not records:
        st.info(f"No saved {LABELS[kind]} inputs yet.")
    for record in records:
        with st.container(border=True):
            st.subheader(record.inputs.name)
            st.text(_summary(record.inputs))
            if missing_details(record.inputs):
                st.caption("Some details still need input")
            with st.container(horizontal=True):
                if st.button("Edit", key=f"planning_edit_{record.id}"):
                    _open(record)
                if st.button("Duplicate", key=f"planning_duplicate_{record.id}"):
                    _open(record, duplicate=True)
                if st.button(
                    "Delete",
                    key=f"planning_delete_{record.id}",
                    disabled=not st.session_state["planning_available"][kind],
                ):
                    st.session_state["planning_delete"] = record
    drafts = [
        (key, draft)
        for key, draft in st.session_state.get("planning_drafts", {}).items()
        if draft["data"]["kind"] == kind
    ]
    if drafts:
        with st.expander(f"{LABELS[kind]} session drafts"):
            for draft_id, draft in drafts:
                if st.button(
                    f"Resume {draft['data'].get('name') or 'Unnamed'} · {draft_id[:8]}",
                    key=f"planning_resume_{draft_id}",
                ):
                    _activate(draft_id)
                    navigate(
                        LABELS[kind],
                        target="plan" if kind == "plan" else "library",
                        view="editor",
                    )
    _backups(kind)
    if pending := st.session_state.get("planning_delete"):
        _confirm_delete(dsn, pending)


def _metadata(data: dict[str, Any], scope: str) -> None:
    """Edit names, money units and optional provenance without economic defaults."""
    forms.text(data, "name", "Name", f"name_{scope}")
    if "money_year" in data:
        forms.number(
            data,
            "money_year",
            "Amounts in this year's dollars",
            f"money_year_{scope}",
            integer=True,
            minimum=1900,
            maximum=9999,
            optional=True,
        )
    with st.expander("Notes and source (optional)"):
        forms.text(data, "notes", "Notes", f"notes_{scope}", multiline=True)
        source = data["source"]
        for field, label in (
            ("description", "Source description"),
            ("url", "Source URL"),
            ("version", "Publication / version"),
        ):
            forms.text(source, field, label, f"source_{field}_{scope}")
        forms.text(
            source,
            "as_of",
            "Source date (YYYY-MM-DD)",
            f"source_date_{scope}",
            optional=True,
        )
        if data.get("annual_source"):
            st.caption(
                f"Copied from annual plan {data['annual_source']['tax_year']}, revision {data['annual_source']['revision']}. Future dates and assumptions require review."
            )


def _editor(
    kind: Kind, dsn: str | None, records: tuple[SavedPlanningDocument, ...]
) -> None:
    """Render one category and save its own validated draft only."""
    draft = _draft(kind)
    data, loaded = draft["data"], draft["loaded"]
    category_link(
        f"All {LABELS[kind]} inputs", LABELS[kind], target="library", view="library"
    )
    st.info(
        f"Editing {'combined plan' if kind == 'plan' else LABELS[kind] + ' assumptions'}: {data['name']}"
    )
    scope = _scope(kind)
    _metadata(data, scope)
    if kind == "work":
        _work(data, scope)
    elif kind == "spending":
        _spending(data, scope)
    elif kind == "investments":
        _investments(data, scope, dsn)
    elif kind == "economy":
        _economy(data, scope)
    elif kind == "retirement":
        _retirement(data, scope)
    else:
        _assembly(data, scope, dsn)
    inputs = None
    try:
        inputs = INPUT_ADAPTER.validate_python(data)
    except ValueError as error:
        _setup_error(error)
    st.caption("Saved" if loaded and inputs == loaded.inputs else "Unsaved changes")
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
                "Save inputs", type="primary", disabled=not available or inputs is None
            )
            and dsn
            and inputs
        ):
            _save(kind, dsn, inputs, new=False)
        if (
            st.button("Save as new inputs", disabled=not available or inputs is None)
            and dsn
            and inputs
        ):
            _save(kind, dsn, inputs, new=True)
    if latest:
        with st.expander("Reload saved inputs"):
            if st.button("Discard edits and reload inputs", disabled=not available):
                _new_draft(latest.inputs, latest)
                st.rerun()
    _backups(kind, inputs, loaded)


def _range(
    data: dict[str, Any], label: str, scope: str, *, unit: str = "money"
) -> None:
    """Show the main assumption while keeping alternatives collapsed and intact."""
    _range_value(data, "central", label, scope, unit)
    lower, upper = data.get("lower"), data.get("upper")
    summary = "Range (optional)"
    if lower is not None or upper is not None:
        from decimal import Decimal, InvalidOperation

        try:
            values = [Decimal(str(v)) for v in (lower, upper)]
            shown = [
                f"{(v * 100).normalize():f}%"
                if unit == "rate"
                else f"${v:,.0f}"
                if unit in {"money", "once"}
                else f"{v:f}"
                for v in values
            ]
            summary = f"Range: {shown[0]} to {shown[1]}"
        except InvalidOperation:
            summary = "Range: complete both values"
    with st.expander(summary.replace("$", r"\$"), key=f"range_{scope}"):
        for column, field in zip(st.columns(2), ("lower", "upper"), strict=True):
            with column:
                _range_value(data, field, field.capitalize(), scope, unit)
        st.caption(
            "Enter both ends of a range to explore alternatives. These are not probabilities."
        )
        if st.button(
            "Remove range",
            key=f"clear_range_{scope}",
            disabled=lower is None and upper is None,
        ):
            data["lower"] = data["upper"] = None
            _changed_rows()


def _range_value(
    data: dict[str, Any], field: str, label: str, scope: str, unit: str
) -> None:
    """Use the same durable controls for central values and optional bounds."""
    key = f"{field}_{scope}"
    if unit == "rate":
        forms.percent(data, field, f"{label} (%)", key)
    elif unit in {"year", "age"}:
        forms.number(
            data,
            field,
            label,
            key,
            integer=True,
            minimum=1900 if unit == "year" else 18,
            maximum=9999 if unit == "year" else 130,
            optional=True,
        )
    else:
        forms.money(
            data, field, f"{label} ($)" if unit == "once" else f"{label} ($/year)", key
        )


def _time(data: dict[str, Any], label: str, scope: str) -> None:
    """Capture fixed or retirement-relative input timing."""
    forms.choice(
        data,
        "kind",
        label,
        ("year", "adult_1", "adult_2", "both"),
        f"anchor_{scope}",
        labels={
            "year": "Calendar year",
            "adult_1": "Household adult 1 retirement",
            "adult_2": "Household adult 2 retirement",
            "both": "Both retired (later date)",
        },
    )
    if data["kind"] == "year":
        data["offset"] = 0
        forms.number(
            data,
            "year",
            f"{label} year",
            f"anchor_year_{scope}",
            integer=True,
            minimum=1900,
            maximum=9999,
            optional=True,
        )
    else:
        data["year"] = None
        forms.number(
            data,
            "offset",
            "Years before (-) / after (+) retirement",
            f"offset_{scope}",
            integer=True,
            minimum=-100,
            maximum=100,
        )


def _remove(rows: list[dict[str, Any]], index: int, label: str, scope: str) -> None:
    """Remove only an explicitly chosen row and regenerate index-based widget keys."""
    if st.button(label, key=f"remove_{scope}"):
        rows.pop(index)
        _changed_rows()


def _add(
    rows: list[dict[str, Any]], value: dict[str, Any], label: str, scope: str
) -> None:
    """Append a new editable row without saving it."""
    if st.button(label, key=f"add_{scope}"):
        rows.append(value)
        _changed_rows()


def _work(data: dict[str, Any], scope: str) -> None:
    """Keep compensation and growth together, independent of retirement dates."""
    forms.rsu_funding(data, scope, annual=False)
    for i, column in enumerate(st.columns(2)):
        with column:
            adult = data["adults"][i]
            key = f"adult_{i}_{scope}"
            st.subheader(adult["name"])
            st.caption(
                "A blank end year continues until retirement. Salary growth of 0% keeps pay in line with inflation."
            )
            for j, phase in enumerate(adult["phases"]):
                phase_key = f"phase_{j}_{key}"
                with st.container(border=True):
                    for field, label in (
                        ("start", "Start year"),
                        ("end", "End year (first year excluded)"),
                    ):
                        forms.number(
                            phase,
                            field,
                            label,
                            f"{field}_{phase_key}",
                            integer=True,
                            minimum=1900,
                            maximum=9999,
                            optional=True,
                        )
                    forms.compensation(phase, phase_key, annual=False)
                    _range(
                        phase["real_growth"],
                        "Salary growth above inflation",
                        f"growth_{phase_key}",
                        unit="rate",
                    )
                    with st.expander("Work phase notes (optional)"):
                        forms.text(phase, "notes", "Notes", f"notes_{phase_key}")
                    _remove(adult["phases"], j, "Remove work phase", phase_key)
            phase = WorkPhase().model_dump(mode="json")
            phase["real_growth"]["central"] = "0"
            _add(adult["phases"], phase, "Add work phase", key)
            _vesting_and_options(adult, key)


def _vesting_and_options(adult: dict[str, Any], scope: str) -> None:
    """Keep future equity schedules and dated exercises separate from salary growth."""
    with st.expander("Vesting and option events"):
        for i, row in enumerate(adult["vesting"]):
            key = f"vesting_{i}_{scope}"
            with st.container(border=True):
                for field, label in (
                    ("start", "Vesting start year"),
                    ("end", "Vesting end year (exclusive)"),
                ):
                    forms.number(
                        row,
                        field,
                        label,
                        f"{field}_{key}",
                        integer=True,
                        minimum=1900,
                        maximum=9999,
                        optional=True,
                    )
                forms.choice(
                    row,
                    "basis",
                    "Vesting amount basis",
                    ("nominal", "real", "shares"),
                    f"basis_{key}",
                    labels={
                        "nominal": "Nominal dollars",
                        "real": "Reference-year dollars",
                        "shares": "Number of shares",
                    },
                )
                forms.text(
                    row,
                    "amount",
                    "Annual vesting amount",
                    f"amount_{key}",
                    optional=True,
                )
                forms.text(row, "notes", "Vesting notes", f"notes_{key}")
                _remove(adult["vesting"], i, "Remove vesting entry", key)
        _add(
            adult["vesting"],
            Vesting().model_dump(mode="json"),
            "Add vesting entry",
            f"vesting_{scope}",
        )
        for i, row in enumerate(adult["options"]):
            key = f"option_{i}_{scope}"
            with st.container(border=True):
                forms.number(
                    row,
                    "year",
                    "Option event year",
                    f"year_{key}",
                    integer=True,
                    minimum=1900,
                    maximum=9999,
                    optional=True,
                )
                if forms.optional_section(
                    row,
                    "award",
                    "Include an option award",
                    f"award_enabled_{key}",
                    {"kind": "ISO"},
                ):
                    forms.option_award(row["award"], key)
                if forms.optional_section(
                    row,
                    "exercise",
                    "Include an ISO exercise",
                    f"exercise_enabled_{key}",
                    {},
                ):
                    forms.option_exercise(row["exercise"], key)
                forms.text(row, "notes", "Option event notes", f"notes_{key}")
                _remove(adult["options"], i, "Remove option event", key)
        _add(
            adult["options"],
            {"year": None, "award": None, "exercise": None, "notes": ""},
            "Add dated option event",
            f"options_{scope}",
        )


def _budget(data: dict[str, Any], label: str, scope: str) -> None:
    """Collect a consumption budget and its optional essential portion."""
    _range(data["amount"], label, scope)
    with st.expander("Budget details (optional)"):
        forms.money(
            data,
            "essential",
            "Essential living costs ($/year)",
            f"essential_{scope}",
            help_text="The part of this budget you could not cut. Leave blank if you do not need this distinction.",
        )
        forms.text(data, "notes", "Included costs", f"notes_{scope}")


def _spending(data: dict[str, Any], scope: str) -> None:
    """Collect baseline replacements and explicit additions without projecting years."""
    st.caption(
        "Enter total household living costs, excluding taxes and saving. Use the purchasing power of the year above; inflation is set in Economy."
    )
    _budget(data["baseline"], "Regular living costs", f"baseline_{scope}")
    st.caption(
        "A change replaces regular living costs from its start date. It does not add another budget."
    )
    for i, row in enumerate(data["replacements"]):
        key = f"replacement_{i}_{scope}"
        with st.expander(
            f"{row['label']} · {_money_label(row['budget']['amount']['central'])}"
        ):
            forms.text(row, "label", "Change name", f"label_{key}")
            _time(row["when"], "Change starts", key)
            _budget(row["budget"], "New regular living costs", key)
            _remove(data["replacements"], i, "Remove change", key)
    _add(
        data["replacements"],
        BudgetReplacement(label="New budget").model_dump(mode="json"),
        "Change regular living costs",
        scope,
    )
    st.caption(
        "Extra expenses are added to whichever living-cost budget applies, including during retirement."
    )
    for i, row in enumerate(data["adjustments"]):
        key = f"adjustment_{i}_{scope}"
        with st.expander(
            f"{row['label']} · {_money_label(row['amount']['central']).removesuffix('/year') if row['frequency'] == 'once' else _money_label(row['amount']['central'])}"
        ):
            forms.text(row, "label", "Expense name", f"label_{key}")
            forms.choice(
                row,
                "frequency",
                "Frequency",
                ("recurring", "once"),
                f"frequency_{key}",
                labels={"recurring": "Annual, recurring", "once": "One-time"},
            )
            _range(
                row["amount"],
                "Extra annual cost"
                if row["frequency"] == "recurring"
                else "One-time amount",
                key,
                unit="money" if row["frequency"] == "recurring" else "once",
            )
            _time(row["start"], "Expense starts", key)
            if row["frequency"] == "once":
                row["end"] = None
            elif forms.optional_section(
                row,
                "end",
                "Specify an end date",
                f"end_enabled_{key}",
                TimeAnchor().model_dump(mode="json"),
            ):
                _time(row["end"], "Expense ends (exclusive)", f"end_{key}")
            forms.text(row, "notes", "Expense notes", f"notes_{key}")
            _remove(data["adjustments"], i, "Remove additional expense", key)
    _add(
        data["adjustments"],
        SpendingAdjustment(label="New expense").model_dump(mode="json"),
        "Add an expense",
        f"adjustment_{scope}",
    )


def _investments(data: dict[str, Any], scope: str, dsn: str | None) -> None:
    """Keep saving by goal separate from the target asset mix and stock-sale intention."""
    from financial_model import app

    st.subheader("Saving instructions")
    if forms.optional_section(
        data,
        "saving",
        "Include saving instructions",
        f"saving_enabled_{scope}",
        default_allocation_plan().model_dump(mode="json"),
    ):
        app._read_goal_catalogue(dsn)
        if forms.saving_instructions(
            data["saving"], scope, st.session_state.get("goals", ())
        ):
            _changed_rows()
    st.subheader("Portfolio strategy")
    forms.choice(
        data,
        "policy",
        "Allocation policy",
        ("undecided", "constant", "gradual"),
        f"policy_{scope}",
    )
    st.caption(
        "Target equities, fixed income and cash must total 100%. Gradual transitions record linear changes between checkpoints and hold the final target. Actual holdings stay in Wealth."
    )
    for i, row in enumerate(data["checkpoints"]):
        key = f"checkpoint_{i}_{scope}"
        with st.container(border=True):
            _time(row["when"], "Allocation effective", key)
            if forms.optional_section(
                row,
                "mix",
                "Specify target allocation",
                f"mix_enabled_{key}",
                {"equities": None, "fixed_income": None, "cash": None},
            ):
                for column, field in zip(
                    st.columns(3), ("equities", "fixed_income", "cash"), strict=True
                ):
                    with column:
                        forms.percent(
                            row["mix"],
                            field,
                            f"{field.replace('_', ' ').capitalize()} (%)",
                            f"{field}_{key}",
                        )
            _remove(data["checkpoints"], i, "Remove allocation checkpoint", key)
    _add(
        data["checkpoints"],
        AllocationCheckpoint().model_dump(mode="json"),
        "Add allocation checkpoint",
        scope,
    )
    forms.choice(
        data,
        "rebalancing",
        "Rebalancing",
        ("undecided", "annual", "flows_only"),
        f"rebalancing_{scope}",
        labels={
            "undecided": "Undecided",
            "annual": "Annual",
            "flows_only": "Contributions and withdrawals only",
        },
    )
    forms.choice(
        data,
        "employer_stock",
        "Employer shares",
        ("undecided", "keep", "diversify"),
        f"employer_stock_{scope}",
        labels={
            "undecided": "Undecided",
            "keep": "Keep",
            "diversify": "Gradually diversify",
        },
    )
    if data["employer_stock"] == "diversify":
        for field, label in (
            ("diversify_start", "Diversification starts"),
            ("diversify_end", "Diversification ends"),
        ):
            forms.number(
                data,
                field,
                label,
                f"{field}_{scope}",
                integer=True,
                minimum=1900,
                maximum=9999,
                optional=True,
            )
    else:
        data["diversify_start"] = data["diversify_end"] = None


def _economy(data: dict[str, Any], scope: str) -> None:
    """Capture sourced compound outlooks separately from optional annual variability."""
    st.caption(
        "Enter average yearly growth after investment fees and before tax. Returns include inflation; do not subtract it here."
    )
    fields = (
        ("inflation", "Inflation"),
        ("equities", "Stock return"),
        ("fixed_income", "Bond return"),
        ("cash", "Cash return"),
    )
    for pair in (fields[:2], fields[2:]):
        for column, (field, label) in zip(st.columns(2), pair, strict=True):
            with column:
                _range(data[field], label, f"{field}_{scope}", unit="rate")
    with st.expander("Simulation details (optional)"):
        st.caption(
            "Volatility measures how much yearly returns fluctuate. Correlation measures how they move together. Leave these blank until setting up a simulation."
        )
        # Use a retained row dictionary so callbacks can capture tuple elements too.
        for i, label in enumerate(("Equities", "Fixed income", "Cash", "Inflation")):
            key = f"volatility_{i}_{scope}"
            _list_percent(
                data["volatility"], i, f"{label} annual standard deviation (%)", key
            )
        include = st.checkbox(
            "Include correlation matrix",
            value=data["correlation"] is not None,
            key=f"correlations_{scope}",
        )
        if include:
            if data["correlation"] is None:
                data["correlation"] = [[None] * 4 for _ in range(4)]
            st.caption(
                "Rows and columns: equities, fixed income, cash, inflation. Enter all 16 values, including the diagonal; use -1 to 1, not percent."
            )
            for i, label in enumerate(
                ("Equities", "Fixed income", "Cash", "Inflation")
            ):
                for j, column in enumerate(st.columns(4)):
                    with column:
                        key = f"corr_{i}_{j}_{scope}"
                        value = st.text_input(
                            f"{label} / {('Equities', 'Fixed income', 'Cash', 'Inflation')[j]}",
                            value=str(data["correlation"][i][j])
                            if data["correlation"][i][j] is not None
                            else "",
                            key=key,
                            on_change=_list_capture,
                            args=(data["correlation"][i], j, key, False),
                        )
                        data["correlation"][i][j] = forms.blank(value)
        else:
            data["correlation"] = None


def _list_capture(row: list[Any], index: int, key: str, percent: bool) -> None:
    """Capture a fixed statistical vector entry before its controls disappear."""
    row[index] = (
        forms.percent_value(st.session_state[key])
        if percent
        else forms.blank(st.session_state[key])
    )


def _list_percent(row: list[Any], index: int, label: str, key: str) -> None:
    """Reuse percentage rendering and route its callback into a fixed vector."""
    from decimal import Decimal, InvalidOperation

    try:
        shown = str(Decimal(str(row[index])) * 100) if row[index] is not None else ""
    except InvalidOperation:
        shown = str(row[index])
    value = st.text_input(
        label,
        value=shown,
        key=key,
        on_change=_list_capture,
        args=(row, index, key, True),
    )
    row[index] = forms.percent_value(value)


def _retirement(data: dict[str, Any], scope: str) -> None:
    """Put retirement timing, lifespan, and retirement spending in one editor."""
    st.subheader("Retirement dates")
    st.caption("The retirement year is the first full year without employment income.")
    for i, column in enumerate(st.columns(2)):
        with column:
            adult = data["adults"][i]
            _range(
                adult["retirement"],
                f"{adult['name']} retires",
                f"retirement_{i}_{scope}",
                unit="year",
            )
    st.subheader("Living costs in retirement")
    st.caption(
        "Enter total household costs, not costs per person. These replace regular living costs; dated extra expenses continue separately."
    )
    for i, field in enumerate(("only_adult_1", "only_adult_2")):
        choice = data[field]
        label = f"Only {data['adults'][i]['name']} retired"
        with st.expander(label):
            forms.choice(
                choice,
                "mode",
                label,
                ("unknown", "continue", "replace"),
                f"budget_mode_{i}_{scope}",
                labels={
                    "unknown": "Choose a budget",
                    "continue": "Continue regular living costs",
                    "replace": "Use a different household budget",
                },
            )
            if choice["mode"] == "replace":
                if choice["budget"] is None:
                    choice["budget"] = Budget().model_dump(mode="json")
                _budget(choice["budget"], "Household living costs", f"only_{i}_{scope}")
            else:
                choice["budget"] = None
    _budget(data["both_retired"], "Both retired", f"both_{scope}")
    with st.expander("Living costs after one person dies"):
        st.caption(
            "The remaining person's total living costs, even if the death occurs before retirement. Housing and other shared costs may continue."
        )
        if forms.optional_section(
            data,
            "survivor",
            "Enter living costs after one person dies",
            f"survivor_enabled_{scope}",
            Budget().model_dump(mode="json"),
        ):
            _budget(
                data["survivor"], "Remaining person's living costs", f"survivor_{scope}"
            )
    st.subheader("How long to plan for")
    st.caption("The age through which each person needs the plan to cover spending.")
    forms.choice(
        data,
        "mode",
        "Lifespan assumptions",
        ("ages", "sample"),
        f"mode_{scope}",
        labels={
            "ages": "Choose planning ages",
            "sample": "Use a mortality model later",
        },
    )
    if data["mode"] == "ages":
        for i, column in enumerate(st.columns(2)):
            with column:
                adult = data["adults"][i]
                _range(
                    adult["age"],
                    f"Plan for {adult['name']} through age",
                    f"age_{i}_{scope}",
                    unit="age",
                )
    else:
        st.caption(
            "A mortality model would sample lifespans. No sampling runs here yet."
        )
        with st.expander("Mortality model details"):
            forms.choice(
                data,
                "table_basis",
                "Life table basis",
                ("unknown", "period", "cohort"),
                f"table_basis_{scope}",
                help_text="Period tables use current death rates; cohort tables allow rates to change over time.",
            )
            for i, adult in enumerate(data["adults"]):
                adult["age"] = {"central": None, "lower": None, "upper": None}
                forms.text(
                    adult,
                    "table_classification",
                    f"{adult['name']} table classification",
                    f"classification_{i}_{scope}",
                )


def _assembly(data: dict[str, Any], scope: str, dsn: str | None) -> None:
    """Choose and explicitly replace self-contained source copies in a Plan."""
    from financial_model import wealth_pages

    st.caption(
        "Choose one scenario to hold a category fixed, or add alternatives to vary it. Household and starting Wealth stay the same."
    )
    with st.container(horizontal=True):
        category_link("Edit plan Household", "Household", target="plan")
        category_link("Map plan Wealth", "Wealth", target="plan")
    for kind in PROFILE_KINDS:
        records = _read(dsn, kind)
        selected = [c for c in data["components"] if c["inputs"]["kind"] == kind]
        state = (
            "Choose a scenario"
            if not selected
            else f"Fixed · {selected[0]['inputs']['name']}"
            if len(selected) == 1
            else f"Vary · {len(selected)} alternatives"
        )
        with st.expander(f"{LABELS[kind]} · {state}", expanded=not selected):
            for selection in selected:
                inputs = INPUT_ADAPTER.validate_python(selection["inputs"])
                st.markdown(f"**{inputs.name}**")
                st.caption(_summary(inputs))
                _review(inputs, "Details")
                current = next(
                    (r for r in records if str(r.id) == selection["source_id"]), None
                )
                if st.session_state["planning_available"][kind]:
                    if current is None:
                        st.caption(
                            "Source no longer in library. This plan keeps its copy."
                        )
                    elif current.revision != selection["source_revision"]:
                        st.caption(
                            "Newer version available. Choose it below to update this copy."
                        )
                with st.container(horizontal=True):
                    if current and st.button(
                        "Edit source", key=f"source_{selection['source_id']}_{scope}"
                    ):
                        _open(current)
                    if st.button(
                        "Remove",
                        key=f"remove_selection_{selection['source_id']}_{scope}",
                    ):
                        data["components"].remove(selection)
                        _changed_rows()
            choices = {str(r.id): r for r in records}
            if choices:
                chosen = st.selectbox(
                    f"Choose {LABELS[kind]} from library",
                    list(choices),
                    format_func=partial(_profile_label, choices),
                    key=f"select_{kind}_{scope}",
                )
                st.caption(_summary(choices[chosen].inputs))
                with st.container(horizontal=True):
                    if st.button(
                        "Use only this scenario" if selected else "Choose",
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
                        _changed_rows()
            else:
                st.caption(f"Save a {LABELS[kind]} scenario to choose it here.")
    plan = PlanningScenario(name="Preview", components=data["components"])
    if plan.combination_count:
        st.info(
            f"{plan.combination_count} {'combination' if plan.combination_count == 1 else 'combinations'} selected. Forecast results are not calculated yet."
        )
        with st.expander("Preview combinations"):
            import itertools

            st.table(
                [
                    {LABELS[c.inputs.kind]: c.inputs.name for c in case.components}
                    for case in itertools.islice(plan.combinations(), 20)
                ]
            )
            if plan.combination_count > 20:
                st.caption("Showing the first 20 combinations.")
    wealth = wealth_pages._read(dsn)
    with st.expander("Starting Wealth", expanded=data["wealth"] is None):
        if data["wealth"]:
            from financial_model.wealth import SavedWealthSnapshot

            selected_wealth = SavedWealthSnapshot.model_validate(data["wealth"])
            st.text(
                f"Selected: {selected_wealth.snapshot.title} · {selected_wealth.snapshot.as_of} · revision {selected_wealth.revision}"
            )
            current_wealth = next(
                (r for r in wealth if r.id == selected_wealth.id), None
            )
            if st.session_state["wealth_store_available"] and (
                current_wealth is None
                or current_wealth.revision != selected_wealth.revision
            ):
                st.caption(
                    "Source no longer in library."
                    if current_wealth is None
                    else "Newer version available."
                )
            if st.button("Remove Wealth selection", key=f"remove_wealth_{scope}"):
                _apply_wealth(data, None)
        wealth_choices = {str(r.id): r for r in wealth}
        if wealth_choices:
            chosen_wealth = st.selectbox(
                "Choose Wealth from library",
                list(wealth_choices),
                format_func=lambda key: f"{wealth_choices[key].snapshot.title} · {wealth_choices[key].snapshot.as_of} · {key[:8]}",
                key=f"select_wealth_{scope}",
            )
            wealth_pages._summary(wealth_choices[chosen_wealth].snapshot)
            if st.button(
                "Replace Wealth" if data["wealth"] else "Choose Wealth",
                disabled=not st.session_state["wealth_store_available"],
            ):
                _apply_wealth(data, wealth_choices[chosen_wealth])
        else:
            st.caption("Save a Wealth snapshot to choose it here.")


def _profile_label(choices: dict[str, SavedPlanningDocument], key: str) -> str:
    """Disambiguate same-name records by their stable identity and reviewed revision."""
    record = choices[key]
    return (
        record.inputs.name
        if sum(r.inputs.name == record.inputs.name for r in choices.values()) == 1
        else f"{record.inputs.name} · {key[:8]}"
    )


def _alignment_label(
    names: list[str], household_names: list[str], mapping: tuple[int, int] | None
) -> str:
    """Explain both person mappings before the user confirms an alignment."""
    if mapping is None:
        return "Not reviewed"
    return " · ".join(
        f"{name} → {household_names[slot - 1]} (adult {slot})"
        for name, slot in zip(names, mapping, strict=True)
    )


def _apply_replacement(data: dict[str, Any], document: SavedPlanningDocument) -> None:
    """Replace the selected component while retaining unrelated invalid draft fields."""
    # Validate only the structural selection operation; unrelated draft text stays raw.
    plan = PlanningScenario(name="Selection", components=data["components"])
    updated = replace_profile(plan, document)
    data["components"] = updated.model_dump(mode="json")["components"]
    _changed_rows()


def _apply_wealth(data: dict[str, Any], document: Any) -> None:
    """Replace inventory and require new account/owner review, without name matching."""
    updated = replace_wealth(PlanningScenario(name="Selection"), document).model_dump(
        mode="json"
    )
    for field in ("wealth", "owners", "portfolio_accounts", "reserved_cash"):
        data[field] = updated[field]
    _changed_rows()


def plan_context(category: str, dsn: str | None) -> None:
    """Edit plan-local family facts or account bindings under their owning category."""
    if "plan" not in st.session_state.get("planning_active", {}):
        navigate("Plans", target="library", view="library")
    data = _draft("plan")["data"]
    scope = _scope("plan")
    st.info(f"Editing combined plan: {data['name']}")
    with st.container(horizontal=True):
        category_link("Return to Plans", "Plans", target="plan", view="editor")
        category_link(
            "Shared Household defaults"
            if category == "Household"
            else "All wealth snapshots",
            category,
            target="library",
            view="library",
        )
    if category == "Household":
        _household_context(data, scope)
    else:
        _wealth_context(data, scope)
    st.caption(
        "These edits belong to this combined plan. Return to Plans to save them."
    )


def _household_context(data: dict[str, Any], scope: str) -> None:
    """Edit copied demographics and explicitly align person-specific profiles."""
    household = data["household"]
    forms.number(
        household,
        "reference_year",
        "Ages at year end",
        f"household_year_{scope}",
        integer=True,
        minimum=1900,
        maximum=9999,
        optional=True,
    )
    for i, column in enumerate(st.columns(2)):
        with column:
            adult = household["adults"][i]
            st.subheader(f"Household adult {i + 1}")
            forms.text(adult, "name", "Name", f"household_name_{i}_{scope}")
            forms.number(
                adult,
                "age",
                "Age at reference year end",
                f"household_age_{i}_{scope}",
                integer=True,
                minimum=18,
                maximum=130,
                optional=True,
            )
            forms.choice(
                adult,
                "wa_cares_exempt",
                "WA Cares exemption",
                (None, True, False),
                f"wa_cares_{i}_{scope}",
                labels={None: "Unknown", True: "Exempt", False: "Not exempt"},
            )
    forms.choice(
        household,
        "filing_status",
        "Filing status",
        (None, "married_filing_jointly"),
        f"filing_{scope}",
        labels={None: "Unknown", "married_filing_jointly": "Married filing jointly"},
    )
    for i, child in enumerate(household["children"]):
        with st.container(border=True):
            forms.text(child, "name", "Child label", f"child_name_{i}_{scope}")
            forms.number(
                child,
                "birth_year",
                "Birth year (actual or expected)",
                f"child_year_{i}_{scope}",
                integer=True,
                minimum=1900,
                maximum=9999,
                optional=True,
            )
            _remove(household["children"], i, "Remove child", f"child_{i}_{scope}")
    _add(
        household["children"],
        PlanningChild(name="Child").model_dump(mode="json"),
        "Add child",
        f"child_{scope}",
    )
    st.caption(
        "Children's costs belong in Spending; adding a child does not create expenses."
    )
    for i, row in enumerate(household["residence"]):
        with st.container(border=True):
            forms.number(
                row,
                "year",
                "Residence effective year",
                f"residence_year_{i}_{scope}",
                integer=True,
                minimum=1900,
                maximum=9999,
                optional=True,
            )
            forms.text(
                row,
                "location",
                "Residence jurisdiction",
                f"residence_location_{i}_{scope}",
                optional=True,
            )
            _remove(
                household["residence"], i, "Remove residence", f"residence_{i}_{scope}"
            )
    _add(
        household["residence"],
        Residence().model_dump(mode="json"),
        "Add residence",
        f"residence_{scope}",
    )
    for component in data["components"]:
        kind = component["inputs"]["kind"]
        if kind in {"work", "retirement"}:
            names = [a["name"] for a in component["inputs"]["adults"]]
            household_names = [a["name"] for a in household["adults"]]
            mapping = component["adult_mapping"]
            selected = None if mapping is None else (mapping[0], mapping[1])
            key = f"mapping_{component['source_id']}_{scope}"
            choices: list[tuple[int, int] | None] = [None, (1, 2), (2, 1)]
            value = st.selectbox(
                f"Match people · {component['inputs']['name']}",
                choices,
                index=choices.index(selected),
                format_func=partial(_alignment_label, names, household_names),
                key=key,
                on_change=forms.capture,
                args=(component, "adult_mapping", key),
            )
            component["adult_mapping"] = value


def _wealth_context(data: dict[str, Any], scope: str) -> None:
    """Map exact embedded Wealth rows and choose the strategy's account coverage."""
    if data["wealth"] is None:
        st.info("Choose a Wealth snapshot in Plans first.")
        return
    snapshot = data["wealth"]["snapshot"]
    st.subheader(snapshot["name"] or snapshot["as_of"])
    st.caption(
        "Bindings apply to this plan's saved copy. Account balances and holdings are edited in the Wealth library."
    )
    owners = {b["row_id"]: b for b in data["owners"]}
    labels = {
        "unknown": "Unknown",
        "joint": "Joint",
        "other": "Other",
        **{
            f"adult_{i + 1}": f"{a['name']} (adult {i + 1})"
            for i, a in enumerate(data["household"]["adults"])
        },
    }
    for row in (*snapshot["accounts"], *snapshot["direct_assets"], *snapshot["debts"]):
        if row["id"] not in owners:
            binding = {"row_id": row["id"], "owner": "unknown"}
            data["owners"].append(binding)
            owners[row["id"]] = binding
        forms.choice(
            owners[row["id"]],
            "owner",
            f"{row['label']} · reported owner: {row.get('owner') or 'unknown'}",
            tuple(labels),
            f"owner_{row['id']}_{scope}",
            labels=labels,
        )
    st.subheader("Portfolio accounts")
    st.caption(
        "Choose explicitly. Property, debt and unclassified holdings are not assumed to be a stock/bond/cash portfolio."
    )
    for row in snapshot["accounts"]:
        key = f"portfolio_{row['id']}_{scope}"
        st.checkbox(
            row["label"],
            value=row["id"] in data["portfolio_accounts"],
            key=key,
            on_change=_portfolio_choice,
            args=(data, row["id"], key),
        )
    forms.money(
        data,
        "reserved_cash",
        "Cash reserved within selected accounts ($)",
        f"reserved_cash_{scope}",
    )


def _portfolio_choice(data: dict[str, Any], account_id: str, key: str) -> None:
    """Retain explicit account IDs instead of guessing membership from their labels."""
    values = data["portfolio_accounts"]
    if st.session_state[key] and account_id not in values:
        values.append(account_id)
    elif not st.session_state[key] and account_id in values:
        values.remove(account_id)


def copy_from_annual(document: SavedScenario, *, stale: bool) -> None:
    """Preview an explicit conversion and create three independent unsaved drafts."""
    with st.expander("Create assumption profiles from this annual plan"):
        st.caption(
            "Uses the last calculated inputs. Work copies one year's pay and vesting; Spending converts monthly living expenses times 12; Investments copies saving instructions. Future dates and portfolio targets still need review."
        )
        profiles = copy_annual_inputs(document)
        for inputs in profiles:
            st.text(f"{LABELS[inputs.kind]}: {_summary(inputs)}")
        if stale:
            st.info("Calculate current edits before copying these inputs.")
        if st.button("Create unsaved assumption drafts", disabled=stale):
            for inputs in profiles:
                _new_draft(inputs)
                st.session_state.setdefault("workspace_views", {})[
                    LABELS[inputs.kind]
                ] = "editor"
            st.session_state["notice"] = (
                "Work, Spending and Investments drafts created. Review and save each separately."
            )
            navigate("Work", target="library", view="editor")
