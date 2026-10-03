"""End-to-end forecast checks using the actual pinned retirement tax calculator."""

from __future__ import annotations

import runpy
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from financial_model.forecast import calculate_forecast
from financial_model.forecast_tax import yearly_tax

# Reuse the complete synthetic plan while its owning test module evolves. This
# executes no tests and leaves the production tax adapter unpatched.
_HELPERS = runpy.run_path(str(Path(__file__).with_name("test_forecast.py")))
forecast_request = _HELPERS["forecast_request"]
_edit = _HELPERS["_edit"]
_profile = _HELPERS["_profile"]


def _account(label, amount, treatment, asset="cash"):
    return {
        "id": str(uuid4()),
        "label": label,
        "balance": amount,
        "tax_treatment": treatment,
        "invested_in": asset,
    }


def _balance(row, label):
    return next(
        value for key, value in row.items() if key.startswith(f"Account: {label} (")
    )


def test_forecast_actual_retirement_tax_grossup_matches_bracket_equation():
    def edit(data):
        account = data["plans"][0]["wealth"]["snapshot"]["accounts"][0]
        account.update(label="Pretax", balance=200000, tax_treatment="tax_deferred")
        data["details"]["accounts"] = [{"account_id": account["id"], "owner": 1}]
        _profile(data, "retirement")["both_retired"]["amount"]["central"] = 60000

    rows = calculate_forecast(_edit(forecast_request(), edit)).inspect(0, 0)
    # 2026 joint filers below65: income D pays2480+.12*(D-57,000).
    # Solve D-tax(D)=60,000 without using the forecast withdrawal solver.
    gross = (60000 - 4360) / 0.88
    for row in rows:
        assert row["withdrawals"] == pytest.approx(gross, abs=0.03)
        assert row["taxes"] == pytest.approx(gross - 60000, abs=0.03)
        assert row["spending_paid"] == 60000
        assert row["unfunded"] < 0.01
    assert rows[-1]["assets"] == pytest.approx(200000 - 3 * gross, abs=0.05)


def test_forecast_actual_taxes_follow_cash_taxable_pretax_roth_order():
    def edit(data):
        accounts = [
            _account("Cash", 10000, "taxable"),
            _account("Brokerage", 50000, "taxable", "stocks"),
            _account("Pretax", 100000, "tax_deferred"),
            _account("Roth", 30000, "roth"),
        ]
        data["plans"][0]["wealth"]["snapshot"]["accounts"] = accounts
        data["details"]["accounts"] = [
            {"account_id": accounts[1]["id"], "cost_basis": 25000},
            {"account_id": accounts[2]["id"], "owner": 1},
            {"account_id": accounts[3]["id"], "owner": 1},
        ]
        _profile(data, "retirement")["both_retired"]["amount"]["central"] = 180000

    row = calculate_forecast(_edit(forecast_request(), edit)).inspect(0, 0)[0]
    # Ordinary100k-standard32.2k=67.8k ->2480+43k*.12=7640.
    # With25k gains, combined taxable92.8k remains below98.9k LTCG0% ceiling.
    assert row["taxes"] == pytest.approx(7640, abs=0.03)
    assert row["realized_gains"] == pytest.approx(25000, abs=0.01)
    assert row["withdrawals"] == pytest.approx(187640, abs=0.05)
    assert (
        _balance(row, "Cash")
        == _balance(row, "Brokerage")
        == _balance(row, "Pretax")
        == 0
    )
    assert _balance(row, "Roth") == pytest.approx(2360, abs=0.05)
    assert row["unfunded"] < 0.01


def test_forecast_partial_year_tax_uses_annualized_income_then_prorates_once():
    def edit(data):
        snapshot = data["plans"][0]["wealth"]["snapshot"]
        snapshot["as_of"] = "2026-07-02"
        account = snapshot["accounts"][0]
        account.update(balance=200000, tax_treatment="tax_deferred")
        data["details"]["accounts"] = [{"account_id": account["id"], "owner": 1}]
        _profile(data, "retirement")["both_retired"]["amount"]["central"] = 60000

    first = calculate_forecast(_edit(forecast_request(), edit)).inspect(0, 0)[0]
    fraction = 183 / 365
    annual_gross = (60000 - 4360) / 0.88
    assert first["spending_paid"] == pytest.approx(60000 * fraction)
    assert first["withdrawals"] == pytest.approx(annual_gross * fraction, abs=0.03)
    assert first["taxes"] == pytest.approx((annual_gross - 60000) * fraction, abs=0.03)


