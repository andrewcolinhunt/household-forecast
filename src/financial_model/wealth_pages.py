"""Wealth library and snapshot editor with independent, retained session drafts."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any, get_args
from uuid import uuid4

import streamlit as st

from financial_model import input_forms as forms
from financial_model.scenario_pages import category_link, navigate
from financial_model.setup_pages import _setup_error
from financial_model.storage import (
    MAX_BACKUP_BYTES,
    RevisionConflictError,
    create_wealth_snapshot,
    delete_wealth_snapshot,
    import_wealth_backup,
    list_wealth_snapshots,
    load_household,
    update_wealth_snapshot,
)
from financial_model.wealth import (
    AccountPosition,
    DebtPosition,
    InvestmentType,
    PropertyPosition,
    SavedWealthSnapshot,
    TaxTreatment,
    WealthSnapshot,
)

LABELS = {
    "hsa": "HSA",
    "tax_deferred": "Pretax retirement",
    "roth": "Roth",
    "equities": "Stocks",
    "fixed_income": "Bonds",
    "unknown": "Not entered",
}


def _label(value: str) -> str:
    """Keep enum values readable without exposing storage spellings."""
    return LABELS.get(value, value.replace("_", " ").capitalize())


def _key(row: dict[str, Any], field: str) -> str:
    """Scope widget identities to the active draft and its stable local row IDs."""
    return (
        f"wealth_{st.session_state['wealth_epoch']}_{row.get('id', 'snapshot')}_{field}"
    )


def _activate(draft_id: str) -> None:
    """Select a retained draft and reinitialize widgets from its raw values."""
    st.session_state["wealth_active"] = draft_id
    st.session_state["wealth_epoch"] = st.session_state.get("wealth_epoch", 0) + 1


def _new_draft(
    snapshot: WealthSnapshot | None = None, record: SavedWealthSnapshot | None = None
) -> None:
    """Copy inputs into a raw editing buffer without changing a saved inventory."""
    draft_id = str(record.id if record else uuid4())
    data = (snapshot or WealthSnapshot(as_of=date.today())).model_dump(mode="json")
    st.session_state.setdefault("wealth_drafts", {})[draft_id] = {
        "data": data,
        "loaded": record,
    }
    _activate(draft_id)


def _open(
    record: SavedWealthSnapshot | None = None, *, duplicate: bool = False
) -> None:
    """Resume edits or open a new independent snapshot, refreshing untouched drafts."""
    cached = (
        st.session_state.get("wealth_drafts", {}).get(str(record.id))
        if record and not duplicate
        else None
    )
    if cached and record and cached["loaded"].revision != record.revision:
        try:
            if (
                WealthSnapshot.model_validate(cached["data"])
                == cached["loaded"].snapshot
            ):
                cached = None
        except ValueError:
            pass  # Invalid pending edits must survive another session's save.
    if cached and record:
        _activate(str(record.id))
    else:
        _new_draft(record.snapshot if record else None, None if duplicate else record)
    navigate("Wealth", target="library", view="editor")


def _read(dsn: str | None) -> tuple[SavedWealthSnapshot, ...]:
    """Read current snapshots without offering cached choices after failure."""
    st.session_state["wealth_store_available"] = bool(dsn)
    if not dsn:
        return ()
    try:
        return list_wealth_snapshots(dsn)
    except (sqlite3.Error, ValueError):
        st.session_state["wealth_store_available"] = False
        st.error(
            "Saved wealth could not be read. Your drafts are still available. Check database setup or try Refresh."
        )
        return ()


def _has_unsaved_edits(draft: dict[str, Any]) -> bool:
    """Keep new and edited drafts visible without duplicating saved snapshots."""
    loaded: SavedWealthSnapshot | None = draft["loaded"]
    if loaded is None:
        return True
    try:
        return WealthSnapshot.model_validate(draft["data"]) != loaded.snapshot
    except ValueError:
        return True


def _text(
    row: dict[str, Any], field: str, label: str, *, optional: bool = False
) -> None:
    """Capture labels before a sub-tab switch removes the field."""
    forms.text(row, field, label, _key(row, field), optional=optional)


def _notes(row: dict[str, Any], field: str, label: str) -> None:
    """Keep raw source notes independently of widget lifetime."""
    forms.text(row, field, label, _key(row, field), multiline=True)


def _amount(row: dict[str, Any], field: str, label: str) -> None:
    """Retain unknown and invalid money text across navigation."""
    forms.money(row, field, label, _key(row, field))


def _date_text(value: date | None) -> str | None:
    """Serialize a selected optional date into the durable raw buffer."""
    return value.isoformat() if value else None


def _date(
    row: dict[str, Any], field: str, label: str, *, disabled: bool = False
) -> None:
    """Capture the snapshot's single financial date."""
    value = date.fromisoformat(row[field]) if row.get(field) else None
    selected = st.date_input(
        label,
        value=value,
        min_value=date(1900, 1, 1),
        max_value=date(9999, 12, 31),
        key=_key(row, field),
        disabled=disabled,
        on_change=forms.capture,
        args=(row, field, _key(row, field), _date_text),
    )
    row[field] = selected.isoformat() if selected else None


