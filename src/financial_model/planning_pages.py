"""Input libraries, retained raw drafts and assembly of reviewed planning copies."""

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from functools import partial
from typing import Any, cast
from uuid import uuid4

import streamlit as st

from financial_model import input_forms as forms
from financial_model.planning import (
    INPUT_ADAPTER,
    PROFILE_KINDS,
    AllocationCheckpoint,
    EconomicAssumptions,
    ExpenseLevel,
    ExpenseSchedule,
    HouseholdAssumptions,
    InvestmentAssumptions,
    Kind,
    PlanningChild,
    PlanningInputs,
    PlanningScenario,
    Residence,
    RetirementAssumptions,
    SavedPlanningDocument,
    SpendingAssumptions,
    TimeAnchor,
    Vesting,
    WorkAssumptions,
    WorkPhase,
    copy_annual_inputs,
    household_from_defaults,
    missing_details,
    plan_years,
    replace_household,
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
    "household": HouseholdAssumptions,
    "work": WorkAssumptions,
    "spending": SpendingAssumptions,
    "investments": InvestmentAssumptions,
    "economy": EconomicAssumptions,
    "retirement": RetirementAssumptions,
    "plan": PlanningScenario,
}
LABELS = {
    "household": "Household",
    "work": "Work",
    "spending": "Spending",
    "investments": "Investments",
    "economy": "Economy",
    "retirement": "Retirement",
    "plan": "Plans",
}
GLIDE_SHAPES = {
    "linear_then_hold": "Linear — steady change",
    "log_then_hold": "Logarithmic — faster at first",
    "exponential_then_hold": "Exponential — faster near the end",
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
    """Read current library records without offering cached choices after failure."""
    available = st.session_state.setdefault("planning_available", {})
    cache = st.session_state.setdefault("planning_records", {})
    available[kind] = bool(dsn)
    if not dsn:
        return ()
    try:
        cache[kind] = list_planning_documents(dsn, kind)
    except ValueError as error:
        available[kind] = False
        _setup_error(error)
        return ()
    except sqlite3.Error:
        available[kind] = False
        st.error(
            f"Saved {LABELS[kind]} could not be read. Your drafts are still here; check database setup or try Refresh."
        )
        return ()
    return tuple(cache.get(kind, ()))


def _has_unsaved_edits(draft: dict[str, Any]) -> bool:
    """Keep new and edited drafts visible without duplicating saved library records."""
    loaded: SavedPlanningDocument | None = draft["loaded"]
    if loaded is None:
        return True
    try:
        return INPUT_ADAPTER.validate_python(draft["data"]) != loaded.inputs
    except ValueError:
        return True


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
    if kind not in {"household", "work", "retirement", "plan"}:
        return inputs
    try:
        household = (
            load_household(dsn) if dsn else st.session_state.get("local_household")
        )
    except (ValueError, sqlite3.Error):
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
        elif kind == "household":
            raw.update(
                household_from_defaults(household).model_dump(
                    mode="json", exclude={"residence"}
                )
            )
        else:
            for adult, source in zip(raw["adults"], household.adults, strict=True):
                adult["name"] = source.name
        inputs = INPUT_ADAPTER.validate_python(raw)
    return inputs


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


def _summary(inputs: PlanningInputs) -> str:
    """Show the values that distinguish saved choices."""
    if isinstance(inputs, HouseholdAssumptions):
        return f"{' · '.join(a.name for a in inputs.adults)} · {len(inputs.children)} {'child' if len(inputs.children) == 1 else 'children'}"
    if isinstance(inputs, WorkAssumptions):
        return " · ".join(
            f"{a.name}: {_money_label(a.phases[0].cash_pay) if a.phases else 'pay not entered'}"
            for a in inputs.adults
        )
    if isinstance(inputs, SpendingAssumptions):
        return f"{len(inputs.expenses)} expenses · {sum(len(e.levels) for e in inputs.expenses)} dated amounts"
    if isinstance(inputs, InvestmentAssumptions):
        mixes = [
            f"{c.mix.equities:.0%} stocks / {c.mix.fixed_income:.0%} bonds / {c.mix.cash:.0%} cash"
            for c in inputs.checkpoints
            if c.mix
        ]
        summary = " → ".join(mixes) or "Portfolio allocation not entered"
        if inputs.policy == "gradual":
            return f"{GLIDE_SHAPES[inputs.interpolation]} · {summary}"
        return summary
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
    from decimal import Decimal

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
        "real_growth": "Growth above inflation",
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
            for row in saving["rows"]:
                if row["cash_amount"] is None:
                    row["cash_amount"] = "All surplus"
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
    except (ValueError, RevisionConflictError, sqlite3.Error) as error:
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
        except (RevisionConflictError, sqlite3.Error) as error:
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
        summary.caption(_summary(record.inputs).replace("$", r"\$"))
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
                _open(record)
            if forms.row_action(
                "Duplicate", "content_copy", f"planning_duplicate_{record.id}"
            ):
                _open(record, duplicate=True)
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
        if draft["data"]["kind"] == kind and _has_unsaved_edits(draft)
    ]
    if drafts:
        with st.container(border=True):
            st.markdown("**Continue editing**")
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
    name, year = st.columns((3, 1))
    with name:
        forms.text(data, "name", "Name", f"name_{scope}")
    if "money_year" in data:
        with year:
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