def test_forecast_realized_loss_carry_is_consumed_once_each_year():
    def edit(data):
        account = data["plans"][0]["wealth"]["snapshot"]["accounts"][0]
        account.update(balance=100000, invested_in="stocks")
        data["details"]["accounts"] = [
            {"account_id": account["id"], "cost_basis": 100000}
        ]
        data["details"]["incomes"][0].update(
            pension=60000, pension_start_age=60, pension_survivor_fraction=0
        )
        _profile(data, "retirement")["both_retired"]["amount"]["central"] = 70000
        data["settings"].update(
            mode="historical",
            history_source="Synthetic loss-carry reference",
            history=[
                [-0.5, 0, 0, 0],
                [1, 0, 0, 0],
                [0, 0, 0, 0],
            ],
        )

    rows = calculate_forecast(_edit(forecast_request(), edit)).inspect(0, 0)
    # Pension60k minus3k loss minus32.2k standard leaves24.8k taxed10%.
    # The first year's12,480 withdrawal realizes12,480 loss (half-price assets).
    # Exactly3k loss is consumed annually; the subsequent price recovery has no
    # gain because it returns each remaining share to its original basis.
    assert [row["taxes"] for row in rows] == pytest.approx([2480, 2480, 2480], abs=0.03)
    assert [row["withdrawals"] for row in rows] == pytest.approx(
        [12480, 12480, 12480], abs=0.03
    )
    assert [row["capital_loss_carryforward"] for row in rows] == pytest.approx(
        [9480, 6480, 3480], abs=0.05
    )


def test_forecast_funds_expenses_at_discontinuous_child_credit_threshold():
    def edit(data):
        data["plans"][0]["household"]["children"] = [
            {"name": "Child", "birth_year": 2016}
        ]
        account = data["plans"][0]["wealth"]["snapshot"]["accounts"][0]
        account.update(balance=10000, invested_in="stocks")
        data["details"]["accounts"] = [
            {"account_id": account["id"], "cost_basis": 20000}
        ]
        data["details"]["incomes"][0].update(
            pension=400100, pension_start_age=60, pension_survivor_fraction=0
        )
        retirement = _profile(data, "retirement")
        retirement["both_retired"]["amount"]["central"] = 328907
        for adult in retirement["adults"]:
            adult["age"]["central"] = 60

    row = calculate_forecast(_edit(forecast_request(), edit)).inspect(0, 0)[0]
    # Crossing AGI400k restores $50 of child credit at once. No withdrawal makes
    # cash exactly equal to expenses: take the smallest funded solution instead
    # of looping forever or fabricating a shortfall at the tax discontinuity.
    assert row["withdrawals"] == pytest.approx(100, abs=0.05)
    assert row["spending_paid"] == 328907
    assert row["unfunded"] < 0.01
    assert row["taxes"] == pytest.approx(71268, abs=0.05)


def test_forecast_survivor_with_no_own_ss_receives_spousal_survivor_benefit():
    def edit(data):
        for adult in data["plans"][0]["household"]["adults"]:
            adult["age"] = 67
        retire = _profile(data, "retirement")
        retire["adults"][0]["age"]["central"] = 67
        retire["adults"][1]["age"]["central"] = 68
        first, second = data["details"]["incomes"]
        first["social_security"] = 20000
        second.update(social_security=0, pension=60000, pension_survivor_fraction=0)

    rows = calculate_forecast(_edit(forecast_request(), edit)).inspect(0, 0)
    assert rows[0]["social_security"] == rows[1]["social_security"] == 20000
    # Survivor AGI77k; standard18,150; senior deduction5,880.
    # Tax52,970 =>1240+4560+2570*.22=6365.4.
    assert rows[1]["taxes"] == pytest.approx(6365.4, abs=0.03)
    assert rows[1]["income"] == 80000


