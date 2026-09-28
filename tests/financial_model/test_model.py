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


@pytest.mark.parametrize("from_shares", [True, False])
def test_evaluate_scenario_annual_accounting(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario, from_shares: bool
) -> None:
    data = scenario.model_dump(mode="json")
    data["rsu_taxes_from_shares"] = from_shares
    data["adults"][0].update(
        rsu_vesting=20000, employee_401k=20000, employer_retirement_rate="0.04"
    )
    data["adults"][1].update(rsu_vesting=10000)
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
    assert calls == (
        [(True, True), (True, False), (False, False)] if from_shares else [(True, True)]
    )
    funding = 6000 if from_shares else 0
    assert result.gross_rsus == 30000
    assert result.rsu_tax_funding == funding
    assert result.retained_rsus == 30000 - funding
    assert result.cash_funded_tax == 47000 - funding
    assert result.employee_401k == 20000
    assert result.employer_retirement == 4000
    assert result.option_cost == 2000
    assert result.option_value == 5000
    assert result.cash_added == 59000 + funding
    assert result.net_saved_from_income == 115000
    assert result.investments_added == 56000 - funding
    assert sum(row.rsus for row in result.allocations.rows) == 30000 - funding
    assert result.taxes.total == 47000
    assert result.effective_tax_rate == Decimal(47000) / 230000
    assert result.cash_added + result.investments_added == result.net_saved_from_income
    assert (
        result.net_saved_from_income
        == result.gross_cash
        + result.gross_rsus
        + result.employer_retirement
        - result.taxes.total
        - result.living_cost
    )


@pytest.mark.parametrize(("rent", "expected"), [(14000, 10000), (16000, -14000)])
def test_cash_added_excludes_equity_and_retirement_and_includes_deficits(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario, rent: int, expected: int
) -> None:
    data = scenario.model_dump(mode="json")
    data["adults"][0].update(
        rsu_vesting=100000, employee_401k=10000, employer_retirement_rate="0.04"
    )
    data["adults"][0]["options"].update(shares=1000, strike=2, fair_market_value=50)
    data["living_costs"]["rent"] = rent
    monkeypatch.setattr(
        model, "calculate_taxes", lambda *args, **kwargs: tax_result(10000)
    )
    result = model.evaluate_scenario(Scenario.model_validate(data))
    assert result.cash_added == expected
    assert result.cash_added == (
        result.gross_cash
        - result.cash_funded_tax
        - result.employee_401k
        - result.option_cost
        - result.living_cost
    )


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


def test_cash_funded_rsu_taxes_reduce_cash_without_selling_retained_shares(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario
) -> None:
    from financial_model.allocations import fund_cash_goals

    data = scenario.model_dump(mode="json")
    data["adults"][0]["rsu_vesting"] = 30000
    data["living_costs"]["rent"] = 13000
    data["allocation_plan"]["rows"][0]["cash_amount"] = 1000
    monkeypatch.setattr(
        model,
        "calculate_taxes",
        lambda inputs, *, include_rsus=True, **kwargs: tax_result(
            50000 if include_rsus else 40000
        ),
    )
    shares = model.evaluate_scenario(Scenario.model_validate(data))
    inputs = Scenario.model_validate(data | {"rsu_taxes_from_shares": False})
    cash = model.evaluate_scenario(inputs)
    assert shares.cash_added == 4000
    assert cash.cash_added == -6000
    assert cash.retained_rsus - shares.retained_rsus == 10000
    assert shares.taxes == cash.taxes
    assert shares.net_saved_from_income == cash.net_saved_from_income == 24000
    assert cash.allocations.funding_gap == 7000
    funded = fund_cash_goals(inputs.allocation_plan, cash.cash_added)
    assert funded.rows[0].funded == 0
    assert funded.remaining == -6000


@pytest.mark.parametrize("rsus", [0, 1000])
def test_cash_funded_taxes_preserve_refunds_and_zero_rsus(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario, rsus: int
) -> None:
    data = scenario.model_dump(mode="json") | {"rsu_taxes_from_shares": False}
    data["adults"][0]["rsu_vesting"] = rsus
    monkeypatch.setattr(model, "calculate_taxes", lambda inputs: tax_result(-1000))
    result = model.evaluate_scenario(Scenario.model_validate(data))
    assert result.rsu_tax_funding == 0
    assert result.cash_funded_tax == -1000
    assert result.retained_rsus == rsus
    assert result.cash_added == 201000


