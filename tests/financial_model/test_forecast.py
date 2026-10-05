"""Independently calculated household account and scenario comparisons."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import numpy as np
import pytest

from financial_model.forecast import calculate_forecast, forecast_issues
from financial_model.forecast_models import ForecastRequest
from financial_model.forecast_tax import ForecastTaxResult
from financial_model.planning import PlanningChild, PlanningScenario
from financial_model.schemas import default_allocation_plan


def forecast_request(**changes):
    """Return a complete small fixed-return plan; suitable for UI smoke tests."""
    account_id = uuid4()
    profiles = [
        {
            "kind": "work",
            "name": "Retired",
            "money_year": 2026,
            "rsu_taxes_from_shares": True,
            "adults": [
                {
                    "slot": slot,
                    "name": f"Adult {slot}",
                    "phases": [{"start": 2026, "status": "not_working"}],
                    "vesting": [{"start": 2026, "end": 2040, "amount": 0}],
                }
                for slot in (1, 2)
            ],
        },
        {
            "kind": "spending",
            "name": "Spending",
            "money_year": 2026,
            "expenses": [
                {
                    "label": "Ordinary living",
                    "category": "ordinary",
                    "levels": [
                        {"when": {"year": 2026}, "amount": {"central": 1000}},
                        {"when": {"kind": "survivor"}, "amount": {"central": 500}},
                    ],
                }
            ],
        },
        {
            "kind": "retirement",
            "name": "Retirement",
            "money_year": 2026,
            "adults": [
                {
                    "slot": slot,
                    "name": f"Adult {slot}",
                    "retirement": {"central": 2026},
                    "age": {"central": 62},
                    "social_security": 0,
                    "pension": 0,
                }
                for slot in (1, 2)
            ],
        },
        {
            "kind": "investments",
            "name": "Investments",
            "money_year": 2026,
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
            "name": "Economy",
            "inflation": {"central": 0},
            "equities": {"central": 0},
            "fixed_income": {"central": 0},
            "cash": {"central": 0},
            "volatility": [0, 0, 0, 0],
            "stock_dividend_yield": 0,
            "bond_interest_yield": 0,
        },
    ]
    plan = PlanningScenario(
        name="Synthetic",
        household={
            "reference_year": 2026,
            "adults": [
                {"name": f"Adult {slot}", "age": 60, "wa_cares_exempt": False}
                for slot in (1, 2)
            ],
            "residence": [{"year": 2026, "location": "WA"}],
            "filing_status": "married_filing_jointly",
        },
        wealth={
            "snapshot": {
                "as_of": date(2026, 1, 1),
                "accounts": [
                    {
                        "id": account_id,
                        "label": "Bank",
                        "balance": 10000,
                        "tax_treatment": "taxable",
                        "invested_in": "cash",
                    }
                ],
            }
        },
        components=[
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
    )
    data = {
        "plans": [plan],
        "settings": {"mode": "fixed"},
    }
    data.update(changes)
    return ForecastRequest.model_validate(data)


def _edit(request, callback):
    data = request.model_dump(mode="json")
    callback(data)
    return ForecastRequest.model_validate(data)


def _profile(data, kind, index=0):
    return next(
        c["inputs"]
        for c in data["plans"][index]["components"]
        if c["inputs"]["kind"] == kind
    )


@pytest.fixture
def zero_tax(monkeypatch):
    def calculate(**inputs):
        zero = np.zeros(len(inputs["wages"]))
        return ForecastTaxResult(*(zero.copy() for _ in range(7)))

    monkeypatch.setattr("financial_model.forecast.yearly_tax", calculate)


def test_forecast_cash_account_reconciles_every_year(zero_tax):
    request = forecast_request()
    assert forecast_issues(request) == ()
    result = calculate_forecast(request)
    rows = result.inspect(0, 0)
    assert [row["assets"] for row in rows] == [9000, 8000, 7000]
    assert result.document.summaries[0].funded_fraction == 1
    assert all(
        row["opening_assets"]
        + row["income"]
        + row["investment_gain"]
        - row["spending_paid"]
        - row["tax_paid"]
        - row["debt_payment"]
        == row["assets"]
        for row in rows
    )
    with pytest.raises(IndexError):
        result.inspect(1, 0)
    with pytest.raises(IndexError):
        result.inspect(0, 1)


def test_forecast_keeps_failed_paths_and_does_not_invent_credit(zero_tax):
    request = _edit(
        forecast_request(),
        lambda d: d["plans"][0]["wealth"]["snapshot"]["accounts"][0].update(
            balance=1500
        ),
    )
    result = calculate_forecast(request)
    assert [row["assets"] for row in result.inspect(0, 0)] == [500, 0, 0]
    assert [row["unfunded"] for row in result.inspect(0, 0)] == [0, 500, 1000]
    summary = result.document.summaries[0]
    assert summary.funded_fraction == 0
    assert summary.first_shortfall_year == 2027
    assert summary.mean_unfunded_total == 1500
    assert summary.mean_unfunded_years == 2


def test_forecast_employee_and_employer_contributions_added_once_and_capped(zero_tax):
    def edit(data):
        retirement = _profile(data, "retirement")
        for adult in retirement["adults"]:
            adult["retirement"]["central"] = 2028
        work = _profile(data, "work")
        work["adults"][0]["phases"] = [
            {
                "start": 2026,
                "status": "working",
                "cash_pay": 100000,
                "real_growth": {"central": 0},
                "employee_401k": 30000,
                "employer_retirement_rate": ".6",
            }
        ]

    result = calculate_forecast(_edit(forecast_request(), edit))
    first, _, retired = result.inspect(0, 0)
    assert first["employee_contributions"] == 24500
    assert first["employer_contributions"] == 47500
    assert first["assets"] == 156500
    assert retired["employee_contributions"] == retired["employer_contributions"] == 0


def test_forecast_pretax_tax_grossup_and_inaccessible_early_retirement(monkeypatch):
    def flat_tax(**inputs):
        n = len(inputs["wages"])
        tax = np.asarray(inputs["ordinary_income"]) * 0.25
        return ForecastTaxResult(tax, *(np.zeros(n) for _ in range(6)))

    monkeypatch.setattr("financial_model.forecast.yearly_tax", flat_tax)

    def edit(data):
        account = data["plans"][0]["wealth"]["snapshot"]["accounts"][0]
        account.update(tax_treatment="tax_deferred")
        account["owner"] = 1

    request = _edit(forecast_request(), edit)
    first = calculate_forecast(request).inspect(0, 0)[0]
    assert first["withdrawals"] == pytest.approx(1000 / 0.75, abs=0.01)
    assert first["tax_paid"] == pytest.approx(1000 / 3, abs=0.01)
    assert first["assets"] == pytest.approx(10000 - 1000 / 0.75, abs=0.01)
    younger = _edit(
        request, lambda d: d["plans"][0]["household"]["adults"][0].update(age=59)
    )
    first = calculate_forecast(younger).inspect(0, 0)[0]
    assert first["unfunded"] == 1000
    assert first["assets"] == 10000
    assert first["accessible"] == 0


def test_forecast_withdrawal_funds_expenses_across_tax_credit_step(monkeypatch):
    def stepped_tax(**inputs):
        n = len(inputs["wages"])
        tax = np.where(np.asarray(inputs["gains"]) <= -999.75, 50.0, 100.0)
        return ForecastTaxResult(tax, *(np.zeros(n) for _ in range(6)))

    monkeypatch.setattr("financial_model.forecast.yearly_tax", stepped_tax)

    def edit(data):
        account = data["plans"][0]["wealth"]["snapshot"]["accounts"][0]
        account.update(balance=100000, invested_in="stocks", cost_basis=200000)
        retirement = _profile(data, "retirement")
        _profile(data, "spending")["expenses"][0]["levels"][0]["amount"]["central"] = (
            900
        )
        for adult in retirement["adults"]:
            adult["age"]["central"] = 60

    row = calculate_forecast(_edit(forecast_request(), edit)).inspect(0, 0)[0]
    # A loss of $999.75 unlocks a $50 credit. No exact-zero residual exists;
    # interpolation alone stalls below the threshold despite sufficient funds.
    assert row["withdrawals"] == pytest.approx(999.75, abs=0.005)
    assert row["realized_gains"] == pytest.approx(-999.75, abs=0.005)
    assert row["taxes"] == row["tax_paid"] == 50
    assert row["spending_paid"] == 900
    assert row["unfunded"] == 0
    assert row["assets"] == pytest.approx(99050, abs=0.005)


def test_forecast_rmd_surplus_is_reinvested(zero_tax):
    def edit(data):
        for adult in data["plans"][0]["household"]["adults"]:
            adult["age"] = 75
        retirement = _profile(data, "retirement")
        for adult in retirement["adults"]:
            adult["age"]["central"] = 75
        _profile(data, "spending")["expenses"][0]["levels"][0]["amount"]["central"] = 0
        account = data["plans"][0]["wealth"]["snapshot"]["accounts"][0]
        account.update(tax_treatment="tax_deferred", balance=24600)
        account["owner"] = 1

    row = calculate_forecast(_edit(forecast_request(), edit)).inspect(0, 0)[0]
    assert row["required_distributions"] == pytest.approx(1000)
    assert row["assets"] == 24600
    assert row["Forecast cash"] == pytest.approx(1000)


def test_forecast_debt_payment_nets_principal_and_accounts_interest(zero_tax):
    debt_id = str(uuid4())

    def edit(data):
        data["plans"][0]["wealth"]["snapshot"]["debts"] = [
            {
                "id": debt_id,
                "label": "Loan",
                "amount_owed": 1000,
                "annual_payment": 550,
                "interest_rate": ".1",
            }
        ]

    rows = calculate_forecast(_edit(forecast_request(), edit)).inspect(0, 0)
    assert rows[0]["debt"] == pytest.approx(550)
    assert rows[0]["assets"] == pytest.approx(8450)
    assert rows[0]["net_worth"] == pytest.approx(7900)
    assert rows[1]["debt"] == pytest.approx(55)
    assert rows[2]["debt"] == pytest.approx(0)
    assert rows[2]["debt_payment"] == pytest.approx(60.5)


@pytest.mark.parametrize("age", [60, 75])
def test_forecast_preserves_hsa_without_funding_general_expenses(zero_tax, age):
    def edit(data):
        account = data["plans"][0]["wealth"]["snapshot"]["accounts"][0]
        account.update(tax_treatment="hsa", owner=1)
        for person in data["plans"][0]["household"]["adults"]:
            person["age"] = age
        for person in _profile(data, "retirement")["adults"]:
            person["age"]["central"] = age + 2

    request = _edit(forecast_request(), edit)
    assert forecast_issues(request) == ()
    rows = calculate_forecast(request).inspect(0, 0)
    for row in rows:
        assert row["withdrawals"] == row["required_distributions"] == 0
        assert row["assets"] == 10000
        assert row["accessible"] == row["spending_paid"] == 0
        assert row["spending_planned"] == row["unfunded"] == 1000


def test_forecast_return_and_real_percentiles_do_not_double_count_yield(zero_tax):
    def edit(data):
        account = data["plans"][0]["wealth"]["snapshot"]["accounts"][0]
        account.update(invested_in="stocks")
        account["cost_basis"] = 10000
        _profile(data, "economy")["stock_dividend_yield"] = ".02"
        economy = _profile(data, "economy")
        economy["equities"]["central"] = ".1"
        economy["inflation"]["central"] = ".1"

    result = calculate_forecast(_edit(forecast_request(), edit))
    row = result.inspect(0, 0)[0]
    assert row["assets"] == pytest.approx(10000)
    assert row["investment_gain"] == pytest.approx(1000)
    assert result.document.summaries[0].points[0].median == pytest.approx(10000 / 1.1)


def test_forecast_zero_uncertainty_matches_fixed_and_identical_scenarios(zero_tax):
    request = forecast_request()
    fixed = calculate_forecast(request)

    def edit(data):
        data["settings"].update(mode="monte_carlo", count=20)
        data["plans"].append(data["plans"][0].copy())

    mc = calculate_forecast(_edit(request, edit))
    for point, reference in zip(
        mc.document.summaries[0].points, fixed.document.summaries[0].points, strict=True
    ):
        assert point.median == reference.median
        assert point.lower == point.upper == point.median
    assert mc.inspect(0, 12) == mc.inspect(1, 12)
    assert all(
        point.median_difference == point.lower_difference == point.upper_difference == 0
        for point in mc.document.summaries[1].points
    )


def test_forecast_partial_year_recurring_flows_are_prorated_once(zero_tax):
    request = _edit(
        forecast_request(),
        lambda d: d["plans"][0]["wealth"]["snapshot"].update(as_of="2026-07-02"),
    )
    row = calculate_forecast(request).inspect(0, 0)[0]
    assert row["spending_paid"] == pytest.approx(1000 * 183 / 365)
    assert row["assets"] == pytest.approx(10000 - 1000 * 183 / 365)


def test_forecast_survivor_inherits_accounts_and_receives_larger_social_security(
    zero_tax,
):
    def edit(data):
        for adult in data["plans"][0]["household"]["adults"]:
            adult["age"] = 67
        retirement = _profile(data, "retirement")
        retirement["adults"][0]["age"]["central"] = 67
        retirement["adults"][1]["age"]["central"] = 69
        account = data["plans"][0]["wealth"]["snapshot"]["accounts"][0]
        account.update(tax_treatment="roth")
        account["owner"] = 1
        _profile(data, "retirement")["adults"][0]["social_security"] = 200
        _profile(data, "retirement")["adults"][1]["social_security"] = 100

    rows = calculate_forecast(_edit(forecast_request(), edit)).inspect(0, 0)
    assert rows[0]["social_security"] == 300
    assert rows[1]["social_security"] == 200
    assert rows[1]["spending_paid"] == 500
    assert rows[1]["unfunded"] == 0


def test_forecast_unpaid_tax_remains_a_liability(monkeypatch):
    def tax_due(**inputs):
        n = len(inputs["wages"])
        return ForecastTaxResult(np.full(n, 15000), *(np.zeros(n) for _ in range(6)))

    monkeypatch.setattr("financial_model.forecast.yearly_tax", tax_due)
    rows = calculate_forecast(forecast_request()).inspect(0, 0)
    assert rows[0]["unpaid_tax_liability"] == 5000
    assert rows[0]["net_worth"] == -5000
    assert rows[1]["unpaid_tax_liability"] == 20000
    assert rows[1]["unfunded"] == 16000


def test_forecast_missing_account_basis_and_unsupported_sources_are_visible():
    request = _edit(
        forecast_request(),
        lambda d: d["plans"][0]["wealth"]["snapshot"]["accounts"][0].update(
            invested_in="stocks"
        ),
    )
    assert any("cost basis" in issue for issue in forecast_issues(request))
    with pytest.raises(ValueError, match="cost basis"):
        calculate_forecast(request)


def test_forecast_mixed_account_cash_has_face_value_basis(zero_tax):
    def edit(data):
        account = data["plans"][0]["wealth"]["snapshot"]["accounts"][0]
        account.update(
            invested_in="mixed", mix={"equities": ".5", "fixed_income": 0, "cash": ".5"}
        )
        account["cost_basis"] = 6000

    request = _edit(forecast_request(), edit)
    row = calculate_forecast(request).inspect(0, 0)[0]
    assert row["realized_gains"] == 0
    assert row["taxable_cost_basis"] == 5000
    invalid = _edit(
        request,
        lambda d: d["plans"][0]["wealth"]["snapshot"]["accounts"][0].update(
            cost_basis=4000
        ),
    )
    assert any(
        "less than the cash balance" in issue for issue in forecast_issues(invalid)
    )


def test_forecast_employer_additions_cannot_exceed_total_compensation(zero_tax):
    def edit(data):
        retirement = _profile(data, "retirement")
        retirement["adults"][0]["retirement"]["central"] = 2027
        work = _profile(data, "work")
        work["adults"][0]["phases"] = [
            {
                "start": 2026,
                "status": "working",
                "cash_pay": 20000,
                "real_growth": {"central": 0},
                "employee_401k": 20000,
                "employer_retirement_rate": ".2",
            }
        ]

    row = calculate_forecast(_edit(forecast_request(), edit)).inspect(0, 0)[0]
    assert row["employee_contributions"] == 20000
    assert row["employer_contributions"] == 0


def test_forecast_rebalancing_realizes_only_sales_and_preserves_reserve(zero_tax):
    def edit(data):
        account = data["plans"][0]["wealth"]["snapshot"]["accounts"][0]
        account.update(
            invested_in="mixed", mix={"equities": ".8", "fixed_income": 0, "cash": ".2"}
        )
        account["cost_basis"] = 6000
        investment = _profile(data, "investments")
        investment.update(rebalancing="annual", reserved_cash=2000)
        investment["checkpoints"][0]["mix"] = {
            "equities": ".5",
            "fixed_income": ".5",
            "cash": 0,
        }
        _profile(data, "spending")["expenses"][0]["levels"][0]["amount"]["central"] = 0

    row = calculate_forecast(_edit(forecast_request(), edit)).inspect(0, 0)[0]
    assert row["realized_gains"] == 2000
    assert row["cash"] == 2000
    assert row["assets"] == 10000
    assert row["reserve_breach"] == 0


def test_forecast_difference_unavailable_after_baseline_lifetime(zero_tax):
    def edit(data):
        import copy

        longer = copy.deepcopy(data["plans"][0])
        longer["name"] = "Longer lives"
        data["plans"].append(longer)
        for adult in _profile(data, "retirement", 1)["adults"]:
            adult["age"]["central"] = 64

    result = calculate_forecast(_edit(forecast_request(), edit))
    later = result.document.summaries[1].points
    assert later[2].median_difference == 0
    assert later[3].median_difference is None


def test_forecast_cash_saving_goals_stay_cash_between_rebalances(zero_tax):
    def edit(data):
        investment = _profile(data, "investments")
        investment["rebalancing"] = "annual"
        investment["checkpoints"][0]["mix"] = {
            "equities": 1,
            "fixed_income": 0,
            "cash": 0,
        }
        _profile(data, "economy")["equities"]["central"] = ".1"
        _profile(data, "retirement")["adults"][0].update(
            pension=10000, pension_start_age=60, pension_survivor_fraction=0
        )

    rows = calculate_forecast(_edit(forecast_request(), edit)).inspect(0, 0)
    assert rows[0]["Forecast cash"] == 9000
    assert rows[1]["Forecast cash"] == 18000
    assert rows[1]["assets"] == pytest.approx(30100)


def test_forecast_only_requires_fields_that_affect_modeled_cash_flows(zero_tax):
    def edit(data):
        economy = _profile(data, "economy")
        economy.update(stock_dividend_yield=None, bond_interest_yield=None)
        for adult in _profile(data, "retirement")["adults"]:
            adult.update(social_security=None, pension=None)

    request = _edit(forecast_request(), edit)
    assert forecast_issues(request) == ()
    assert calculate_forecast(request).inspect(0, 0)[0]["assets"] == 9000


def test_forecast_retirement_income_follows_household_mapping(zero_tax):
    def edit(data):
        data["plans"][0]["household"]["adults"][0]["age"] = 65
        retirement = _profile(data, "retirement")
        retirement["adults"][0].update(
            age={"central": 60},
            pension=200,
            pension_start_age=62,
            social_security=200,
            claiming_age=62,
        )
        retirement["adults"][1].update(
            age={"central": 65},
            pension=100,
            pension_start_age=65,
            social_security=100,
            claiming_age=65,
        )
        selection = next(
            c
            for c in data["plans"][0]["components"]
            if c["inputs"]["kind"] == "retirement"
        )
        selection["adult_mapping"] = [2, 1]

    row = calculate_forecast(_edit(forecast_request(), edit)).inspect(0, 0)[0]
    assert row["pensions"] == row["social_security"] == 100
    assert row["spending_planned"] == 1000
    assert row["assets"] == 9200


def test_forecast_comparison_uses_each_plans_retirement_benefits(zero_tax):
    def edit(data):
        import copy

        data["plans"].append(copy.deepcopy(data["plans"][0]))
        for index, amount in enumerate((200, 500)):
            data["plans"][index]["name"] = f"Pension {amount}"
            _profile(data, "retirement", index)["adults"][0].update(
                pension=amount, pension_start_age=60
            )

    result = calculate_forecast(_edit(forecast_request(), edit))
    assert result.inspect(0, 0)[0]["pensions"] == 200
    assert result.inspect(1, 0)[0]["pensions"] == 500
    assert result.document.summaries[1].points[0].median_difference == 300


def test_forecast_comparison_uses_each_plans_investment_yields(monkeypatch):
    def dividend_tax(**inputs):
        n = len(inputs["wages"])
        return ForecastTaxResult(
            np.asarray(inputs["qualified_dividends"]) * 0.25,
            *(np.zeros(n) for _ in range(6)),
        )

    monkeypatch.setattr("financial_model.forecast.yearly_tax", dividend_tax)

    def edit(data):
        import copy

        data["plans"][0]["wealth"]["snapshot"]["accounts"][0].update(
            invested_in="stocks", cost_basis=10000
        )
        data["plans"].append(copy.deepcopy(data["plans"][0]))
        for index, amount in enumerate((".02", ".04")):
            _profile(data, "economy", index)["stock_dividend_yield"] = amount

    result = calculate_forecast(_edit(forecast_request(), edit))
    assert result.inspect(0, 0)[0]["taxes"] == 50
    assert result.inspect(1, 0)[0]["taxes"] == 100
    assert result.document.summaries[1].points[0].median_difference == -50


@pytest.fixture
def family_request() -> ForecastRequest:
    """Compare zero, one and two children with sufficient income for their costs."""
    children = [
        PlanningChild(name=f"Child {index}", birth_year=2026 + index)
        for index in (1, 2)
    ]
    data = forecast_request().model_dump(mode="json")
    data["plans"][0]["wealth"]["snapshot"]["accounts"][0].update(
        invested_in="stocks", cost_basis=10000
    )
    _profile(data, "retirement")["adults"][0].update(
        pension=100000, pension_start_age=60, pension_survivor_fraction=0
    )
    _profile(data, "economy")["volatility"] = [".18", ".06", 0, 0]
    original = PlanningScenario.model_validate(data["plans"][0])
    data["plans"] = []
    for count in range(3):
        plan = original.model_dump(mode="json")
        plan["name"] = f"{count} children"
        plan["household"]["children"] = [
            child.model_dump(mode="json") for child in children[:count]
        ]
        spending = next(
            c["inputs"] for c in plan["components"] if c["inputs"]["kind"] == "spending"
        )
        spending["expenses"].extend(
            {
                "label": f"{child.name} living costs",
                "category": "children",
                "levels": [
                    {
                        "when": {"kind": "age", "person": str(child.id), "age": 0},
                        "amount": {"central": 5000},
                    }
                ],
                "end": {"kind": "age", "person": str(child.id), "age": 18},
            }
            for child in children[:count]
        )
        data["plans"].append(plan)
    return ForecastRequest.model_validate(data)


@pytest.mark.parametrize("mode", ["fixed", "monte_carlo"])
def test_forecast_comparison_uses_each_plans_children_and_shared_paths(
    family_request: ForecastRequest, mode: str
) -> None:
    request = _edit(
        family_request, lambda data: data["settings"].update(mode=mode, count=3)
    )
    result = calculate_forecast(request)
    for count, trace in enumerate(result.traces):
        for year_index, (row, baseline) in enumerate(
            zip(trace, result.traces[0], strict=True)
        ):
            dependent_count = min(count, year_index)
            np.testing.assert_allclose(
                row["spending_planned"], 1000 + dependent_count * 5000
            )
            # At $100k income, each child receives the pinned 2026 $2,200 credit.
            np.testing.assert_allclose(
                baseline["taxes"] - row["taxes"], dependent_count * 2200, atol=0.02
            )
            for field in ("equity_return", "bond_return", "cash_return", "inflation"):
                np.testing.assert_array_equal(row[field], baseline[field])
        np.testing.assert_array_equal(
            trace[0]["net_worth"], result.traces[0][0]["net_worth"]
        )
    if mode == "monte_carlo":
        assert np.ptp(result.traces[0][0]["equity_return"]) > 0
    assert all(summary.funded_fraction == 1 for summary in result.document.summaries)


@pytest.mark.parametrize("mismatch", ["absent_child", "unknown_birth_year"])
def test_forecast_comparison_rejects_unresolved_child_expenses(
    family_request: ForecastRequest, mismatch: str
) -> None:
    def edit(data):
        plan = data["plans"][1]
        plan["name"] = "Unresolved child"
        if mismatch == "absent_child":
            plan["household"]["children"] = []
        else:
            plan["household"]["children"][0]["birth_year"] = None

    request = _edit(family_request, edit)
    assert any(
        "Unresolved child" in issue and "Spending timing unresolved" in issue
        for issue in forecast_issues(request)
    )
    with pytest.raises(
        ValueError, match="Child 1 living costs; date or person needs input"
    ):
        calculate_forecast(request)


def test_forecast_partial_year_event_occurs_once_without_timing_question(zero_tax):
    def edit(data):
        data["plans"][0]["wealth"]["snapshot"]["as_of"] = "2026-07-02"
        _profile(data, "spending")["expenses"] += [
            {
                "label": "Move",
                "frequency": "once",
                "levels": [{"when": {"year": year}, "amount": {"central": 500}}],
            }
            for year in (2025, 2026)
        ]

    rows = calculate_forecast(_edit(forecast_request(), edit)).inspect(0, 0)
    assert rows[0]["spending_planned"] == pytest.approx(1000 * 183 / 365 + 500)
    assert rows[1]["spending_planned"] == 1000


def test_forecast_splitting_expenses_preserves_household_outcomes(zero_tax):
    import copy

    request = forecast_request()

    def edit(data):
        spending = _profile(data, "spending")
        original = spending["expenses"][0]
        spending["expenses"] = []
        for category, fraction in (
            ("ordinary", 0.5),
            ("children", 0.3),
            ("healthcare", 0.2),
        ):
            item = copy.deepcopy(original)
            item.update(id=str(uuid4()), category=category, label=category)
            for level in item["levels"]:
                level["amount"]["central"] = (
                    float(level["amount"]["central"]) * fraction
                )
            spending["expenses"].append(item)

    original = calculate_forecast(request)
    split = calculate_forecast(_edit(request, edit))
    for first, second in zip(original.inspect(0, 0), split.inspect(0, 0), strict=True):
        assert {k: v for k, v in first.items() if not k.startswith("expense_")} == {
            k: v for k, v in second.items() if not k.startswith("expense_")
        }
        assert (
            sum(v for k, v in second.items() if k.startswith("expense_"))
            == second["spending_planned"]
        )
    assert original.document.summaries[0].points == split.document.summaries[0].points


@pytest.mark.parametrize("mode", ["fixed", "monte_carlo"])
def test_forecast_expense_details_apply_growth_prices_and_proration_once(
    zero_tax, mode
):
    from financial_model.forecast_models import SavedForecast

    annual_id, once_id = uuid4(), uuid4()

    def edit(data):
        data["plans"][0]["wealth"]["snapshot"]["as_of"] = "2026-07-02"
        data["plans"][0]["wealth"]["snapshot"]["accounts"][0]["balance"] = 100
        data["settings"].update(mode=mode, count=3)
        economy = _profile(data, "economy")
        economy["inflation"]["central"] = ".1"
        economy["volatility"] = [0, 0, 0, ".01"]
        _profile(data, "spending")["expenses"] = [
            {
                "id": str(annual_id),
                "label": "Coverage",
                "category": "healthcare",
                "real_growth": ".02",
                "levels": [
                    {"when": {"year": 2026}, "amount": {"central": 1000}},
                    {"when": {"year": 2027}, "amount": {"central": 500}},
                ],
            },
            {
                "id": str(once_id),
                "label": "Equipment",
                "frequency": "once",
                "category": "care",
                "levels": [{"when": {"year": 2026}, "amount": {"central": 200}}],
            },
        ]

    result = calculate_forecast(_edit(forecast_request(), edit))
    summary = result.document.summaries[0]
    for path in range(summary.paths):
        rows = result.inspect(0, path)
        opening_price = 1.0
        for offset, row in enumerate(rows):
            expected = (
                1000 * 183 / 365 if offset == 0 else 500 * 1.02**offset * opening_price
            )
            assert row[f"expense_{annual_id}"] == pytest.approx(expected)
            assert row.get(f"expense_{once_id}", 0) == (200 if offset == 0 else 0)
            assert row["spending_planned"] == pytest.approx(
                expected + (200 if offset == 0 else 0)
            )
            assert row["spending_paid"] + row["unfunded_living"] == pytest.approx(
                row["spending_planned"]
            )
            opening_price = row["price_index"]
    annual_sources = [item for item in summary.expenses if item.item_id == annual_id]
    assert [item.base_amount for item in annual_sources] == [1000, 500, 500]
    assert [item.real_amount for item in annual_sources] == pytest.approx(
        [1000, 510, 520.2]
    )
    assert (
        SavedForecast.model_validate_json(result.document.model_dump_json())
        == result.document
    )


def test_forecast_deceased_benefits_are_optional_when_no_survivor_payment_is_modeled(
    zero_tax,
):
    def edit(data):
        data["plans"][0]["household"]["adults"][1]["age"] = 55
        retirement = _profile(data, "retirement")
        retirement["adults"][0].update(
            age={"central": 60},
            social_security=None,
            claiming_age=62,
            pension=None,
            pension_survivor_fraction=0,
        )
        retirement["adults"][1].update(
            age={"central": 60}, social_security=None, pension=None
        )

    request = _edit(forecast_request(), edit)
    assert forecast_issues(request) == ()
    rows = calculate_forecast(request).inspect(0, 0)
    assert len(rows) == 6
    assert all(row["pensions"] == row["social_security"] == 0 for row in rows)


def test_forecast_pension_beyond_horizon_does_not_require_survivor_fraction():
    def edit(data):
        retirement = _profile(data, "retirement")
        retirement["adults"][0].update(
            age={"central": 60}, pension=1000, pension_start_age=80
        )

    assert forecast_issues(_edit(forecast_request(), edit)) == ()


def test_forecast_still_requires_deceased_social_security_when_survivor_can_claim():
    def edit(data):
        data["plans"][0]["household"]["adults"][1]["age"] = 65
        retirement = _profile(data, "retirement")
        retirement["adults"][0].update(
            age={"central": 60}, social_security=None, claiming_age=62
        )
        retirement["adults"][1].update(age={"central": 67}, claiming_age=65)

    assert any(
        "Adult 1: enter Social Security" in issue
        for issue in forecast_issues(_edit(forecast_request(), edit))
    )


def test_forecast_partial_year_living_expenses_do_not_consume_hsa(
    zero_tax,
):
    def edit(data):
        snapshot = data["plans"][0]["wealth"]["snapshot"]
        snapshot["as_of"] = "2026-07-02"
        snapshot["accounts"][0].update(tax_treatment="hsa", owner=1)
        _profile(data, "economy")["inflation"]["central"] = ".1"

    rows = calculate_forecast(_edit(forecast_request(), edit)).inspect(0, 0)
    fraction = 183 / 365
    assert rows[0]["spending_planned"] == pytest.approx(1000 * fraction)
    assert rows[0]["unfunded"] == pytest.approx(1000 * fraction)
    assert rows[1]["spending_planned"] == pytest.approx(1000 * 1.1**fraction)
    assert rows[1]["unfunded"] == pytest.approx(1000 * 1.1**fraction)
    assert all(row["assets"] == 10000 for row in rows)
