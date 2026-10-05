"""Independent retirement examples and batching checks for the forecast tax policy."""

from __future__ import annotations

from dataclasses import fields
from typing import Any

import numpy as np
import pytest

from financial_model.forecast_tax import ForecastTaxResult, yearly_tax
from financial_model.schemas import Scenario
from financial_model.taxes import calculate_taxes


def tax(**overrides: Any) -> ForecastTaxResult:
    """Calculate synthetic retired joint filers without private household data."""
    values = {
        "wages": np.zeros((1, 2)),
        "contributions": np.zeros((1, 2)),
        "ages": (67, 67),
        "state": "CA",
    }
    values.update(overrides)
    return yearly_tax(**values)


@pytest.mark.parametrize("state", ["CA", "WA"])
@pytest.mark.parametrize("children", [(), (3, 13)])
def test_yearly_tax_preserves_working_household_adapter(
    scenario: Scenario, state: str, children: tuple[int, ...]
) -> None:
    data = scenario.model_dump(mode="json")
    data.update(
        tax_year=2026, state=state, dependents=[{"age": age} for age in children]
    )
    for adult in data["adults"]:
        adult["employee_401k"] = 24500
    data["living_costs"].update(
        childcare=1000, eligible_care_expense=6000 if children else 0
    )
    reference = calculate_taxes(Scenario.model_validate(data))
    result = tax(
        wages=[[100000, 100000]],
        contributions=[[24500, 24500]],
        ages=(40, 38),
        state=state,
        dependent_ages=children,
        eligible_care_expense=6000 if children else 0,
    )
    assert result.federal_income_tax[0] == pytest.approx(
        float(reference.federal_income_tax), abs=0.05
    )
    assert result.state_income_tax[0] == pytest.approx(
        float(reference.state_income_tax), abs=0.05
    )
    assert result.employee_payroll_tax[0] == pytest.approx(
        float(reference.employee_payroll_tax), abs=0.05
    )


def test_yearly_tax_retirement_social_security_and_senior_deductions() -> None:
    # IRS Pub915: provisional income80k -> taxable benefits min(34k,36.6k)=34k.
    # IRS2026 RevProc2025-32:32,200 standard +1,650 each aged65+.
    # Frozen2026 policy retains the temporary6,000 deduction per senior.
    # Taxable94,000-35,500-12,000=46,500;24,800*.1+21,700*.12=5,084.
    result = tax(ordinary_income=60000, social_security=40000)
    assert result.taxable_social_security[0] == 34000
    assert result.federal_income_tax[0] == pytest.approx(5084, abs=0.02)
    assert result.employee_payroll_tax[0] == 0
    # FTB explicitly excludes Social Security; its presence must not change CA tax.
    without_social_security = tax(ordinary_income=60000)
    assert result.state_income_tax[0] == without_social_security.state_income_tax[0]


def test_yearly_tax_survivor_uses_single_filing_and_living_person_age() -> None:
    # AGI77,000; single standard18,150; senior6,000-.06*2,000=5,880.
    # Taxable52,970:1,240+4,560+2,570*.22=6,365.40.
    survivor = tax(ordinary_income=60000, social_security=20000, alive=(False, True))
    assert survivor.federal_income_tax[0] == pytest.approx(6365.4, abs=0.03)
    reversed_survivor = tax(
        ordinary_income=60000, social_security=20000, alive=(True, False)
    )
    assert survivor.total[0] == reversed_survivor.total[0]
    assert survivor.taxable_social_security[0] == 17000


@pytest.mark.parametrize(
    ("ordinary", "benefits", "taxable"),
    [(0, 40000, 0), (30000, 20000, 4000), (40000, 20000, 11100), (80000, 20000, 17000)],
)
def test_yearly_tax_social_security_thresholds(
    ordinary: int, benefits: int, taxable: int
) -> None:
    # IRS Publication915 joint provisional-income boundaries32k/44k.
    assert (
        tax(ordinary_income=ordinary, social_security=benefits).taxable_social_security[
            0
        ]
        == taxable
    )


