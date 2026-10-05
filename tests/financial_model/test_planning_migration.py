"""Explicit timeline conversion preserves old effective cash flows and originals."""

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
    missing_details,
)
from financial_model.planning_migration import (
    convert_planning_documents,
    convert_timeline_documents,
    timeline_conversion_review,
)


def document(inputs: dict) -> dict:
    return {
        "document_type": "planning_document",
        "schema_version": 3,
        "id": str(uuid4()),
        "revision": 3,
        "updated_at": datetime.now(UTC).isoformat(),
        "inputs": inputs,
    }


def chosen(record: dict) -> dict:
    return {
        "source_id": record["id"],
        "source_revision": record["revision"],
        "source_name": record["inputs"]["name"],
        "inputs": deepcopy(record["inputs"]),
        "adult_mapping": [1, 2] if record["inputs"]["kind"] == "retirement" else None,
    }


def budget(amount: int | None) -> dict:
    return {
        "amount": {"central": amount, "lower": None, "upper": None},
        "essential": None,
        "notes": "",
    }


@pytest.fixture
def old_documents() -> list[dict]:
    spending = document(
        {
            "kind": "spending",
            "name": "Expenses",
            "money_year": 2026,
            "baseline": budget(100000),
            "replacements": [
                {
                    "label": "Overridden move",
                    "when": {"kind": "year", "year": 2028},
                    "budget": budget(90000),
                }
            ],
            "adjustments": [
                {
                    "label": "Children",
                    "amount": {"central": 5000},
                    "start": {"kind": "year", "year": 2026},
                    "end": {"kind": "year", "year": 2030},
                    "frequency": "recurring",
                },
                {
                    "label": "Future bill",
                    "amount": {"central": 25000},
                    "start": {"kind": "year", "year": 2026},
                    "frequency": "once",
                },
            ],
        }
    )
    retirement = document(
        RetirementAssumptions(name="Retire", money_year=2026).model_dump(mode="json")
    )
    retirement["inputs"] |= {
        "only_adult_1": {"mode": "replace", "budget": budget(110000)},
        "only_adult_2": {"mode": "continue", "budget": None},
        "both_retired": budget(80000),
        "survivor": budget(60000),
    }
    for person, year, age in zip(
        retirement["inputs"]["adults"], (2027, 2029), (44, 43), strict=True
    ):
        person["retirement"]["central"], person["age"]["central"] = year, age
    plan = document(PlanningScenario(name="Reviewed plan").model_dump(mode="json"))
    plan["inputs"] |= {
        "household": {
            "reference_year": 2026,
            "adults": [{"name": "A", "age": 40}, {"name": "B", "age": 38}],
            "children": [{"name": "Child", "birth_year": 2025}],
        },
        "wealth": {
            "document_type": "wealth_snapshot",
            "schema_version": 2,
            "id": str(uuid4()),
            "revision": 1,
            "updated_at": datetime.now(UTC).isoformat(),
            "snapshot": {"as_of": "2026-07-01"},
        },
        "components": [chosen(spending), chosen(retirement)],
    }
    return [spending, retirement, plan]


def contexts(records: list[dict]) -> dict[str, str]:
    return {d["id"]: records[-1]["id"] for d in records[:-1]}


def test_conversion_preserves_old_precedence_and_partial_year_separately(old_documents):
    before = deepcopy(old_documents)
    converted = convert_timeline_documents(
        old_documents, profile_contexts=contexts(old_documents)
    )
    assert old_documents == before
    assert all(d.schema_version == 4 for d in converted)
    assert len(converted) == 4  # Standalone retirement budgets retain a Spending home.
    plan = next(d for d in converted if d.inputs.kind == "plan")
    spending = next(
        c.inputs for c in plan.inputs.components if c.inputs.kind == "spending"
    )
    assert [level.amount.central for level in spending.expenses[0].levels] == [
        105000,
        115000,
        85000,
        80000,
        60000,
    ]
    assert spending.expenses[0].end.year == 2032
    assert spending.expenses[1].frequency == "once"
    assert spending.expenses[1].levels[0].amount.central == 25000
    review = timeline_conversion_review(old_documents, converted)
    assert all(row["old_total"] == row["new_total"] for row in review)
    assert (
        Decimal(review[0]["new_total"])
        == Decimal(105000) * Decimal(str(184 / 365)) + 25000
    )
    assert "fixed calendar dates" in spending.expenses[0].notes
    assert plan.revision == 4
    assert all(c.source_revision == 3 for c in plan.inputs.components)