def _investment_choice(row: dict[str, Any]) -> None:
    """Discard an obsolete split when switching to a single investment category."""
    row["invested_in"] = st.session_state[_key(row, "invested_in")]
    row["mix"] = (
        row["mix"] or {"equities": None, "fixed_income": None, "cash": None}
        if row["invested_in"] == "mixed"
        else None
    )
    for field in ("equities", "fixed_income", "cash"):
        st.session_state.pop(_key(row, field), None)


def _owner_labels(dsn: str | None) -> dict[int | None, str]:
    """Label household slots without deriving ownership from account names."""
    labels: dict[int | None, str] = {None: "Not entered", 1: "Adult 1", 2: "Adult 2"}
    try:
        household = (
            load_household(dsn) if dsn else st.session_state.get("local_household")
        )
    except (sqlite3.Error, ValueError):
        return labels
    if household:
        labels.update(
            {
                i: f"{adult.name} (adult {i})"
                for i, adult in enumerate(household.adults, 1)
            }
        )
    return labels


def _account_tax_details(row: dict[str, Any]) -> None:
    """Keep cost basis with the balance it describes."""
    basis = row["tax_treatment"] == "hsa" or (
        row["tax_treatment"] == "taxable" and row["invested_in"] != "cash"
    )
    if not basis:
        return
    hsa = row["tax_treatment"] == "hsa"
    forms.money(
        row,
        "cost_basis",
        "California HSA cost basis ($)" if hsa else "Taxable cost basis ($)",
        _key(row, "cost_basis"),
        help_text=(
            "California tax basis for this HSA, needed when a plan includes California. "
            if hsa
            else "Total purchase cost adjusted for prior transactions, used to calculate gains on sale. "
        )
        + "Include cash at face value in the total. Leave blank if unknown.",
    )