def test_yearly_tax_qualified_gains_stack_above_ordinary_income() -> None:
    # 2026 joint LTCG0% threshold98,900; standard32,200.
    # All gains:150,000-32,200-98,900=18,900 taxed15%.
    gains = tax(gains=150000, ages=(40, 40), state="WA")
    dividends = tax(qualified_dividends=150000, ages=(40, 40), state="WA")
    assert gains.federal_income_tax[0] == pytest.approx(2835, abs=0.03)
    assert gains.total[0] == dividends.total[0]


def test_yearly_tax_investment_income_tax_excludes_retirement_distributions() -> None:
    # IRS Form8960: qualified retirement distributions do not enter net investment income.
    args = {"wages": [[150000, 150000]], "ages": (40, 40), "state": "WA"}
    retirement = tax(**args, ordinary_income=50000)
    investment = tax(**args, investment_income=50000)
    assert investment.federal_income_tax[0] - retirement.federal_income_tax[
        0
    ] == pytest.approx(1900, abs=0.02)
    assert investment.employee_payroll_tax[0] == retirement.employee_payroll_tax[0]


def test_yearly_tax_rental_profit_is_investment_income_without_payroll_or_qbi() -> None:
    args = {"wages": [[150000, 150000]], "ages": (40, 40), "state": "CA"}
    retirement = tax(**args, ordinary_income=50000)
    rental = tax(**args, rental_income=50000)
    interest = tax(**args, investment_income=50000)
    assert rental.federal_income_tax[0] - retirement.federal_income_tax[
        0
    ] == pytest.approx(1900, abs=0.02)
    assert rental.federal_income_tax[0] == interest.federal_income_tax[0]
    assert rental.state_income_tax[0] == interest.state_income_tax[0]
    assert rental.employee_payroll_tax[0] == retirement.employee_payroll_tax[0]


def test_yearly_tax_rejects_unmodeled_rental_losses() -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        tax(rental_income=-1000)


def test_yearly_tax_wa_capital_gains_progressive_rates() -> None:
    # WA RCW82.87.040:7% first1m of taxablegain,9.9% excess.
    # Pinned engine's2026 standard deduction projection is284,000.
    result = tax(gains=1500000, state="WA", ages=(40, 40))
    assert result.state_income_tax[0] == pytest.approx(70000 + 216000 * 0.099, abs=0.1)


def test_yearly_tax_nominal_results_scale_under_frozen_real_policy() -> None:
    reference = tax(ordinary_income=60000, social_security=40000, gains=-10000)
    inflated = tax(
        ordinary_income=120000, social_security=80000, gains=-20000, inflation=2
    )
    for field in fields(reference):
        np.testing.assert_allclose(
            getattr(inflated, field.name), 2 * getattr(reference, field.name)
        )


def test_yearly_tax_batch_paths_do_not_share_income_or_credits() -> None:
    # Comparing batched diverse households to independent calls catches population mixing.
    ordinary = np.array([0, 34000, 60000, 300000])
    gains = np.array([-10000, -10000, 150000, 1500000])
    result = tax(
        wages=np.zeros((4, 2)),
        contributions=np.zeros((4, 2)),
        ordinary_income=ordinary,
        gains=gains,
        social_security=40000,
    )
    for index in range(4):
        single = tax(
            ordinary_income=ordinary[index], gains=gains[index], social_security=40000
        )
        for field in fields(result):
            assert getattr(result, field.name)[index] == pytest.approx(
                getattr(single, field.name)[0], abs=0.1
            )


def test_yearly_tax_capital_losses_unused_below_deduction_carry_forward() -> None:
    # IRS ScheduleD worksheet permits negative pre-floor taxableincome.
    # 34k-3k-32.2k=-1.2k: only1.8k of10k netloss used;8.2k carries.
    result = tax(ordinary_income=34000, gains=-10000, ages=(40, 40))
    assert result.capital_loss_carryforward[0] == 8200
    # CA's smaller standard deduction permits the full3k loss deduction.
    assert result.ca_capital_loss_carryforward[0] == 7000
    no_income = tax(gains=-10000, ages=(40, 40))
    assert no_income.capital_loss_carryforward[0] == 10000
    assert no_income.ca_capital_loss_carryforward[0] == 10000


