"""Legacy file conversion preserves financial inputs and requires explicit goals."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest

from financial_model.migration import convert_legacy_inputs
from financial_model.model import evaluate_scenario
from financial_model.schemas import (
    SavedScenario,
    ScenarioResult,
    default_allocation_plan,
)
from financial_model.storage import import_backup


def _verify_legacy_totals(old: dict[str, Any], result: ScenarioResult) -> None:
    """Check that migration preserves financial sources, taxes, and net additions."""
    for key in (
        "gross_cash",
        "gross_rsus",
        "employee_401k",
        "employer_retirement",
        "option_cost",
        "option_value",
        "living_cost",
        "rsu_tax_funding",
        "retained_rsus",
        "cash_funded_tax",
    ):
        if Decimal(str(old[key])) != getattr(result, key):
            msg = f"Migration changed {key}; review before applying."
            raise ValueError(msg)
    if old["taxes"] != result.taxes.model_dump(mode="json"):
        # JSON amounts may use different equivalent decimal spellings.
        from financial_model.schemas import TaxResult

        if TaxResult.model_validate(old["taxes"]) != result.taxes:
            msg = "Migration changed taxes; review before applying."
            raise ValueError(msg)
    net = (
        Decimal(str(old["total_savings_and_assets"]))
        - Decimal(str(old["option_value"]))
        + Decimal(str(old["option_cost"]))
    )
    if net != result.net_saved_from_income:
        msg = "Migration changed total net additions."
        raise ValueError(msg)


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
    _verify_legacy_totals(legacy["result"], result)
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
        _verify_legacy_totals(changed, result)
