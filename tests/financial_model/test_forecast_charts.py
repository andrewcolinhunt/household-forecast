"""Charts preserve accounting, price conventions, and saved person mappings."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

import altair as alt
import pytest

from financial_model import forecast_charts as charts
from financial_model.planning import PlanningScenario


@pytest.fixture
def plan() -> PlanningScenario:
    return PlanningScenario(
        name="Chart household",
        household={
            "reference_year": 2030,
            "adults": [{"name": "Casey", "age": 59}, {"name": "Morgan", "age": 64}],
        },
        wealth={
            "snapshot": {
                "as_of": "2030-07-01",
                "accounts": [
                    {
                        "id": UUID(int=i << 96),
                        "label": "Savings",
                        "balance": amount,
                        "tax_treatment": treatment,
                        "invested_in": "cash",
                    }
                    for i, (treatment, amount) in enumerate(
                        [
                            ("taxable", 100),
                            ("tax_deferred", 200),
                            ("roth", 300),
                            ("hsa", 50),
                        ],
                        1,
                    )
                ],
            }
        },
        components=[
            {
                "source_id": UUID(int=9),
                "source_revision": 1,
                "source_name": "Reversed adults",
                "adult_mapping": [2, 1],
                "inputs": {
                    "kind": "retirement",
                    "name": "Reversed adults",
                    "money_year": 2030,
                    "adults": [
                        {
                            "slot": 1,
                            "name": "Library first",
                            "retirement": {"central": 2032},
                            "age": {"central": 68},
                            "social_security": 5000,
                            "claiming_age": 67,
                            "pension": 1000,
                            "pension_start_age": 65,
                        },
                        {
                            "slot": 2,
                            "name": "Library second",
                            "retirement": {"central": 2031},
                            "age": {"central": 70},
                            "social_security": 0,
                            "pension": 0,
                        },
                    ],
                },
            }
        ],
    )


@pytest.fixture
def ledger(plan: PlanningScenario) -> list[charts.LedgerRow]:
    first = {
        "year": 2030,
        "price_index": 1.1,
        "opening_assets": 600,
        "income": 120,
        "investment_gain": 30,
        "spending_paid": 25,
        "spending_planned": 30,
        "tax_paid": 15,
        "taxes": 99,
        "debt_payment": 10,
        "assets": 700,
        "net_worth": 590,
        "accessible": 130,
        "debt": 100,
        "unpaid_tax_liability": 10,
        "cash_pay": 100,
        "social_security": 15,
        "pensions": 5,
        "employee_contributions": 200,
        "withdrawals": 300,
        "required_distributions": 100,
        "unfunded": 5,
        "equity_return": -0.2,
        "bond_return": 0.03,
        "cash_return": 0.01,
        "inflation": 0.1,
        "Forecast cash": 10,
        "Forecast investments": 20,
        "Adult 1 contributions": 5,
        "Adult 2 contributions": 15,
    }
    assert plan.wealth is not None
    first.update(
        {
            f"Account: {account.label} ({str(account.id)[:8]})": float(account.balance)
            for account in plan.wealth.snapshot.accounts
        }
    )
    second = first | {
        "year": 2031,
        "price_index": 1.32,
        "cash_pay": 110,
        "spending_planned": 33,
        "withdrawals": 330,
        "unfunded": 5.5,
    }
    return [first, second]


def values(spec: dict[str, Any], year: int) -> dict[str, float]:
    return {
        r["series"]: r["value"]
        for r in spec["layer"][0]["data"]["values"]
        if r["year"] == year
    }


@pytest.mark.parametrize("real", [False, True])
def test_income_spec_uses_opening_price_without_annualizing_partial_year(
    plan: PlanningScenario, ledger: list[charts.LedgerRow], real: bool
) -> None:
    spec = charts.income_spec(plan, ledger, real)
    assert values(spec, 2030)["Cash pay"] == 100
    assert values(spec, 2031)["Cash pay"] == pytest.approx(100 if real else 110)
    assert values(spec, 2030)["Paid taxes"] == -15
    assert values(spec, 2030)["Planned spending"] == -30
    assert values(spec, 2031)["Paid taxes"] == pytest.approx(-15 / 1.1 if real else -15)
    assert values(spec, 2031)["Planned spending"] == pytest.approx(-30 if real else -33)
    assert values(spec, 2030)["All external income"] == 120
    assert "Unpaid expenses" not in values(spec, 2030)


def test_plan_events_map_saved_people_and_end_lifetimes_after_last_age(
    plan: PlanningScenario,
) -> None:
    events = {e["year"]: e["event"] for e in charts.plan_events(plan)}
    assert "Casey retires" in events[2031]
    assert "Casey: retirement account access at 60" in events[2031]
    assert "Morgan: pension age" in events[2031]
    assert events[2032] == "Morgan retires"
    assert events[2033] == "Morgan: Social Security age"
    assert events[2035] == "After Morgan's planning age 68"
    assert events[2042] == "After Casey's planning age 70"
    assert all("Library" not in str(e) for e in events.values())


def test_income_spec_limits_event_rules_to_ledger_horizon(
    plan: PlanningScenario, ledger: list[charts.LedgerRow]
) -> None:
    spec = charts.income_spec(plan, ledger, True)
    assert [e["year"] for e in spec["layer"][1]["data"]["values"]] == [2031]


@pytest.mark.parametrize("real", [False, True])
def test_waterfall_spec_uses_paid_costs_and_one_common_price(
    ledger: list[charts.LedgerRow], real: bool
) -> None:
    row = ledger[0]
    spec = charts.waterfall_spec(row, real, money_year=2030)
    bars = spec["data"]["values"]
    divisor = 1.1 if real else 1
    assert [bar["amount"] for bar in bars] == pytest.approx(
        [value / divisor for value in [600, 120, 30, -25, -15, -10, 700]]
    )
    assert bars[-2]["end"] == pytest.approx(bars[-1]["end"])
    assert charts.waterfall_residual(row, real) == pytest.approx(0)
    assert row["employee_contributions"] == 200
    assert row["withdrawals"] == 300


def test_waterfall_spec_preserves_losses_refunds_and_unexplained_difference(
    ledger: list[charts.LedgerRow],
) -> None:
    row = ledger[0] | {"investment_gain": -30, "tax_paid": -5, "assets": 671}
    bars = charts.waterfall_spec(row, True, money_year=2030)["data"]["values"]
    assert bars[2]["amount"] == pytest.approx(-30 / 1.1)
    assert bars[4]["amount"] == pytest.approx(5 / 1.1)
    assert charts.waterfall_residual(row, False) == 11
    assert charts.waterfall_residual(row, True) == pytest.approx(10)


def test_balances_spec_counts_each_account_and_generated_balance_once(
    plan: PlanningScenario, ledger: list[charts.LedgerRow]
) -> None:
    spec = charts.balances_spec(plan, ledger, True)
    balances = values(spec, 2030)
    assert balances == pytest.approx(
        {
            "Total assets": 700 / 1.1,
            "Net wealth": 590 / 1.1,
            "Accessible, before tax": 130 / 1.1,
            "Taxable accounts": 130 / 1.1,
            "Pretax accounts": 220 / 1.1,
            "Roth accounts": 300 / 1.1,
            "HSA": 50 / 1.1,
        }
    )
    assert sum(
        balances[k]
        for k in ["Taxable accounts", "Pretax accounts", "Roth accounts", "HSA"]
    ) == pytest.approx(balances["Total assets"])
    assert "Unpaid living expenses" not in balances
    assert "New unpaid taxes" not in balances


def test_specs_use_one_ledger_and_distinct_flow_balance_units(
    plan: PlanningScenario, ledger: list[charts.LedgerRow]
) -> None:
    wealth = charts.balances_spec(plan, ledger, True)
    spending = charts.future_spending_spec(ledger, True, money_year=2030)
    assert values(wealth, 2031)["Net wealth"] == pytest.approx(590 / 1.32)
    assert values(spending, 2031) == pytest.approx(
        {
            "Planned spending": 30,
            "Account withdrawals": 300,
            "Unpaid expenses": 5,
        }
    )
    assert "2030 dollars" in wealth["layer"][0]["encoding"]["y"]["title"]


def test_returns_spec_keeps_full_annual_nominal_rates(
    ledger: list[charts.LedgerRow],
) -> None:
    assert values(charts.returns_spec(ledger), 2030) == {
        "Stocks": -0.2,
        "Bonds": 0.03,
        "Cash": 0.01,
        "Inflation": 0.1,
    }


def test_specs_validate_and_do_not_mutate_saved_or_live_ledgers(
    plan: PlanningScenario, ledger: list[charts.LedgerRow]
) -> None:
    original = deepcopy(ledger)
    specs = [
        charts.income_spec(plan, ledger, True),
        charts.balances_spec(plan, ledger, True),
        charts.returns_spec(ledger),
        charts.future_spending_spec(ledger, True, money_year=2030),
    ]
    for spec in specs:
        alt.LayerChart.from_dict(spec, validate=True)
    alt.Chart.from_dict(
        charts.waterfall_spec(ledger[0], True, money_year=2030), validate=True
    )
    assert charts.income_spec(plan, tuple(ledger), True) == specs[0]
    assert ledger == original


def test_incomplete_ledger_is_not_silently_filled_with_zero(
    plan: PlanningScenario, ledger: list[charts.LedgerRow]
) -> None:
    del ledger[0]["net_worth"]
    with pytest.raises(KeyError, match="net_worth"):
        charts.balances_spec(plan, ledger, True)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("household", "reference_year"), None, "reference year"),
        (("household", "adults", 0, "age"), None, "Casey's age"),
        (("components",), [], "Retirement inputs"),
        (("components", 0, "adult_mapping"), None, "person mapping"),
        (
            ("components", 0, "inputs", "adults", 0, "retirement", "central"),
            None,
            "Morgan's retirement year",
        ),
        (
            ("components", 0, "inputs", "adults", 0, "age", "central"),
            None,
            "planning age",
        ),
    ],
)
def test_plan_events_missing_saved_metadata_has_clear_error(
    plan: PlanningScenario, path: tuple[str | int, ...], value: Any, message: str
) -> None:
    data = plan.model_dump(mode="json")
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    incomplete = PlanningScenario.model_validate(data)
    with pytest.raises(ValueError, match=message):
        charts.plan_events(incomplete)