def test_yearly_tax_ca_uses_separate_carryover_to_adjust_federal_income() -> None:
    carried = tax(
        ordinary_income=60000,
        gains=20000,
        capital_loss_carryforward=8200,
        ca_capital_loss_carryforward=7000,
    )
    federal_reference = tax(ordinary_income=60000, gains=11800)
    ca_reference = tax(ordinary_income=60000, gains=13000)
    assert carried.federal_income_tax[0] == federal_reference.federal_income_tax[0]
    assert carried.state_income_tax[0] == ca_reference.state_income_tax[0]
    assert carried.capital_loss_carryforward[0] == 0
    assert carried.ca_capital_loss_carryforward[0] == 0


def test_yearly_tax_wa_tracks_only_allocated_losses() -> None:
    lost = tax(gains=-10000, state="WA", ages=(40, 40))
    assert lost.wa_capital_loss_carryforward[0] == 10000
    ca_loss = tax(gains=-10000, state="CA", ages=(40, 40))
    assert ca_loss.wa_capital_loss_carryforward[0] == 0
    used = tax(
        ordinary_income=60000,
        gains=4000,
        capital_loss_carryforward=20000,
        ca_capital_loss_carryforward=20000,
        wa_capital_loss_carryforward=10000,
        state="WA",
    )
    assert used.capital_loss_carryforward[0] == 13000
    assert used.wa_capital_loss_carryforward[0] == 3000
    excluded = tax(gains=350000, capital_loss_carryforward=20000, state="WA")
    included = tax(
        gains=350000,
        capital_loss_carryforward=20000,
        wa_capital_loss_carryforward=20000,
        state="WA",
    )
    assert excluded.state_income_tax[0] - included.state_income_tax[0] == 1400


def test_yearly_tax_wa_cares_exempts_correct_surviving_adult() -> None:
    original = tax(wages=[[0, 100000]], alive=(False, True), ages=(40, 40), state="WA")
    exempt = tax(
        wages=[[0, 100000]],
        alive=(False, True),
        ages=(40, 40),
        state="WA",
        wa_cares_exempt=(False, True),
    )
    assert original.employee_payroll_tax[0] - exempt.employee_payroll_tax[0] == 580


def test_yearly_tax_hsa_earnings_are_taxed_only_by_california() -> None:
    # FTB Pub1001: California does not conform to federal HSA tax exemption.
    original = tax(ordinary_income=200000)
    hsa = tax(ordinary_income=200000, ca_hsa_income=5000, ca_hsa_gains=10000)
    california_reference = tax(ordinary_income=215000)
    assert hsa.federal_income_tax[0] == original.federal_income_tax[0]
    assert hsa.state_income_tax[0] == california_reference.state_income_tax[0]
    washington = tax(ordinary_income=200000, state="WA")
    wa_hsa = tax(
        ordinary_income=200000, ca_hsa_income=5000, ca_hsa_gains=10000, state="WA"
    )
    assert wa_hsa.total[0] == washington.total[0]


def test_yearly_tax_hsa_losses_change_only_ca_carryover() -> None:
    hsa_loss = tax(ordinary_income=60000, ca_hsa_gains=-10000)
    capital_loss = tax(ordinary_income=60000, gains=-10000)
    assert hsa_loss.state_income_tax[0] == capital_loss.state_income_tax[0]
    assert hsa_loss.capital_loss_carryforward[0] == 0
    assert hsa_loss.ca_capital_loss_carryforward[0] == 7000
    assert hsa_loss.wa_capital_loss_carryforward[0] == 0


def test_yearly_tax_deceased_adult_age_does_not_invalidate_survivor() -> None:
    result = tax(ages=(131, 100), alive=(False, True), ordinary_income=60000)
    reference = tax(ages=(100, 100), alive=(False, True), ordinary_income=60000)
    assert result.total[0] == reference.total[0]


@pytest.mark.parametrize(
    "changes",
    [
        {"wages": [100, 100]},
        {"ordinary_income": float("nan")},
        {"inflation": 0},
        {"state": "OR"},
        {"ages": (10, 40)},
        {"dependent_ages": (19,)},
        {"wages": [[100, 100]], "contributions": [[101, 0]]},
        {"wages": [[100000, 100000]], "contributions": [[25000, 0]]},
        {"wages": [[100, 0]], "alive": (False, True)},
        {"capital_loss_carryforward": 100, "wa_capital_loss_carryforward": 101},
    ],
)
def test_yearly_tax_rejects_invalid_inputs(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        tax(**changes)