def test_forecast_future_market_path_cannot_change_prior_withdrawals():
    def edit(data):
        account = data["plans"][0]["wealth"]["snapshot"]["accounts"][0]
        account.update(
            balance=250000, invested_in="stocks", tax_treatment="tax_deferred"
        )
        data["details"]["accounts"] = [{"account_id": account["id"], "owner": 1}]
        _profile(data, "retirement")["both_retired"]["amount"]["central"] = 60000
        data["settings"].update(
            mode="historical",
            history_source="Synthetic causality test",
            history=[
                [0.1, 0, 0, 0.02],
                [-0.1, 0, 0, 0.03],
                [-0.8, 0, 0, 0.1],
            ],
        )

    adverse = _edit(forecast_request(), edit)
    favorable = _edit(
        adverse,
        lambda data: data["settings"]["history"].__setitem__(2, [1, 0, 0, -0.05]),
    )
    first = calculate_forecast(adverse).inspect(0, 0)
    second = calculate_forecast(favorable).inspect(0, 0)
    assert first[:2] == second[:2]
    assert first[2]["assets"] != second[2]["assets"]


def test_forecast_actual_wage_taxes_and_saving_conserve_household_money():
    def edit(data):
        retire = _profile(data, "retirement")
        for adult in retire["adults"]:
            adult["retirement"]["central"] = 2028
        work = _profile(data, "work")
        for adult in work["adults"]:
            adult["phases"] = [
                {
                    "start": 2026,
                    "status": "working",
                    "cash_pay": 100000,
                    "real_growth": {"central": 0},
                    "employee_401k": 24500,
                    "employer_retirement_rate": ".1",
                }
            ]

    row = calculate_forecast(_edit(forecast_request(), edit)).inspect(0, 0)[0]
    # Federal15,560; employeeFICA15,300; WAleave1,614.32; WACares1,160.
    total_tax = 15560 + 15300 + 1614.32 + 1160
    assert row["taxes"] == pytest.approx(total_tax, abs=0.04)
    assert row["employee_contributions"] == 49000
    assert row["employer_contributions"] == 20000
    assert row["assets"] == pytest.approx(
        10000 + 200000 + 20000 - 1000 - total_tax, abs=0.04
    )
    assert row["Adult 1 contributions"] == row["Adult 2 contributions"] == 34500


@pytest.mark.parametrize("state", ["CA", "WA"])
def test_forecast_hsa_growth_and_qualified_withdrawal_use_state_basis(state):
    def edit(data):
        hsa = _account("HSA", 100000, "hsa", "stocks")
        data["plans"][0]["wealth"]["snapshot"]["accounts"].append(hsa)
        data["plans"][0]["household"]["residence"][0]["location"] = state
        data["details"]["accounts"] = [
            {"account_id": hsa["id"], "owner": 1, "cost_basis": 50000}
        ]
        data["details"]["stock_dividend_yield"] = ".02"
        for income in data["details"]["incomes"]:
            income.update(hsa_eligible_before_65=0, hsa_eligible_from_65=0)
        data["details"]["incomes"][0].update(
            pension=100000,
            pension_start_age=60,
            pension_survivor_fraction=0,
            health_before_65=10000,
            hsa_eligible_before_65=10000,
        )
        _profile(data, "economy")["equities"]["central"] = ".1"

    row = calculate_forecast(_edit(forecast_request(), edit)).inspect(0, 0)[0]
    # Dividend2k is part of totalreturn10k, reinvested and added to CA basis.
    # Selling10k from110k with52k basis realizes10k*(1-52/110).
    ca_earnings = 2000 + 10000 * (1 - 52000 / 110000)
    state_reference = yearly_tax(
        wages=np.zeros((1, 2)),
        contributions=np.zeros((1, 2)),
        ordinary_income=100000 + (ca_earnings if state == "CA" else 0),
        ages=(60, 60),
        state=state,
    )
    expected_tax = 7640 + state_reference.state_income_tax[0]
    assert row["taxes"] == pytest.approx(expected_tax, abs=0.05)
    assert row["hsa_withdrawals"] == 10000
    assert _balance(row, "HSA") == pytest.approx(100000)
    assert row["assets"] == pytest.approx(
        110000 + 100000 + 10000 - 11000 - expected_tax, abs=0.05
    )
