"""Annual schedules respect person mappings, event boundaries and money bases."""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

import pytest

from financial_model.forecast_timeline import compile_plan
from financial_model.planning import (
    EconomicAssumptions,
    InvestmentAssumptions,
    PlanningScenario,
    RetirementAssumptions,
    SavedPlanningDocument,
    SpendingAssumptions,
    WorkAssumptions,
    select_profile,
)
from financial_model.schemas import default_allocation_plan


@pytest.fixture
def timeline_plan():
    """A small complete plan with independently calculable annual transitions."""
    work = WorkAssumptions(
        name="Work",
        money_year=2026,
        rsu_taxes_from_shares=True,
        adults=(
            {
                "slot": 1,
                "name": "A",
                "phases": [
                    {
                        "start": 2026,
                        "status": "working",
                        "cash_pay": 100,
                        "real_growth": {"central": ".1"},
                        "contribution_basis": "pay_fraction",
                        "contribution_rate": ".1",
                        "employer_retirement_rate": ".05",
                    }
                ],
                "vesting": [
                    {"start": 2026, "end": 2040, "amount": 10, "basis": "real"}
                ],
            },
            {
                "slot": 2,
                "name": "B",
                "phases": [
                    {
                        "start": 2026,
                        "status": "working",
                        "cash_pay": 200,
                        "real_growth": {"central": 0},
                        "contribution_basis": "nominal",
                        "employee_401k": 20,
                        "employer_retirement_rate": ".1",
                    }
                ],
                "vesting": [{"start": 2026, "end": 2040, "amount": 20}],
            },
        ),
    )
    spending = SpendingAssumptions(
        name="Costs", money_year=2026, baseline={"amount": {"central": 1000}}
    )
    retirement = RetirementAssumptions(
        name="Retire",
        money_year=2026,
        adults=(
            {
                "slot": 1,
                "name": "A",
                "retirement": {"central": 2028},
                "age": {"central": 64},
            },
            {
                "slot": 2,
                "name": "B",
                "retirement": {"central": 2030},
                "age": {"central": 64},
            },
        ),
        only_adult_1={"mode": "replace", "budget": {"amount": {"central": 800}}},
        only_adult_2={"mode": "replace", "budget": {"amount": {"central": 900}}},
        both_retired={"amount": {"central": 600}},
        survivor={"amount": {"central": 400}},
    )
    investments = InvestmentAssumptions(
        name="Invest",
        money_year=2026,
        reserved_cash=0,
        saving=default_allocation_plan(),
        policy="gradual",
        rebalancing="annual",
        checkpoints=(
            {
                "when": {"year": 2026},
                "mix": {"equities": ".8", "fixed_income": ".2", "cash": 0},
            },
            {
                "when": {"year": 2030},
                "mix": {"equities": ".4", "fixed_income": ".6", "cash": 0},
            },
        ),
    )
    economy = EconomicAssumptions(
        name="Fixed",
        inflation={"central": ".02"},
        equities={"central": ".06"},
        fixed_income={"central": ".03"},
        cash={"central": ".01"},
    )
    components = tuple(
        select_profile(SavedPlanningDocument(inputs=p)).model_copy(
            update={
                "adult_mapping": (1, 2) if p.kind in {"work", "retirement"} else None
            }
        )
        for p in (work, spending, retirement, investments, economy)
    )
    return PlanningScenario(
        name="Synthetic",
        components=components,
        household={
            "reference_year": 2026,
            "adults": [
                {"name": "A", "age": 60, "wa_cares_exempt": False},
                {"name": "B", "age": 58, "wa_cares_exempt": False},
            ],
            "residence": [{"year": 2026, "location": "WA"}],
            "filing_status": "married_filing_jointly",
        },
        wealth={
            "snapshot": {
                "as_of": date(2026, 1, 1),
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
    )


def _change(plan, kind, **changes):
    data = plan.model_dump()
    next(c for c in data["components"] if c["inputs"]["kind"] == kind)["inputs"].update(
        changes
    )
    return PlanningScenario.model_validate(data)


def test_compile_plan_cashflow_growth_and_retirement_boundaries(timeline_plan):
    compiled = compile_plan(timeline_plan)
    assert compiled.money_year == 2026
    assert compiled.years == tuple(range(2026, 2033))
    assert compiled.retirement_years == (2028, 2030)
    assert compiled.end_years == (2030, 2032)
    first, second = compiled.annual[1].adults
    assert first.cash_pay == 110
    assert first.employee_401k_real == 11
    assert first.employer_retirement == Decimal("5.5")
    assert first.rsu_vesting_real == 10
    assert second.employee_401k_nominal == 20
    assert second.rsu_vesting_nominal == 20
    retired = compiled.annual[2].adults[0]
    assert retired.retired
    assert (
        retired.cash_pay == retired.employee_401k_real == retired.rsu_vesting_real == 0
    )
    assert [y.spending for y in compiled.annual] == [
        1000,
        1000,
        800,
        800,
        600,
        400,
        400,
    ]
    assert compiled.annual[4].adults[0].alive
    assert not compiled.annual[5].adults[0].alive


def test_compile_plan_relative_extras_survive_retirement_and_survivor(timeline_plan):
    plan = _change(
        timeline_plan,
        "spending",
        replacements=[
            {
                "label": "Move",
                "when": {"year": 2027},
                "budget": {"amount": {"central": 1100}},
            }
        ],
        adjustments=[
            {
                "label": "Care",
                "start": {"kind": "adult_1", "offset": 1},
                "end": {"year": 2032},
                "amount": {"central": 100},
            },
            {
                "label": "Trip",
                "start": {"kind": "both"},
                "frequency": "once",
                "amount": {"central": 50},
            },
        ],
    )
    annual = compile_plan(plan).annual
    assert [y.recurring_spending for y in annual] == [
        1000,
        1100,
        800,
        900,
        700,
        500,
        400,
    ]
    assert [y.one_time_spending for y in annual] == [0, 0, 0, 0, 50, 0, 0]


def test_compile_plan_person_mappings_control_pay_dates_and_single_retiree_budget(
    timeline_plan,
):
    data = timeline_plan.model_dump()
    for selection in data["components"]:
        if selection["inputs"]["kind"] in {"work", "retirement"}:
            selection["adult_mapping"] = (2, 1)
    compiled = compile_plan(PlanningScenario.model_validate(data))
    assert compiled.retirement_years == (2030, 2028)
    assert compiled.annual[0].adults[0].cash_pay == 200
    assert compiled.annual[2].adults[1].retired
    assert compiled.annual[2].recurring_spending == 800


def test_compile_plan_retiree_continue_uses_current_replacement_budget(timeline_plan):
    plan = _change(timeline_plan, "retirement", only_adult_1={"mode": "continue"})
    plan = _change(
        plan,
        "spending",
        replacements=[
            {
                "label": "Move",
                "when": {"kind": "adult_1"},
                "budget": {"amount": {"central": 1200}},
            }
        ],
    )
    annual = compile_plan(plan).annual
    assert annual[2].recurring_spending == 1200
    assert annual[4].recurring_spending == 600


def test_compile_plan_real_fixed_contributions_do_not_follow_salary_growth(
    timeline_plan,
):
    data = timeline_plan.model_dump()
    work = next(
        c["inputs"] for c in data["components"] if c["inputs"]["kind"] == "work"
    )
    work["adults"][0]["phases"][0].update(
        contribution_basis="real", contribution_rate=None, employee_401k=10
    )
    first = compile_plan(PlanningScenario.model_validate(data)).annual[1].adults[0]
    assert first.cash_pay == 110
    assert first.employee_401k_real == 10


@pytest.mark.parametrize(
    ("method", "midpoint"),
    [
        ("linear_then_hold", 0.5),
        ("log_then_hold", math.log(2.5) / math.log(4)),
        ("exponential_then_hold", 1 / 3),
    ],
)
def test_compile_plan_allocation_interpolation_and_final_hold(
    timeline_plan, method, midpoint
):
    plan = _change(timeline_plan, "investments", interpolation=method)
    annual = compile_plan(plan).annual
    assert annual[0].allocation == (0.8, 0.2, 0)
    assert annual[2].allocation == pytest.approx(
        (0.8 - 0.4 * midpoint, 0.2 + 0.4 * midpoint, 0)
    )
    assert annual[4].allocation == annual[6].allocation == (0.4, 0.6, 0)


def test_compile_plan_opening_year_events_remain_scheduled_without_extra_choice(
    timeline_plan,
):
    data = timeline_plan.model_dump()
    data["wealth"]["snapshot"]["as_of"] = date(2026, 7, 2)
    plan = _change(
        PlanningScenario.model_validate(data),
        "spending",
        adjustments=[
            {
                "label": "Move",
                "frequency": "once",
                "start": {"year": 2026},
                "amount": {"central": 50},
            }
        ],
    )
    remaining = compile_plan(plan).annual[0]
    assert remaining.year_fraction == 183 / 365
    assert remaining.recurring_spending == 1000
    assert remaining.one_time_spending == 50
    assert remaining.adults[0].cash_pay == 100


def test_compile_plan_partial_year_options_and_postretirement_exercise(timeline_plan):
    data = timeline_plan.model_dump()
    data["wealth"]["snapshot"]["as_of"] = date(2026, 7, 2)
    work = next(
        c["inputs"] for c in data["components"] if c["inputs"]["kind"] == "work"
    )
    work["adults"][0]["options"] = [
        {"year": year, "exercise": {"shares": 10, "strike": 2, "fair_market_value": 5}}
        for year in (2026, 2028)
    ]
    plan = PlanningScenario.model_validate(data)
    annual = compile_plan(plan).annual
    assert annual[0].adults[0].options[0].exercise.cost == 20
    assert annual[2].adults[0].options[0].exercise.cost == 20
    work["adults"][0]["options"] = [{"year": 2028, "award": {"vesting_shares": 10}}]
    with pytest.raises(ValueError, match="new option awards after retirement"):
        compile_plan(PlanningScenario.model_validate(data))


def test_compile_plan_missing_employment_interval_is_not_zero_income(timeline_plan):
    data = timeline_plan.model_dump()
    work = next(
        c["inputs"] for c in data["components"] if c["inputs"]["kind"] == "work"
    )
    work["adults"][0]["phases"][0]["end"] = 2027
    with pytest.raises(ValueError, match="ends before retirement"):
        compile_plan(PlanningScenario.model_validate(data))
    work["adults"][0]["phases"] += ({"start": 2027, "status": "not_working"},)
    assert (
        compile_plan(PlanningScenario.model_validate(data)).annual[1].adults[0].cash_pay
        == 0
    )


def test_compile_plan_negative_retirement_spending_rejected(timeline_plan):
    plan = _change(
        timeline_plan,
        "spending",
        adjustments=[
            {"label": "Reduction", "start": {"year": 2028}, "amount": {"central": -900}}
        ],
    )
    with pytest.raises(ValueError, match="negative in 2028"):
        compile_plan(plan)


def test_compile_plan_unknown_and_unresolved_inputs_rejected(timeline_plan):
    with pytest.raises(ValueError, match="exactly one"):
        compile_plan(PlanningScenario(name="Empty"))
    plan = _change(timeline_plan, "spending", money_year=2025)
    with pytest.raises(ValueError, match="reference years differ"):
        compile_plan(plan)
    plan = _change(
        timeline_plan,
        "investments",
        policy="constant",
        checkpoints=[
            {
                "when": {"year": 2027},
                "mix": {"equities": 1, "fixed_income": 0, "cash": 0},
            }
        ],
    )
    with pytest.raises(ValueError, match="first checkpoint is later"):
        compile_plan(plan)


def test_compile_plan_shares_need_a_price_model(timeline_plan):
    data = timeline_plan.model_dump()
    work = next(
        c["inputs"] for c in data["components"] if c["inputs"]["kind"] == "work"
    )
    work["adults"][0]["vesting"][0]["basis"] = "shares"
    with pytest.raises(ValueError, match="share-price model"):
        compile_plan(PlanningScenario.model_validate(data))


def test_compile_plan_residence_years_are_effective_dates(timeline_plan):
    data = timeline_plan.model_dump()
    data["household"]["residence"] += ({"year": 2028, "location": "CA"},)
    annual = compile_plan(PlanningScenario.model_validate(data)).annual
    assert [y.state for y in annual] == ["WA", "WA", "CA", "CA", "CA", "CA", "CA"]
    data["household"]["residence"] = [{"year": 2027, "location": "WA"}]
    with pytest.raises(ValueError, match="residence at 2026 is unknown"):
        compile_plan(PlanningScenario.model_validate(data))