def _account_row(row: dict[str, Any], owner_labels: dict[int | None, str]) -> bool:
    """Keep ownership visible beside the account's balance and classification."""
    name, balance, remove = st.columns([3, 2, 0.5], vertical_alignment="bottom")
    with name:
        forms.text(row, "label", "Account", _key(row, "label"))
    with balance:
        forms.money(row, "balance", "Balance ($)", _key(row, "balance"))
    tax, investment, owner = st.columns(3)
    with tax:
        forms.choice(
            row,
            "tax_treatment",
            "Tax treatment",
            get_args(TaxTreatment),
            _key(row, "tax_treatment"),
            labels={v: _label(v) for v in get_args(TaxTreatment)},
        )
    with investment:
        st.selectbox(
            "Invested in",
            get_args(InvestmentType),
            index=get_args(InvestmentType).index(row["invested_in"]),
            format_func=_label,
            key=_key(row, "invested_in"),
            on_change=_investment_choice,
            args=(row,),
        )
    with owner:
        if row["tax_treatment"] in {"tax_deferred", "roth", "hsa"}:
            forms.choice(
                row,
                "owner",
                "Account owner",
                (None, 1, 2),
                _key(row, "owner"),
                labels=owner_labels,
                help_text="Choose the person who owns this retirement or HSA account.",
            )
        else:
            st.caption(
                "Taxable accounts are shared for forecasting."
                if row["tax_treatment"] == "taxable"
                else "Choose tax treatment to see owner requirements."
            )
    removed = remove.button(
        f"Remove {row['label'] or 'account'}",
        icon=":material/close:",
        type="tertiary",
        width=32,
        key=_key(row, "remove"),
        help=f"Remove {row['label'] or 'account'}",
    )
    if row["invested_in"] == "mixed":
        if row["mix"] is None:
            row["mix"] = {"equities": None, "fixed_income": None, "cash": None}
        st.caption("Investment mix: enter percentages totaling 100%.")
        for column, field in zip(
            st.columns(3), ("equities", "fixed_income", "cash"), strict=True
        ):
            with column:
                forms.percent(
                    row["mix"], field, f"{_label(field)} (%)", _key(row, field)
                )
    _account_tax_details(row)
    return removed


def _contents(data: dict[str, Any], owner_labels: dict[int | None, str]) -> bool:
    """Edit account and debt rows without a second holdings inventory."""
    # Keep descriptive remove labels available to screen readers.
    st.html("""<style>
    [class*="st-key-wealth_"][class*="_remove"] button { padding: 4px; }
    [class*="st-key-wealth_"][class*="_remove"] [data-testid="stMarkdownContainer"] {
      position: absolute; width: 1px; height: 1px; overflow: hidden;
      clip-path: inset(50%); white-space: nowrap;
    }
    </style>""")
    st.subheader("Accounts")
    st.caption(
        "Enter each balance as of the snapshot date. Invested in determines forecast returns; choose Mixed to enter a stocks, bonds, and cash split."
    )
    st.caption(
        "Choose an owner for each retirement or HSA account. Taxable accounts are pooled for forecasting and do not need an owner."
    )
    removed = []
    for row in data["accounts"]:
        with st.container(border=True):
            if _account_row(row, owner_labels):
                removed.append(row["id"])
    data["accounts"] = [row for row in data["accounts"] if row["id"] not in removed]
    added = st.button("Add account", key=_key(data, "add_account"))
    if added:
        data["accounts"].append(
            AccountPosition(label="New account").model_dump(mode="json")
        )
    st.subheader("Debts")
    st.caption(
        "Enter balances and repayment terms. Forecasts add debt payments to living costs, so exclude them from your spending budget."
    )
    debt_removed = []
    for row in data["debts"]:
        row.setdefault("payment_frequency", "annual")
        with st.container(border=True):
            name, amount, remove = st.columns([3, 2, 0.5], vertical_alignment="bottom")
            with name:
                _text(row, "label", "Debt name")
            with amount:
                _amount(row, "amount_owed", "Amount owed ($)")
            if remove.button(
                f"Remove {row['label'] or 'debt'}",
                icon=":material/close:",
                type="tertiary",
                width=32,
                key=_key(row, "remove"),
                help=f"Remove {row['label'] or 'debt'}",
            ):
                debt_removed.append(row["id"])
            payment, interest = st.columns(2)
            with payment:
                forms.money(
                    row,
                    "annual_payment",
                    "Annual payment ($)",
                    _key(row, "annual_payment"),
                    help_text="Total principal and interest paid per year. Forecasts add this to living costs; exclude it from the spending budget.",
                )
            with interest:
                forms.percent(
                    row,
                    "interest_rate",
                    "Interest rate (%)",
                    _key(row, "interest_rate"),
                )
            forms.choice(
                row,
                "payment_frequency",
                "Payment frequency",
                ("annual", "monthly"),
                _key(row, "payment_frequency"),
                labels={"annual": "Annual", "monthly": "Monthly"},
                help_text="Monthly loans use one twelfth of the annual principal-and-interest payment each month. Exclude escrow; enter it under the property's monthly expenses.",
            )
    data["debts"] = [row for row in data["debts"] if row["id"] not in debt_removed]
    for property in data.setdefault("properties", []):
        if property["mortgage_id"] in debt_removed:
            property["mortgage_id"] = None
            st.session_state.pop(_key(property, "mortgage_id"), None)
    debt_added = st.button("Add debt", key=_key(data, "add_debt"))
    if debt_added:
        data["debts"].append(DebtPosition(label="New debt").model_dump(mode="json"))
    property_changed = _properties(data)
    return bool(removed or added or debt_removed or debt_added or property_changed)