def test_conversion_stable_ids_and_independent_copies(old_documents):
    old_documents[-1]["inputs"]["components"][0]["inputs"]["baseline"] = budget(120000)
    first = convert_timeline_documents(
        old_documents, profile_contexts=contexts(old_documents)
    )
    second = convert_timeline_documents(
        old_documents, profile_contexts=contexts(old_documents)
    )
    assert [d.inputs for d in first] == [d.inputs for d in second]
    standalone = next(d.inputs for d in first if str(d.id) == old_documents[0]["id"])
    plan = next(d.inputs for d in first if d.inputs.kind == "plan")
    selected = next(c.inputs for c in plan.components if c.inputs.kind == "spending")
    assert standalone.expenses[0].levels[0].amount.central == 105000
    assert selected.expenses[0].levels[0].amount.central == 125000
    assert plan.household.children[0].id is not None
    assert (
        "both_retired"
        not in next(
            c.inputs for c in plan.components if c.inputs.kind == "retirement"
        ).model_dump()
    )


def test_conversion_requires_explicit_context_and_resolved_plan(old_documents):
    with pytest.raises(ValueError, match="Plan context"):
        convert_timeline_documents(old_documents)
    plan = old_documents[-1]
    plan["inputs"]["components"].append(deepcopy(plan["inputs"]["components"][0]))
    with pytest.raises(ValueError, match="one reviewed profile combination"):
        convert_timeline_documents([plan])
    plan["inputs"]["components"].pop()
    plan["inputs"]["wealth"] = None
    with pytest.raises(ValueError, match="fixed lifespans"):
        convert_timeline_documents([plan])


def test_conversion_rejects_resolved_conflicts_and_negative_net_costs(old_documents):
    plan = old_documents[-1]
    spending = plan["inputs"]["components"][0]["inputs"]
    spending["replacements"].append(
        {
            "label": "Collision",
            "when": {"kind": "adult_1", "offset": 1},
            "budget": budget(70000),
        }
    )
    with pytest.raises(ValueError, match="same year"):
        convert_timeline_documents([plan])
    spending["replacements"].pop()
    spending["adjustments"][1]["amount"]["central"] = -1
    with pytest.raises(ValueError, match="Negative net"):
        convert_timeline_documents([plan])


def test_conversion_preserves_signed_adjustment_totals_and_ranges(old_documents):
    plan = old_documents[-1]
    spending = plan["inputs"]["components"][0]["inputs"]
    spending["baseline"]["amount"] |= {"lower": 95000, "upper": 110000}
    spending["adjustments"][0]["amount"]["central"] = -5000
    (converted,) = convert_timeline_documents([plan])
    expense = converted.inputs.components[0].inputs.expenses[0]
    assert expense.levels[0].amount.central == 95000
    assert expense.levels[0].amount.lower == 90000
    assert expense.levels[0].amount.upper == 105000


def test_conversion_rejects_unknown_overridden_budget_and_mismatched_people(
    old_documents,
):
    old_documents[-1]["inputs"]["components"][0]["inputs"]["replacements"][0][
        "budget"
    ] = budget(None)
    with pytest.raises(ValueError, match="including overridden budgets"):
        convert_timeline_documents([old_documents[-1]])
    old_documents[-1]["inputs"]["components"][0]["inputs"]["replacements"][0][
        "budget"
    ] = budget(90000)
    old_documents[1]["inputs"]["adults"].reverse()
    with pytest.raises(ValueError, match="person mapping"):
        convert_timeline_documents(
            old_documents, profile_contexts=contexts(old_documents)
        )


def test_retirement_contexts_preserve_distinct_alternatives_with_stable_ids(
    old_documents,
):
    spending, retirement, first = old_documents
    second = deepcopy(first)
    second["id"], second["inputs"]["name"] = str(uuid4()), "Seattle"
    second["inputs"]["components"][0]["inputs"]["baseline"]["amount"] = {
        "central": 120000,
        "lower": 110000,
        "upper": 130000,
    }
    duplicate = deepcopy(first)
    duplicate["id"], duplicate["inputs"]["name"] = str(uuid4()), "Same costs"
    records = [*old_documents, second, duplicate]
    before = deepcopy(records)
    selected = {
        spending["id"]: first["id"],
        retirement["id"]: [first["id"], second["id"], duplicate["id"]],
    }
    converted = convert_timeline_documents(records, profile_contexts=selected)
    assert records == before
    generated = [d for d in converted if str(d.id) not in {r["id"] for r in records}]
    assert len(generated) == 2
    assert {d.inputs.expenses[0].levels[0].amount.central for d in generated} == {
        105000,
        125000,
    }
    seattle = next(d.inputs for d in generated if "Seattle" in d.inputs.name)
    assert seattle.name == "Household spending — Seattle"
    assert seattle.expenses[0].levels[0].amount.lower == 115000
    assert seattle.expenses[0].levels[0].amount.upper == 135000
    assert "Full household spending alternative" in seattle.notes
    assert second["id"] in seattle.notes
    assert retirement["inputs"]["name"] in seattle.notes
    assert retirement["id"] in seattle.notes
    assert all(d.inputs.expenses[0].end.year == 2032 for d in generated)
    assert {e.id for e in generated[0].inputs.expenses}.isdisjoint(
        e.id for e in generated[1].inputs.expenses
    )
    selected[retirement["id"]].reverse()
    reversed_conversion = convert_timeline_documents(records, profile_contexts=selected)
    assert {d.id: d.inputs for d in converted} == {
        d.id: d.inputs for d in reversed_conversion
    }
    assert all(
        row["old_total"] == row["new_total"]
        for row in timeline_conversion_review(records, converted)
    )


