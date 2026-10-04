"""Controlled inputs, invalid alternatives, and independent fixed sensitivity curves."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import numpy as np
import pytest

from financial_model import forecast_sensitivity as sensitivity
from financial_model.forecast import forecast_issues
from financial_model.forecast_models import ForecastRequest
from financial_model.forecast_tax import ForecastTaxResult
from financial_model.planning import PlanningScenario
from financial_model.schemas import default_allocation_plan


@pytest.fixture
def plan() -> PlanningScenario:
    profiles = [
        {
            "kind": "work",
            "rsu_taxes_from_shares": True,
            "adults": [
                {
                    "slot": slot,
                    "name": f"Profile {slot}",
                    "phases": [
                        {
                            "start": 2024,
                            "status": "working",
                            "cash_pay": 10000,
                            "real_growth": {"central": 0},
                            "employee_401k": 0,
                            "employer_retirement_rate": 0,
                        }
                    ],
                    "vesting": [{"start": 2024, "end": 2040, "amount": 0}],
                }
                for slot in (1, 2)
            ],
        },
        {
            "kind": "spending",
            "expenses": [
                {
                    "label": "Ordinary living",
                    "category": "ordinary",
                    "levels": [{"when": {"year": 2026}, "amount": {"central": 1000}}],
                }
            ],
        },
        {
            "kind": "retirement",
            "adults": [
                {
                    "slot": slot,
                    "name": f"Profile {slot}",
                    "retirement": {"central": 2027},
                    "age": {"central": 64},
                    "social_security": 0,
                    "pension": 0,
                }
                for slot in (1, 2)
            ],
        },
        {
            "kind": "investments",
            "policy": "constant",
            "rebalancing": "flows_only",
            "reserved_cash": 0,
            "saving": default_allocation_plan().model_dump(),
            "checkpoints": [
                {
                    "when": {"year": 2026},
                    "mix": {"equities": 0, "fixed_income": 0, "cash": 1},
                }
            ],
        },
        {
            "kind": "economy",
            "inflation": {"central": 0},
            "equities": {"central": 0},
            "fixed_income": {"central": 0},
            "cash": {"central": 0},
            "volatility": [0, 0, 0, 0],
            "stock_dividend_yield": 0,
            "bond_interest_yield": 0,
        },
    ]
    for profile in profiles:
        profile["name"] = profile["kind"]
        if profile["kind"] != "economy":
            profile["money_year"] = 2026
    return PlanningScenario.model_validate(
        {
            "name": "Small household",
            "household": {
                "reference_year": 2026,
                "adults": [
                    {"name": name, "age": 60, "wa_cares_exempt": False}
                    for name in ("Ari", "Bo")
                ],
                "residence": [{"year": 2026, "location": "WA"}],
                "filing_status": "married_filing_jointly",
            },
            "wealth": {
                "snapshot": {
                    "as_of": "2026-01-01",
                    "accounts": [
                        {
                            "label": "Cash",
                            "balance": 10000,
                            "tax_treatment": "taxable",
                            "invested_in": "cash",
                        }
                    ],
                }
            },
            "components": [
                {
                    "source_id": uuid4(),
                    "source_revision": 1,
                    "source_name": p["name"],
                    "inputs": p,
                    "adult_mapping": [1, 2]
                    if p["kind"] in {"work", "retirement"}
                    else None,
                }
                for p in profiles
            ],
        }
    )


def _profile(data: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(c["inputs"] for c in data["components"] if c["inputs"]["kind"] == kind)


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[ForecastRequest]:
    requests = []

    def calculate(request: ForecastRequest, progress=None) -> SimpleNamespace:
        requests.append(request)
        if progress:
            progress("2026", 0.25)
            progress("2027", 0.75)
        points = tuple(
            SimpleNamespace(year=year, median=wealth, accessible_median=wealth / 2)
            for year, wealth in ((2026, 1234.5), (2027, 2345.6))
        )
        return SimpleNamespace(
            document=SimpleNamespace(summaries=(SimpleNamespace(points=points),))
        )

    monkeypatch.setattr(sensitivity, "calculate_forecast", calculate)
    return requests


def test_calculate_sensitivity_fixed_baseline_and_progress(plan, recorded) -> None:
    before = plan.model_dump()
    progress = []
    result = sensitivity.calculate_sensitivity(
        plan, lambda label, value: progress.append((label, value))
    )
    assert len(recorded) == 15
    assert all(r.settings.mode == "fixed" and r.settings.count == 1 for r in recorded)
    assert recorded[0].plans == (plan,)
    assert plan.model_dump() == before
    assert result.plan_name == plan.name
    assert result.money_year == 2026
    assert result.baseline.annual == (
        sensitivity.SensitivityPoint(2026, 1234.5, 617.25),
        sensitivity.SensitivityPoint(2027, 2345.6, 1172.8),
    )
    assert len(result.drivers) == 7
    assert all(d.low.status == d.high.status == "ok" for d in result.drivers)
    fractions = [value for _, value in progress]
    assert fractions == sorted(fractions)
    assert fractions[0] == 0
    assert fractions[-1] == 1
    assert any("Stock return" in label and "2026" in label for label, _ in progress)


def test_calculate_sensitivity_scales_all_recurring_spending(plan, recorded) -> None:
    data = plan.model_dump()
    spending = _profile(data, "spending")
    amount = {"central": 1000, "lower": 500, "upper": 2000}
    spending["expenses"] = [
        {
            "label": "Living",
            "category": "ordinary",
            "real_growth": ".02",
            "levels": [
                {"when": {"year": 2026}, "amount": amount},
                {"when": {"year": 2028}, "amount": amount},
            ],
        },
        {
            "label": "Coverage",
            "category": "healthcare",
            "levels": [{"when": {"year": 2026}, "amount": amount}],
        },
        {
            "label": "Move",
            "frequency": "once",
            "levels": [{"when": {"year": 2027}, "amount": {"central": 25}}],
        },
    ]
    original = PlanningScenario.model_validate(data)
    original_data = original.model_dump()
    sensitivity.calculate_sensitivity(original)
    for request, factor in zip(
        recorded[1:3], (Decimal(".8"), Decimal("1.2")), strict=True
    ):
        expenses = _profile(request.plans[0].model_dump(), "spending")["expenses"]
        for item, source in zip(
            expenses, _profile(original_data, "spending")["expenses"], strict=True
        ):
            assert item["id"] == source["id"]
            assert item["real_growth"] == source["real_growth"]
            for level, source_level in zip(
                item["levels"], source["levels"], strict=True
            ):
                assert level["when"] == source_level["when"]
                expected_factor = factor if item["frequency"] == "recurring" else 1
                assert level["amount"] == {
                    key: None if value is None else value * expected_factor
                    for key, value in source_level["amount"].items()
                }
    assert original.model_dump() == original_data


def test_calculate_sensitivity_resolves_linked_costs_and_explains_invalid_variant(
    plan, monkeypatch
):
    def no_tax(**inputs):
        return ForecastTaxResult(*(np.zeros(len(inputs["wages"])) for _ in range(7)))

    monkeypatch.setattr("financial_model.forecast.yearly_tax", no_tax)
    data = plan.model_dump()
    _profile(data, "spending")["expenses"] += (
        {
            "label": "Coverage",
            "category": "healthcare",
            "levels": [
                {"when": {"year": 2024}, "amount": {"central": 100}},
                {"when": {"kind": "adult_1"}, "amount": {"central": 1000}},
                {"when": {"year": 2028}, "amount": {"central": 500}},
            ],
        },
    )
    original = PlanningScenario.model_validate(data)
    before = original.model_dump()
    result = sensitivity.calculate_sensitivity(original)
    retirement = next(driver for driver in result.drivers if driver.key == "adult_1")
    assert retirement.low.status == "ok"
    assert retirement.high.status == "invalid"
    assert "Coverage" in retirement.high.error
    assert "boundaries" in retirement.high.error
    # In 2026 the earlier retirement ends $10,000 of wages and starts $900 more coverage.
    assert (
        retirement.low.annual[0].wealth_real - result.baseline.annual[0].wealth_real
        == -10900
    )
    assert original.model_dump() == before


def test_calculate_sensitivity_retirement_respects_household_mapping(
    plan, recorded
) -> None:
    data = plan.model_dump()
    selection = next(
        c for c in data["components"] if c["inputs"]["kind"] == "retirement"
    )
    selection["adult_mapping"] = (2, 1)
    selection["inputs"]["adults"][1]["retirement"] = {
        "central": 2028,
        "lower": 2027,
        "upper": 2029,
    }
    mapped = PlanningScenario.model_validate(data)
    result = sensitivity.calculate_sensitivity(mapped)
    assert result.drivers[1].label == "Ari's retirement"
    assert result.drivers[2].label == "Bo's retirement"
    early = _profile(recorded[3].plans[0].model_dump(), "retirement")["adults"]
    late = _profile(recorded[4].plans[0].model_dump(), "retirement")["adults"]
    assert early[0] == late[0] == selection["inputs"]["adults"][0]
    assert early[1]["retirement"] == {"central": 2026, "lower": 2025, "upper": 2027}
    assert late[1]["retirement"] == {"central": 2030, "lower": 2029, "upper": 2031}
    assert _profile(recorded[3].plans[0].model_dump(), "work") == _profile(data, "work")


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (
            ("Chris", "Chris"),
            ("Chris (adult 1)'s retirement", "Chris (adult 2)'s retirement"),
        ),
        (("Ari", "Bo"), ("Ari's retirement", "Bo's retirement")),
    ],
)
def test_calculate_sensitivity_retirement_labels_are_distinct(
    plan, recorded, names, expected
) -> None:
    data = plan.model_dump()
    for adult, name in zip(data["household"]["adults"], names, strict=True):
        adult["name"] = name
    result = sensitivity.calculate_sensitivity(PlanningScenario.model_validate(data))
    assert tuple(driver.label for driver in result.drivers[1:3]) == expected
    assert result.drivers[1].low.label != result.drivers[2].low.label
    assert result.drivers[1].high.label != result.drivers[2].high.label


def test_calculate_sensitivity_economy_changes_only_selected_rate(
    plan, recorded
) -> None:
    data = plan.model_dump()
    economy = _profile(data, "economy")
    economy.update(
        equities={"central": ".06", "lower": ".04", "upper": ".08"},
        inflation={"central": ".025"},
        volatility=[".18", ".06", ".01", ".015"],
    )
    plan = PlanningScenario.model_validate(data)
    before = _profile(plan.model_dump(), "economy")
    result = sensitivity.calculate_sensitivity(plan)
    assert result.drivers[3].range_label == "5% to 7%"
    assert result.drivers[4].range_label == "1.5% to 3.5%"
    for index, field in ((7, "equities"), (9, "inflation")):
        for request, offset in zip(
            recorded[index : index + 2], (Decimal("-.01"), Decimal(".01")), strict=True
        ):
            after = _profile(request.plans[0].model_dump(), "economy")
            assert after[field] == {
                k: v + offset if v is not None else None
                for k, v in before[field].items()
            }
            assert {k: v for k, v in after.items() if k != field} == {
                k: v for k, v in before.items() if k != field
            }


def test_calculate_sensitivity_pay_and_account_basis_only(plan, recorded) -> None:
    data = plan.model_dump()
    work = _profile(data, "work")
    work["adults"][0]["phases"][0].update(
        employee_401k=None, contribution_basis="pay_fraction", contribution_rate=".1"
    )
    snapshot = data["wealth"]["snapshot"]
    snapshot["accounts"] = (
        *snapshot["accounts"],
        {
            "label": "Shares",
            "balance": "1234.57",
            "cost_basis": "345.61",
            "tax_treatment": "taxable",
            "invested_in": "stocks",
            "owner": 2,
        },
        {
            "label": "HSA",
            "balance": 100,
            "cost_basis": 80,
            "tax_treatment": "hsa",
            "invested_in": "cash",
            "owner": 1,
        },
    )
    snapshot["debts"] = [
        {"label": "Loan", "amount_owed": 200, "annual_payment": 100, "interest_rate": 0}
    ]
    plan = PlanningScenario.model_validate(data)
    sensitivity.calculate_sensitivity(plan)
    before = plan.model_dump()
    for request, factor in zip(
        recorded[11:13], (Decimal(".9"), Decimal("1.1")), strict=True
    ):
        after = request.plans[0].model_dump()
        for adult in _profile(after, "work")["adults"]:
            assert adult["phases"][0]["cash_pay"] == 10000 * factor
            adult["phases"][0]["cash_pay"] = Decimal(10000)
        assert after == before
    for request, balances in zip(
        recorded[13:15], ((9000, "1111.11", 90), (11000, "1358.03", 110)), strict=True
    ):
        after = request.plans[0].model_dump()
        accounts = after["wealth"]["snapshot"]["accounts"]
        assert tuple(a["balance"] for a in accounts) == tuple(
            Decimal(v) for v in balances
        )
        assert accounts[0]["cost_basis"] is None
        factor = Decimal(".9") if balances[0] == 9000 else Decimal("1.1")
        assert accounts[1]["cost_basis"] == (Decimal("345.61") * factor).quantize(
            Decimal(".01")
        )
        assert accounts[2]["cost_basis"] == 80 * factor
        for after_account, before_account in zip(
            accounts, before["wealth"]["snapshot"]["accounts"], strict=True
        ):
            after_account.update(
                balance=before_account["balance"],
                cost_basis=before_account["cost_basis"],
            )
        assert after == before


def test_calculate_sensitivity_invalid_retirement_does_not_extend_life(
    plan, recorded
) -> None:
    data = plan.model_dump()
    _profile(data, "retirement")["adults"][0]["retirement"]["central"] = 2030
    progress = []
    result = sensitivity.calculate_sensitivity(
        PlanningScenario.model_validate(data), lambda _, p: progress.append(p)
    )
    late = result.drivers[1].high
    assert late.status == "invalid"
    assert late.annual == ()
    assert "lifetime ends in 2030" in late.error
    assert len(recorded) == 14
    assert result.drivers[-1].high.status == "ok"
    assert progress == sorted(progress)
    assert progress[-1] == 1


def test_calculate_sensitivity_invalid_rate_is_reported(plan, recorded) -> None:
    data = plan.model_dump()
    _profile(data, "economy")["equities"]["central"] = "-.995"
    result = sensitivity.calculate_sensitivity(PlanningScenario.model_validate(data))
    assert result.drivers[3].low.status == "invalid"
    assert "greater than -1" in result.drivers[3].low.error
    assert result.drivers[3].high.status == "ok"


@pytest.mark.parametrize(
    "error", [ValueError("Invalid baseline"), RuntimeError("Solver failed")]
)
def test_calculate_sensitivity_baseline_failure_raises(
    plan, monkeypatch, error
) -> None:
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(sensitivity, "calculate_forecast", fail)
    with pytest.raises(type(error), match=str(error)):
        sensitivity.calculate_sensitivity(plan)


def test_calculate_sensitivity_variant_solver_failure_is_not_invalid(
    plan, recorded, monkeypatch
) -> None:
    original = sensitivity.calculate_forecast

    def fail_second(request, progress=None):
        if recorded:
            msg = "Solver failed"
            raise RuntimeError(msg)
        return original(request, progress)

    monkeypatch.setattr(sensitivity, "calculate_forecast", fail_second)
    with pytest.raises(RuntimeError, match="Solver failed"):
        sensitivity.calculate_sensitivity(plan)


def test_calculate_sensitivity_real_forecasts_and_missing_work(
    plan, monkeypatch
) -> None:
    def no_tax(**inputs):
        return ForecastTaxResult(*(np.zeros(len(inputs["wages"])) for _ in range(7)))

    monkeypatch.setattr("financial_model.forecast.yearly_tax", no_tax)
    data = plan.model_dump()
    for adult in _profile(data, "work")["adults"]:
        adult["phases"][0].update(cash_pay=0, end=2027)
    plan = PlanningScenario.model_validate(data)
    assert (
        forecast_issues(ForecastRequest(plans=(plan,), settings={"mode": "fixed"}))
        == ()
    )
    result = sensitivity.calculate_sensitivity(plan)
    assert [p.wealth_real for p in result.baseline.annual] == [
        9000,
        8000,
        7000,
        6000,
        5000,
    ]
    assert [p.wealth_real for p in result.drivers[0].low.annual] == [
        9200,
        8400,
        7600,
        6800,
        6000,
    ]
    assert [p.wealth_real for p in result.drivers[0].high.annual] == [
        8800,
        7600,
        6400,
        5200,
        4000,
    ]
    for driver in result.drivers[1:3]:
        assert driver.high.status == "invalid"
        assert "work schedule ends before retirement" in driver.high.error.lower()
        assert driver.high.annual == ()
    inflation = result.drivers[4]
    for case, rate in ((inflation.low, -0.01), (inflation.high, 0.01)):
        for n, point in enumerate(case.annual, start=1):
            nominal = 10000 - 1000 * sum((1 + rate) ** k for k in range(n))
            expected = nominal / (1 + rate) ** n
            assert point.wealth_real == pytest.approx(expected)
            assert point.accessible_real == pytest.approx(expected)


def test_calculate_sensitivity_real_stock_compounding(plan, monkeypatch) -> None:
    def no_tax(**inputs):
        return ForecastTaxResult(*(np.zeros(len(inputs["wages"])) for _ in range(7)))

    monkeypatch.setattr("financial_model.forecast.yearly_tax", no_tax)
    data = plan.model_dump()
    for adult in _profile(data, "work")["adults"]:
        adult["phases"][0]["cash_pay"] = 0
    account = data["wealth"]["snapshot"]["accounts"][0]
    account.update(tax_treatment="roth", invested_in="stocks", owner=1)
    investment = _profile(data, "investments")
    investment["checkpoints"][0]["mix"] = {"equities": 1, "fixed_income": 0, "cash": 0}
    _profile(data, "spending")["expenses"][0]["levels"][0]["amount"]["central"] = 0
    economy = _profile(data, "economy")
    economy["equities"]["central"] = ".06"
    economy["inflation"]["central"] = ".02"
    result = sensitivity.calculate_sensitivity(PlanningScenario.model_validate(data))
    cases = (
        (result.baseline, 0.06),
        (result.drivers[3].low, 0.05),
        (result.drivers[3].high, 0.07),
    )
    for case, rate in cases:
        assert case.status == "ok"
        for n, point in enumerate(case.annual, start=1):
            assert point.wealth_real == pytest.approx(10000 * ((1 + rate) / 1.02) ** n)