def _properties(data: dict[str, Any]) -> bool:
    """Keep homes separate from accounts available to fund household spending."""
    st.subheader("Real estate")
    st.caption(
        "Property value contributes to net wealth but cannot fund spending. Rent, property costs, and taxable rental income stay fixed in nominal dollars, including after mortgage payoff. No sale or vacancy is assumed."
    )
    removed = []
    mortgages = {None: "No mortgage", **{d["id"]: d["label"] for d in data["debts"]}}
    for row in data["properties"]:
        with st.container(border=True):
            _text(row, "label", "Property name")
            _amount(row, "market_value", "Property market value ($)")
            forms.percent(
                row,
                "annual_appreciation",
                "Annual nominal appreciation (%)",
                _key(row, "annual_appreciation"),
            )
            st.caption(
                "Zero appreciation holds the property value fixed in nominal dollars."
            )
            forms.choice(
                row,
                "mortgage_id",
                "Mortgage debt",
                tuple(mortgages),
                _key(row, "mortgage_id"),
                labels=mortgages,
                help_text="Add the mortgage under Debts and enter its principal-and-interest payment there.",
            )
            _amount(row, "monthly_rent", "Monthly rental income ($)")
            forms.money(
                row,
                "monthly_expenses",
                "Monthly property expenses ($)",
                _key(row, "monthly_expenses"),
                help_text="Escrow, property tax, insurance, HOA, and other operating costs; exclude principal and interest already entered under Debts. Exclude these costs from Spending to avoid counting them twice.",
            )
            forms.money(
                row,
                "annual_taxable_rental_income",
                "Annual taxable rental income ($)",
                _key(row, "annual_taxable_rental_income"),
                help_text="Enter your reviewed nonnegative taxable rental profit after allowable deductions, including an explicit zero when appropriate. Rent covering the mortgage does not imply zero taxable profit: principal is not deductible. This fixed nominal assumption does not calculate depreciation or passive losses; no qualified business income deduction is assumed.",
            )
            if st.button(
                f"Remove {row['label'] or 'property'}", key=_key(row, "remove")
            ):
                removed.append(row["id"])
    data["properties"] = [p for p in data["properties"] if p["id"] not in removed]
    added = st.button("Add property", key=_key(data, "add_property"))
    if added:
        data["properties"].append(
            PropertyPosition(label="New property").model_dump(mode="json")
        )
    return bool(removed or added)


def _metadata(data: dict[str, Any], *, saved: bool) -> None:
    """Apply one financial date to every account and debt."""
    name, when = st.columns([3, 1])
    with name:
        _text(data, "name", "Snapshot name (optional)")
    with when:
        _date(data, "as_of", "Snapshot date", disabled=saved)
    if saved:
        st.caption("To record a different date, duplicate this snapshot.")


def _summary(snapshot: WealthSnapshot, *, details: bool = False) -> None:
    """Show the recorded position and only the inputs relevant to a projection."""
    if not snapshot.has_balances:
        st.info("No balances entered.")
    else:
        for column, label, value in zip(
            st.columns(3),
            ("Assets", "Debts", "Net recorded wealth"),
            (snapshot.assets, snapshot.liabilities, snapshot.net_recorded),
            strict=True,
        ):
            column.metric(label, f"${value:,.2f}")
    if snapshot.missing_details:
        st.caption(
            f"{len(snapshot.missing_details)} inputs to complete. Totals include entered amounts only."
        )
        if details:
            st.markdown("**Inputs still needed for forecasting**")
            for detail in snapshot.missing_details:
                st.text(detail)