def test_retirement_single_context_list_preserves_existing_conversion(old_documents):
    selected = contexts(old_documents)
    original = convert_timeline_documents(old_documents, profile_contexts=selected)
    selected[old_documents[1]["id"]] = [old_documents[-1]["id"]]
    converted = convert_timeline_documents(old_documents, profile_contexts=selected)
    assert {d.id: d.inputs for d in original} == {d.id: d.inputs for d in converted}


@pytest.mark.parametrize("case", ["empty", "duplicate", "missing", "not_plan"])
def test_retirement_context_list_rejects_invalid_choices(old_documents, case):
    selected = contexts(old_documents)
    plan_id = old_documents[-1]["id"]
    selected[old_documents[1]["id"]] = {
        "empty": [],
        "duplicate": [plan_id, plan_id],
        "missing": [str(uuid4())],
        "not_plan": [old_documents[0]["id"]],
    }[case]
    with pytest.raises(ValueError, match="Plan context"):
        convert_timeline_documents(old_documents, profile_contexts=selected)


@pytest.fixture
def undated_documents(old_documents):
    source = old_documents[0]["inputs"]
    source["replacements"] = []
    source["adjustments"] = [
        {
            "label": "Future childcare",
            "notes": "Dates need a family decision.",
            "frequency": "recurring",
            "amount": {"central": 36000, "lower": 24000, "upper": 48000},
            "start": {"kind": "year", "year": None, "offset": 0},
            "end": {"kind": "year", "year": None, "offset": 0},
        }
    ]
    return old_documents


def test_standalone_undated_addition_preserves_unknowns_without_entering_totals(
    undated_documents,
):
    before = deepcopy(undated_documents)
    converted = convert_timeline_documents(
        undated_documents, profile_contexts=contexts(undated_documents)
    )
    assert undated_documents == before
    spending = next(d.inputs for d in converted if str(d.id) == before[0]["id"])
    assert [level.amount.central for level in spending.expenses[0].levels] == [
        100000,
        110000,
        80000,
        60000,
    ]
    addition = spending.expenses[1]
    assert len(spending.expenses) == 2
    assert addition.label == "Future childcare"
    assert addition.category == "other"
    assert addition.frequency == "recurring"
    assert addition.notes == "Dates need a family decision."
    assert addition.levels[0].when.kind == addition.end.kind == "year"
    assert addition.levels[0].when.year is None
    assert addition.end.year is None
    assert addition.levels[0].amount.central == 36000
    assert addition.levels[0].amount.lower == 24000
    assert addition.levels[0].amount.upper == 48000
    assert addition.real_growth == 0
    assert any("timing unresolved" in detail for detail in missing_details(spending))
    assert all(
        row["old_total"] == row["new_total"]
        for row in timeline_conversion_review(undated_documents, converted)
    )


@pytest.mark.parametrize(
    "case",
    [
        "negative",
        "negative_bound",
        "zero",
        "one_time",
        "absent_end",
        "known_start",
        "replacement",
        "unknown_baseline",
    ],
)
def test_undated_additions_reject_other_semantics(undated_documents, case):
    source = undated_documents[0]["inputs"]
    adjustment = source["adjustments"][0]
    if case == "negative":
        adjustment["amount"] = {"central": -1}
    elif case == "negative_bound":
        adjustment["amount"]["lower"] = -1
    elif case == "zero":
        adjustment["amount"] = {"central": 0}
    elif case == "one_time":
        adjustment["frequency"] = "once"
    elif case == "absent_end":
        adjustment["end"] = None
    elif case == "known_start":
        adjustment["start"]["year"] = 2028
    elif case == "replacement":
        source["replacements"] = [
            {
                "label": "Unreviewed replacement",
                "when": {"kind": "year", "year": 2028},
                "budget": budget(90000),
            }
        ]
    else:
        source["baseline"] = budget(None)
    with pytest.raises(ValueError):
        convert_timeline_documents(
            undated_documents, profile_contexts=contexts(undated_documents)
        )