def _legacy_inputs(data: dict[str, Any]) -> bool:
    """Identify obsolete draft fields without interpreting or converting them."""
    if data["kind"] == "spending":
        return "expenses" not in data
    if data["kind"] == "retirement":
        return any(
            field in data
            for field in ("only_adult_1", "only_adult_2", "both_retired", "survivor")
        )
    if data["kind"] == "plan":
        return any(_legacy_inputs(row["inputs"]) for row in data["components"]) or any(
            "id" not in child for child in data["household"]["children"]
        )
    return False


def _preserve_legacy_draft(
    kind: Kind,
    draft: dict[str, Any],
    records: tuple[SavedPlanningDocument, ...],
) -> bool:
    """Block obsolete editors while retaining an export of every unsaved input."""
    data, loaded = draft["data"], draft["loaded"]
    current = SavedPlanningDocument.model_fields["schema_version"].default
    if not (loaded and loaded.schema_version != current) and not _legacy_inputs(data):
        return False
    st.error(
        "This draft uses an older planning format and cannot be edited in this version. Your unsaved entries are unchanged."
    )
    st.download_button(
        "Download preserved draft",
        json.dumps(data, indent=2),
        file_name=f"{kind}.preserved-draft.json",
        mime="application/json",
        key=f"preserved_draft_{kind}",
    )
    st.caption(
        "This recovery file contains raw unsaved inputs, not a current-format import. Export it before reloading. Use the original app version to convert and export current-format saved inputs, then reopen them here. Recreate unsaved changes from the recovery file after migration."
    )
    latest = next(
        (
            record
            for record in records
            if loaded and record.id == loaded.id and record.schema_version == current
        ),
        None,
    )
    if latest and st.button(
        "Discard preserved draft and reload migrated inputs",
        key=f"reload_preserved_{kind}",
    ):
        _new_draft(latest.inputs, latest)
        st.rerun()
    return True


def _editor(
    kind: Kind, dsn: str | None, records: tuple[SavedPlanningDocument, ...]
) -> None:
    """Render one category and save its own validated draft only."""
    draft = _draft(kind)
    data, loaded = draft["data"], draft["loaded"]
    category_link(
        f"All {LABELS[kind]} inputs", LABELS[kind], target="library", view="library"
    )
    if _preserve_legacy_draft(kind, draft, records):
        return
    st.caption("Combined plan" if kind == "plan" else f"{LABELS[kind]} assumptions")
    scope = _scope(kind)
    _metadata(data, scope)
    if kind == "household":
        _household_demographics(data, scope)
    elif kind == "work":
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
            and dsn
            and inputs
        ):
            _save(kind, dsn, inputs, new=False)
        if (
            st.button(
                "Save as new plan" if kind == "plan" else "Save as new inputs",
                disabled=not available or inputs is None,
            )
            and dsn
            and inputs
        ):
            _save(kind, dsn, inputs, new=True)
        if kind == "plan":
            from financial_model.app import PAGE_DIR

            st.page_link(str(PAGE_DIR / "forecast.py"), label="Open Forecast")
    if kind == "plan" and not saved:
        st.caption("Save this plan before selecting it in Forecast.")
    if latest:
        with st.expander("Reload saved inputs"):
            if st.button("Discard edits and reload inputs", disabled=not available):
                _new_draft(latest.inputs, latest)
                st.rerun()
    _backups(kind, inputs, loaded)


def _range_summary(data: dict[str, Any], unit: str = "money") -> str:
    """Show entered alternatives even when their controls are hidden."""
    from decimal import Decimal

    values = (data.get("lower"), data.get("upper"))
    if all(value is None for value in values):
        return "—"
    try:
        shown = [
            f"{(Decimal(str(v)) * 100).normalize():f}%"
            if unit == "rate"
            else f"\\${Decimal(str(v)):,.0f}"
            if unit in {"money", "once"}
            else str(v)
            for v in values
        ]
    except InvalidOperation:
        return "Range needs input"
    if any(value is None for value in values):
        return "Range needs input"
    return " to ".join(shown)


def _range_limits(
    data: dict[str, Any], scope: str, unit: str, columns: list[Any]
) -> None:
    """Edit both bounds without clearing them when the row is collapsed."""
    for column, field in zip(columns[:2], ("lower", "upper"), strict=True):
        with column:
            _range_value(data, field, field.capitalize(), scope, unit, compact=True)
    with columns[2]:
        if forms.row_action("Remove range", "close", f"clear_range_{scope}"):
            data["lower"] = data["upper"] = None
            _changed_rows()


def _range_details(
    data: dict[str, Any], label: str, scope: str, unit: str = "money"
) -> None:
    """Keep range summaries readable and controls available in row details."""
    st.caption(f"{label} range: {_range_summary(data, unit)}")
    forms.table_header(("Lower", "Upper", ""), (2, 2, 0.5))
    _range_limits(data, scope, unit, st.columns((2, 2, 0.5)))


def _when(
    data: dict[str, Any] | None,
    *,
    empty: str = "Not entered",
    household: dict[str, Any] | None = None,
) -> str:
    """Summarize an entered boundary without requiring a complete Plan."""
    if data is None:
        return empty
    if data["kind"] == "year":
        return str(data["year"]) if data["year"] is not None else "Year needed"
    if data["kind"] == "age":
        people = {"adult_1": "Person 1", "adult_2": "Person 2"}
        if household:
            people.update(
                {
                    f"adult_{i + 1}": adult["name"]
                    for i, adult in enumerate(household["adults"])
                }
            )
            people.update(
                {child["id"]: child["name"] for child in household["children"]}
            )
        name = people.get(data.get("person") or "", "Person reference needs review")
        return f"{name} · age {data.get('age') if data.get('age') is not None else 'needed'}"
    label = {
        "adult_1": "Person 1 retires",
        "adult_2": "Person 2 retires",
        "both": "Both retired",
        "survivor": "After first death",
    }[data["kind"]]
    offset = data.get("offset", 0)
    return f"{label} {offset:+} years" if offset else label