def test_evaluate_scenario_negative_cash(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario
) -> None:
    monkeypatch.setattr(model, "calculate_taxes", lambda inputs: tax_result(300000))
    result = model.evaluate_scenario(scenario)
    assert result.cash_added == -100000
    assert result.allocations.funding_gap == 100000
    assert result.net_saved_from_income == -100000


def test_evaluate_scenario_refund_and_zero_income(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario
) -> None:
    data = scenario.model_dump(mode="json")
    for adult in data["adults"]:
        adult["cash_pay"] = 0
    monkeypatch.setattr(model, "calculate_taxes", lambda inputs: tax_result(-1000))
    result = model.evaluate_scenario(Scenario.model_validate(data))
    assert result.cash_added == 1000
    assert result.effective_tax_rate is None


@pytest.mark.parametrize("amount", ["0", "20000", "200000"])
def test_goals_only_allocate_cash_without_changing_total_saved(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario, amount: str
) -> None:
    data = scenario.model_dump(mode="json")
    data["allocation_plan"]["rows"][0]["cash_amount"] = amount
    data["adults"][0].update(employee_401k=10000, employer_retirement_rate="0.05")
    data["living_costs"].update(rent=2000, other=3000)
    monkeypatch.setattr(model, "calculate_taxes", lambda inputs: tax_result(40000))
    result = model.evaluate_scenario(Scenario.model_validate(data))
    assert result.cash_added == 90000
    assert result.investments_added == 15000
    assert result.net_saved_from_income == 105000
    assert result.allocations.rows[0].cash == Decimal(amount)
    assert result.allocations.funding_gap == max(Decimal(amount) - 90000, 0)
    assert result.allocations.net_total == result.net_saved_from_income


def test_effective_tax_rate_includes_all_taxes_and_only_cash_and_rsu_wages(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario
) -> None:
    data = scenario.model_dump(mode="json")
    data["adults"][0].update(
        rsu_vesting=50000, employee_401k=20000, employer_retirement_rate="0.05"
    )
    data["adults"][0]["options"].update(shares=1000, strike=2, fair_market_value=10)
    taxes = tax_result(30000).model_copy(
        update={
            "state_income_tax": Decimal(5000),
            "social_security": Decimal(10000),
            "medicare": Decimal(2000),
            "additional_medicare": Decimal(1000),
            "ca_sdi": Decimal(1000),
            "federal_amt": Decimal(10000),
        }
    )
    monkeypatch.setattr(model, "calculate_taxes", lambda *args, **kwargs: taxes)
    result = model.evaluate_scenario(Scenario.model_validate(data))
    assert result.taxes.total == 49000  # AMT is already in income tax.
    assert result.effective_tax_rate == Decimal("0.196")


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
    corrupted = result.model_dump() | {"cash_added": 999}
    with pytest.raises(ValidationError, match="does not reconcile"):
        ScenarioResult.model_validate(corrupted)


def test_saved_scenario_rejects_share_funding_when_cash_was_selected(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario
) -> None:
    data = scenario.model_dump(mode="json")
    data["adults"][0]["rsu_vesting"] = 1000
    shares = Scenario.model_validate(data)
    monkeypatch.setattr(
        model,
        "calculate_taxes",
        lambda inputs, *, include_rsus=True, **kwargs: tax_result(
            10000 + 200 * include_rsus
        ),
    )
    result = model.evaluate_scenario(shares)
    cash = Scenario.model_validate(data | {"rsu_taxes_from_shares": False})
    result = result.model_copy(update={"input_digest": cash.digest()})
    with pytest.raises(ValidationError, match="does not match"):
        SavedScenario(inputs=cash, result=result)


def test_saved_scenario_without_awards_keeps_original_digest(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario
) -> None:
    import hashlib

    monkeypatch.setattr(model, "calculate_taxes", lambda inputs: tax_result(10000))
    result = model.evaluate_scenario(scenario)
    old_json = scenario.model_dump_json(
        exclude={"adults": {"__all__": {"option_award"}}}
    )
    old_digest = hashlib.sha256(old_json.encode()).hexdigest()
    assert result.input_digest == old_digest
    data = SavedScenario(inputs=scenario, result=result).model_dump(mode="json")
    data["result"]["input_digest"] = old_digest
    assert SavedScenario.model_validate(data).inputs == scenario
