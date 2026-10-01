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
    AllocationCheckpoint,
    Budget,
    BudgetReplacement,
    EconomicAssumptions,
    InvestmentAssumptions,
    Kind,
    LongevityAssumptions,
    PlanningChild,
    PlanningInputs,
    PlanningScenario,
    Residence,
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
    "longevity": LongevityAssumptions,
    "plan": PlanningScenario,
}
LABELS = {
    "work": "Work",
    "spending": "Spending",
    "investments": "Investments",
    "economy": "Economy",
    "longevity": "Longevity",
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
    if kind not in {"work", "longevity", "plan"}:
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
    st.caption(f"Needs input · {len(details)} items" if details else "Inputs entered")
    if details:
        with st.expander("Input checks"):
            for detail in details:
                st.text(detail)
    st.caption(
        "Input collection only. Projection, tax, mortality and withdrawal requirements are not evaluated."
    )


def _summary(inputs: PlanningInputs) -> str:
    """Describe the main entered assumptions without projecting financial outcomes."""
    if isinstance(inputs, WorkAssumptions):
        return " · ".join(
            f"{a.name}: retirement {a.retirement.central or 'unknown'}, {len(a.phases)} work phases"
            for a in inputs.adults
        )
    if isinstance(inputs, SpendingAssumptions):
        amount = inputs.baseline.amount.central
        return f"Baseline {'unknown' if amount is None else f'${amount:,.2f}/year'} · {len(inputs.replacements)} replacements · {len(inputs.adjustments)} additional expenses"
    if isinstance(inputs, InvestmentAssumptions):
        return f"{inputs.policy.capitalize()} allocation · {len(inputs.checkpoints)} checkpoints · Rebalancing: {inputs.rebalancing.replace('_', ' ')}"
    if isinstance(inputs, EconomicAssumptions):
        inflation = inputs.inflation.central
        return f"Inflation {'unknown' if inflation is None else f'{inflation:.2%}'} · Forecast horizon {inputs.forecast_years or 'unknown'} years"
    if isinstance(inputs, LongevityAssumptions):
        return (
            "Sample lifespans later"
            if inputs.mode == "sample"
            else " · ".join(
                f"{a.name}: planning age {a.age.central or 'unknown'}"
                for a in inputs.adults
            )
        )
    return f"{len(inputs.components)} selected profiles · Wealth: {inputs.wealth.snapshot.title if inputs.wealth else 'not selected'}"


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
        "forecast_years": "Forecast horizon (years)",
        "fees": "Annual fees",
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
        "Reusable inputs for future planning. Saving here does not calculate projections."
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
            st.caption(
                f"Saved · {str(record.id)[:8]} · Revision {record.revision} · {len(missing_details(record.inputs))} input checks"
            )
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
            "Money reference year (USD purchasing power)",
            f"money_year_{scope}",
            integer=True,
            minimum=1900,
            maximum=9999,
            optional=True,
        )
    forms.text(data, "notes", "Notes", f"notes_{scope}", multiline=True)
    with st.expander("Source and evidence"):
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
    elif kind == "longevity":
        _longevity(data, scope)
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
    """Capture a central value and optional paired alternatives without distributions."""
    st.markdown(f"**{label}**")
    bounded = st.checkbox(
        "Include exploration range",
        value=data.get("lower") is not None or data.get("upper") is not None,
        key=f"bounds_{scope}",
    )
    fields = ("lower", "central", "upper") if bounded else ("central",)
    if not bounded:
        data["lower"] = data["upper"] = None
    for column, field in zip(st.columns(len(fields)), fields, strict=True):
        with column:
            title = f"{field.capitalize()} {label}"
            if unit == "rate":
                forms.percent(data, field, f"{title} (%)", f"{field}_{scope}")
            elif unit in {"year", "age"}:
                forms.number(
                    data,
                    field,
                    title,
                    f"{field}_{scope}",
                    integer=True,
                    minimum=1900 if unit == "year" else 18,
                    maximum=9999 if unit == "year" else 130,
                    optional=True,
                )
            else:
                forms.money(data, field, f"{title} ($/year)", f"{field}_{scope}")
    if bounded:
        st.caption(
            "Alternatives only; these bounds have no probabilities or sampling distribution."
        )
    forms.text(data, "notes", f"{label} notes", f"range_notes_{scope}")


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
    """Reuse compensation controls with explicit work, vesting and retirement dates."""
    forms.rsu_funding(data, scope, annual=False)
    for i, column in enumerate(st.columns(2)):
        with column:
            adult = data["adults"][i]
            key = f"adult_{i}_{scope}"
            st.subheader(adult["name"])
            _range(
                adult["retirement"], "Retirement year", f"retirement_{key}", unit="year"
            )
            st.caption(
                "Retirement is the first year with no employment. Phase end years are exclusive. Salary growth is above inflation."
            )
            for j, phase in enumerate(adult["phases"]):
                phase_key = f"phase_{j}_{key}"
                with st.container(border=True):
                    for field, label in (
                        ("start", "Phase start year"),
                        ("end", "Phase end year (exclusive)"),
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
                    forms.text(phase, "notes", "Phase notes", f"notes_{phase_key}")
                    _remove(adult["phases"], j, "Remove work phase", phase_key)
            phase = WorkPhase().model_dump(mode="json")
            phase["real_growth"]["central"] = "0"
            _add(adult["phases"], phase, "Add work phase", key)
            st.caption(
                "New phases start with editable 0% real salary growth: pay keeps pace with inflation."
            )
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
    forms.money(
        data, "essential", "Essential portion ($/year, optional)", f"essential_{scope}"
    )
    forms.text(
        data, "notes", "Included costs / notes", f"notes_{scope}", multiline=True
    )


def _spending(data: dict[str, Any], scope: str) -> None:
    """Collect baseline replacements and explicit additions without projecting years."""
    st.caption(
        "Household consumption in reference-year dollars. Exclude taxes and investment contributions. Additional expenses are on top of the active baseline."
    )
    _budget(data["baseline"], "Baseline spending", f"baseline_{scope}")
    for i, row in enumerate(data["replacements"]):
        key = f"replacement_{i}_{scope}"
        with st.container(border=True):
            forms.text(row, "label", "Replacement label", f"label_{key}")
            _time(row["when"], "Replacement starts", key)
            _budget(row["budget"], "Replacement spending", key)
            _remove(data["replacements"], i, "Remove replacement", key)
    _add(
        data["replacements"],
        BudgetReplacement(label="New budget").model_dump(mode="json"),
        "Add replacement budget",
        scope,
    )
    for i, row in enumerate(data["adjustments"]):
        key = f"adjustment_{i}_{scope}"
        with st.container(border=True):
            forms.text(row, "label", "Additional expense label", f"label_{key}")
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
                "Additional spending"
                if row["frequency"] == "recurring"
                else "One-time amount",
                key,
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
        "Add additional expense",
        f"adjustment_{scope}",
    )
    if forms.optional_section(
        data,
        "survivor",
        "Specify survivor spending",
        f"survivor_enabled_{scope}",
        Budget().model_dump(mode="json"),
    ):
        _budget(data["survivor"], "Survivor spending", f"survivor_{scope}")


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
        "Returns are nominal annualized compound total returns before fees and taxes. Exploration bounds are alternative outlooks, not limits on one-year market movements."
    )
    for field, label in (
        ("inflation", "General inflation"),
        ("equities", "Equity return"),
        ("fixed_income", "Fixed income return"),
        ("cash", "Cash return"),
    ):
        _range(data[field], label, f"{field}_{scope}", unit="rate")
    forms.number(
        data,
        "forecast_years",
        "Forecast horizon (years)",
        f"horizon_{scope}",
        integer=True,
        minimum=1,
        maximum=150,
        optional=True,
    )
    forms.percent(data, "fees", "Annual investment fees (%)", f"fees_{scope}")
    with st.expander("Advanced: annual variability and relationships"):
        st.caption(
            "Optional inputs for a later calibrated simulation. Missing correlations do not mean independence."
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


def _longevity(data: dict[str, Any], scope: str) -> None:
    """Record fixed horizons or sampling intent, without loading a mortality model."""
    forms.choice(
        data,
        "mode",
        "Longevity assumption",
        ("ages", "sample"),
        f"mode_{scope}",
        labels={"ages": "Specified planning ages", "sample": "Sample lifespans later"},
    )
    st.caption(
        "Lifespan is an input. When funds run out is a future calculated outcome."
    )
    if data["mode"] == "sample":
        forms.choice(
            data,
            "table_basis",
            "Life table basis",
            ("unknown", "period", "cohort"),
            f"table_basis_{scope}",
        )
        st.caption(
            "Record the table, publication/version and date under Source and evidence. No lifespans are sampled here."
        )
    for i, column in enumerate(st.columns(2)):
        with column:
            adult = data["adults"][i]
            st.subheader(adult["name"])
            if data["mode"] == "ages":
                _range(adult["age"], "Planning age", f"age_{i}_{scope}", unit="age")
            else:
                adult["age"] = {
                    "central": None,
                    "lower": None,
                    "upper": None,
                    "notes": "",
                }
                forms.text(
                    adult,
                    "table_classification",
                    "Table classification",
                    f"classification_{i}_{scope}",
                )


def _assembly(data: dict[str, Any], scope: str, dsn: str | None) -> None:
    """Choose and explicitly replace self-contained source copies in a Plan."""
    from financial_model import wealth_pages

    st.caption(
        "Selected inputs are saved copies. Library changes never update this plan automatically."
    )
    with st.container(horizontal=True):
        category_link("Edit plan Household", "Household", target="plan")
        category_link("Map plan Wealth", "Wealth", target="plan")
    for kind in ("work", "spending", "investments", "economy", "longevity"):
        records = _read(dsn, kind)
        selected = next(
            (c for c in data["components"] if c["inputs"]["kind"] == kind), None
        )
        with st.expander(LABELS[kind], expanded=selected is None):
            if selected:
                inputs = INPUT_ADAPTER.validate_python(selected["inputs"])
                st.text(
                    f"Selected: {inputs.name} · revision {selected['source_revision']}"
                )
                st.text(_summary(inputs))
                _review(inputs, "Review selected inputs")
                current = next(
                    (r for r in records if str(r.id) == selected["source_id"]), None
                )
                if st.session_state["planning_available"][kind]:
                    if current is None:
                        st.caption(
                            "Source no longer in library. This plan retains its saved copy."
                        )
                    elif current.revision != selected["source_revision"]:
                        st.caption(
                            "Newer version available. Replace explicitly to use it."
                        )
                if current and st.button("Open source", key=f"source_{kind}_{scope}"):
                    _open(current)
                if st.button(
                    "Remove selection", key=f"remove_selection_{kind}_{scope}"
                ):
                    data["components"] = [
                        c for c in data["components"] if c["inputs"]["kind"] != kind
                    ]
                    if kind in {"work", "longevity"}:
                        data[f"{kind}_adults"] = None
                    _changed_rows()
            choices = {str(r.id): r for r in records}
            if choices:
                chosen = st.selectbox(
                    f"Choose {LABELS[kind]} from library",
                    list(choices),
                    format_func=partial(_profile_label, choices),
                    key=f"select_{kind}_{scope}",
                )
                st.text(_summary(choices[chosen].inputs))
                _review(choices[chosen].inputs, "Review library inputs before choosing")
                if st.button(
                    "Replace from library" if selected else "Choose",
                    key=f"choose_{kind}_{scope}",
                    disabled=not st.session_state["planning_available"][kind],
                ):
                    _apply_replacement(data, choices[chosen])
            else:
                st.caption(f"Save a {LABELS[kind]} profile to choose it here.")
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
    return f"{record.inputs.name} · {key[:8]} · r{record.revision}"


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
    if document.inputs.kind in {"work", "longevity"}:
        data[f"{document.inputs.kind}_adults"] = None
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
    for kind in ("work", "longevity"):
        component = next(
            (c for c in data["components"] if c["inputs"]["kind"] == kind), None
        )
        if component:
            names = [a["name"] for a in component["inputs"]["adults"]]
            household_names = [a["name"] for a in household["adults"]]
            mapping = data[f"{kind}_adults"]
            selected = None if mapping is None else (mapping[0], mapping[1])
            key = f"mapping_{kind}_{scope}"
            choices: list[tuple[int, int] | None] = [None, (1, 2), (2, 1)]
            value = st.selectbox(
                f"{LABELS[kind]} adult alignment",
                choices,
                index=choices.index(selected),
                format_func=partial(_alignment_label, names, household_names),
                key=key,
                on_change=forms.capture,
                args=(data, f"{kind}_adults", key),
            )
            data[f"{kind}_adults"] = value


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