def _range_value(
    data: dict[str, Any],
    field: str,
    label: str,
    scope: str,
    unit: str,
    *,
    compact: bool = False,
) -> None:
    """Use the same durable controls for central values and optional bounds."""
    key = f"{field}_{scope}"
    if unit == "rate":
        forms.percent(data, field, f"{label} (%)", key, compact=compact)
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
            compact=compact,
        )
    else:
        forms.money(
            data,
            field,
            f"{label} ($)" if unit == "once" else f"{label} ($/year)",
            key,
            compact=compact,
        )


def _time(
    data: dict[str, Any],
    label: str,
    scope: str,
    household: dict[str, Any] | None = None,
) -> None:
    """Capture annual timing while clearing fields from other anchor kinds."""
    forms.choice(
        data,
        "kind",
        label,
        ("year", "age", "adult_1", "adult_2", "both", "survivor"),
        f"anchor_{scope}",
        labels={
            "year": "Calendar year",
            "age": "Person's age",
            "adult_1": "Person 1 retires",
            "adult_2": "Person 2 retires",
            "both": "Both retired",
            "survivor": "After first death",
        },
    )
    if data["kind"] != "age":
        data["person"] = data["age"] = None
    if data["kind"] != "year":
        data["year"] = None
    if data["kind"] in {"year", "age"}:
        data["offset"] = 0
    if data["kind"] == "year":
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
    elif data["kind"] == "age":
        people = {"adult_1": "Person 1", "adult_2": "Person 2"}
        if household:
            people.update(
                {f"adult_{i + 1}": a["name"] for i, a in enumerate(household["adults"])}
            )
            people.update({c["id"]: c["name"] for c in household["children"]})
        if data.get("person") not in people and data.get("person"):
            people[data["person"]] = (
                "Missing child — choose a person"
                if household
                else "Child reference (review in Plan)"
            )
            if household:
                st.warning(
                    "This expense refers to a child who is no longer in this Plan. Choose a person to resolve the date."
                )
        if data.get("person") is None:
            data["person"] = "adult_1"
        forms.choice(
            data, "person", "Person", tuple(people), f"person_{scope}", labels=people
        )
        forms.number(
            data,
            "age",
            "Age",
            f"age_{scope}",
            integer=True,
            minimum=0,
            maximum=130,
            optional=True,
        )
        if not household:
            st.caption(
                "Choose child ages in the Plan timeline, where household details are available."
            )
    else:
        forms.number(
            data,
            "offset",
            "Years before (-) / after (+) boundary",
            f"offset_{scope}",
            integer=True,
            minimum=-100,
            maximum=100,
        )


def _remove(rows: list[dict[str, Any]], index: int, label: str, scope: str) -> None:
    """Remove only an explicitly chosen row and regenerate index-based widget keys."""
    if forms.row_action(label, "close", f"remove_{scope}"):
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
                    for date_column, field, label in zip(
                        st.columns(2),
                        ("start", "end"),
                        ("Start year", "End year (first year excluded)"),
                        strict=True,
                    ):
                        with date_column:
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
                    _range_value(
                        phase["real_growth"],
                        "central",
                        "Salary growth above inflation",
                        f"growth_{phase_key}",
                        "rate",
                    )
                    if _range_summary(phase["real_growth"], "rate") != "—":
                        st.caption(
                            f"Growth range: {_range_summary(phase['real_growth'], 'rate')}"
                        )
                    with st.expander("Growth range and notes (optional)"):
                        _range_details(
                            phase["real_growth"],
                            "Salary growth",
                            f"growth_{phase_key}",
                            "rate",
                        )
                        forms.text(phase, "notes", "Notes", f"notes_{phase_key}")
                    _remove(adult["phases"], j, "Remove work phase", phase_key)
            phase = WorkPhase().model_dump(mode="json")
            phase["real_growth"]["central"] = "0"
            _add(adult["phases"], phase, "Add work phase", key)
            _vesting_and_options(adult, key)


def _vesting_and_options(adult: dict[str, Any], scope: str) -> None:
    """Keep dated equity events together and make entered events visible."""
    with st.expander(
        "Dated equity events (optional)",
        expanded=bool(adult["vesting"] or adult["options"]),
    ):
        for i, row in enumerate(adult["vesting"]):
            key = f"vesting_{i}_{scope}"
            with st.container(border=True):
                st.markdown(f"**Vesting schedule {i + 1}**")
                for column, field, label in zip(
                    st.columns(2),
                    ("start", "end"),
                    ("Vesting start year", "First year without vesting"),
                    strict=True,
                ):
                    with column:
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
                forms.text(
                    row,
                    "amount",
                    "Annual vesting amount",
                    f"amount_{key}",
                    optional=True,
                )
                forms.choice(
                    row,
                    "basis",
                    "Vesting amount basis",
                    ("nominal", "real", "shares"),
                    f"basis_{key}",
                    labels={
                        "nominal": "Future dollars",
                        "real": "Reference-year dollars",
                        "shares": "Number of shares",
                    },
                    segmented=True,
                )
                forms.text(row, "notes", "Vesting notes (optional)", f"notes_{key}")
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
                st.markdown(f"**Option event {i + 1}**")
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
                forms.text(
                    row, "notes", "Option event notes (optional)", f"notes_{key}"
                )
                _remove(adult["options"], i, "Remove option event", key)
        _add(
            adult["options"],
            {"year": None, "award": None, "exercise": None, "notes": ""},
            "Add dated option event",
            f"options_{scope}",
        )


