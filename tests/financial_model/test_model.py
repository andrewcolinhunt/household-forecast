"""Cash-flow identities and incremental RSU funding, isolated from tax rules."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from financial_model import model
from financial_model.schemas import SavedScenario, Scenario, ScenarioResult, TaxResult


def tax_result(amount: int) -> TaxResult:
    """Controlled tax output for testing accounting independently of tax formulas."""
    return TaxResult(
        federal_income_tax=amount,
        state_income_tax=0,
        social_security=0,
        medicare=0,
        additional_medicare=0,
        ca_sdi=0,
        wa_paid_leave=0,
        wa_cares=0,
        federal_amt=0,
        state_amt=0,
        federal_taxable_income=0,
        engine_version="test",
        core_version="test",
        correction_version="test",
        rules_status="verified_parameters",
    )


def test_evaluate_scenario_annual_accounting(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario
) -> None:
    data = scenario.model_dump(mode="json")
    data["adults"][0].update(
        rsu_vesting=20000, employee_401k=20000, employer_retirement_rate="0.04"
    )
    data["adults"][1].update(rsu_vesting=10000, savings_rate="0.20")
    data["adults"][1]["options"].update(shares=1000, strike=2, fair_market_value=5)
    data["living_costs"].update(rent=2000, childcare=1000, other=3000)
    scenario = Scenario.model_validate(data)
    calls = []

    def taxes(
        inputs: Scenario, *, include_rsus: bool = True, include_iso: bool = True
    ) -> TaxResult:
        calls.append((include_rsus, include_iso))
        return tax_result(40000 + 6000 * include_rsus + 1000 * include_iso)

    monkeypatch.setattr(model, "calculate_taxes", taxes)
    result = model.evaluate_scenario(scenario)
    assert calls == [(True, True), (True, False), (False, False)]
    assert result.gross_rsus == 30000
    assert result.rsu_tax_funding == 6000
    assert result.retained_rsus == 24000
    assert result.cash_funded_tax == 41000
    assert result.employee_401k == 20000
    assert result.extra_savings == 22000
    assert result.employer_retirement == 4000
    assert result.option_cost == 2000
    assert result.option_value == 5000
    assert result.annual_remaining_cash == 43000
    assert result.monthly_remaining_cash == Decimal("3583.33")
    assert result.total_savings_and_assets == 118000


@pytest.mark.parametrize(
    ("increment", "expected_funding"), [(-500, 0), (500, 500), (2000, 1000)]
)
def test_evaluate_scenario_equity_funding_bounds(
    monkeypatch: pytest.MonkeyPatch,
    scenario: Scenario,
    increment: int,
    expected_funding: int,
) -> None:
    data = scenario.model_dump(mode="json")
    data["adults"][0]["rsu_vesting"] = 1000
    scenario = Scenario.model_validate(data)

    def taxes(
        inputs: Scenario, *, include_rsus: bool = True, include_iso: bool = True
    ) -> TaxResult:
        return tax_result(10000 + (increment if include_rsus else 0))

    monkeypatch.setattr(model, "calculate_taxes", taxes)
    result = model.evaluate_scenario(scenario)
    assert result.rsu_tax_funding == expected_funding
    assert result.retained_rsus >= 0
    assert result.cash_funded_tax + result.rsu_tax_funding == result.taxes.total


def test_evaluate_scenario_negative_cash(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario
) -> None:
    monkeypatch.setattr(model, "calculate_taxes", lambda inputs: tax_result(300000))
    result = model.evaluate_scenario(scenario)
    assert result.annual_remaining_cash == -120000
    assert result.total_savings_and_assets == -100000


def test_evaluate_scenario_refund_and_zero_income(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario
) -> None:
    data = scenario.model_dump(mode="json")
    for adult in data["adults"]:
        adult["cash_pay"] = 0
    monkeypatch.setattr(model, "calculate_taxes", lambda inputs: tax_result(-1000))
    result = model.evaluate_scenario(Scenario.model_validate(data))
    assert result.annual_remaining_cash == 1000
    assert result.effective_tax_rate is None


def test_saved_scenario_result_integrity(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario
) -> None:
    monkeypatch.setattr(model, "calculate_taxes", lambda inputs: tax_result(10000))
    result = model.evaluate_scenario(scenario)
    document = SavedScenario(inputs=scenario, result=result)
    assert SavedScenario.model_validate_json(document.model_dump_json()) == document
    changed = Scenario.model_validate(scenario.model_dump() | {"name": "Different"})
    with pytest.raises(ValidationError, match="does not match"):
        SavedScenario(inputs=changed, result=result)
    corrupted = result.model_dump() | {"annual_remaining_cash": 999}
    with pytest.raises(ValidationError, match="does not reconcile"):
        ScenarioResult.model_validate(corrupted)
