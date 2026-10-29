"""Isolated SQLite persistence and portable planning document boundaries."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest

from financial_model.planning import (
    EconomicAssumptions,
    HouseholdAssumptions,
    InvestmentAssumptions,
    PlanningScenario,
    RetirementAssumptions,
    SavedPlanningDocument,
    SpendingAssumptions,
    WorkAssumptions,
    replace_household,
    select_profile,
)
from financial_model.planning_storage import (
    create_planning_document,
    delete_planning_document,
    import_planning_backup,
    list_planning_documents,
    load_planning_document,
    update_planning_document,
)
from financial_model.storage import list_goals, list_scenarios
from financial_model.storage_common import MAX_BACKUP_BYTES, RevisionConflictError


@pytest.mark.integration
@pytest.mark.parametrize(
    "model",
    [
        HouseholdAssumptions,
        WorkAssumptions,
        SpendingAssumptions,
        InvestmentAssumptions,
        EconomicAssumptions,
        RetirementAssumptions,
        PlanningScenario,
    ],
)
def test_planning_round_trip_and_concurrency(database_url, model):
    record = SavedPlanningDocument(inputs=model(name="Synthetic"))
    saved = create_planning_document(database_url, record)
    assert load_planning_document(database_url, saved.id) == saved
    assert list_planning_documents(database_url, record.inputs.kind) == (saved,)
    with pytest.raises(sqlite3.IntegrityError):
        create_planning_document(database_url, saved)
    updated = update_planning_document(database_url, saved)
    assert updated.revision == 2
    with pytest.raises(RevisionConflictError):
        update_planning_document(database_url, saved)
    with pytest.raises(RevisionConflictError):
        delete_planning_document(database_url, saved.id, 1)
    delete_planning_document(database_url, saved.id, 2)
    assert load_planning_document(database_url, saved.id) is None
    with pytest.raises(RevisionConflictError):
        update_planning_document(database_url, updated)
    assert not list_scenarios(database_url)


@pytest.mark.integration
def test_plan_listing_keeps_name_and_identity_order_after_edit(
    database_url: str,
) -> None:
    records = [
        create_planning_document(
            database_url,
            SavedPlanningDocument(
                id=UUID(int=identity), inputs=PlanningScenario(name=name)
            ),
        )
        for identity, name in (
            (2, "01 ALPHA"),
            (1, "01 alpha"),
            (3, "03 Charlie"),
            (4, "02 Bravo"),
        )
    ]
    expected = (records[1], records[0], records[3], records[2])
    assert list_planning_documents(database_url, "plan") == expected
    edited = records[2].model_copy(
        update={"inputs": records[2].inputs.model_copy(update={"notes": "Reviewed"})}
    )
    updated = update_planning_document(database_url, edited)
    assert list_planning_documents(database_url, "plan") == (*expected[:3], updated)


@pytest.mark.integration
def test_profile_listing_retains_recency_order_after_edit(database_url: str) -> None:
    first, second = (
        create_planning_document(
            database_url, SavedPlanningDocument(inputs=SpendingAssumptions(name=name))
        )
        for name in ("01 First", "02 Second")
    )
    assert list_planning_documents(database_url, "spending") == (second, first)
    updated = update_planning_document(database_url, first)
    assert list_planning_documents(database_url, "spending") == (updated, second)


@pytest.mark.integration
def test_profile_listing_orders_timestamps_with_different_offsets(
    database_url: str,
) -> None:
    first, second = (
        create_planning_document(
            database_url,
            SavedPlanningDocument(
                inputs=SpendingAssumptions(name=name),
                updated_at=datetime.fromisoformat(timestamp),
            ),
        )
        for name, timestamp in (
            ("Earlier", "2026-10-05T12:00:00+09:00"),
            ("Later", "2026-10-05T05:00:00+00:00"),
        )
    )
    assert list_planning_documents(database_url, "spending") == (second, first)


@pytest.mark.integration
def test_kind_cannot_change_and_source_deletion_does_not_mutate_plan(database_url):
    source = create_planning_document(
        database_url, SavedPlanningDocument(inputs=WorkAssumptions(name="Work"))
    )
    plan = create_planning_document(
        database_url,
        SavedPlanningDocument(
            inputs=PlanningScenario(name="Plan", components=(select_profile(source),))
        ),
    )
    with pytest.raises(RevisionConflictError):
        update_planning_document(
            database_url,
            SavedPlanningDocument(
                id=source.id, inputs=SpendingAssumptions(name="Wrong category")
            ),
        )
    delete_planning_document(database_url, source.id, source.revision)
    assert load_planning_document(database_url, plan.id) == plan
    imported = import_planning_backup(plan.model_dump_json().encode())
    new = create_planning_document(database_url, SavedPlanningDocument(inputs=imported))
    assert new.id != plan.id
    assert new.inputs.components[0].source_id == source.id
    assert not list_goals(database_url)
    assert not list_planning_documents(database_url, "work")


@pytest.mark.integration
def test_household_source_deletion_preserves_plan_copy(database_url):
    source = create_planning_document(
        database_url,
        SavedPlanningDocument(
            inputs=HouseholdAssumptions(
                name="Family", children=[{"name": "Child", "birth_year": 2029}]
            )
        ),
    )
    plan = create_planning_document(
        database_url,
        SavedPlanningDocument(
            inputs=replace_household(PlanningScenario(name="Plan"), source)
        ),
    )
    delete_planning_document(database_url, source.id, source.revision)
    assert load_planning_document(database_url, plan.id) == plan
    assert import_planning_backup(plan.model_dump_json().encode()) == plan.inputs


@pytest.mark.parametrize(
    "change",
    [{"schema_version": 1}, {"document_type": "wealth_snapshot"}, {"extra": True}],
)
def test_import_rejects_bad_envelopes(change):
    document = SavedPlanningDocument(inputs=EconomicAssumptions(name="Example"))
    raw = document.model_dump(mode="json") | change
    with pytest.raises(ValueError):
        import_planning_backup(json.dumps(raw).encode())


def test_import_strips_identity_and_rejects_size_and_bad_json():
    document = SavedPlanningDocument(inputs=EconomicAssumptions(name="Example"))
    inputs = import_planning_backup(document.model_dump_json().encode())
    assert SavedPlanningDocument(inputs=inputs).id != document.id
    for data in (
        b"invalid",
        b" " * (MAX_BACKUP_BYTES + 1),
        b'{"inputs":{"kind":"work","name":"Missing envelope"}}',
    ):
        with pytest.raises(ValueError):
            import_planning_backup(data)


def test_import_old_planning_version_requires_explicit_conversion():
    raw = SavedPlanningDocument(inputs=EconomicAssumptions(name="Old")).model_dump(
        mode="json"
    )
    raw["schema_version"] = 3
    with pytest.raises(ValueError, match="explicit conversion"):
        import_planning_backup(json.dumps(raw).encode())


@pytest.mark.integration
def test_schema_reapply_preserves_planning_documents(database_url: str) -> None:
    existing = create_planning_document(
        database_url, SavedPlanningDocument(inputs=PlanningScenario(name="Existing"))
    )
    schema = (Path(__file__).parents[2] / "src/financial_model/schema.sql").read_text()
    connection = sqlite3.connect(database_url)
    try:
        connection.executescript(schema)
        connection.executescript(schema)
    finally:
        connection.close()
    assert load_planning_document(database_url, existing.id) == existing
    household = create_planning_document(
        database_url, SavedPlanningDocument(inputs=HouseholdAssumptions(name="Family"))
    )
    assert load_planning_document(database_url, household.id) == household


@pytest.mark.integration
@pytest.mark.parametrize(
    "change",
    [
        {"id": "different"},
        {"revision": 2},
        {"revision": "1"},
        {"schema_version": 3},
        {"schema_version": "4"},
        {"document_type": "forecast"},
        {"inputs": {"kind": "unknown"}},
        {"inputs": {}},
    ],
)
def test_planning_database_rejects_invalid_envelopes(
    database_url: str, change: dict
) -> None:
    document = SavedPlanningDocument(inputs=EconomicAssumptions(name="Synthetic"))
    raw = document.model_dump(mode="json") | change
    from financial_model.storage_common import connect

    with pytest.raises(sqlite3.IntegrityError), connect(database_url) as connection:
        connection.execute(
            "INSERT INTO planning_documents VALUES (?, 1, ?, ?)",
            (str(document.id), json.dumps(raw), document.updated_at.isoformat()),
        )
    assert not list_planning_documents(database_url, "economy")