EXPENSE_CATEGORIES = {
    "ordinary": "Ordinary living",
    "children": "Children",
    "healthcare": "Healthcare",
    "care": "Care",
    "other": "Other",
}


def _expense(
    row: dict[str, Any],
    scope: str,
    household: dict[str, Any] | None = None,
) -> None:
    """Edit the authoritative expense row in either a library or a Plan copy."""
    forms.text(row, "label", "Expense name", f"label_{scope}")
    forms.choice(
        row,
        "category",
        "Category",
        tuple(EXPENSE_CATEGORIES),
        f"category_{scope}",
        labels=EXPENSE_CATEGORIES,
    )
    forms.choice(
        row,
        "frequency",
        "Frequency",
        ("recurring", "once"),
        f"frequency_{scope}",
        labels={"recurring": "Every year", "once": "One time"},
        segmented=True,
    )
    forms.text(row, "notes", "Included costs and exclusions", f"notes_{scope}")
    details = st.toggle("Edit amount ranges", key=f"expense_details_{scope}")
    unit = "once" if row["frequency"] == "once" else "money"
    for i, level in enumerate(row["levels"]):
        key = f"level_{i}_{scope}"
        with st.container(border=True):
            forms.text(level, "label", "Phase name (optional)", f"label_{key}")
            _range_value(level["amount"], "central", "Amount", key, unit)
            _time(level["when"], "Amount starts", key, household)
            if _range_summary(level["amount"], unit) != "—":
                st.caption(f"Range: {_range_summary(level['amount'], unit)}")
            if details:
                _range_details(level["amount"], "Amount", key, unit)
            if len(row["levels"]) > 1:
                _remove(row["levels"], i, "Remove phase", key)
    if row["frequency"] == "recurring":
        _add(
            row["levels"],
            ExpenseLevel().model_dump(mode="json"),
            "Add spending phase",
            scope,
        )
    if forms.optional_section(
        row,
        "end",
        "Specify an end date",
        f"end_enabled_{scope}",
        TimeAnchor().model_dump(mode="json"),
    ):
        _time(row["end"], "Payments stop", f"end_{scope}", household)
    if row["frequency"] == "once" and (len(row["levels"]) != 1 or row["end"]):
        st.warning(
            "A one-time expense needs one amount and date. Remove extra phases and the end date."
        )
    forms.percent(row, "real_growth", "Growth above inflation (%)", f"growth_{scope}")