def _save(dsn: str, snapshot: WealthSnapshot, *, new: bool) -> None:
    """Commit current values only, retaining raw edits and reviewed revision on failure."""
    active = st.session_state["wealth_active"]
    draft = st.session_state["wealth_drafts"][active]
    loaded = draft["loaded"]
    document = SavedWealthSnapshot(snapshot=snapshot)
    if loaded and not new:
        document = SavedWealthSnapshot(
            id=loaded.id, revision=loaded.revision, snapshot=snapshot
        )
    try:
        saved = (
            update_wealth_snapshot(dsn, document)
            if loaded and not new
            else create_wealth_snapshot(dsn, document)
        )
    except (ValueError, RevisionConflictError, sqlite3.Error) as error:
        _setup_error(error)
        return
    if not loaded or not new:
        st.session_state["wealth_drafts"].pop(active, None)
    _new_draft(saved.snapshot, saved)
    st.session_state["notice"] = "Wealth snapshot saved."
    st.rerun()


def _backups(
    snapshot: WealthSnapshot | None, record: SavedWealthSnapshot | None = None
) -> None:
    """Export current validated contents and import into an independent unsaved draft."""
    with st.expander("Wealth backups"):
        if snapshot:
            document = (
                SavedWealthSnapshot(
                    id=record.id, revision=record.revision, snapshot=snapshot
                )
                if record
                else SavedWealthSnapshot(snapshot=snapshot)
            )
            data = document.model_dump_json(indent=2)
            if len(data.encode()) <= MAX_BACKUP_BYTES:
                st.download_button(
                    "Download current wealth snapshot",
                    data,
                    file_name=f"{snapshot.as_of}.wealth.json",
                    mime="application/json",
                )
            else:
                st.error(
                    "This backup exceeds the 1 MB import limit. Shorten notes before exporting."
                )
        upload = st.file_uploader(
            "Import a wealth backup",
            type=["json"],
            max_upload_size=1,
            key="wealth_upload",
        )
        imported = None
        if upload:
            try:
                imported = import_wealth_backup(upload.getvalue())
            except ValueError:
                st.error(
                    "This wealth backup could not be read. Check its format and version."
                )
        if st.button("Load wealth backup as new draft", disabled=imported is None):
            _new_draft(imported)
            navigate("Wealth", target="library", view="editor")
        st.caption(
            "Exports include current valid edits, even before saving. Imports do not overwrite saved snapshots."
        )


def wealth_editor_page(dsn: str | None) -> None:
    """Edit a dated snapshot without invoking the annual tax or allocation engine."""
    records = _read(dsn)
    if "wealth_active" not in st.session_state:
        _new_draft()
    draft = st.session_state["wealth_drafts"][st.session_state["wealth_active"]]
    data, loaded = draft["data"], draft["loaded"]
    category_link("All wealth snapshots", "Wealth", target="library", view="library")
    st.title("Edit wealth snapshot" if loaded else "New wealth snapshot")
    _metadata(data, saved=loaded is not None)
    changed = _contents(data, _owner_labels(dsn))
    if changed:
        st.rerun()
    _notes(data, "notes", "Notes (optional)")
    snapshot = None
    try:
        snapshot = WealthSnapshot.model_validate(data)
    except ValueError as error:
        _setup_error(error)
    st.caption("Saved" if loaded and snapshot == loaded.snapshot else "Unsaved changes")
    if snapshot:
        _summary(snapshot, details=True)
    latest = next((r for r in records if loaded and r.id == loaded.id), None)
    if (
        loaded
        and st.session_state["wealth_store_available"]
        and (not latest or latest.revision != loaded.revision)
    ):
        st.warning(
            "This snapshot changed or was deleted. Save a new copy or reload the saved version."
        )
    available = st.session_state["wealth_store_available"] and snapshot is not None
    save, copy = st.columns(2)
    if (
        save.button("Save snapshot", type="primary", disabled=not available)
        and dsn
        and snapshot
    ):
        _save(dsn, snapshot, new=False)
    if copy.button("Save as new snapshot", disabled=not available) and dsn and snapshot:
        _save(dsn, snapshot, new=True)
    if latest:
        with st.expander("Reload saved snapshot"):
            if st.button(
                "Discard edits and reload wealth",
                disabled=not st.session_state["wealth_store_available"],
            ):
                _new_draft(latest.snapshot, latest)
                st.rerun()
    _backups(snapshot, loaded)


