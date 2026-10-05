"""Legacy file conversion preserves financial inputs and requires explicit goals."""

from __future__ import annotations

import json

import pytest

from financial_model.migration import convert_legacy_inputs, verify_legacy_totals
from financial_model.model import evaluate_scenario
from financial_model.schemas import SavedScenario, default_allocation_plan
from financial_model.storage import import_backup


@pytest.fixture
def legacy(scenario):
    result = evaluate_scenario(scenario)
    raw = SavedScenario(inputs=scenario, result=result).model_dump(mode="json")
    raw["schema_version"] = 1
    del raw["inputs"]["allocation_plan"]
    for person in raw["inputs"]["adults"]:
        person["savings_rate"] = "0.10"
    del raw["result"]["allocations"]
    del raw["result"]["cash_added"]
    raw["result"].update(
        extra_savings="20000",
        annual_remaining_cash=str(result.cash_added - 20000),
        monthly_remaining_cash=str((result.cash_added - 20000) / 12),
        total_savings_and_assets=str(result.net_saved_from_income),
    )
    return raw


def test_legacy_conversion_requires_destinations_and_keeps_cash_target(legacy):
    inputs = convert_legacy_inputs(legacy, destinations=default_allocation_plan())
    assert inputs.allocation_plan.fixed_cash == 20000
    assert all("savings_rate" not in adult for adult in inputs.model_dump()["adults"])
    result = evaluate_scenario(inputs)
    verify_legacy_totals(legacy["result"], result)
    assert (
        import_backup(
            json.dumps(legacy).encode(), legacy_destinations=default_allocation_plan()
        )
        == inputs
    )
    with pytest.raises(ValueError, match="schema_version"):
        import_backup(json.dumps(legacy).encode())
    changed = dict(legacy["result"], gross_cash="1")
    with pytest.raises(ValueError, match="gross_cash"):
        verify_legacy_totals(changed, result)
