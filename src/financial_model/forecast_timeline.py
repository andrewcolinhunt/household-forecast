"""Compile reviewed planning inputs into an explicit annual household timeline.

Real monetary values retain the common profiles' ``money_year`` purchasing
power. Nominal contributions, vesting and option exercise prices stay nominal.
The ledger applies path inflation and prorates recurring flows by year_fraction;
one-time spending and option events are never prorated. Events scheduled in the
opening calendar year remain planned year-end expenses. A snapshot describes the
opening of its as-of date. Retirement stops recurring pay, contributions and
vesting; dated exercises of already-earned options remain scheduled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID

from financial_model.planning import (
    PROFILE_KINDS,
    AdultWork,
    ExpenseCategory,
    ExpenseFrequency,
    InvestmentAssumptions,
    OptionEvent,
    PlanningHousehold,
    PlanningScenario,
    RetirementAssumptions,
    SpendingAssumptions,
    WorkAssumptions,
    missing_details,
    plan_years,
)
from financial_model.schemas import ZERO


@dataclass(frozen=True)
class AnnualAdultInputs:
    """Full-year cash flows for one person, in household order."""

    age: int
    alive: bool
    retired: bool
    cash_pay: Decimal = ZERO
    employee_401k_real: Decimal = ZERO
    employee_401k_nominal: Decimal = ZERO
    employer_retirement: Decimal = ZERO
    rsu_vesting_real: Decimal = ZERO
    rsu_vesting_nominal: Decimal = ZERO
    options: tuple[OptionEvent, ...] = ()


@dataclass(frozen=True)
class AnnualExpense:
    """One planned expense, before path inflation and opening-year proration."""

    item_id: UUID
    label: str
    category: ExpenseCategory
    base_amount: Decimal
    real_growth: Decimal
    amount: Decimal
    frequency: ExpenseFrequency


@dataclass(frozen=True)
class AnnualInputs:
    """One calendar year's resolved schedules, before inflation and proration."""

    year: int
    year_fraction: float
    adults: tuple[AnnualAdultInputs, AnnualAdultInputs]
    expenses: tuple[AnnualExpense, ...]
    allocation: tuple[float, float, float]
    state: str

    @property
    def recurring_spending(self) -> Decimal:
        """Sum active recurring items after real growth, before inflation."""
        return sum(
            (item.amount for item in self.expenses if item.frequency == "recurring"),
            ZERO,
        )

    @property
    def one_time_spending(self) -> Decimal:
        """Sum dated payments without recurring-flow proration."""
        return sum(
            (item.amount for item in self.expenses if item.frequency == "once"), ZERO
        )

    @property
    def spending(self) -> Decimal:
        """Return total scheduled consumption before recurring-flow proration."""
        return self.recurring_spending + self.one_time_spending


@dataclass(frozen=True)
class CompiledPlan:
    """Resolved annual inputs with retirement and final-alive years by person."""

    years: tuple[int, ...]
    annual: tuple[AnnualInputs, ...]
    money_year: int
    retirement_years: tuple[int, int]
    end_years: tuple[int, int]


