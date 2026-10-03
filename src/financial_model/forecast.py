"""Annual account accounting for implementable household forecast policies.

Balances open on the snapshot date. Opening portfolios rebalance, earn the
remaining year's return, and then receive year-end cash flows. Recurring flows
are prorated; explicitly remaining one-time events are not. Taxes annualize a
partial year's income and then prorate its liability, an approximation without
historical year-to-date tax records. All taxable sales use average basis and
long-term treatment; dividends are qualified. Roth withdrawals require age 60
and assume the five-year qualification requirement has been met.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version
from typing import cast

import numpy as np
from numpy.typing import NDArray

from financial_model.forecast_models import (
    ForecastPoint,
    ForecastRequest,
    ForecastSummary,
    SavedForecast,
)
from financial_model.forecast_paths import generate_paths
from financial_model.forecast_rules import (
    contribution_limits,
    rmd_divisor,
    rmd_start_age,
)
from financial_model.forecast_tax import (
    FORECAST_TAX_POLICY,
    ForecastTaxResult,
    yearly_tax,
)
from financial_model.forecast_timeline import AnnualInputs, CompiledPlan, compile_plan
from financial_model.planning import (
    EconomicAssumptions,
    InvestmentAssumptions,
    PlanningScenario,
    WorkAssumptions,
)

FloatArray = NDArray[np.float64]
Trace = list[dict[str, FloatArray | int]]
Progress = Callable[[str, float], None]


@dataclass(frozen=True)
class ForecastCalculation:
    """Portable summary and in-memory annual traces for every simulated path."""

    document: SavedForecast
    traces: tuple[Trace, ...]

    def inspect(
        self, scenario_index: int, path_index: int
    ) -> list[dict[str, float | int]]:
        """Return one realized annual ledger without replacing it with percentiles."""
        if not 0 <= scenario_index < len(self.traces):
            msg = "Forecast scenario index is out of range."
            raise IndexError(msg)
        if not 0 <= path_index < self.document.summaries[scenario_index].paths:
            msg = "Forecast path index is out of range."
            raise IndexError(msg)
        return _inspect(self.traces[scenario_index], path_index)


def _inspect(trace: Trace, path: int) -> list[dict[str, float | int]]:
    return [
        {
            key: value if isinstance(value, int) else float(value[path])
            for key, value in row.items()
        }
        for row in trace
    ]


def forecast_issues(request: ForecastRequest) -> tuple[str, ...]:
    """Collect incomplete or unsupported material inputs before calculating."""
    issues: list[str] = []
    details = request.details
    if not details.assumptions_confirmed:
        issues.append(
            "Confirm the forecast's tax, timing, withdrawal and account assumptions."
        )
    if details.stock_dividend_yield is None or details.bond_interest_yield is None:
        issues.append(
            "Enter stock dividend and bond interest yields, including explicit zeros."
        )
    for income in details.incomes:
        if any(
            getattr(income, field) is None
            for field in (
                "social_security",
                "pension",
                "health_before_65",
                "health_from_65",
            )
        ):
            issues.append(
                f"Adult {income.owner}: enter benefits and additional retirement health costs, including zeros."
            )
        if income.pension and income.pension_survivor_fraction is None:
            issues.append(f"Adult {income.owner}: enter the pension survivor fraction.")
    for plan in request.plans:
        try:
            compiled = compile_plan(plan, first_year_events=details.first_year_events)
        except ValueError as exc:
            issues.append(f"{plan.name}: {exc}")
            continue
        issues.extend(_plan_issues(request, plan, compiled))
    return tuple(dict.fromkeys(issues))


def _plan_issues(
    request: ForecastRequest, plan: PlanningScenario, compiled: CompiledPlan
) -> list[str]:
    issues = []
    assert plan.wealth is not None
    snapshot = plan.wealth.snapshot
    metadata = {a.account_id: a for a in request.details.accounts}
    debts = {d.debt_id: d for d in request.details.debts}
    has_ca = any(y.state == "CA" for y in compiled.annual)
    has_hsa = any(a.tax_treatment == "hsa" and a.balance for a in snapshot.accounts)
    if has_hsa:
        for income in request.details.incomes:
            if (
                income.hsa_eligible_before_65 is None
                or income.hsa_eligible_from_65 is None
            ):
                issues.append(
                    f"Adult {income.owner}: enter the HSA-eligible portion of additional health costs."
                )
    if compiled.money_year != 2026 or snapshot.as_of.year != 2026:
        issues.append(
            "Forecasting currently requires 2026 money reference years and a 2026 Wealth snapshot."
        )
    for account in snapshot.accounts:
        detail = metadata.get(account.id)
        if (
            account.balance
            and account.tax_treatment in {"tax_deferred", "roth", "hsa"}
            and (detail is None or detail.owner is None)
        ):
            issues.append(f"{account.label}: choose an account owner.")
        if (
            (
                account.tax_treatment == "taxable"
                or (account.tax_treatment == "hsa" and has_ca)
            )
            and account.balance
            and account.invested_in != "cash"
            and (detail is None or detail.cost_basis is None)
        ):
            issues.append(f"{account.label}: enter taxable cost basis.")
        if (
            detail
            and detail.cost_basis is not None
            and account.allocation
            and float(detail.cost_basis)
            < float(account.balance or 0) * float(account.allocation.cash)
        ):
            issues.append(
                f"{account.label}: total cost basis cannot be less than the cash balance."
            )
        if (
            account.tax_treatment == "tax_deferred"
            and account.balance
            and detail
            and detail.owner
        ):
            owner = detail.owner - 1
            for annual in compiled.annual:
                person = annual.adults[owner]
                if person.alive and person.age >= rmd_start_age(
                    annual.year - person.age
                ):
                    if annual.year_fraction < 1:
                        issues.append(
                            "An RMD-eligible part-year start needs the prior December balance and distributions already taken; use a January 1 snapshot."
                        )
                    if (
                        annual.adults[1 - owner].alive
                        and person.age - annual.adults[1 - owner].age > 10
                    ):
                        issues.append(
                            "RMDs with a spouse more than ten years younger require a different beneficiary table, not yet supported."
                        )
    for debt in snapshot.debts:
        debt_detail = debts.get(debt.id)
        if debt.amount_owed and (
            debt_detail is None
            or debt_detail.annual_payment is None
            or debt_detail.interest_rate is None
        ):
            issues.append(f"{debt.label}: enter annual debt payment and interest rate.")
    contribution_owners = {
        i
        for year in compiled.annual
        for i, adult in enumerate(year.adults)
        if adult.employee_401k_real
        or adult.employee_401k_nominal
        or adult.employer_retirement
    }
    for annual in compiled.annual:
        if annual.state not in {"CA", "WA"}:
            issues.append(
                f"Unsupported forecast residence: {annual.state}; choose CA or WA."
            )
        for adult, income in zip(annual.adults, request.details.incomes, strict=True):
            if any(event.exercise and event.exercise.shares for event in adult.options):
                issues.append(
                    "ISO exercise and subsequent sale tax accounting is not supported in forecasts yet."
                )
            if (
                income.social_security
                and adult.age >= income.claiming_age
                and adult.cash_pay
            ):
                issues.append(
                    "Social Security while still earning wages requires an earnings-test model; choose a later claiming age or retirement date."
                )
        for owner in contribution_owners:
            adult = annual.adults[owner]
            if (
                adult.alive
                and adult.age >= rmd_start_age(annual.year - adult.age)
                and annual.adults[1 - owner].alive
                and adult.age - annual.adults[1 - owner].age > 10
            ):
                issues.append(
                    "RMDs with a spouse more than ten years younger require a different beneficiary table, not yet supported."
                )
    economy = cast(EconomicAssumptions, _profile(plan, "economy"))
    settings = request.settings
    investment = cast(InvestmentAssumptions, _profile(plan, "investments"))
    assert investment.saving is not None
    if (
        any(
            a.rsu_vesting_real or a.rsu_vesting_nominal
            for y in compiled.annual
            for a in y.adults
        )
        and sum(r.rsu_rate for r in investment.saving.rows) != 1
    ):
        issues.append(
            "Allocate 100% of retained RSUs across the saved investment goals."
        )
    if settings.mode == "monte_carlo":
        if any(v is None for v in economy.volatility):
            issues.append(
                "Enter all four economic volatilities; use zero for fixed rates."
            )
        elif any(economy.volatility) and economy.correlation is None:
            issues.append("Enter the correlation matrix for the economic simulation.")
    if settings.mode in {"historical", "bootstrap"} and (
        not settings.history or not settings.history_source.strip()
    ):
        issues.append(
            "Historical methods require joint annual observations and a source description."
        )
    return issues


def _profile(plan: PlanningScenario, kind: str) -> object:
    return next(c.inputs for c in plan.components if c.inputs.kind == kind)


def calculate_forecast(
    request: ForecastRequest, progress: Progress | None = None
) -> ForecastCalculation:
    """Run matching economic futures through each household's annual ledger.

    Args:
        request: Immutable plans, supplemental inputs and simulation settings.
        progress: Optional callback receiving a description and fraction complete.

    Returns:
        Saved reproducible summaries and inspectable individual simulation paths.

    Raises:
        ValueError: Material inputs are missing or unsupported.
        RuntimeError: Account reconciliation or numerical tax solving fails.
    """
    issues = forecast_issues(request)
    if issues:
        raise ValueError(" ".join(issues))
    compiled = tuple(
        compile_plan(p, first_year_events=request.details.first_year_events)
        for p in request.plans
    )
    years = tuple(range(compiled[0].years[0], max(c.years[-1] for c in compiled) + 1))
    traces = []
    for index, (plan, timeline) in enumerate(zip(request.plans, compiled, strict=True)):
        settings = request.settings
        paths = generate_paths(
            cast(EconomicAssumptions, _profile(plan, "economy")),
            years,
            count=settings.count,
            seed=settings.seed,
            mode=settings.mode,
            history=settings.history,
            stress=settings.stress,
            block_years=settings.block_years,
        )
        traces.append(_forecast(request, plan, timeline, paths, index, progress))
    summaries = tuple(
        _summarize(plan, timeline, trace, traces[0])
        for plan, timeline, trace in zip(request.plans, compiled, traces, strict=True)
    )
    document = SavedForecast(
        request=request,
        numpy_version=np.__version__,
        tax_engine_version=(
            f"policyengine-us {version('policyengine-us')}; "
            f"policyengine-core {version('policyengine-core')}; {FORECAST_TAX_POLICY}"
        ),
        summaries=summaries,
    )
    return ForecastCalculation(document, tuple(traces))


@dataclass
class _Accounts:
    holdings: FloatArray
    basis: FloatArray
    labels: list[str]
    treatment: list[str]
    owners: list[int | None]
    cash: int
    brokerage: int
    retirement: tuple[int, int]
    ca_hsa_income: FloatArray
    ca_hsa_gains: FloatArray

    def total(self) -> FloatArray:
        return self.holdings.sum(axis=(1, 2))


def _accounts(
    request: ForecastRequest, plan: PlanningScenario, count: int
) -> _Accounts:
    assert plan.wealth is not None
    metadata = {a.account_id: a for a in request.details.accounts}
    holdings, bases, labels, owners = [], [], [], []
    treatments: list[str] = []
    for account in plan.wealth.snapshot.accounts:
        mix = account.allocation
        fractions = (
            [float(mix.equities), float(mix.fixed_income), float(mix.cash)]
            if mix
            else [0, 0, 1]
        )
        detail = metadata.get(account.id)
        balance = float(account.balance or 0)
        basis = (
            float(detail.cost_basis)
            if detail and detail.cost_basis is not None
            else balance
        )
        holdings.append(np.asarray(fractions) * balance)
        cash_basis = balance * fractions[2]
        invested_basis = basis - cash_basis
        invested_mix = np.asarray(fractions[:2])
        if invested_mix.sum():
            invested_mix = invested_mix / invested_mix.sum() * invested_basis
        bases.append(np.r_[invested_mix, cash_basis])
        labels.append(f"Account: {account.label} ({str(account.id)[:8]})")
        treatments.append(account.tax_treatment)
        owners.append(detail.owner - 1 if detail and detail.owner else None)
    cash, brokerage = len(holdings), len(holdings) + 1
    retirement = (len(holdings) + 2, len(holdings) + 3)
    for name, treatment, owner in (
        ("Forecast cash", "taxable", None),
        ("Forecast investments", "taxable", None),
        ("Adult 1 contributions", "tax_deferred", 0),
        ("Adult 2 contributions", "tax_deferred", 1),
    ):
        holdings.append(np.zeros(3))
        bases.append(np.zeros(3))
        labels.append(name)
        treatments.append(treatment)
        owners.append(owner)
    return _Accounts(
        np.broadcast_to(holdings, (count, len(holdings), 3)).copy(),
        np.broadcast_to(bases, (count, len(bases), 3)).copy(),
        labels,
        treatments,
        owners,
        cash,
        brokerage,
        retirement,
        np.zeros(count),
        np.zeros(count),
    )


def _sell(
    accounts: _Accounts, index: int, asset: int, amount: FloatArray
) -> tuple[FloatArray, FloatArray]:
    balance = accounts.holdings[:, index, asset]
    sale = np.minimum(np.maximum(amount, 0), balance)
    fraction = np.divide(sale, balance, out=np.zeros_like(sale), where=balance > 0)
    basis = accounts.basis[:, index, asset] * fraction
    accounts.holdings[:, index, asset] -= sale
    accounts.basis[:, index, asset] -= basis
    gains = (
        sale - basis if accounts.treatment[index] == "taxable" else np.zeros_like(sale)
    )
    if accounts.treatment[index] == "hsa":
        accounts.ca_hsa_gains += sale - basis
    return sale, gains


def _buy(accounts: _Accounts, index: int, values: FloatArray) -> None:
    accounts.holdings[:, index, :] += values
    accounts.basis[:, index, :] += values


def _rebalance(
    accounts: _Accounts, allocation: tuple[float, float, float], reserve: FloatArray
) -> FloatArray:
    gains = np.zeros(len(reserve))
    protected = reserve.copy()
    for index, treatment in enumerate(accounts.treatment):
        if index == accounts.cash:
            protected -= np.minimum(protected, accounts.holdings[:, index, 2])
            continue
        balance = accounts.holdings[:, index].sum(axis=1)
        held = (
            np.minimum(protected, accounts.holdings[:, index, 2])
            if treatment == "taxable"
            else np.zeros_like(reserve)
        )
        protected -= held
        target = (balance - held)[:, None] * np.asarray(allocation)
        target[:, 2] += held
        purchases = np.maximum(target - accounts.holdings[:, index], 0)
        for asset in range(3):
            _, realized = _sell(
                accounts,
                index,
                asset,
                np.maximum(accounts.holdings[:, index, asset] - target[:, asset], 0),
            )
            gains += realized
        _buy(accounts, index, purchases)
    return gains


def _growth(
    accounts: _Accounts, rates: FloatArray, fraction: float, request: ForecastRequest
) -> tuple[FloatArray, FloatArray, FloatArray]:
    before = accounts.total()
    dividend = np.zeros(len(rates))
    interest = np.zeros(len(rates))
    yields = np.column_stack(
        (
            np.full(
                len(rates), float(request.details.stock_dividend_yield or 0) * fraction
            ),
            np.full(
                len(rates), float(request.details.bond_interest_yield or 0) * fraction
            ),
            np.maximum((1 + rates[:, 2]) ** fraction - 1, 0),
        )
    )
    for index, treatment in enumerate(accounts.treatment):
        if treatment in {"taxable", "hsa"}:
            distribution = accounts.holdings[:, index] * yields
            if treatment == "taxable":
                dividend += distribution[:, 0]
                interest += distribution[:, 1:].sum(axis=1)
            else:
                accounts.ca_hsa_income += distribution.sum(axis=1)
            accounts.basis[:, index] += distribution
    accounts.holdings *= ((1 + rates[:, :3]) ** fraction)[:, None, :]
    for index, treatment in enumerate(accounts.treatment):
        if treatment in {"taxable", "hsa"}:
            accounts.basis[:, index, 2] = accounts.holdings[:, index, 2]
    return accounts.total() - before, dividend, interest


def _withdraw(
    accounts: _Accounts, need: FloatArray, annual: AnnualInputs
) -> tuple[FloatArray, FloatArray, FloatArray]:
    remaining = np.maximum(need, 0).copy()
    gains = np.zeros_like(remaining)
    ordinary = np.zeros_like(remaining)
    order = [(i, 2) for i, t in enumerate(accounts.treatment) if t == "taxable"]
    order += [
        (i, asset)
        for i, t in enumerate(accounts.treatment)
        if t == "taxable"
        for asset in (1, 0)
    ]
    for treatment in ("tax_deferred", "roth"):
        order += [
            (i, asset)
            for i, t in enumerate(accounts.treatment)
            if t == treatment
            and accounts.owners[i] is not None
            and annual.adults[cast(int, accounts.owners[i])].age >= 60
            for asset in (2, 1, 0)
        ]
    for index, asset in order:
        sale, gain = _sell(accounts, index, asset, remaining)
        remaining -= sale
        gains += gain
        if accounts.treatment[index] == "tax_deferred":
            ordinary += sale
    return np.maximum(need, 0) - remaining, gains, ordinary


def _mandatory(
    accounts: _Accounts, annual: AnnualInputs, opening: FloatArray, health: FloatArray
) -> tuple[FloatArray, FloatArray]:
    required = np.zeros(len(health))
    hsa = np.zeros(len(health))
    for index, treatment in enumerate(accounts.treatment):
        owner = accounts.owners[index]
        if owner is None:
            continue
        adult = annual.adults[owner]
        if treatment == "tax_deferred" and adult.age >= rmd_start_age(
            annual.year - adult.age
        ):
            need = opening[:, index] / rmd_divisor(adult.age) * annual.year_fraction
        elif treatment == "hsa":
            need = np.maximum(health - hsa, 0)
        else:
            continue
        for asset in (2, 1, 0):
            sale, _ = _sell(accounts, index, asset, need)
            need -= sale
            if treatment == "hsa":
                hsa += sale
            else:
                required += sale
    return required, hsa


def _benefits(
    request: ForecastRequest, annual: AnnualInputs, price: FloatArray
) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray]:
    pensions, social, health, eligible_health = 0.0, [], 0.0, 0.0
    alive = tuple(a.alive for a in annual.adults)
    for adult, detail in zip(annual.adults, request.details.incomes, strict=True):
        social.append(
            float(detail.social_security or 0)
            if adult.age >= detail.claiming_age
            else 0.0
        )
        pension = (
            float(detail.pension or 0) if adult.age >= detail.pension_start_age else 0.0
        )
        pensions += pension * (
            1 if adult.alive else float(detail.pension_survivor_fraction or 0)
        )
        if adult.alive and adult.retired:
            health += float(
                (detail.health_before_65 if adult.age < 65 else detail.health_from_65)
                or 0
            )
            eligible_health += float(
                (
                    detail.hsa_eligible_before_65
                    if adult.age < 65
                    else detail.hsa_eligible_from_65
                )
                or 0
            )
    survivor = alive.index(True)
    survivor_claimed = (
        annual.adults[survivor].age >= request.details.incomes[survivor].claiming_age
    )
    ss = sum(social) if all(alive) else max(social) if survivor_claimed else 0.0
    factor = price * annual.year_fraction
    return pensions * factor, ss * factor, health * factor, eligible_health * factor


def _pay(
    annual: AnnualInputs, price: FloatArray
) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray]:
    factor = price[:, None] * annual.year_fraction
    cash = factor * np.asarray([float(a.cash_pay) for a in annual.adults])
    rsu = factor * np.asarray([float(a.rsu_vesting_real) for a in annual.adults])
    rsu += annual.year_fraction * np.asarray(
        [float(a.rsu_vesting_nominal) for a in annual.adults]
    )
    contributions = factor * np.asarray(
        [float(a.employee_401k_real) for a in annual.adults]
    )
    contributions += annual.year_fraction * np.asarray(
        [float(a.employee_401k_nominal) for a in annual.adults]
    )
    employer = factor * np.asarray(
        [float(a.employer_retirement) for a in annual.adults]
    )
    for index, adult in enumerate(annual.adults):
        employee_limit, total_limit = contribution_limits(adult.age)
        contributions[:, index] = np.minimum(
            contributions[:, index],
            np.minimum(cash[:, index], factor[:, 0] * employee_limit),
        )
        employer[:, index] = np.minimum(
            employer[:, index],
            np.maximum(
                np.minimum(factor[:, 0] * total_limit, cash[:, index] + rsu[:, index])
                - contributions[:, index],
                0,
            ),
        )
    return cash, rsu, contributions, employer


def _tax(
    plan: PlanningScenario,
    annual: AnnualInputs,
    price: FloatArray,
    wages: FloatArray,
    contributions: FloatArray,
    ordinary: FloatArray,
    interest: FloatArray,
    gains: FloatArray,
    dividend: FloatArray,
    social: FloatArray,
    carry: tuple[FloatArray, FloatArray, FloatArray],
    ca_hsa_income: FloatArray | float = 0,
    ca_hsa_gains: FloatArray | float = 0,
) -> ForecastTaxResult:
    fraction = annual.year_fraction
    ages = tuple(a.age for a in annual.adults)
    alive = tuple(a.alive for a in annual.adults)
    children = tuple(
        annual.year - c.birth_year
        for c in plan.household.children
        if c.birth_year is not None and 0 <= annual.year - c.birth_year <= 17
    )
    return yearly_tax(
        wages=wages / fraction,
        contributions=contributions / fraction,
        ordinary_income=ordinary / fraction,
        investment_income=interest / fraction,
        gains=gains / fraction,
        qualified_dividends=dividend / fraction,
        social_security=social / fraction,
        ages=cast(tuple[int, int], ages),
        alive=cast(tuple[bool, bool], alive),
        state=annual.state,
        inflation=price,
        dependent_ages=children,
        wa_cares_exempt=cast(
            tuple[bool, bool],
            tuple(bool(a.wa_cares_exempt) for a in plan.household.adults),
        ),
        capital_loss_carryforward=carry[0],
        ca_capital_loss_carryforward=carry[1],
        wa_capital_loss_carryforward=carry[2],
        ca_hsa_income=ca_hsa_income / fraction,
        ca_hsa_gains=ca_hsa_gains / fraction,
    )


def _surplus(
    accounts: _Accounts,
    amount: FloatArray,
    reserve: FloatArray,
    investment: InvestmentAssumptions,
    annual: AnnualInputs,
    price: FloatArray,
) -> None:
    cash = sum(
        (
            accounts.holdings[:, i, 2]
            for i, t in enumerate(accounts.treatment)
            if t == "taxable"
        ),
        np.zeros_like(amount),
    )
    reserve_topup = np.minimum(amount, np.maximum(reserve - cash, 0))
    accounts.holdings[:, accounts.cash, 2] += reserve_topup
    accounts.basis[:, accounts.cash, 2] += reserve_topup
    remaining = amount - reserve_topup
    assert investment.saving is not None
    fixed = [r for r in investment.saving.rows if r.cash_amount is not None]
    remainder = next(r for r in investment.saving.rows if r.cash_amount is None)
    for row in [*fixed, remainder]:
        allocation = (
            remaining
            if row.cash_amount is None
            else np.minimum(
                remaining, float(row.cash_amount) * price * annual.year_fraction
            )
        )
        index = (
            accounts.brokerage
            if row.goal.id == investment.saving.retirement_goal_id
            else accounts.cash
        )
        mix = np.asarray(
            annual.allocation if index == accounts.brokerage else (0, 0, 1)
        )
        _buy(accounts, index, allocation[:, None] * mix)
        remaining -= allocation


def _forecast(
    request: ForecastRequest,
    plan: PlanningScenario,
    timeline: CompiledPlan,
    paths: FloatArray,
    scenario_index: int,
    progress: Progress | None,
) -> Trace:
    count = len(paths)
    accounts = _accounts(request, plan, count)
    price = np.ones(count)
    unpaid_tax = np.zeros(count)
    carry = (np.zeros(count), np.zeros(count), np.zeros(count))
    assert plan.wealth is not None
    debt_details = {d.debt_id: d for d in request.details.debts}
    debts = np.broadcast_to(
        [float(d.amount_owed or 0) for d in plan.wealth.snapshot.debts],
        (count, len(plan.wealth.snapshot.debts)),
    ).copy()
    payments = np.asarray(
        [
            float(debt_details[d.id].annual_payment or 0) if d.amount_owed else 0
            for d in plan.wealth.snapshot.debts
        ]
    )
    debt_rates = np.asarray(
        [
            float(debt_details[d.id].interest_rate or 0) if d.amount_owed else 0
            for d in plan.wealth.snapshot.debts
        ]
    )
    investment = cast(InvestmentAssumptions, _profile(plan, "investments"))
    work = cast(WorkAssumptions, _profile(plan, "work"))
    trace: Trace = []
    previously_alive: tuple[bool, ...] = (True, True)
    for index, annual in enumerate(timeline.annual):
        if progress:
            progress(
                f"{plan.name}: {annual.year}",
                (scenario_index + index / len(timeline.annual)) / len(request.plans),
            )
        alive = tuple(a.alive for a in annual.adults)
        if alive != previously_alive:
            survivor = alive.index(True)
            accounts.owners = [
                survivor if owner is not None and not alive[owner] else owner
                for owner in accounts.owners
            ]
            carry = (np.zeros(count), np.zeros(count), np.zeros(count))
        previously_alive = alive
        accounts.ca_hsa_income.fill(0)
        accounts.ca_hsa_gains.fill(0)
        opening_accounts = accounts.holdings.sum(axis=2).copy()
        opening_assets = accounts.total()
        reserve = float(investment.reserved_cash or 0) * price
        gains = (
            _rebalance(accounts, annual.allocation, reserve)
            if investment.rebalancing == "annual"
            else np.zeros(count)
        )
        growth, dividend, interest = _growth(
            accounts, paths[:, index], annual.year_fraction, request
        )
        pensions, social, health, eligible_health = _benefits(request, annual, price)
        cash_pay, rsu, contributions, employer = _pay(annual, price)
        for owner, target in enumerate(accounts.retirement):
            _buy(
                accounts,
                target,
                (contributions[:, owner] + employer[:, owner])[:, None]
                * np.asarray(annual.allocation),
            )
        mandatory, hsa = _mandatory(accounts, annual, opening_accounts, eligible_health)
        ordinary = pensions + mandatory
        cash = (
            cash_pay.sum(axis=1)
            - contributions.sum(axis=1)
            + pensions
            + social
            + mandatory
            + hsa
        )
        spending = (
            float(annual.recurring_spending) * annual.year_fraction
            + float(annual.one_time_spending)
        ) * price + health
        debt_interest = debts * ((1 + debt_rates) ** annual.year_fraction - 1)
        debts += debt_interest
        due_debt = np.minimum(debts, payments * annual.year_fraction)
        wage_total = cash_pay + rsu
        tax = _tax(
            plan,
            annual,
            price,
            wage_total,
            contributions,
            ordinary,
            interest,
            gains,
            dividend,
            social,
            carry,
            accounts.ca_hsa_income,
            accounts.ca_hsa_gains,
        )
        if work.rsu_taxes_from_shares and rsu.any():
            without = _tax(
                plan,
                annual,
                price,
                cash_pay,
                contributions,
                ordinary,
                interest,
                gains,
                dividend,
                social,
                carry,
                accounts.ca_hsa_income,
                accounts.ca_hsa_gains,
            )
            withholding = np.minimum(
                np.maximum((tax.total - without.total) * annual.year_fraction, 0),
                rsu.sum(axis=1),
            )
        else:
            withholding = np.zeros(count)
        cash += withholding
        retained_rsu = rsu.sum(axis=1) - withholding
        _buy(accounts, accounts.brokerage, retained_rsu[:, None] * np.array([1, 0, 0]))
        tax, cash, gains, ordinary, withdrawals = _fund(
            accounts,
            plan,
            annual,
            price,
            wage_total,
            contributions,
            ordinary,
            interest,
            gains,
            dividend,
            social,
            carry,
            cash,
            spending + due_debt.sum(axis=1),
            unpaid_tax,
            tax,
        )
        carry = tuple(
            getattr(tax, field) * annual.year_fraction
            for field in (
                "capital_loss_carryforward",
                "ca_capital_loss_carryforward",
                "wa_capital_loss_carryforward",
            )
        )
        tax_due = tax.total * annual.year_fraction
        total_tax = unpaid_tax + tax_due
        tax_paid = np.minimum(
            np.maximum(cash, 0), np.maximum(total_tax, 0)
        ) + np.minimum(total_tax, 0)
        cash -= tax_paid
        new_tax_shortfall = np.maximum(
            tax_due - np.maximum(tax_paid - unpaid_tax, 0), 0
        )
        unpaid_tax = np.maximum(total_tax - tax_paid, 0)
        actual_debt = np.zeros(count)
        for debt_index in range(debts.shape[1]):
            paid = np.minimum(np.maximum(cash, 0), due_debt[:, debt_index])
            cash -= paid
            debts[:, debt_index] -= paid
            actual_debt += paid
        spent = np.minimum(np.maximum(cash, 0), spending)
        cash -= spent
        shortfall = (
            spending - spent + due_debt.sum(axis=1) - actual_debt + new_tax_shortfall
        )
        _surplus(accounts, np.maximum(cash, 0), reserve, investment, annual, price)
        end_price = price * (1 + paths[:, index, 3]) ** annual.year_fraction
        ending_assets = accounts.total()
        income = (
            cash_pay.sum(axis=1)
            + rsu.sum(axis=1)
            + pensions
            + social
            + employer.sum(axis=1)
        )
        expected = opening_assets + income + growth - spent - tax_paid - actual_debt
        if not np.allclose(ending_assets, expected, atol=0.02, rtol=1e-10):
            msg = f"Account reconciliation failed in {annual.year}."
            raise RuntimeError(msg)
        row: dict[str, FloatArray | int] = {
            "year": annual.year,
            "opening_assets": opening_assets,
            "income": income,
            "cash_pay": cash_pay.sum(axis=1),
            "rsu_income": rsu.sum(axis=1),
            "rsu_tax_withholding": withholding,
            "investment_gain": growth,
            "spending_planned": spending,
            "spending_paid": spent,
            "taxes": tax_due,
            "federal_income_tax": tax.federal_income_tax * annual.year_fraction,
            "state_income_tax": tax.state_income_tax * annual.year_fraction,
            "employee_payroll_tax": tax.employee_payroll_tax * annual.year_fraction,
            "tax_paid": tax_paid,
            "unpaid_tax_liability": unpaid_tax.copy(),
            "debt_interest": debt_interest.sum(axis=1),
            "debt_payment": actual_debt,
            "employee_contributions": contributions.sum(axis=1),
            "employer_contributions": employer.sum(axis=1),
            "pensions": pensions,
            "social_security": social,
            "health_spending": health,
            "required_distributions": mandatory,
            "hsa_withdrawals": hsa,
            "withdrawals": withdrawals + mandatory + hsa,
            "realized_gains": gains,
            "assets": ending_assets,
            "debt": debts.sum(axis=1),
            "net_worth": ending_assets - debts.sum(axis=1) - unpaid_tax,
            "unfunded": shortfall,
            "unfunded_living": spending - spent,
            "unfunded_debt_payment": due_debt.sum(axis=1) - actual_debt,
            "new_unfunded_tax": new_tax_shortfall,
            "price_index": end_price,
            "equity_return": paths[:, index, 0].copy(),
            "bond_return": paths[:, index, 1].copy(),
            "cash_return": paths[:, index, 2].copy(),
            "inflation": paths[:, index, 3].copy(),
            "taxable_cost_basis": sum(
                (
                    accounts.basis[:, i].sum(axis=1)
                    for i, treatment in enumerate(accounts.treatment)
                    if treatment == "taxable"
                ),
                np.zeros(count),
            ),
            "capital_loss_carryforward": carry[0].copy(),
            "ca_capital_loss_carryforward": carry[1].copy(),
            "wa_capital_loss_carryforward": carry[2].copy(),
            "ca_hsa_income": accounts.ca_hsa_income.copy(),
            "ca_hsa_gains": accounts.ca_hsa_gains.copy(),
        }
        _account_trace(row, accounts, annual, reserve)
        trace.append(row)
        price = end_price
    return trace


def _fund(
    accounts: _Accounts,
    plan: PlanningScenario,
    annual: AnnualInputs,
    price: FloatArray,
    wages: FloatArray,
    contributions: FloatArray,
    ordinary: FloatArray,
    interest: FloatArray,
    gains: FloatArray,
    dividend: FloatArray,
    social: FloatArray,
    carry: tuple[FloatArray, FloatArray, FloatArray],
    cash: FloatArray,
    spending: FloatArray,
    unpaid_tax: FloatArray,
    tax: ForecastTaxResult,
) -> tuple[ForecastTaxResult, FloatArray, FloatArray, FloatArray, FloatArray]:
    deficit = spending + unpaid_tax + tax.total * annual.year_fraction - cash
    finished = deficit <= 0.005
    if finished.all():
        return tax, cash, gains, ordinary, np.zeros_like(cash)
    holdings, basis = accounts.holdings.copy(), accounts.basis.copy()
    available = sum(
        (
            holdings[:, index].sum(axis=1)
            for index, treatment in enumerate(accounts.treatment)
            if treatment == "taxable"
            or (
                treatment in {"tax_deferred", "roth"}
                and accounts.owners[index] is not None
                and annual.adults[cast(int, accounts.owners[index])].age >= 60
            )
        ),
        np.zeros_like(cash),
    )
    low, low_error = np.zeros_like(cash), -deficit
    high, high_error = available.copy(), np.full_like(cash, np.nan)
    candidate = np.minimum(np.maximum(deficit, 0), available)
    previous, previous_error = np.zeros_like(cash), -deficit
    for _ in range(40):
        # Trials must not irrevocably consume a tax-free account or sell excess
        # shares when crossing a tax bracket or realizing a deductible loss.
        accounts.holdings[:] = holdings
        accounts.basis[:] = basis
        sale, extra_gains, extra_ordinary = _withdraw(accounts, candidate, annual)
        tax = _tax(
            plan,
            annual,
            price,
            wages,
            contributions,
            ordinary + extra_ordinary,
            interest,
            gains + extra_gains,
            dividend,
            social,
            carry,
            accounts.ca_hsa_income,
            accounts.ca_hsa_gains,
        )
        error = cash + sale - spending - unpaid_tax - tax.total * annual.year_fraction
        low, low_error = (
            np.where(error < 0, sale, low),
            np.where(error < 0, error, low_error),
        )
        high, high_error = (
            np.where(error >= 0, sale, high),
            np.where(error >= 0, error, high_error),
        )
        # Credits can change in discrete steps, leaving no withdrawal with an
        # exact zero residual. Accept the least funded cent-sized bracket.
        narrow = np.isfinite(high_error) & (high_error >= 0) & (high - low < 0.005)
        finished |= (
            (np.abs(error) < 0.005)
            | ((sale + 0.005 >= available) & (error < 0))
            | (narrow & (error >= 0))
        )
        if finished.all():
            return (
                tax,
                cash + sale,
                gains + extra_gains,
                ordinary + extra_ordinary,
                sale,
            )
        slope = np.divide(
            error - previous_error,
            sale - previous,
            out=np.ones_like(cash),
            where=np.abs(sale - previous) > 1e-8,
        )
        extrapolated = sale - error / np.clip(slope, 0.05, 3)
        interpolated = low - np.divide(
            low_error * (high - low),
            high_error - low_error,
            out=np.zeros_like(cash),
            where=np.isfinite(high_error) & (high_error != low_error),
        )
        next_candidate = np.clip(
            np.where(np.isfinite(high_error), interpolated, extrapolated), low, high
        )
        stalled = np.abs(next_candidate - sale) < 1e-8
        near_edge = (next_candidate - low < 0.1 * (high - low)) | (
            high - next_candidate < 0.1 * (high - low)
        )
        bisect = stalled | (np.isfinite(high_error) & near_edge & (slope > 3))
        next_candidate = np.where(bisect, (low + high) / 2, next_candidate)
        next_candidate = np.where(narrow, high, next_candidate)
        previous, previous_error = sale, error
        candidate = np.where(finished, sale, next_candidate)
    msg = f"Withdrawal taxes did not converge in {annual.year}."
    raise RuntimeError(msg)


def _account_trace(
    row: dict[str, FloatArray | int],
    accounts: _Accounts,
    annual: AnnualInputs,
    reserve: FloatArray,
) -> None:
    accessible = np.zeros(len(reserve))
    cash = np.zeros(len(reserve))
    for index, treatment in enumerate(accounts.treatment):
        balance = accounts.holdings[:, index].sum(axis=1)
        row[accounts.labels[index]] = balance.copy()
        owner = accounts.owners[index]
        if treatment == "taxable" or (
            treatment in {"tax_deferred", "roth"}
            and owner is not None
            and annual.adults[owner].age >= 60
        ):
            accessible += balance
        if treatment == "taxable":
            cash += accounts.holdings[:, index, 2]
    row["accessible"] = accessible
    row["cash"] = cash
    row["reserve_breach"] = (cash + 0.01 < reserve).astype(float)


def _summarize(
    plan: PlanningScenario, timeline: CompiledPlan, trace: Trace, baseline: Trace
) -> ForecastSummary:
    points = []
    gaps = np.stack(
        [
            cast(FloatArray, row["unfunded"]) / cast(FloatArray, row["price_index"])
            for row in trace
        ],
        axis=1,
    )
    unpaid = np.stack(
        [cast(FloatArray, row["unpaid_tax_liability"]) for row in trace], axis=1
    )
    years_with_gap = (gaps > 0.01) | (unpaid > 0.01)
    baseline_years = {cast(int, row["year"]): row for row in baseline}
    for row in trace:
        price = cast(FloatArray, row["price_index"])
        values = cast(FloatArray, row["net_worth"]) / price
        base = baseline_years.get(cast(int, row["year"]))
        # A comparison ends when either household planning horizon ends.
        difference = (
            (
                values
                - cast(FloatArray, base["net_worth"])
                / cast(FloatArray, base["price_index"])
            )
            if base is not None
            else None
        )
        lower, median, upper = np.percentile(values, [10, 50, 90])
        dlower, dmedian, dupper = (
            np.percentile(difference, [10, 50, 90])
            if difference is not None
            else (None, None, None)
        )
        points.append(
            ForecastPoint(
                year=cast(int, row["year"]),
                lower=lower,
                median=median,
                upper=upper,
                accessible_median=float(
                    np.median(cast(FloatArray, row["accessible"]) / price)
                ),
                cash_median=float(np.median(cast(FloatArray, row["cash"]) / price)),
                debt_median=float(np.median(cast(FloatArray, row["debt"]) / price)),
                shortfall_probability=float(
                    np.mean(
                        (cast(FloatArray, row["unfunded"]) > 0.01)
                        | (cast(FloatArray, row["unpaid_tax_liability"]) > 0.01)
                    )
                ),
                reserve_breach_probability=float(np.mean(row["reserve_breach"])),
                median_difference=dmedian,
                lower_difference=dlower,
                upper_difference=dupper,
            )
        )
    failures = np.flatnonzero(years_with_gap.any(axis=0))
    return ForecastSummary(
        name=plan.name,
        money_year=timeline.money_year,
        retirement_years=timeline.retirement_years,
        points=tuple(points),
        funded_fraction=float(np.mean(~years_with_gap.any(axis=1))),
        first_shortfall_year=timeline.years[int(failures[0])]
        if len(failures)
        else None,
        mean_unfunded_total=float(gaps.sum(axis=1).mean()),
        mean_unfunded_years=float(years_with_gap.sum(axis=1).mean()),
        paths=len(gaps),
        example=tuple(_inspect(trace, 0)),
    )
