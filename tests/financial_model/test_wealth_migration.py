"""Migration preserves balances, independent Plan copies, and cash reserves."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from financial_model.planning import (
    InvestmentAssumptions,
    PlanningScenario,
    SavedPlanningDocument,
)
from financial_model.wealth import SavedWealthSnapshot
from financial_model.wealth_migration import (
    convert_wealth_and_planning,
    convert_wealth_document,
)


def legacy_wealth():
    return {
        "document_type": "wealth_snapshot",
        "schema_version": 1,
        "id": str(uuid4()),
        "revision": 4,
        "updated_at": datetime.now(UTC).isoformat(),
        "snapshot": {
            "name": "Synthetic wealth",
            "as_of": "2026-01-01",
            "currency": "USD",
            "kind": "actual",
            "coverage": "partial",
            "source_notes": "Historical evidence",
            "direct_assets": [],
            "accounts": [
                {
                    "id": str(uuid4()),
                    "label": "Named account",
                    "reported_value": "1234.56",
                    "owner": "A",
                    "tax_treatment": "taxable",
                    "account_type": "brokerage",
                    "valuation_date": None,
                    "holdings": [],
                }
            ],
            "debts": [
                {
                    "id": str(uuid4()),
                    "label": "Card",
                    "amount_owed": "23.45",
                    "owner": None,
                    "is_combined": True,
                }
            ],
        },
    }


def legacy_planning(inputs):
    document = SavedPlanningDocument(inputs=inputs).model_dump(mode="json")
    document["schema_version"] = 2
    if inputs.kind == "investments":
        document["inputs"].pop("reserved_cash")
        document["inputs"].update(
            employer_stock="undecided", diversify_start=None, diversify_end=None
        )
    if inputs.kind == "plan":
        document["inputs"].update(owners=[], portfolio_accounts=[], reserved_cash=None)
    return document


def selection(document):
    return {
        "source_id": document["id"],
        "source_revision": document["revision"],
        "source_name": document["inputs"]["name"],
        "inputs": deepcopy(document["inputs"]),
        "adult_mapping": None,
    }


def test_wealth_conversion_preserves_identity_names_balances_and_unknowns():
    original = legacy_wealth()
    before = deepcopy(original)
    converted = convert_wealth_document(original)
    account = converted.snapshot.accounts[0]
    assert original == before
    assert converted.snapshot.assets == Decimal("1234.56")
    assert converted.snapshot.net_recorded == Decimal("1211.11")
    assert str(account.id) == original["snapshot"]["accounts"][0]["id"]
    assert account.label == "Named account"
    assert account.invested_in == "unknown"
    assert converted.revision == 5
    assert converted.snapshot.as_of.isoformat() == "2026-01-01"
    assert converted.snapshot.notes == ""
    changed = convert_wealth_document(
        original, {str(account.id): {"invested_in": "stocks"}}
    )
    assert changed.snapshot.accounts[0].invested_in == "stocks"
    assert changed.snapshot.assets == converted.snapshot.assets
    with pytest.raises(ValueError, match="only set"):
        convert_wealth_document(original, {str(account.id): {"balance": "999"}})


def test_wealth_conversion_derives_complete_mixes_without_inventing_remainders():
    original = legacy_wealth()
    row = original["snapshot"]["accounts"][0]
    row["reported_value"] = "3"
    row["holdings"] = [
        {"asset_class": "equities", "value": "1"},
        {"asset_class": "fixed_income", "value": "2"},
    ]
    converted = convert_wealth_document(original)
    mix = converted.snapshot.accounts[0].mix
    assert mix.equities + mix.fixed_income + mix.cash == 1
    assert mix.cash == 0
    row["holdings"][1]["value"] = "1"
    assert (
        convert_wealth_document(original).snapshot.accounts[0].invested_in == "unknown"
    )
    row["reported_value"] = None
    with pytest.raises(ValueError, match="no total"):
        convert_wealth_document(original)
    row["reported_value"] = "2"
    original["snapshot"]["direct_assets"] = [{"value": "10"}]
    with pytest.raises(ValueError, match="directly held"):
        convert_wealth_document(original)


def test_plans_keep_independent_wealth_and_reserves_without_mutating_library():
    wealth = legacy_wealth()
    investments = legacy_planning(InvestmentAssumptions(name="Invest", money_year=2026))
    plans = []
    for reserve in ("10", "20"):
        plan = legacy_planning(PlanningScenario(name=f"Plan {reserve}"))
        plan["inputs"].update(
            wealth=deepcopy(wealth),
            components=[selection(investments)],
            reserved_cash=reserve,
        )
        plans.append(plan)
    plans[1]["inputs"]["wealth"]["snapshot"]["accounts"][0]["reported_value"] = "999"
    before = deepcopy(plans)
    converted = convert_wealth_and_planning([wealth], [investments, *plans])
    assert plans == before
    library = SavedPlanningDocument.model_validate(converted["planning_documents"][0])
    assert library.inputs.reserved_cash is None
    first, second = [
        SavedPlanningDocument.model_validate(d)
        for d in converted["planning_documents"][1:]
    ]
    assert first.inputs.components[0].inputs.reserved_cash == 10
    assert second.inputs.components[0].inputs.reserved_cash == 20
    assert first.inputs.components[0].source_revision == investments["revision"]
    assert first.inputs.wealth.revision == wealth["revision"] + 1
    assert second.inputs.wealth.revision == wealth["revision"]
    assert second.inputs.wealth.snapshot.assets == 999
    assert first.inputs.wealth == SavedWealthSnapshot.model_validate(
        converted["wealth_snapshots"][0]
    )
    assert (
        not {"owners", "portfolio_accounts", "reserved_cash"}
        & first.inputs.model_dump().keys()
    )


def test_matching_investment_copies_get_new_source_revision():
    investments = legacy_planning(InvestmentAssumptions(name="Invest"))
    plan = legacy_planning(PlanningScenario(name="Plan"))
    plan["inputs"]["components"] = [selection(investments)]
    result = convert_wealth_and_planning([], [investments, plan])
    assert (
        result["planning_documents"][1]["inputs"]["components"][0]["source_revision"]
        == investments["revision"] + 1
    )
    investments["inputs"]["employer_stock"] = "diversify"
    with pytest.raises(ValueError, match="sale strategy"):
        convert_wealth_and_planning([], [investments])


def test_migration_rejects_missing_classification_targets_and_orphan_reserves():
    with pytest.raises(ValueError, match="no longer exists"):
        convert_wealth_and_planning(
            [], [], account_updates={str(uuid4()): {"invested_in": "cash"}}
        )
    plan = legacy_planning(PlanningScenario(name="Plan"))
    plan["inputs"]["reserved_cash"] = "10"
    with pytest.raises(ValueError, match="Investment scenario"):
        convert_wealth_and_planning([], [plan])


def test_wealth_migration_preserves_plans_already_upgraded_by_retirement_migration():
    old = legacy_wealth()
    current = SavedPlanningDocument(
        inputs=PlanningScenario(name="Already upgraded")
    ).model_dump(mode="json")
    converted = convert_wealth_and_planning([old], [current])
    assert converted["planning_documents"] == [current]
    assert converted["wealth_snapshots"][0]["schema_version"] == 2
