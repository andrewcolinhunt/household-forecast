"""Real PostgreSQL transactions, concurrent edits, and portable backups."""

from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest
from pydantic import ValidationError

from financial_model.model import evaluate_scenario
from financial_model.schemas import SavedScenario, Scenario
from financial_model.storage import (
    RevisionConflictError,
    create_scenario,
    delete_scenario,
    import_backup,
    list_scenarios,
    load_scenario,
    update_scenario,
)


@pytest.fixture
def document(scenario: Scenario) -> SavedScenario:
    return SavedScenario(inputs=scenario, result=evaluate_scenario(scenario))


@pytest.mark.integration
def test_create_scenario_persists_across_connections(
    database_url: str, document: SavedScenario
) -> None:
    assert create_scenario(database_url, document) == document
    assert load_scenario(database_url, document.id) == document
    assert list_scenarios(database_url) == (document,)
    assert load_scenario(database_url, uuid4()) is None


@pytest.mark.integration
def test_update_scenario_detects_two_session_conflict(
    database_url: str, document: SavedScenario
) -> None:
    create_scenario(database_url, document)
    first = load_scenario(database_url, document.id)
    second = load_scenario(database_url, document.id)
    updated = update_scenario(database_url, first)
    assert updated.revision == 2
    with pytest.raises(RevisionConflictError):
        update_scenario(database_url, second)
    assert load_scenario(database_url, document.id) == updated


@pytest.mark.integration
def test_create_scenario_duplicate_id_does_not_overwrite(
    database_url: str, document: SavedScenario
) -> None:
    create_scenario(database_url, document)
    with pytest.raises(psycopg.errors.UniqueViolation):
        create_scenario(database_url, document)
    assert list_scenarios(database_url) == (document,)


@pytest.mark.integration
def test_update_scenario_inputs_and_results_are_atomic(
    database_url: str, document: SavedScenario
) -> None:
    create_scenario(database_url, document)
    inputs = Scenario.model_validate(
        document.inputs.model_dump() | {"name": "Alternative"}
    )
    replacement = SavedScenario(
        id=document.id,
        revision=document.revision,
        inputs=inputs,
        result=evaluate_scenario(inputs),
    )
    saved = update_scenario(database_url, replacement)
    loaded = load_scenario(database_url, document.id)
    assert loaded == saved
    assert loaded.inputs.name == "Alternative"
    assert loaded.result.input_digest == inputs.digest()


def test_import_backup_returns_inputs_only(document: SavedScenario) -> None:
    assert import_backup(document.model_dump_json().encode()) == document.inputs


def test_import_backup_rejects_invalid_content(document: SavedScenario) -> None:
    with pytest.raises(ValueError, match="1 MB"):
        import_backup(b" " * 1_000_001)
    with pytest.raises(ValidationError):
        import_backup(b"not JSON")
    data = document.model_dump() | {"schema_version": 999}
    with pytest.raises(ValidationError, match="schema_version"):
        SavedScenario.model_validate(data)


@pytest.mark.integration
def test_delete_scenario_removes_only_requested_record(
    database_url: str, document: SavedScenario
) -> None:
    create_scenario(database_url, document)
    other = create_scenario(
        database_url, SavedScenario(inputs=document.inputs, result=document.result)
    )
    delete_scenario(database_url, document.id, document.revision)
    assert load_scenario(database_url, document.id) is None
    assert list_scenarios(database_url) == (other,)
    with pytest.raises(RevisionConflictError):
        update_scenario(database_url, document)
    assert load_scenario(database_url, document.id) is None


@pytest.mark.integration
def test_delete_scenario_rejects_stale_and_missing_revisions(
    database_url: str, document: SavedScenario
) -> None:
    create_scenario(database_url, document)
    updated = update_scenario(database_url, document)
    with pytest.raises(RevisionConflictError):
        delete_scenario(database_url, document.id, document.revision)
    assert load_scenario(database_url, document.id) == updated
    delete_scenario(database_url, updated.id, updated.revision)
    with pytest.raises(RevisionConflictError):
        delete_scenario(database_url, updated.id, updated.revision)


def test_legacy_backup_loads_with_missing_employer_details(
    document: SavedScenario,
) -> None:
    raw = document.model_dump_json()
    assert '"employer":' not in raw
    assert '"not_working":' not in raw
    assert '"rsu_taxes_from_shares":' not in raw
    restored = SavedScenario.model_validate_json(raw)
    assert restored == document
    assert restored.inputs.rsu_taxes_from_shares
    assert restored.inputs.digest() == document.inputs.digest()
    assert all(
        person.employer is None and not person.not_working
        for person in restored.inputs.adults
    )


@pytest.mark.integration
def test_shared_goals_rename_revisions_and_referenced_deletion(database_url, document):
    from financial_model.schemas import Goal
    from financial_model.storage import (
        create_goal,
        delete_goal,
        list_goals,
        update_goal,
    )

    create_scenario(database_url, document)
    goals = list_goals(database_url)
    assert len(goals) == 2
    retirement = next(g for g in goals if g.name == "Retirement")
    renamed = update_goal(
        database_url, retirement.model_copy(update={"name": "Later life"})
    )
    assert renamed.id == retirement.id
    assert renamed.revision == 2
    with pytest.raises(RevisionConflictError):
        update_goal(database_url, retirement)
    with pytest.raises(ValueError, match="Reassign"):
        delete_goal(database_url, renamed)
    # A stale backup must explicitly resolve the renamed destination before saving.
    with pytest.raises(ValueError, match="catalogue"):
        create_scenario(
            database_url, SavedScenario(inputs=document.inputs, result=document.result)
        )
    assert len(list_scenarios(database_url)) == 1
    spare = create_goal(database_url, Goal(name="Travel"))
    delete_goal(database_url, spare)
    assert spare not in list_goals(database_url)


def test_import_identity_resolution_does_not_merge_same_names(document):
    from financial_model.schemas import Goal
    from financial_model.storage import resolve_goal_conflicts

    old = document.inputs.allocation_plan.rows[0].goal
    new = Goal(name=old.name)
    inputs = resolve_goal_conflicts(document.inputs, {old.id: new})
    assert inputs.allocation_plan.rows[0].goal == new
    assert inputs.allocation_plan.retirement_goal_id == new.id
    assert inputs.allocation_plan.option_goal_id == new.id
    assert inputs.allocation_plan.rows[1] == document.inputs.allocation_plan.rows[1]
    assert (
        evaluate_scenario(inputs).net_saved_from_income
        == document.result.net_saved_from_income
    )


@pytest.mark.integration
def test_household_revision_checks_and_scenario_isolation(database_url, document):
    from financial_model.schemas import Household
    from financial_model.storage import load_household, save_household

    create_scenario(database_url, document)
    assert load_household(database_url) is None
    draft = Household(
        tax_year=2026, adults=({"name": "Alex", "age": 32}, {"name": "Sam", "age": 37})
    )
    saved = save_household(database_url, draft)
    assert saved.revision == 1
    assert load_household(database_url) == saved
    with pytest.raises(RevisionConflictError):
        save_household(database_url, draft)
    changed = save_household(database_url, saved.model_copy(update={"tax_year": 2025}))
    with pytest.raises(RevisionConflictError):
        save_household(database_url, saved)
    assert load_household(database_url) == changed
    assert load_scenario(database_url, document.id) == document
    assert "revision" not in changed.scenario_defaults()
    assert "allocation_plan" not in changed.scenario_defaults()
