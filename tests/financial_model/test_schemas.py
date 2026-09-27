"""Validation boundaries for financial inputs and portable snapshots."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from financial_model.schemas import (
    ISOExercise,
    LivingCosts,
    OptionAward,
    PersonInputs,
    Scenario,
)


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


def test_option_award_distinguishes_unknown_zero_and_positive_value() -> None:
    assert OptionAward(vesting_shares=1000, strike=2).exercise_spread is None
    assert (
        OptionAward(
            vesting_shares=1000, strike=2, assumed_share_value=0
        ).exercise_spread
        == 0
    )
    assert (
        OptionAward(
            kind="NSO", vesting_shares=1000, strike=2, assumed_share_value=5
        ).exercise_spread
        == 3000
    )
    assert OptionAward(
        vesting_shares=100, strike="1.001", assumed_share_value="1.002"
    ).exercise_spread == Decimal("0.10")


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "unknown"},
        {"vesting_shares": -1},
        {"vesting_shares": 1.5},
        {"strike": -1},
        {"assumed_share_value": "NaN"},
    ],
)
def test_option_award_rejects_invalid_inputs(changes: dict) -> None:
    with pytest.raises(ValidationError):
        OptionAward.model_validate(changes)


def test_option_award_round_trip_and_input_digest(scenario: Scenario) -> None:
    data = scenario.model_dump(mode="json")
    assert "option_award" not in data["adults"][0]
    original_digest = scenario.digest()
    data["adults"][0]["option_award"] = None
    assert Scenario.model_validate(data).digest() == original_digest
    data["adults"][0]["option_award"] = {
        "kind": "NSO",
        "vesting_shares": 1000,
        "strike": "2",
        "assumed_share_value": None,
    }
    updated = Scenario.model_validate(data)
    assert updated.digest() != original_digest
    assert Scenario.model_validate_json(updated.model_dump_json()) == updated


def test_employer_metadata_keeps_existing_input_digest(scenario: Scenario) -> None:
    # Captured before employer fields were added, not recomputed by this test.
    original = "aea1b736b8c76c475eca17add57b66f371030d69afc2942b70b0bb21feff615d"
    assert scenario.digest() == original
    data = scenario.model_dump(mode="json")
    assert "employer" not in data["adults"][0]
    assert "not_working" not in data["adults"][0]
    data["adults"][0].update(employer="  ", not_working=False)
    assert Scenario.model_validate(data).digest() == original
    data["adults"][0]["employer"] = "  Example Co  "
    updated = Scenario.model_validate(data)
    assert updated.adults[0].employer == "Example Co"
    assert updated.digest() != original
    assert Scenario.model_validate_json(updated.model_dump_json()) == updated


@pytest.mark.parametrize(
    "changes",
    [
        {"employer": "x" * 81},
        {"employer": 123},
        {"not_working": 1},
        {"not_working": True, "employer": "Example"},
    ],
)
def test_employer_metadata_rejects_invalid_values(changes) -> None:
    with pytest.raises(ValidationError):
        PersonInputs(name="A", age=32, **changes)


def test_not_working_is_explicit_and_does_not_clear_compensation() -> None:
    missing = PersonInputs(name="A", age=32, cash_pay=0)
    assert missing.employer is None
    assert not missing.not_working
    declared = PersonInputs(
        name="A", age=32, cash_pay=1000, rsu_vesting=500, not_working=True
    )
    assert declared.cash_pay == 1000
    assert declared.rsu_vesting == 500
    assert declared.model_dump(mode="json")["not_working"] is True


@pytest.mark.parametrize("name", ["", "   "])
def test_scenario_name_can_be_blank(scenario: Scenario, name: str) -> None:
    inputs = Scenario.model_validate(scenario.model_dump() | {"name": name})
    assert inputs.name == ""
    assert Scenario.model_validate_json(inputs.model_dump_json()) == inputs
    missing = scenario.model_dump()
    del missing["name"]
    assert Scenario.model_validate(missing).name == ""