def test_undated_additions_in_saved_plan_still_require_review(undated_documents):
    plan = undated_documents[-1]
    plan["inputs"]["components"][0]["inputs"] = deepcopy(undated_documents[0]["inputs"])
    with pytest.raises(ValueError, match="unresolved expense date"):
        convert_timeline_documents([plan])


def test_conversion_preserves_all_budget_metadata_and_independent_copy_notes(
    old_documents,
):
    before = convert_timeline_documents(
        old_documents, profile_contexts=contexts(old_documents)
    )
    source, retirement, plan = old_documents
    for inputs, prefix in (
        (source["inputs"], "Library"),
        (plan["inputs"]["components"][0]["inputs"], "Plan copy"),
    ):
        inputs["baseline"] |= {
            "notes": f"{prefix} rent and other costs.",
            "essential": 70000,
        }
        inputs["replacements"][0]["label"] = f"{prefix} move"
        inputs["replacements"][0]["budget"] |= {
            "notes": f"{prefix} replacement explanation.",
            "essential": 60000,
        }
        for index, adjustment in enumerate(inputs["adjustments"]):
            adjustment["label"] = f"{prefix} adjustment {index}"
            adjustment["notes"] = f"{prefix} adjustment explanation {index}."
    for inputs, prefix in (
        (retirement["inputs"], "Library retirement"),
        (plan["inputs"]["components"][1]["inputs"], "Plan retirement"),
    ):
        for label, item in (
            ("one retired", inputs["only_adult_1"]["budget"]),
            ("both retired", inputs["both_retired"]),
            ("survivor", inputs["survivor"]),
        ):
            item |= {"notes": f"{prefix}: {label} explanation.", "essential": 50000}
    converted = convert_timeline_documents(
        old_documents, profile_contexts=contexts(old_documents)
    )
    library = next(d.inputs for d in converted if str(d.id) == source["id"])
    converted_plan = next(d.inputs for d in converted if str(d.id) == plan["id"])
    selected = next(
        c.inputs for c in converted_plan.components if c.inputs.kind == "spending"
    )
    for spending, prefix in ((library, "Library"), (selected, "Plan copy")):
        notes = spending.expenses[0].notes
        assert (
            "Historical budget context (before conversion; not additional costs)"
            in notes
        )
        assert f"{prefix} rent and other costs." in notes
        assert f"{prefix} move" in notes
        assert f"{prefix} replacement explanation." in notes
        for index in (0, 1):
            assert f"{prefix} adjustment {index}" in notes
            assert f"{prefix} adjustment explanation {index}." in notes
        for essential in (70000, 60000, 50000):
            assert f"Essential annual spending (2026 dollars): {essential}." in notes
        for label in ("one retired", "both retired", "survivor"):
            assert f"Plan retirement: {label} explanation." in notes
    assert "Library rent and other costs." not in selected.expenses[0].notes
    assert "Plan copy rent and other costs." not in library.expenses[0].notes
    generated = next(
        d.inputs for d in converted if str(d.id) not in {r["id"] for r in old_documents}
    )
    assert (
        "Library retirement: both retired explanation." in generated.expenses[0].notes
    )

    def without_notes(value):
        if isinstance(value, dict):
            return {k: without_notes(v) for k, v in value.items() if k != "notes"}
        if isinstance(value, list):
            return [without_notes(v) for v in value]
        return value

    assert {d.id: without_notes(d.inputs.model_dump(mode="json")) for d in before} == {
        d.id: without_notes(d.inputs.model_dump(mode="json")) for d in converted
    }


def test_version_one_conversion_preserves_return_bounds_and_original():
    old = document(
        EconomicAssumptions(
            name="Gross",
            equities={"central": ".06", "lower": ".03", "upper": ".08"},
            inflation={"central": ".025"},
        ).model_dump(mode="json")
    )
    old["schema_version"] = 1
    old["inputs"] |= {
        "fees": ".01",
        "forecast_years": 10,
        "return_basis": "nominal_compound_before_fees_and_taxes",
    }
    before = deepcopy(old)
    (new,) = convert_planning_documents([old])
    assert old == before
    assert new.inputs.equities.central == Decimal(".0494")
    assert new.inputs.equities.lower == Decimal(".0197")
    assert new.inputs.equities.upper == Decimal(".0692")
    assert new.inputs.inflation.central == Decimal(".025")
    assert new.schema_version == 4
    assert new.revision == 4