def _card(record: SavedWealthSnapshot) -> None:
    """Identify a saved snapshot and expose explicit editing, copying, and deletion."""
    with st.container(border=True):
        st.subheader(record.snapshot.title)
        st.caption(
            f"{record.snapshot.as_of} · Saved {record.updated_at:%Y-%m-%d %H:%M UTC} · {str(record.id)[:8]} · Revision {record.revision}"
        )
        _summary(record.snapshot)
        with st.container(horizontal=True):
            if st.button("Edit", key=f"wealth_edit_{record.id}"):
                _open(record)
            if st.button("Duplicate", key=f"wealth_duplicate_{record.id}"):
                _open(record, duplicate=True)
            if st.button(
                "Delete",
                key=f"wealth_delete_{record.id}",
                disabled=not st.session_state["wealth_store_available"],
            ):
                st.session_state["wealth_pending_delete"] = record


@st.dialog("Delete wealth snapshot?", dismissible=False)
def _confirm_delete(dsn: str | None, record: SavedWealthSnapshot) -> None:
    """Delete the reviewed revision only, after an explicit shared-record confirmation."""
    st.text(f"{record.snapshot.title} · {record.snapshot.as_of} · {str(record.id)[:8]}")
    st.caption(
        "This removes the saved snapshot and its draft from this session. It cannot be undone."
    )
    cancel, confirm = st.columns(2)
    if cancel.button("Cancel"):
        st.session_state.pop("wealth_pending_delete", None)
        st.rerun()
    if (
        confirm.button("Delete wealth snapshot", type="primary", disabled=not dsn)
        and dsn
    ):
        try:
            delete_wealth_snapshot(dsn, record.id, record.revision)
        except (RevisionConflictError, sqlite3.Error) as error:
            _setup_error(error)
            return
        st.session_state.get("wealth_drafts", {}).pop(str(record.id), None)
        if st.session_state.get("wealth_active") == str(record.id):
            st.session_state.pop("wealth_active", None)
        st.session_state.pop("wealth_pending_delete", None)
        st.session_state["notice"] = "Wealth snapshot deleted."
        st.rerun()


def wealth_page(dsn: str | None) -> None:
    """List reusable dated positions separately from annual planning scenarios."""
    st.title("Wealth")
    st.caption(
        "Record account balances and debts at one date to set a combined plan's starting wealth. Set future investments and cash reserves in Investments."
    )
    records = _read(dsn)
    with st.container(horizontal=True):
        if st.button("New snapshot", type="primary"):
            _open()
        st.button("Refresh", help="Read the latest saved wealth snapshots.")
    if not records:
        st.info("No saved wealth snapshots yet.")
    for record in records:
        _card(record)
    drafts = {
        key: draft
        for key, draft in st.session_state.get("wealth_drafts", {}).items()
        if _has_unsaved_edits(draft)
    }
    if drafts:
        with st.container():
            st.subheader("Continue editing")
            st.caption("Save a snapshot to keep changes beyond this browser session.")
            for draft_id, draft in drafts.items():
                name = (
                    draft["data"].get("name")
                    or draft["data"].get("as_of")
                    or "New snapshot"
                )
                if st.button(
                    f"Resume {name} · {draft_id[:8]}", key=f"wealth_resume_{draft_id}"
                ):
                    _activate(draft_id)
                    navigate("Wealth", target="library", view="editor")
    _backups(None)
    if pending := st.session_state.get("wealth_pending_delete"):
        _confirm_delete(dsn, pending)
