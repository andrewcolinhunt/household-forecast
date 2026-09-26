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
    data = document.model_dump() | {"schema_version": 2}
    with pytest.raises(ValidationError, match="schema_version"):
        SavedScenario.model_validate(data)
