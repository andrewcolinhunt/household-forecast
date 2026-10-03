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

from financial_model.planning import (
    PROFILE_KINDS,
    AdultWork,
    InvestmentAssumptions,
    OptionEvent,
    PlanningScenario,
    RetirementAssumptions,
    SpendingAssumptions,
    WorkAssumptions,
    missing_details,
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
class AnnualInputs:
    """One calendar year's resolved schedules, before inflation and proration."""

    year: int
    year_fraction: float
    adults: tuple[AnnualAdultInputs, AnnualAdultInputs]
    recurring_spending: Decimal
    one_time_spending: Decimal
    allocation: tuple[float, float, float]
    state: str

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
    retirements, end_years = _person_years(plan, retirement)
    years = tuple(range(as_of.year, max(end_years) + 1))
    work_mapping = _mapping(plan, "work")
    retirement_mapping = _mapping(plan, "retirement")
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
        recurring, once = _spending(
            spending,
            retirement,
            retirement_mapping,
            year,
            retirements,
            adults,
            fraction,
        )
        annual.append(
            AnnualInputs(
                year=year,
                year_fraction=fraction,
                adults=adults,
                recurring_spending=recurring,
                one_time_spending=once,
                allocation=_allocation(investments, year, retirements),
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


def _person_years(
    plan: PlanningScenario, retirement: RetirementAssumptions
) -> tuple[tuple[int, int], tuple[int, int]]:
    reference = plan.household.reference_year
    assert reference is not None
    retirements = [0, 0]
    ends = [0, 0]
    for adult, slot in zip(
        retirement.adults, _mapping(plan, "retirement"), strict=True
    ):
        age = plan.household.adults[slot - 1].age
        assert age is not None
        assert adult.age.central is not None
        assert adult.retirement.central is not None
        retirements[slot - 1] = adult.retirement.central
        ends[slot - 1] = reference + adult.age.central - age
    return (retirements[0], retirements[1]), (ends[0], ends[1])


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


def _spending(
    spending: SpendingAssumptions,
    retirement: RetirementAssumptions,
    mapping: tuple[int, int],
    year: int,
    retirements: tuple[int, int],
    adults: tuple[AnnualAdultInputs, AnnualAdultInputs],
    fraction: float,
) -> tuple[Decimal, Decimal]:
    budget = spending.baseline
    replacements = sorted(
        spending.replacements, key=lambda r: cast(int, r.when.resolve(retirements))
    )
    for replacement in replacements:
        when = replacement.when.resolve(retirements)
        assert when is not None
        if when <= year:
            budget = replacement.budget
    if sum(a.alive for a in adults) == 1:
        assert retirement.survivor is not None
        budget = retirement.survivor
    elif all(a.retired for a in adults):
        budget = retirement.both_retired
    elif any(a.retired for a in adults):
        retired_slot = next(i + 1 for i, adult in enumerate(adults) if adult.retired)
        choice = (retirement.only_adult_1, retirement.only_adult_2)[
            mapping.index(retired_slot)
        ]
        if choice.mode == "replace":
            assert choice.budget is not None
            budget = choice.budget
    assert budget.amount.central is not None
    recurring, once = budget.amount.central, ZERO
    for adjustment in spending.adjustments:
        start = adjustment.start.resolve(retirements)
        end = adjustment.end.resolve(retirements) if adjustment.end else None
        assert start is not None
        assert adjustment.amount.central is not None
        if adjustment.frequency == "once":
            if year == start:
                once += adjustment.amount.central
        elif start <= year and (end is None or year < end):
            recurring += adjustment.amount.central
    if recurring * Decimal(str(fraction)) + once < 0:
        msg = f"Spending is negative in {year} after applying the retirement budget and adjustments."
        raise ValueError(msg)
    return recurring, once


def _allocation(
    investments: InvestmentAssumptions, year: int, retirements: tuple[int, int]
) -> tuple[float, float, float]:
    checkpoints = [
        (c.when.resolve(retirements), c.mix) for c in investments.checkpoints
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
