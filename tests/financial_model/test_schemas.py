"""Validation boundaries for financial inputs and portable snapshots."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from financial_model.schemas import ISOExercise, LivingCosts, PersonInputs, Scenario


@pytest.mark.parametrize("value", [-1, "NaN", "Infinity", "1.001", True])
def test_person_inputs_invalid_money(value: object) -> None:
    with pytest.raises(ValidationError):
        PersonInputs(name="A", age=40, cash_pay=value)


@pytest.mark.parametrize("age", [17, 65, 40.5, True])
def test_person_inputs_unsupported_age(age: object) -> None:
    with pytest.raises(ValidationError):
        PersonInputs(name="A", age=age)


@pytest.mark.parametrize(("year", "limit"), [(2025, 23500), (2026, 24500)])
def test_scenario_contribution_limit(scenario: Scenario, year: int, limit: int) -> None:
    data = scenario.model_dump(mode="json")
    data["tax_year"] = year
    data["adults"][0]["employee_401k"] = limit
    assert Scenario.model_validate(data).adults[0].employee_401k == limit
    data["adults"][0]["employee_401k"] = limit + 1
    with pytest.raises(ValidationError, match="base annual limit"):
        Scenario.model_validate(data)


def test_scenario_retirement_exceeds_cash_or_plan(scenario: Scenario) -> None:
    data = scenario.model_dump(mode="json")
    data["adults"][0].update(cash_pay=1000, employee_401k=1001)
    with pytest.raises(ValidationError, match="cash pay"):
        Scenario.model_validate(data)
    data["adults"][0].update(
        cash_pay=100000, employee_401k=23500, employer_retirement_rate="0.50"
    )
    with pytest.raises(ValidationError, match="annual plan limit"):
        Scenario.model_validate(data)


@pytest.mark.parametrize(
    "changes",
    [
        {"tax_year": 2027},
        {"state": "NY"},
        {"filing_status": "single"},
        {"full_year_resident": False},
        {"assumptions_confirmed": False},
        {"investment_income": 1000},
        {"deduction_method": "itemized"},
    ],
)
def test_scenario_unsupported_facts(scenario: Scenario, changes: dict) -> None:
    with pytest.raises(ValidationError):
        Scenario.model_validate(scenario.model_dump() | changes)


def test_scenario_care_requires_eligible_child(scenario: Scenario) -> None:
    data = scenario.model_dump(mode="json")
    data["living_costs"].update(childcare=100, eligible_care_expense=1200)
    with pytest.raises(ValidationError, match="under age 13"):
        Scenario.model_validate(data)
    data["dependents"] = [{"age": 12}]
    assert Scenario.model_validate(data).living_costs.eligible_care_expense == 1200


def test_living_costs_care_exceeds_spending() -> None:
    with pytest.raises(ValidationError, match="cannot exceed"):
        LivingCosts(childcare=100, eligible_care_expense=1201)


def test_scenario_state_specific_facts(scenario: Scenario) -> None:
    data = scenario.model_dump(mode="json")
    data["adults"][0]["wa_cares_exempt"] = True
    with pytest.raises(ValidationError, match="Washington"):
        Scenario.model_validate(data)
    data["state"] = "WA"
    assert Scenario.model_validate(data).adults[0].wa_cares_exempt


def test_scenario_json_round_trip(scenario: Scenario) -> None:
    restored = Scenario.model_validate_json(scenario.model_dump_json())
    assert restored == scenario
    assert restored.digest() == scenario.digest()
    assert isinstance(restored.adults[0].cash_pay, Decimal)
    with pytest.raises(ValidationError, match="frozen"):
        restored.name = "Changed"


def test_iso_exercise_fractional_price_and_negative_spread() -> None:
    option = ISOExercise(shares=100, strike="1.001", fair_market_value="0.9")
    assert option.cost == Decimal("100.10")
    assert option.value == 90
    assert option.spread == 0
    with pytest.raises(ValidationError):
        ISOExercise(shares=1.5)
    with pytest.raises(ValidationError):
        ISOExercise(treatment="nso")