def compile_plan(plan: PlanningScenario) -> CompiledPlan:
    """Resolve a complete single combination without forecasting its balances.

    Args:
        plan: Reviewed Plan with one selection of every profile kind.

    Returns:
        Annual inputs through the later person's final planning-age year.

    Raises:
        ValueError: Inputs are incomplete, ambiguous or require unsupported
            share-price or sampled-lifespan assumptions.
    """
    profiles = {c.inputs.kind: c.inputs for c in plan.components}
    if len(plan.components) != len(PROFILE_KINDS) or plan.combination_count != 1:
        msg = "Compile exactly one selection of each Plan profile."
        raise ValueError(msg)
    details = missing_details(plan)
    if details:
        raise ValueError("Cannot forecast this Plan: " + " ".join(details))
    retirement = cast(RetirementAssumptions, profiles["retirement"])
    work = cast(WorkAssumptions, profiles["work"])
    spending = cast(SpendingAssumptions, profiles["spending"])
    investments = cast(InvestmentAssumptions, profiles["investments"])
    if retirement.mode != "ages":
        msg = "Forecasting requires fixed planning ages; mortality sampling is unsupported."
        raise ValueError(msg)
    assert plan.wealth is not None
    assert spending.money_year is not None
    as_of = plan.wealth.snapshot.as_of
    retirements, end_years = cast(
        tuple[tuple[int, int], tuple[int, int]], plan_years(plan)
    )
    years = tuple(range(as_of.year, max(end_years) + 1))
    work_mapping = _mapping(plan, "work")
    annual = []
    for year in years:
        fraction = _year_fraction(as_of) if year == as_of.year else 1.0
        adults = _adults(
            plan,
            work,
            work_mapping,
            year,
            retirements,
            end_years,
        )
        annual.append(
            AnnualInputs(
                year=year,
                year_fraction=fraction,
                adults=adults,
                expenses=annual_expenses(
                    spending, year, retirements, plan.household, end_years
                ),
                allocation=_allocation(
                    investments, year, retirements, plan.household, end_years
                ),
                state=_residence(plan, year),
            )
        )
    return CompiledPlan(
        years, tuple(annual), spending.money_year, retirements, end_years
    )


def _mapping(plan: PlanningScenario, kind: str) -> tuple[int, int]:
    selection = next(c for c in plan.components if c.inputs.kind == kind)
    assert selection.adult_mapping is not None
    return selection.adult_mapping


def _year_fraction(as_of: date) -> float:
    start, end = date(as_of.year, 1, 1), date(as_of.year + 1, 1, 1)
    return (end - as_of).days / (end - start).days


def _adults(
    plan: PlanningScenario,
    work: WorkAssumptions,
    mapping: tuple[int, int],
    year: int,
    retirements: tuple[int, int],
    end_years: tuple[int, int],
) -> tuple[AnnualAdultInputs, AnnualAdultInputs]:
    people = []
    reference = plan.household.reference_year
    assert reference is not None
    for index, person in enumerate(plan.household.adults):
        assert person.age is not None
        worker = work.adults[mapping.index(index + 1)]
        alive, retired = year <= end_years[index], year >= retirements[index]
        fields = _compensation(worker, year, alive and not retired)
        real_vesting, nominal_vesting = (
            _vesting(worker, year) if alive and not retired else (ZERO, ZERO)
        )
        events = tuple(e for e in worker.options if alive and e.year == year)
        if retired and any(e.award and e.award.vesting_shares for e in events):
            msg = f"{worker.name}: new option awards after retirement conflict with the retirement date."
            raise ValueError(msg)
        people.append(
            AnnualAdultInputs(
                age=person.age + year - reference,
                alive=alive,
                retired=retired,
                **fields,
                rsu_vesting_real=real_vesting,
                rsu_vesting_nominal=nominal_vesting,
                options=events,
            )
        )
    return people[0], people[1]


def _compensation(
    worker: AdultWork, year: int, working_age: bool
) -> dict[str, Decimal]:
    if not working_age:
        return {}
    phases = [
        p
        for p in worker.phases
        if p.start is not None and p.start <= year and (p.end is None or year < p.end)
    ]
    if len(phases) != 1:
        msg = f"{worker.name}: employment at {year} is unknown."
        raise ValueError(msg)
    phase = phases[0]
    if phase.status == "not_working":
        return {}
    assert phase.cash_pay is not None
    assert phase.real_growth.central is not None
    assert phase.start is not None
    assert phase.employer_retirement_rate is not None
    pay = phase.cash_pay * (1 + phase.real_growth.central) ** (year - phase.start)
    if phase.contribution_basis == "pay_fraction":
        assert phase.contribution_rate is not None
        contribution = pay * phase.contribution_rate
    else:
        assert phase.employee_401k is not None
        contribution = phase.employee_401k
    return {
        "cash_pay": pay,
        "employee_401k_nominal"
        if phase.contribution_basis == "nominal"
        else "employee_401k_real": contribution,
        "employer_retirement": pay * phase.employer_retirement_rate,
    }


