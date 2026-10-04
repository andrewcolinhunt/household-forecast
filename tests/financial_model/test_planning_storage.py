"""Isolated PostgreSQL persistence and portable planning document boundaries."""

from __future__ import annotations

import json
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from financial_model.planning import (
    EconomicAssumptions,
    InvestmentAssumptions,
    PlanningScenario,
    RetirementAssumptions,
    SavedPlanningDocument,
    SpendingAssumptions,
    WorkAssumptions,
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
from financial_model.storage import (
    MAX_BACKUP_BYTES,
    RevisionConflictError,
    list_goals,
    list_scenarios,
)


@pytest.mark.integration
@pytest.mark.parametrize(
    "model",
    [
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
    with pytest.raises(psycopg.errors.UniqueViolation):
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
    with pytest.raises(ValueError, match=r"migrate_household_timeline\.py"):
        import_planning_backup(json.dumps(raw).encode())


@pytest.mark.integration
def test_planning_runtime_grants_and_database_constraints(database_url):
    role = "planning_test_" + uuid4().hex
    document = SavedPlanningDocument(inputs=EconomicAssumptions(name="Synthetic"))
    with psycopg.connect(database_url) as connection:
        schema = connection.execute("SELECT current_schema()").fetchone()[0]
        connection.execute(
            sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role))
        )
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(schema), sql.Identifier(role)
            )
        )
        connection.execute(
            sql.SQL(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON planning_documents TO {}"
            ).format(sql.Identifier(role))
        )
        connection.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
        connection.execute(
            "INSERT INTO planning_documents VALUES (%s, 1, %s, %s)",
            (document.id, Jsonb(document.model_dump(mode="json")), document.updated_at),
        )
        assert (
            connection.execute("SELECT count(*) FROM planning_documents").fetchone()[0]
            == 1
        )
        connection.execute(
            "UPDATE planning_documents SET updated_at = now() WHERE id = %s",
            (document.id,),
        )
        connection.execute(
            "DELETE FROM planning_documents WHERE id = %s", (document.id,)
        )
        assert (
            connection.execute("SELECT count(*) FROM planning_documents").fetchone()[0]
            == 0
        )
        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute(
                "INSERT INTO planning_documents VALUES (%s, 1, %s, %s)",
                (document.id, Jsonb({}), document.updated_at),
            )
        connection.rollback()  # Dispose of the test-only role and grants too.
