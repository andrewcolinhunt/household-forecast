"""Preservation of old inputs, independent copies, and reviewed person mappings."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from financial_model.planning import (
    EconomicAssumptions,
    PlanningScenario,
    RetirementAssumptions,
    SpendingAssumptions,
    WorkAssumptions,
)
from financial_model.planning_migration import convert_planning_documents


def legacy(inputs):
    raw = inputs.model_dump(mode="json")
    kind = raw["kind"]
    if kind == "work":
        for adult in raw["adults"]:
            adult["retirement"] = {
                "central": None,
                "lower": None,
                "upper": None,
                "notes": "",
            }
    elif kind == "spending":
        raw["survivor"] = None
    elif kind == "retirement":
        raw["kind"] = "longevity"
        for key in (
            "currency",
            "money_year",
            "only_adult_1",
            "only_adult_2",
            "both_retired",
            "survivor",
        ):
            raw.pop(key)
        for adult in raw["adults"]:
            adult.pop("retirement")
    elif kind == "economy":
        raw |= {
            "fees": None,
            "forecast_years": 10,
            "return_basis": "nominal_compound_before_fees_and_taxes",
        }
    elif kind == "plan":
        raw |= {"work_adults": None, "longevity_adults": None}
    return {
        "document_type": "planning_document",
        "schema_version": 1,
        "id": str(uuid4()),
        "revision": 3,
        "updated_at": datetime.now(UTC).isoformat(),
        "inputs": raw,
    }


def chosen(record):
    return {
        "source_id": record["id"],
        "source_revision": record["revision"],
        "source_name": record["inputs"]["name"],
        "inputs": deepcopy(record["inputs"]),
    }


def test_migration_combines_retirement_without_matching_names_or_mutating_originals():
    work = legacy(WorkAssumptions(name="Work", money_year=2026))
    work["inputs"]["adults"][0]["retirement"]["central"] = 2040
    work["inputs"]["adults"][1]["retirement"]["central"] = 2050
    life = legacy(
        RetirementAssumptions(
            name="Life",
            adults=(
                {"slot": 1, "name": "B", "age": {"central": 90}},
                {"slot": 2, "name": "A", "age": {"central": 95}},
            ),
        )
    )
    spending = legacy(
        SpendingAssumptions(
            name="Spend",
            money_year=2026,
            baseline={"amount": {"central": 100000}},
            replacements=(
                {
                    "label": "Retire",
                    "when": {"kind": "both"},
                    "budget": {"amount": {"central": 80000}},
                },
            ),
        )
    )
    spending["inputs"]["survivor"] = {
        "amount": {
            "central": "60000",
            "lower": None,
            "upper": None,
            "notes": "Old explanation",
        },
        "essential": None,
        "notes": "Includes housing",
    }
    plan = legacy(PlanningScenario(name="Plan"))
    plan["inputs"] |= {
        "components": [chosen(d) for d in (work, life, spending)],
        "work_adults": [1, 2],
        "longevity_adults": [2, 1],
    }
    # Saved copies must remain independent from more recent library values.
    plan["inputs"]["components"][0]["inputs"]["adults"][0]["retirement"]["central"] = (
        2042
    )
    originals = [work, life, spending, plan]
    before = deepcopy(originals)
    converted = convert_planning_documents(originals, money_year=2026)
    assert originals == before
    by_id = {str(d.id): d for d in converted}
    assert all(d.schema_version == 2 for d in converted)
    assert all(by_id[d["id"]].revision == 4 for d in originals)
    assert "retirement" not in by_id[work["id"]].inputs.adults[0].model_dump()
    assert not by_id[spending["id"]].inputs.replacements
    selected = next(
        c for c in by_id[plan["id"]].inputs.components if c.inputs.kind == "retirement"
    )
    assert selected.adult_mapping == (2, 1)
    assert [a.retirement.central for a in selected.inputs.adults] == [2050, 2042]
    assert selected.inputs.both_retired.amount.central == 80000
    assert selected.inputs.survivor.amount.central == 60000
    assert selected.inputs.survivor.notes == "Includes housing"


def test_migration_converts_all_return_bounds_after_fees_and_preserves_inflation():
    old = legacy(
        EconomicAssumptions(
            name="Gross",
            equities={"central": ".06", "lower": ".03", "upper": ".08"},
            inflation={"central": ".025"},
        )
    )
    old["inputs"]["fees"] = ".01"
    old["inputs"]["equities"]["notes"] = "No longer a field"
    (new,) = convert_planning_documents([old])
    assert new.inputs.equities.central == Decimal(".0494")
    assert new.inputs.equities.lower == Decimal(".0197")
    assert new.inputs.equities.upper == Decimal(".0692")
    assert new.inputs.inflation.central == Decimal(".025")
    assert "forecast_years" not in new.inputs.model_dump()
    old["inputs"]["fees"] = None
    (no_fee,) = convert_planning_documents([old])
    assert no_fee.inputs.equities.central == Decimal(".06")
    assert "kept unchanged" in no_fee.inputs.notes


def test_migration_requires_review_for_undated_rows_and_rejects_retiming():
    old = legacy(
        SpendingAssumptions(
            name="Spend",
            replacements=(
                {"label": "Retirement", "budget": {"amount": {"central": 80000}}},
            ),
        )
    )
    (retained,) = convert_planning_documents([old])
    assert len(retained.inputs.replacements) == 1
    records = convert_planning_documents([old], retirement_rows={old["id"]: (0,)})
    assert len(records) == 2
    assert not records[0].inputs.replacements
    assert records[1].inputs.both_retired.amount.central == 80000
    old["inputs"]["replacements"][0]["when"]["year"] = 2045
    with pytest.raises(ValueError, match="retimed"):
        convert_planning_documents([old], retirement_rows={old["id"]: (0,)})
    old["schema_version"] = 9
    with pytest.raises(ValueError, match="version-one"):
        convert_planning_documents([old])


def test_migration_unknown_budgets_do_not_create_duplicate_retirement_scenarios():
    life = legacy(RetirementAssumptions(name="Life"))
    spending = legacy(SpendingAssumptions(name="Spend", money_year=2026))
    spending["inputs"]["survivor"] = {
        "amount": {"central": None, "lower": None, "upper": None},
        "essential": None,
        "notes": "",
    }
    plan = legacy(PlanningScenario(name="Plan"))
    plan["inputs"]["components"] = [chosen(life), chosen(spending)]
    records = convert_planning_documents([life, spending, plan], money_year=2026)
    assert len(records) == 3
    assert sum(d.inputs.kind == "retirement" for d in records) == 1


@pytest.mark.integration
def test_migration_is_atomic_and_rejects_a_stale_preview(database_url, tmp_path):
    import importlib.util
    import json
    from pathlib import Path

    from psycopg.types.json import Jsonb

    from financial_model.planning_storage import list_planning_documents
    from financial_model.storage import _connect

    spec = importlib.util.spec_from_file_location(
        "migrate_retirement",
        Path(__file__).parents[2] / "scripts/migrate_planning_retirement.py",
    )
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    old = legacy(EconomicAssumptions(name="Economy"))
    with _connect(database_url) as connection:
        connection.execute("DROP TABLE planning_documents")
        schema = (
            (Path(__file__).parents[2] / "scripts/schema.sql")
            .read_text()
            .split("CREATE TABLE IF NOT EXISTS planning_documents")[1]
        )
        connection.execute(
            "CREATE TABLE planning_documents"
            + schema.replace("FROM '2'", "FROM '1'").replace(
                "'retirement'", "'longevity'"
            )
        )
        connection.execute(
            "INSERT INTO planning_documents VALUES (%s, %s, %s, %s)",
            (old["id"], old["revision"], Jsonb(old), old["updated_at"]),
        )
    directory = tmp_path / "preview"
    script.preview(database_url, directory, {})
    assert json.loads((directory / "originals.json").read_text()) == [old]
    with _connect(database_url) as connection:
        edited = deepcopy(old)
        edited["inputs"]["name"] = "Changed since preview"
        connection.execute(
            "UPDATE planning_documents SET document = %s", (Jsonb(edited),)
        )
    with pytest.raises(ValueError, match="changed since preview"):
        script.apply(database_url, directory)
    with _connect(database_url) as connection:
        assert (
            connection.execute("SELECT document FROM planning_documents").fetchone()[
                "document"
            ]
            == edited
        )
        connection.execute("UPDATE planning_documents SET document = %s", (Jsonb(old),))
    script.apply(database_url, directory)
    (new,) = list_planning_documents(database_url, "economy")
    assert new.inputs.name == "Economy"
    assert new.revision == old["revision"] + 1
    with pytest.raises(ValueError, match="changed since preview"):
        script.apply(database_url, directory)