def _split_expense(
    expense: dict[str, Any],
    share: Decimal,
    label: str,
    category: str,
    remaining_notes: str,
    separated_notes: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split every phase and range without changing timing or combined amounts."""
    original = ExpenseSchedule.model_validate(expense).model_dump(mode="json")
    separated = deepcopy(original)
    separated.update(
        id=str(uuid4()), label=label, category=category, notes=separated_notes
    )
    original["notes"] = remaining_notes
    for remaining, added in zip(original["levels"], separated["levels"], strict=True):
        for field, amount in remaining["amount"].items():
            if amount is not None:
                part = (Decimal(amount) * share).quantize(Decimal("0.01"))
                added["amount"][field] = str(part)
                remaining["amount"][field] = str(Decimal(amount) - part)
    return original, separated


def _reclassify(data: dict[str, Any], scope: str) -> None:
    """Preview an explicit transfer out of an existing expense before applying it."""
    if not data["expenses"]:
        return
    with st.expander("Separate costs already included in an expense"):
        st.caption(
            "Split every phase by the same percentage. Dates, growth, and combined totals stay the same. For different timing, review and edit both schedules afterward."
        )
        choices = {row["id"]: row for row in data["expenses"]}
        selected = st.selectbox(
            "Expense to split",
            tuple(choices),
            format_func=lambda key: choices[key]["label"],
            key=f"split_source_{scope}",
        )
        share = st.number_input(
            "Share to separate (%)",
            min_value=0.0,
            max_value=100.0,
            value=0.0,
            key=f"split_share_{scope}",
        )
        label = st.text_input("Separated expense name", key=f"split_label_{scope}")
        category = st.selectbox(
            "Separated category",
            tuple(EXPENSE_CATEGORIES),
            format_func=EXPENSE_CATEGORIES.__getitem__,
            key=f"split_category_{scope}",
        )
        remaining_notes = st.text_input(
            "Costs remaining in the original expense", key=f"split_remaining_{scope}"
        )
        separated_notes = st.text_input(
            "Costs included in the separated expense", key=f"split_notes_{scope}"
        )
        if not (
            0 < share < 100
            and label.strip()
            and remaining_notes.strip()
            and separated_notes.strip()
        ):
            st.caption(
                "Enter a percentage between 0 and 100 and describe both sets of costs to review the split."
            )
            return
        try:
            remaining, separated = _split_expense(
                choices[selected],
                Decimal(str(share)) / 100,
                label,
                category,
                remaining_notes,
                separated_notes,
            )
        except ValueError:
            st.warning("Complete the original expense before splitting it.")
            return
        st.table(
            [
                {
                    "Phase starts": _when(before["when"]),
                    "Before": before["amount"]["central"],
                    "Remaining": after["amount"]["central"],
                    "Separated": added["amount"]["central"],
                    "Combined after": str(
                        Decimal(after["amount"]["central"])
                        + Decimal(added["amount"]["central"])
                    )
                    if before["amount"]["central"] is not None
                    else "Unknown",
                }
                for before, after, added in zip(
                    choices[selected]["levels"],
                    remaining["levels"],
                    separated["levels"],
                    strict=True,
                )
            ]
        )
        if st.button("Apply reviewed split", key=f"apply_split_{scope}"):
            data["expenses"][data["expenses"].index(choices[selected])] = remaining
            data["expenses"].append(separated)
            _changed_rows()


def _spending(
    data: dict[str, Any],
    scope: str,
    household: dict[str, Any] | None = None,
    category: str | None = None,
) -> None:
    """Edit additive expenses with one timeline across working and retired years."""
    st.caption(
        "Amounts use this profile's money year. Each new phase replaces only this expense's amount. Starts include the year; ends exclude it. Inflation is applied once, with growth above inflation measured from the money year."
    )
    st.caption(
        "Include healthcare and care here. Exclude taxes, saving, and debt payments already modeled elsewhere. Categories group expenses; they do not calculate costs."
    )
    category = category or st.selectbox(
        "Show expense category",
        ("all", *EXPENSE_CATEGORIES),
        format_func=lambda value: "All expenses"
        if value == "all"
        else EXPENSE_CATEGORIES[value],
        key=f"expense_filter_{scope.rsplit('_', 1)[0]}",
    )
    for i, row in enumerate(data["expenses"]):
        if category != "all" and row["category"] != category:
            continue
        key = f"expense_{row['id']}_{scope}"
        with st.container(border=True):
            _expense(row, key, household)
            _remove(data["expenses"], i, "Remove expense", key)
    _add(
        data["expenses"],
        ExpenseSchedule.model_validate(
            {
                "label": "New expense",
                "category": "ordinary" if category == "all" else category,
            }
        ).model_dump(mode="json"),
        "Add an expense",
        scope,
    )
    _reclassify(data, scope)


def _investments(data: dict[str, Any], scope: str, dsn: str | None) -> None:
    """Keep saving, cash reserves, and dated allocation targets distinct."""
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
            data["saving"], scope, st.session_state.get("goals", ()), surplus=True
        ):
            _changed_rows()
    st.subheader("Cash reserve")
    forms.money(
        data,
        "reserved_cash",
        "Cash reserve ($)",
        f"reserved_cash_{scope}",
        help_text="Cash kept outside the portfolio allocation, in the reference-year dollars set above. It is already part of Wealth, not an extra asset. Enter 0 for none.",
    )
    st.subheader("Portfolio strategy")
    forms.choice(
        data,
        "policy",
        "Allocation policy",
        ("undecided", "constant", "gradual"),
        f"policy_{scope}",
        labels={
            "undecided": "Undecided",
            "constant": "Keep the same mix",
            "gradual": "Change the mix over time",
        },
        segmented=True,
    )
    st.caption(
        "Stocks, bonds, and cash must total 100%. The cash reserve is separate. To change the mix over time, add a starting target and a final target with their dates."
    )
    if data["policy"] == "gradual":
        forms.choice(
            data,
            "interpolation",
            "Glide-path shape",
            tuple(GLIDE_SHAPES),
            f"glide_shape_{scope}",
            labels=GLIDE_SHAPES,
            segmented=True,
        )
        st.caption(
            "The shape changes how quickly you move toward the final mix. All shapes reach it on the finish date and hold it afterward. With extra dated targets, the same shape applies between each pair."
        )
    for i, row in enumerate(data["checkpoints"]):
        key = f"checkpoint_{i}_{scope}"
        with st.container(border=True):
            st.markdown(f"**Allocation target {i + 1}**")
            _time(row["when"], "Allocation effective", key)
            if forms.optional_section(
                row,
                "mix",
                "Specify target allocation",
                f"mix_enabled_{key}",
                {"equities": None, "fixed_income": None, "cash": None},
            ):
                for column, field, label in zip(
                    st.columns(3),
                    ("equities", "fixed_income", "cash"),
                    ("Stocks", "Bonds", "Cash"),
                    strict=True,
                ):
                    with column:
                        forms.percent(
                            row["mix"], field, f"{label} (%)", f"{field}_{key}"
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
            "flows_only": "When money enters or leaves",
        },
        segmented=True,
        help_text="Annual rebalancing restores the target mix each year. The other option moves toward the target only through contributions and withdrawals.",
    )


def _economy(data: dict[str, Any], scope: str) -> None:
    """Capture compound outlooks separately from editable simulation assumptions."""
    st.caption(
        "Enter annual inflation and total investment returns. Returns are after fees, before tax, and include inflation."
    )
    fields = (
        ("inflation", "Inflation"),
        ("equities", "Stock return"),
        ("fixed_income", "Bond return"),
        ("cash", "Cash return"),
    )
    ranges = st.toggle("Edit ranges", key=f"ranges_{scope}")
    forms.table_header(
        ("Assumption", "Annual rate (%)", "Lower (%)", "Upper (%)", "")
        if ranges
        else ("Assumption", "Annual rate (%)", "Range"),
        (3, 2, 2, 2, 0.5) if ranges else (3, 2, 4.5),
    )
    for field, label in fields:
        columns = st.columns(
            (3, 2, 2, 2, 0.5) if ranges else (3, 2, 4.5), vertical_alignment="center"
        )
        columns[0].text(label)
        with columns[1]:
            _range_value(
                data[field], "central", label, f"{field}_{scope}", "rate", compact=True
            )
        if ranges:
            _range_limits(data[field], f"{field}_{scope}", "rate", columns[2:])
        else:
            columns[2].caption(_range_summary(data[field], "rate"))
    if ranges:
        st.caption(
            "Enter both ends. Ranges describe alternative assumptions, not probabilities."
        )
    st.subheader("Investment income")
    st.caption(
        "The returns above already include dividends and interest. These yields determine how much income is taxed each year."
    )
    for column, field, label in zip(
        st.columns(2),
        ("stock_dividend_yield", "bond_interest_yield"),
        ("Stock dividend yield (%)", "Bond interest yield (%)"),
        strict=True,
    ):
        with column:
            forms.percent(data, field, label, f"{field}_{scope}")
    st.subheader("Forecast uncertainty")
    _simulation_summary(data)
    st.caption(
        "The default 10th to 90th percentile band shows the middle 80% of simulated balances in each year. The lower/upper ranges above describe alternative assumptions, not this outcome band."
    )
    with st.expander("Advanced simulation settings"):
        st.caption(
            "Volatility measures how much yearly returns fluctuate. Correlation measures how they move together. These settings affect Monte Carlo forecasts."
        )
        st.caption(
            "Preset sources: [Vanguard volatility forecasts](https://www.vanguardsouthamerica.com/en/home/insights/economic-market-outlook/vanguard-capital-markets-model-forecasts-2026) and [JPMorgan 2026 correlations](https://am.jpmorgan.com/content/dam/jpm-am-aem/global/en/insights/portfolio-insights/ltcma/noindex/ltcma-full-report.pdf#page=82). Values are rounded and adapted for this model."
        )
        st.button(
            "Use recommended simulation settings",
            key=f"reset_simulation_{scope}",
            on_click=_reset_simulation,
            args=(data, scope),
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
                "Correlations apply to log gross returns (log of 1 + the annual rate). Rows and columns: equities, fixed income, cash, inflation. Enter all 16 values, including the diagonal; use -1 to 1, not percent."
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


def _simulation_summary(data: dict[str, Any]) -> None:
    """Describe the retained statistics without replacing missing or custom values."""
    try:
        settings = EconomicAssumptions(
            name="Simulation summary",
            volatility=data["volatility"],
            correlation=data["correlation"],
        )
    except ValueError:
        st.caption("Simulation settings need attention; review the advanced settings.")
        return
    parts = [
        f"{label} {(value * 100).normalize():f}%"
        if value is not None
        else f"{label} not entered"
        for label, value in zip(
            ("stocks", "bonds", "cash", "inflation"), settings.volatility, strict=True
        )
    ]
    st.caption("Annual volatility: " + "; ".join(parts) + ".")
    if settings.correlation is None:
        st.caption("Correlations are not entered; review the advanced settings.")


def _reset_simulation(data: dict[str, Any], scope: str) -> None:
    """Apply the recommended statistics while retaining all other raw edits."""
    defaults = EconomicAssumptions(name="Recommended simulation").model_dump(
        mode="json"
    )
    for field in ("volatility", "correlation"):
        data[field] = defaults[field]
    keys = [f"correlations_{scope}"]
    keys += [f"volatility_{i}_{scope}" for i in range(4)]
    keys += [f"corr_{i}_{j}_{scope}" for i in range(4) for j in range(4)]
    for key in keys:
        st.session_state.pop(key, None)


def _list_capture(row: list[Any], index: int, key: str, percent: bool) -> None:
    """Capture a fixed statistical vector entry before its controls disappear."""
    row[index] = (
        forms.percent_value(st.session_state[key])
        if percent
        else forms.blank(st.session_state[key])
    )


def _list_percent(row: list[Any], index: int, label: str, key: str) -> None:
    """Reuse percentage rendering and route its callback into a fixed vector."""
    from decimal import Decimal

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
    """Keep retirement dates and benefits visible; offer ranges on demand."""
    st.subheader("Retirement and lifespan")
    st.caption(
        "Retirement year is the first full year without employment income. Plan through age sets how long spending must be covered."
    )
    details = st.toggle("Edit retirement ranges", key=f"retirement_details_{scope}")
    with st.expander(
        "Mortality assumptions (advanced)", expanded=data["mode"] == "sample"
    ):
        forms.choice(
            data,
            "mode",
            "Lifespan assumptions",
            ("ages", "sample"),
            f"mode_{scope}",
            labels={
                "ages": "Choose planning ages",
                "sample": "Mortality model (not forecastable yet)",
            },
            segmented=True,
        )
        if data["mode"] == "sample":
            st.warning(
                "Forecasts currently require planning ages. Mortality model inputs can be saved for later use."
            )
            forms.choice(
                data,
                "table_basis",
                "Life table basis",
                ("unknown", "period", "cohort"),
                f"table_basis_{scope}",
                help_text="Period tables use current death rates; cohort tables allow rates to change over time.",
                segmented=True,
            )
            for i, adult in enumerate(data["adults"]):
                adult["age"] = {"central": None, "lower": None, "upper": None}
                forms.text(
                    adult,
                    "table_classification",
                    f"{adult['name']} table classification",
                    f"classification_{i}_{scope}",
                )
    forms.table_header(("Person", "Retirement year", "Plan through age"), (2, 2, 2))
    for i, adult in enumerate(data["adults"]):
        name, year, age = st.columns(3, vertical_alignment="center")
        name.text(adult["name"])
        with year:
            _range_value(
                adult["retirement"],
                "central",
                f"{adult['name']} retires",
                f"retirement_{i}_{scope}",
                "year",
                compact=True,
            )
        with age:
            if data["mode"] == "ages":
                _range_value(
                    adult["age"],
                    "central",
                    f"Plan for {adult['name']} through age",
                    f"age_{i}_{scope}",
                    "age",
                    compact=True,
                )
            else:
                st.caption("Mortality model selected")
        if any(
            _range_summary(adult[field], unit) != "—"
            for field, unit in (("retirement", "year"), ("age", "age"))
        ):
            st.caption(
                f"{adult['name']} ranges · Retire: {_range_summary(adult['retirement'], 'year')} · Age: {_range_summary(adult['age'], 'age')}"
            )
        if details:
            _range_details(
                adult["retirement"],
                f"{adult['name']} retirement year",
                f"retirement_{i}_{scope}",
                "year",
            )
            if data["mode"] == "ages":
                _range_details(
                    adult["age"],
                    f"{adult['name']} planning age",
                    f"age_{i}_{scope}",
                    "age",
                )
    st.caption(
        "All household expenses, including retirement and survivor spending, are entered in Spending."
    )
    _retirement_income(data, scope)


def _benefit_status(value: Any) -> str:
    """Distinguish missing benefits, known zero, and entered amounts."""
    from decimal import Decimal

    if value is None:
        return "unknown"
    try:
        amount = Decimal(str(value))
        if amount.is_zero():
            return "none"
        return "positive" if amount.is_finite() and amount > 0 else "invalid"
    except InvalidOperation:
        return "invalid"


def _pension_choice(adult: dict[str, Any], key: str, amount_key: str) -> None:
    """Make the no-pension choice explicit and clear a stale amount widget."""
    choice = st.session_state[key]
    if choice in {"unknown", "none"}:
        adult["pension"] = None if choice == "unknown" else "0"
        st.session_state.pop(amount_key, None)


def _pension(adult: dict[str, Any], scope: str) -> None:
    """Hide pension timing and survivor choices unless a pension is expected."""
    key = f"pension_choice_{scope}"
    amount_key = f"pension_{scope}"
    amount = adult.get("pension")
    status = _benefit_status(amount)
    selected = status if status in {"unknown", "none"} else "pension"
    choices = ("unknown", "none", "pension")
    choice = st.segmented_control(
        f"{adult['name']} pension",
        choices,
        default=selected,
        required=True,
        format_func=lambda value: {
            "unknown": "Not entered",
            "none": "No pension",
            "pension": "Enter pension",
        }[value],
        key=key,
        on_change=_pension_choice,
        args=(adult, key, amount_key),
    )
    if choice != "pension":
        return
    forms.money(adult, "pension", "Pension ($/year)", amount_key)
    if _benefit_status(adult["pension"]) == "positive":
        with st.container(horizontal=True):
            forms.number(
                adult,
                "pension_start_age",
                "Pension starts at age",
                f"pension_age_{scope}",
                integer=True,
                minimum=18,
                maximum=100,
            )
            forms.percent(
                adult,
                "pension_survivor_fraction",
                "Pension paid to survivor (%)",
                f"pension_survivor_{scope}",
            )


def _retirement_income(data: dict[str, Any], scope: str) -> None:
    """Collect benefits once in the reusable Retirement scenario."""
    st.subheader("Retirement income")
    st.caption(
        f"Annual amounts in {data['money_year'] or 'the reference-year'} dollars, before tax. Enter the Social Security estimate for the chosen claiming age; use 0 if none."
    )
    for i, adult in enumerate(data["adults"]):
        key = f"income_{i}_{scope}"
        adult.setdefault("claiming_age", 67)
        adult.setdefault("pension_start_age", 65)
        with st.container(border=True):
            st.text(adult["name"])
            with st.container(horizontal=True):
                forms.money(
                    adult,
                    "social_security",
                    f"{adult['name']} Social Security ($/year)",
                    f"social_security_{key}",
                )
                if _benefit_status(adult.get("social_security")) == "positive":
                    forms.number(
                        adult,
                        "claiming_age",
                        "Social Security claiming age",
                        f"claiming_age_{key}",
                        integer=True,
                        minimum=62,
                        maximum=70,
                    )
            _pension(adult, key)


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
                "Timing": _when(
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


def _plan_timeline(data: dict[str, Any], scope: str, dsn: str | None) -> None:
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
                f"Timeline {LABELS[kind]} alternative",
                tuple(choices),
                format_func=partial(_timeline_choice_label, choices),
                key=f"timeline_choice_{kind}_{scope.rsplit('_', 1)[0]}",
            )
        components.append(choices[selected])
    categories = {
        "all": "All events",
        "work": "Work",
        **EXPENSE_CATEGORIES,
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
    labels = {**LABELS, "residence": "Household and residence"}
    available = (*editors, "residence")
    requested = "spending" if category in EXPENSE_CATEGORIES else category
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
        _household_context(data, f"timeline_household_{scope}")
        return
    selection = editors[selected]
    key = f"timeline_{selection['source_id']}_{scope}"
    inputs = selection["inputs"]
    st.subheader(inputs["name"])
    if selected == "spending":
        _spending(
            inputs,
            key,
            data["household"],
            category if category in EXPENSE_CATEGORIES else None,
        )
    elif selected == "work":
        _work(inputs, key)
    elif selected == "retirement":
        _retirement(inputs, key)
    elif selected == "investments":
        _investments(inputs, key, dsn)


def _assembly(data: dict[str, Any], scope: str, dsn: str | None) -> None:
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
        records = _read(dsn, kind)
        selected = [c for c in data["components"] if c["inputs"]["kind"] == kind]
        with st.container(border=True):
            st.subheader(LABELS[kind])
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
                        _summary(inputs).replace("$", r"\$")
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
                            _open(current)
                        if st.button(
                            "Remove",
                            key=f"remove_selection_{selection['source_id']}_{scope}",
                        ):
                            data["components"].remove(selection)
                            _changed_rows()
                    if inputs is not None:
                        _review(inputs, "Review copied inputs")
            with choose_column:
                choices = {str(r.id): r for r in records}
                if choices:
                    chosen = st.selectbox(
                        f"Choose {LABELS[kind]} from library",
                        list(choices),
                        index=list(choices).index(selected[0]["source_id"])
                        if selected and selected[0]["source_id"] in choices
                        else 0,
                        format_func=partial(_profile_label, choices),
                        key=f"select_{kind}_{scope}",
                    )
                    st.caption(_summary(choices[chosen].inputs).replace("$", r"\$"))
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
                            _changed_rows()
                    if len(selected) > 1:
                        st.caption(
                            "Replace selection removes the other alternatives in this category."
                        )
                else:
                    st.caption(f"Save {LABELS[kind]} assumptions to choose them here.")
                    category_link(
                        f"Create {LABELS[kind]} assumptions",
                        LABELS[kind],
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
                    {LABELS[c.inputs.kind]: c.inputs.name for c in case.components}
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
    updated = replace_profile(PlanningScenario(name="Selection"), document)
    data["components"] = [
        *[
            row
            for row in data["components"]
            if row["inputs"]["kind"] != document.inputs.kind
        ],
        *updated.model_dump(mode="json")["components"],
    ]
    _changed_rows()


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
    _changed_rows()


def _apply_wealth(data: dict[str, Any], document: Any) -> None:
    """Replace starting balances without changing the selected Investments."""
    updated = replace_wealth(PlanningScenario(name="Selection"), document)
    data["wealth"] = updated.model_dump(mode="json")["wealth"]
    _changed_rows()


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
    _changed_rows()


def _household_selection(data: dict[str, Any], scope: str, dsn: str | None) -> None:
    """Choose a reusable family and explicitly refresh its self-contained copy."""
    records = _read(dsn, "household")
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
                _open(current)
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
            st.caption(_summary(choices[chosen].inputs))
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


def plan_context(category: str, dsn: str | None) -> None:
    """Edit family facts and person mappings in the selected Plan."""
    if "plan" not in st.session_state.get("planning_active", {}):
        navigate("Plans", target="library", view="library")
    data = _draft("plan")["data"]
    scope = _scope("plan")
    st.info(f"Editing combined plan: {data['name']}")
    with st.container(horizontal=True):
        category_link("Return to Plans", "Plans", target="plan", view="editor")
        category_link(
            "Household library", "Household", target="library", view="library"
        )
    _household_selection(data, scope, dsn)
    _household_context(data, scope)
    _child_expense_warning(data)
    st.caption(
        "These edits belong to this combined plan. Return to Plans to save them."
    )


def _household_demographics(household: dict[str, Any], scope: str) -> None:
    """Edit the family details shared by library sources and Plan copies."""
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
    forms.table_header(("Person", "Age at year end", "WA Cares"), (3, 1.5, 2))
    for i, adult in enumerate(household["adults"]):
        name, age, exemption = st.columns((3, 1.5, 2), vertical_alignment="center")
        with name:
            forms.text(
                adult, "name", "Name", f"household_name_{i}_{scope}", compact=True
            )
        with age:
            forms.number(
                adult,
                "age",
                "Age at reference year end",
                f"household_age_{i}_{scope}",
                integer=True,
                minimum=18,
                maximum=130,
                optional=True,
                compact=True,
            )
        with exemption:
            forms.choice(
                adult,
                "wa_cares_exempt",
                "WA Cares exemption",
                (None, True, False),
                f"wa_cares_{i}_{scope}",
                labels={None: "Unknown", True: "Exempt", False: "Not exempt"},
                compact=True,
            )
    forms.choice(
        household,
        "filing_status",
        "Filing status",
        (None, "married_filing_jointly"),
        f"filing_{scope}",
        labels={None: "Unknown", "married_filing_jointly": "Married filing jointly"},
    )
    st.subheader("Children")
    if household["children"]:
        forms.table_header(
            ("Child", "Birth year (actual or expected)", ""), (3, 2, 0.4)
        )
    for i, child in enumerate(household["children"]):
        name, year, remove = st.columns((3, 2, 0.4), vertical_alignment="center")
        with name:
            forms.text(
                child, "name", "Child label", f"child_name_{i}_{scope}", compact=True
            )
        with year:
            forms.number(
                child,
                "birth_year",
                "Birth year (actual or expected)",
                f"child_year_{i}_{scope}",
                integer=True,
                minimum=1900,
                maximum=9999,
                optional=True,
                compact=True,
            )
        with remove:
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


def _household_context(data: dict[str, Any], scope: str) -> None:
    """Edit copied demographics, residence and person-specific profile mappings."""
    household = data["household"]
    _household_demographics(household, scope)
    st.subheader("Residence")
    if household["residence"]:
        forms.table_header(("Starting year", "Location", ""), (2, 3, 0.4))
    for i, row in enumerate(household["residence"]):
        year, location, remove = st.columns((2, 3, 0.4), vertical_alignment="center")
        with year:
            forms.number(
                row,
                "year",
                "Residence effective year",
                f"residence_year_{i}_{scope}",
                integer=True,
                minimum=1900,
                maximum=9999,
                optional=True,
                compact=True,
            )
        with location:
            forms.text(
                row,
                "location",
                "Residence jurisdiction",
                f"residence_location_{i}_{scope}",
                optional=True,
                compact=True,
            )
        with remove:
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
