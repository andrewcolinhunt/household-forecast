"""Wealth documents in real isolated SQLite schemas and portable backups."""

from __future__ import annotations

import sqlite3
from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from financial_model.storage import (
    RevisionConflictError,
    create_wealth_snapshot,
    delete_wealth_snapshot,
    import_backup,
    import_wealth_backup,
    list_scenarios,
    list_wealth_snapshots,
    load_wealth_snapshot,
    update_wealth_snapshot,
)
from financial_model.wealth import (
    AccountPosition,
    SavedWealthSnapshot,
    WealthSnapshot,
)


@pytest.fixture
def wealth_document() -> SavedWealthSnapshot:
    return SavedWealthSnapshot(
        snapshot=WealthSnapshot(
            as_of=date(2026, 9, 1),
            accounts=(
                AccountPosition(
                    label="Synthetic brokerage",
                    balance=100,
                    invested_in="stocks",
                ),
            ),
        )
    )


@pytest.mark.integration
def test_wealth_storage_round_trip_order_and_atomic_revision(
    database_url: str, wealth_document: SavedWealthSnapshot
) -> None:
    original = create_wealth_snapshot(database_url, wealth_document)
    assert load_wealth_snapshot(database_url, original.id) == original
    assert load_wealth_snapshot(database_url, uuid4()) is None
    later = create_wealth_snapshot(
        database_url,
        SavedWealthSnapshot(snapshot=WealthSnapshot(as_of=date(2026, 10, 1))),
    )
    assert list_wealth_snapshots(database_url) == (later, original)
    raw = original.snapshot.model_dump(mode="json")
    raw["accounts"][0]["invested_in"] = "bonds"
    edited = SavedWealthSnapshot(
        id=original.id,
        revision=original.revision,
        snapshot=WealthSnapshot.model_validate(raw),
    )
    updated = update_wealth_snapshot(database_url, edited)
    assert updated.revision == 2
    assert updated.updated_at >= original.updated_at
    assert load_wealth_snapshot(database_url, original.id) == updated
    with pytest.raises(RevisionConflictError):
        update_wealth_snapshot(database_url, original)
    assert load_wealth_snapshot(database_url, original.id) == updated
    assert not list_scenarios(database_url)


@pytest.mark.integration
def test_wealth_storage_date_changes_require_new_identity(
    database_url: str, wealth_document: SavedWealthSnapshot
) -> None:
    create_wealth_snapshot(database_url, wealth_document)
    changed = WealthSnapshot.model_validate(
        wealth_document.snapshot.model_dump() | {"as_of": date(2026, 10, 1)}
    )
    with pytest.raises(RevisionConflictError, match="financial date"):
        update_wealth_snapshot(
            database_url, SavedWealthSnapshot(id=wealth_document.id, snapshot=changed)
        )
    assert load_wealth_snapshot(database_url, wealth_document.id) == wealth_document
    copied = create_wealth_snapshot(database_url, SavedWealthSnapshot(snapshot=changed))
    assert copied.id != wealth_document.id
    assert len(list_wealth_snapshots(database_url)) == 2


@pytest.mark.integration
def test_wealth_storage_create_delete_conflicts(
    database_url: str, wealth_document: SavedWealthSnapshot
) -> None:
    create_wealth_snapshot(database_url, wealth_document)
    with pytest.raises(sqlite3.IntegrityError):
        create_wealth_snapshot(database_url, wealth_document)
    with pytest.raises(ValueError, match="revision 1"):
        create_wealth_snapshot(
            database_url, wealth_document.model_copy(update={"revision": 2})
        )
    updated = update_wealth_snapshot(database_url, wealth_document)
    with pytest.raises(RevisionConflictError):
        delete_wealth_snapshot(
            database_url, wealth_document.id, wealth_document.revision
        )
    assert load_wealth_snapshot(database_url, updated.id) == updated
    delete_wealth_snapshot(database_url, updated.id, updated.revision)
    assert not list_wealth_snapshots(database_url)
    with pytest.raises(RevisionConflictError):
        update_wealth_snapshot(database_url, updated)
    with pytest.raises(RevisionConflictError):
        delete_wealth_snapshot(database_url, updated.id, updated.revision)


def test_wealth_backup_returns_inputs_without_identity(
    wealth_document: SavedWealthSnapshot,
) -> None:
    snapshot = import_wealth_backup(wealth_document.model_dump_json().encode())
    assert snapshot == wealth_document.snapshot
    assert SavedWealthSnapshot(snapshot=snapshot).id != wealth_document.id
    with pytest.raises(ValidationError):
        import_backup(wealth_document.model_dump_json().encode())


@pytest.mark.parametrize(
    "data", [b"invalid", b"{}", b'{"schema_version":2}', b" " * 1_000_001]
)
def test_wealth_backup_rejects_invalid_or_oversized_data(data: bytes) -> None:
    with pytest.raises(ValueError):
        import_wealth_backup(data)