def _vesting(worker: AdultWork, year: int) -> tuple[Decimal, Decimal]:
    real, nominal = ZERO, ZERO
    for vest in worker.vesting:
        assert vest.start is not None
        assert vest.end is not None
        assert vest.amount is not None
        if not vest.start <= year < vest.end:
            continue
        if vest.basis == "shares" and vest.amount:
            msg = f"{worker.name}: share-denominated vesting needs an explicit share-price model."
            raise ValueError(msg)
        if vest.basis == "real":
            real += vest.amount
        else:
            nominal += vest.amount
    return real, nominal


def annual_expenses(
    spending: SpendingAssumptions,
    year: int,
    retirements: tuple[int, int],
    household: PlanningHousehold,
    end_years: tuple[int, int],
) -> tuple[AnnualExpense, ...]:
    """Evaluate validated schedules before path inflation and opening-year proration.

    Args:
        spending: Reviewed expense schedules with resolved, increasing boundaries.
        year: Calendar year to evaluate.
        retirements: Retirement years in household adult order.
        household: People whose ages anchor expense boundaries.
        end_years: Final-alive years in household adult order.

    Returns:
        Active items with both base amounts and amounts after real growth.
    """
    assert spending.money_year is not None
    expenses = []
    for item in spending.expenses:
        end = item.end.resolve(retirements, household, end_years) if item.end else None
        if end is not None and year >= end:
            continue
        levels = []
        for level in item.levels:
            start = level.when.resolve(retirements, household, end_years)
            assert start is not None
            active = year == start if item.frequency == "once" else year >= start
            if active:
                levels.append(level)
        if not levels:
            continue
        amount = levels[-1].amount.central
        assert amount is not None
        expenses.append(
            AnnualExpense(
                item.id,
                item.label,
                item.category,
                amount,
                item.real_growth,
                amount * (1 + item.real_growth) ** (year - spending.money_year),
                item.frequency,
            )
        )
    return tuple(expenses)


def _allocation(
    investments: InvestmentAssumptions,
    year: int,
    retirements: tuple[int, int],
    household: PlanningHousehold,
    end_years: tuple[int, int],
) -> tuple[float, float, float]:
    checkpoints = [
        (c.when.resolve(retirements, household, end_years), c.mix)
        for c in investments.checkpoints
    ]
    first_year, first_mix = checkpoints[0]
    assert first_year is not None
    assert first_mix is not None
    if year < first_year:
        msg = f"Investment allocation at {year} is unknown; the first checkpoint is later."
        raise ValueError(msg)
    previous_year, previous_mix = first_year, first_mix
    for target_year, target_mix in checkpoints[1:]:
        assert target_year is not None
        assert target_mix is not None
        if year < target_year:
            t = (year - previous_year) / (target_year - previous_year)
            if investments.interpolation == "log_then_hold":
                t = math.log1p(3 * t) / math.log(4)
            elif investments.interpolation == "exponential_then_hold":
                t = (4**t - 1) / 3
            start = (
                previous_mix.equities,
                previous_mix.fixed_income,
                previous_mix.cash,
            )
            end = (target_mix.equities, target_mix.fixed_income, target_mix.cash)
            values = tuple(
                float(a) + t * float(b - a) for a, b in zip(start, end, strict=True)
            )
            return values[0], values[1], values[2]
        previous_year, previous_mix = target_year, target_mix
    return (
        float(previous_mix.equities),
        float(previous_mix.fixed_income),
        float(previous_mix.cash),
    )


def _residence(plan: PlanningScenario, year: int) -> str:
    residences = [
        r for r in plan.household.residence if r.year is not None and r.year <= year
    ]
    if not residences:
        msg = f"Household residence at {year} is unknown."
        raise ValueError(msg)
    residence = max(residences, key=lambda r: cast(int, r.year))
    assert residence.location is not None
    return residence.location
