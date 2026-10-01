"""Wealth library and snapshot editor with independent, retained session drafts."""

from __future__ import annotations

from datetime import date
from typing import Any, get_args
from uuid import uuid4

import psycopg
import streamlit as st

from financial_model import input_forms as forms
from financial_model.scenario_pages import category_link, navigate
from financial_model.schemas import Household
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
    AccountType,
    AssetClass,
    AssetPosition,
    DebtPosition,
    DebtType,
    DirectAssetPosition,
    InstrumentType,
    SavedWealthSnapshot,
    TaxTreatment,
    WealthSnapshot,
)

LABELS = {
    "401k": "401(k)",
    "403b": "403(b)",
    "ira": "IRA",
    "hsa": "HSA",
    "etf": "ETF",
    "stock": "Individual stock",
    "bond": "Individual bond",
    "cash": "Cash",
    "unknown": "Unknown",
    "unitemized": "Unitemized contents",
    "direct_assets": "Directly held assets",
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
    """Retain the last library on connection failure while disabling shared writes."""
    st.session_state["wealth_store_available"] = bool(dsn)
    if not dsn:
        return ()
    try:
        records = list_wealth_snapshots(dsn)
        st.session_state["wealth_last_records"] = records
        return records
    except (psycopg.Error, ValueError):
        st.session_state["wealth_store_available"] = False
        st.error(
            "Saved wealth could not be read. Your drafts are still available. Check database setup or try Refresh."
        )
        return tuple(st.session_state.get("wealth_last_records", ()))


def _owners(dsn: str | None) -> list[str]:
    """Suggest saved household names without linking old snapshots to live defaults."""
    try:
        household: Household | None = (
            load_household(dsn) if dsn else st.session_state.get("local_household")
        )
    except (psycopg.Error, ValueError):
        st.warning(
            "Household names could not be loaded. You can enter an owner directly."
        )
        household = None
    return list(
        dict.fromkeys([*(a.name for a in household.adults)] if household else [])
    )


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


def _choice(
    row: dict[str, Any], field: str, label: str, options: tuple[str, ...]
) -> None:
    """Edit an explicit classification without inferring it from other fields."""
    row[field] = st.selectbox(
        label,
        options,
        index=options.index(row[field]),
        format_func=_label,
        key=_key(row, field),
        on_change=forms.capture,
        args=(row, field, _key(row, field)),
    )


def _date_text(value: date | None) -> str | None:
    """Serialize a selected optional date into the durable raw buffer."""
    return value.isoformat() if value else None


def _date(
    row: dict[str, Any], field: str, label: str, *, disabled: bool = False
) -> None:
    """Keep the financial date separate from optional source valuation dates."""
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


def _owner(row: dict[str, Any], owners: list[str]) -> None:
    """Offer household names and joint ownership, retaining previously copied names."""
    choices = list(dict.fromkeys([None, *owners, "Joint", row.get("owner")]))
    row["owner"] = st.selectbox(
        "Owner / borrower",
        choices,
        index=choices.index(row.get("owner")),
        format_func=lambda v: v or "Unknown",
        accept_new_options=True,
        key=_key(row, "owner"),
        on_change=forms.capture,
        args=(row, "owner", _key(row, "owner")),
    )


def _asset_fields(row: dict[str, Any]) -> None:
    """Edit shared holding and direct-asset details with separate form and exposure."""
    _text(row, "label", "Asset name")
    left, right = st.columns(2)
    with left:
        _amount(row, "value", "Asset value ($)")
        _choice(row, "instrument_type", "Holding type", get_args(InstrumentType))
    with right:
        _text(
            row, "security_id", "Ticker / security identifier (optional)", optional=True
        )
        _choice(row, "asset_class", "Asset class", get_args(AssetClass))
    _notes(row, "source_notes", "Asset source / notes")


def _holdings(account: dict[str, Any]) -> bool:
    """Edit account contents, applying row changes after their values are captured."""
    st.markdown("**Holdings**")
    st.caption(
        "These explain the account balance; they are not added to it. Replace a grouped holding when entering its individual assets."
    )
    remove = []
    for holding in account["holdings"]:
        with st.container(border=True):
            _asset_fields(holding)
            if st.button("Remove holding", key=_key(holding, "remove")):
                remove.append(holding["id"])
    account["holdings"] = [h for h in account["holdings"] if h["id"] not in remove]
    add = st.button("Add holding", key=_key(account, "add_holding"))
    if add:
        account["holdings"].append(
            AssetPosition(label="New holding").model_dump(mode="json")
        )
    return bool(remove or add)


def _account_fields(row: dict[str, Any], owners: list[str]) -> bool:
    """Edit an account total, classification, and optional holding detail."""
    _text(row, "label", "Account name")
    left, right = st.columns(2)
    with left:
        _owner(row, owners)
        _choice(row, "account_type", "Account type", get_args(AccountType))
        _amount(row, "reported_value", "Reported account value ($)")
    with right:
        _text(row, "institution", "Institution (optional)", optional=True)
        _choice(row, "tax_treatment", "Tax treatment", get_args(TaxTreatment))
        _date(row, "valuation_date", "Balance valuation date (if known)")
    row["is_combined"] = st.checkbox(
        "Combined accounts, not yet itemized",
        value=row["is_combined"],
        key=_key(row, "is_combined"),
        on_change=forms.capture,
        args=(row, "is_combined", _key(row, "is_combined")),
    )
    _notes(row, "source_notes", "Account source / notes")
    return _holdings(row)


def _direct_fields(row: dict[str, Any], owners: list[str]) -> None:
    """Capture assets held outside accounts, including their ownership context."""
    _asset_fields(row)
    _owner(row, owners)
    _choice(row, "tax_treatment", "Tax treatment", get_args(TaxTreatment))
    _date(row, "valuation_date", "Asset valuation date (if known)")


def _debt_fields(row: dict[str, Any], owners: list[str]) -> None:
    """Capture amounts owed as positive balances, independently of asset values."""
    _text(row, "label", "Debt name")
    _owner(row, owners)
    _choice(row, "debt_type", "Debt type", get_args(DebtType))
    _amount(row, "amount_owed", "Amount owed ($)")
    _date(row, "valuation_date", "Debt valuation date (if known)")
    row["is_combined"] = st.checkbox(
        "Combined debts, not yet itemized",
        value=row["is_combined"],
        key=_key(row, "is_combined"),
        on_change=forms.capture,
        args=(row, "is_combined", _key(row, "is_combined")),
    )
    _notes(row, "source_notes", "Debt source / notes")


def _contents(data: dict[str, Any], owners: list[str]) -> bool:
    """Edit all inventory sections before rerunning for structural row changes."""
    changed = False
    for field, label, model in (
        ("accounts", "account", AccountPosition),
        ("direct_assets", "direct asset", DirectAssetPosition),
        ("debts", "debt", DebtPosition),
    ):
        st.subheader(_label(field))
        remove = []
        for row in data[field]:
            with st.expander(row["label"] or f"New {label}", expanded=True):
                if field == "accounts":
                    changed = _account_fields(row, owners) or changed
                elif field == "direct_assets":
                    _direct_fields(row, owners)
                else:
                    _debt_fields(row, owners)
                if st.button(f"Remove {label}", key=_key(row, "remove")):
                    remove.append(row["id"])
        data[field] = [r for r in data[field] if r["id"] not in remove]
        add = st.button(f"Add {label}", key=_key(data, f"add_{field}"))
        if add:
            data[field].append(model(label=f"New {label}").model_dump(mode="json"))
        changed = bool(remove or add) or changed
    return changed


def _metadata(data: dict[str, Any], *, saved: bool) -> None:
    """Edit identifying facts; a later financial date belongs to a new snapshot."""
    _text(data, "name", "Snapshot name (optional)")
    left, right = st.columns(2)
    with left:
        _date(data, "as_of", "Financial as-of date", disabled=saved)
    with right:
        _choice(data, "kind", "Snapshot kind", ("actual", "hypothetical"))
    if saved:
        st.caption("To record a different date, duplicate this snapshot from Wealth.")
    _choice(data, "coverage", "Household coverage", ("partial", "complete_as_reported"))
    _notes(data, "coverage_notes", "Coverage / exclusions")
    _notes(data, "source_notes", "Snapshot source / notes")


def _summary(snapshot: WealthSnapshot, *, details: bool = False) -> None:
    """Present known subtotals and reconciled classifications without overstating coverage."""
    if not snapshot.has_balances:
        st.info("No balances entered.")
    else:
        for column, label, value in zip(
            st.columns(3),
            ("Recorded assets", "Recorded liabilities", "Net recorded position"),
            (snapshot.assets, snapshot.liabilities, snapshot.net_recorded),
            strict=True,
        ):
            column.metric(label, f"${value:,.2f}")
    if snapshot.missing_details:
        st.caption(
            "Incomplete information. Totals include known amounts only; unitemized contents are not assumed to be cash."
        )
    if details:
        left, right = st.columns(2)
        for column, label, groups in (
            (left, "Assets by account type", snapshot.account_types),
            (right, "Assets by asset class", snapshot.asset_classes),
        ):
            with column:
                st.markdown(f"**{label}**")
                if groups:
                    st.table(
                        [
                            {"Category": _label(k), "Known value": f"${v:,.2f}"}
                            for k, v in groups.items()
                        ]
                    )
        if snapshot.missing_details:
            with st.expander("Missing information"):
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
    except (ValueError, RevisionConflictError, psycopg.Error) as error:
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
                    "This backup exceeds the 1 MB import limit. Reduce notes or grouped detail before exporting."
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
            "Exports include current valid edits, even in local preview. Imports do not overwrite saved snapshots."
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
    changed = _contents(data, _owners(dsn))
    if changed:
        st.rerun()
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
            f"{record.snapshot.as_of} · {_label(record.snapshot.kind)} · Saved {record.updated_at:%Y-%m-%d %H:%M UTC} · {str(record.id)[:8]} · Revision {record.revision}"
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
        "This removes the saved snapshot for both of you and its draft in this session. It cannot be undone."
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
        except (RevisionConflictError, psycopg.Error) as error:
            _setup_error(error)
            return
        st.session_state.get("wealth_drafts", {}).pop(str(record.id), None)
        st.session_state["wealth_last_records"] = tuple(
            r
            for r in st.session_state.get("wealth_last_records", ())
            if r.id != record.id
        )
        if st.session_state.get("wealth_active") == str(record.id):
            st.session_state.pop("wealth_active", None)
        st.session_state.pop("wealth_pending_delete", None)
        st.session_state["notice"] = "Wealth snapshot deleted."
        st.rerun()


def wealth_page(dsn: str | None) -> None:
    """List reusable dated positions separately from annual planning scenarios."""
    st.title("Wealth")
    st.caption(
        "Saved assets and debts at a date. These balances are not yet used in annual scenario calculations or retirement projections."
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
    drafts = st.session_state.get("wealth_drafts", {})
    if drafts:
        with st.expander("Session drafts"):
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
