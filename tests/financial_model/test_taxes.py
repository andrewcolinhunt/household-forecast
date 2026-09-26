"""Independent tax references and regressions for supported household facts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from financial_model.schemas import Scenario
from financial_model.taxes import calculate_taxes

REFERENCES = json.loads(
    (Path(__file__).parents[1] / "fixtures/tax_cases.json").read_text()
)


@pytest.mark.parametrize("case", REFERENCES["cases"], ids=lambda case: case["id"])
def test_calculate_taxes_reference_cases(scenario: Scenario, case: dict) -> None:
    data = scenario.model_dump(mode="json")
    data.update(tax_year=case["year"], state=case["state"])
    for index, adult in enumerate(data["adults"]):
        adult.update(
            cash_pay=case.get("wages", [100000, 100000])[index],
            employee_401k=case.get("contribution_each", 0),
        )
    data["adults"][0]["options"].update(
        shares=case.get("iso_spread", 0), strike=1, fair_market_value=2
    )
    data["dependents"] = [{"age": age} for age in case.get("children", [])]
    data["living_costs"].update(
        childcare=1000, eligible_care_expense=case.get("care", 0)
    )
    result = calculate_taxes(Scenario.model_validate(data))
    for field, expected in case["expected"].items():
        assert float(getattr(result, field)) == pytest.approx(expected, abs=0.05), field
    assert (
        result.total
        == result.federal_income_tax
        + result.state_income_tax
        + result.employee_payroll_tax
    )
    assert result.rules_status == (
        "projected_ca_parameters"
        if case["year"] == 2026 and case["state"] == "CA"
        else "verified_parameters"
    )


def test_calculate_taxes_wa_cares_exemption(scenario: Scenario) -> None:
    data = scenario.model_dump(mode="json")
    data["state"] = "WA"
    original = calculate_taxes(Scenario.model_validate(data))
    data["adults"][0]["wa_cares_exempt"] = True
    exempt = calculate_taxes(Scenario.model_validate(data))
    assert original.wa_cares - exempt.wa_cares == 580
    assert original.total - exempt.total == 580
    assert original.wa_paid_leave == exempt.wa_paid_leave


def test_calculate_taxes_rsu_wages_and_counterfactual(scenario: Scenario) -> None:
    cash_result = calculate_taxes(scenario)
    data = scenario.model_dump(mode="json")
    data["adults"][1].update(cash_pay=50000, rsu_vesting=50000)
    with_rsus = Scenario.model_validate(data)
    assert calculate_taxes(with_rsus) == cash_result
    assert (
        calculate_taxes(with_rsus, include_rsus=False).federal_income_tax
        < cash_result.federal_income_tax
    )


def test_calculate_taxes_zero_income(scenario: Scenario) -> None:
    data = scenario.model_dump(mode="json")
    for adult in data["adults"]:
        adult["cash_pay"] = 0
    assert calculate_taxes(Scenario.model_validate(data)).total == 0


def test_calculate_taxes_california_renter_credit(scenario: Scenario) -> None:
    data = scenario.model_dump(mode="json")
    for adult in data["adults"]:
        adult["cash_pay"] = 40000
    original = calculate_taxes(Scenario.model_validate(data))
    data["living_costs"].update(rent=2000, ca_renter_credit_eligible=True)
    renter = calculate_taxes(Scenario.model_validate(data))
    assert original.state_income_tax - renter.state_income_tax == 120


def test_calculate_taxes_corrections_do_not_mutate_baseline(scenario: Scenario) -> None:
    from policyengine_us.system import system

    calculate_taxes(scenario)
    assert (
        system.parameters("2025").gov.states.ca.tax.payroll.disability.employee_rate
        == 0.013
    )
    assert "amt_non_agi_income" not in system.variables["amt_income"].adds
