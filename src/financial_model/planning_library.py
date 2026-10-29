"""Saved planning records, retained drafts, and input review displays."""

from __future__ import annotations

import json
import sqlite3
from decimal import InvalidOperation
from typing import Any, cast
from uuid import uuid4

import streamlit as st

from financial_model.navigation import navigate
from financial_model.planning import (
    INPUT_ADAPTER,
    EconomicAssumptions,
    HouseholdAssumptions,
    InvestmentAssumptions,
    Kind,
    PlanningInputs,
    PlanningScenario,
    RetirementAssumptions,
    SavedPlanningDocument,
    SpendingAssumptions,
    WorkAssumptions,
    household_from_defaults,
)
from financial_model.planning_storage import (
    create_planning_document,
    delete_planning_document,
    import_planning_backup,
    list_planning_documents,
    update_planning_document,
)
from financial_model.setup_pages import _setup_error
from financial_model.storage import load_household
from financial_model.storage_common import MAX_BACKUP_BYTES, RevisionConflictError

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


def activate_draft(draft_id: str) -> None:
    """Select a category draft without parsing or discarding intermediate text."""
    draft = st.session_state["planning_drafts"][draft_id]
    st.session_state.setdefault("planning_active", {})[draft["data"]["kind"]] = draft_id
    st.session_state["planning_epoch"] = st.session_state.get("planning_epoch", 0) + 1


def new_draft(
    inputs: PlanningInputs, loaded: SavedPlanningDocument | None = None
) -> str:
    """Start an independent copy or a freshly loaded revision."""
    draft_id = str(loaded.id if loaded else uuid4())
    st.session_state.setdefault("planning_drafts", {})[draft_id] = {
        "data": inputs.model_dump(mode="json"),
        "loaded": loaded,
    }
    activate_draft(draft_id)
    return draft_id


def active_draft(kind: str) -> dict[str, Any]:
    """Get the retained raw draft for a category."""
    return cast(
        dict[str, Any],
        st.session_state["planning_drafts"][st.session_state["planning_active"][kind]],
    )


def draft_scope(kind: str) -> str:
    """Scope widgets to a draft identity and explicit reload epoch."""
    return f"planning_{kind}_{st.session_state['planning_active'][kind]}_{st.session_state.get('planning_epoch', 0)}"


def changed_rows() -> None:
    """Rebuild row widget identities after an explicit structural edit."""
    st.session_state["planning_epoch"] = st.session_state.get("planning_epoch", 0) + 1
    st.rerun()


def read_records(dsn: str, kind: Kind) -> tuple[SavedPlanningDocument, ...]:
    """Read current library records without offering cached choices after failure."""
    available = st.session_state.setdefault("planning_available", {})
    cache = st.session_state.setdefault("planning_records", {})
    available[kind] = True
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


def has_unsaved_edits(draft: dict[str, Any]) -> bool:
    """Keep new and edited drafts visible without duplicating saved library records."""
    loaded: SavedPlanningDocument | None = draft["loaded"]
    if loaded is None:
        return True
    try:
        return INPUT_ADAPTER.validate_python(draft["data"]) != loaded.inputs
    except ValueError:
        return True


def open_record(record: SavedPlanningDocument, *, duplicate: bool = False) -> None:
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
        activate_draft(str(record.id))
    else:
        new_draft(record.inputs, None if duplicate else record)
    navigate(
        LABELS[record.inputs.kind],
        target="plan" if record.inputs.kind == "plan" else "library",
        view="editor",
    )


def initial_inputs(kind: Kind, dsn: str) -> PlanningInputs:
    """Copy shared family labels when available, without inventing financial inputs."""
    inputs: PlanningInputs = MODELS[kind](name=f"New {kind}")
    if kind not in {"household", "work", "retirement", "plan"}:
        return inputs
    try:
        household = load_household(dsn)
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


def input_summary(inputs: PlanningInputs) -> str:
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


def review_inputs(inputs: PlanningInputs, label: str) -> None:
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


def save_inputs(kind: Kind, dsn: str, inputs: PlanningInputs, *, new: bool) -> None:
    """Save validated inputs and preserve drafts after conflicts or store failures."""
    draft = active_draft(kind)
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
    new_draft(saved.inputs, saved)
    st.session_state["notice"] = "Inputs saved."
    st.rerun()


def backups(
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
            new_draft(imported)
            navigate(
                LABELS[kind],
                target="plan" if kind == "plan" else "library",
                view="editor",
            )


@st.dialog("Delete saved inputs?", dismissible=False)
def confirm_delete(dsn: str, record: SavedPlanningDocument) -> None:
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
    if st.button("Delete inputs", type="primary"):
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


def preserve_legacy_draft(
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
        new_draft(latest.inputs, latest)
        st.rerun()
    return True
