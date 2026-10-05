"""Batched household taxes under an explicit frozen-real-2026 planning policy.

This adapter reuses the application's pinned and corrected PolicyEngine rules.
It does not assert that those rules describe enacted law in later years. All
amounts, including normally nonindexed thresholds and temporary deductions, are
held fixed in 2026 purchasing power. California parameters and Washington's
capital-gains deduction include the pinned engine's 2026 projections.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray

from financial_model.taxes import _ENGINE_LOCK, _tax_system

FloatArray = NDArray[np.float64]
POLICY_YEAR = 2026
FORECAST_TAX_POLICY = "frozen_real_2026_v1"
FORECAST_TAX_ASSUMPTION = (
    "Taxes use pinned 2026 federal, California and Washington rules in constant "
    "purchasing power. All dollar thresholds rise with the simulated price level, "
    "including thresholds that current law does not index. Temporary 2026 "
    "deductions continue, and later changes, including Washington's 2028 income "
    "tax, are excluded. California parameters and Washington's capital-gains "
    "deduction include engine projections. This is a planning assumption, not "
    "a projection of enacted future tax law. Standard deductions, full-year "
    "CA or WA residence, ordinary taxable retirement distributions, and listed "
    "income sources only; no ISO lifecycle, itemization, foreign income, "
    "tax-exempt investments, or short-term gains are modeled. Federal and CA "
    "long-term loss carryovers are tracked separately; opening capital-loss and "
    "AMT-credit carryovers are assumed zero. No dependent-care or renter credit "
    "is inferred from general spending. WA-allocated losses are "
    "consumed first when federal losses are used, a conservative allocation "
    "assumption after a move. Carryovers must be reset at death unless ownership "
    "is known. Rental profit is an explicit nonnegative input, with no automatic "
    "depreciation, passive-loss allowance, or qualified business income deduction."
)


@dataclass(frozen=True)
class ForecastTaxResult:
    """Nominal household taxes for each path; AMT is already in income taxes."""

    federal_income_tax: FloatArray
    state_income_tax: FloatArray
    employee_payroll_tax: FloatArray
    taxable_social_security: FloatArray
    capital_loss_carryforward: FloatArray
    ca_capital_loss_carryforward: FloatArray
    wa_capital_loss_carryforward: FloatArray

    @property
    def total(self) -> FloatArray:
        """Return combined liability, including any refundable credit balance."""
        return (
            self.federal_income_tax + self.state_income_tax + self.employee_payroll_tax
        )


def _vector(value: ArrayLike, size: int, name: str) -> FloatArray:
    """Normalize scalar or per-path inputs and reject nonfinite amounts."""
    result = np.asarray(value, dtype=np.float64)
    if result.ndim == 0:
        result = np.full(size, result.item(), dtype=np.float64)
    if result.shape != (size,) or not np.isfinite(result).all():
        message = f"{name} must contain one finite amount per forecast path."
        raise ValueError(message)
    return result


def _simulation(size: int, adult_count: int, dependent_count: int) -> Any:
    """Construct independent household populations without per-path Python dicts."""
    from policyengine_us import Simulation

    system = _tax_system()
    populations = system.instantiate_entities()
    members = adult_count + dependent_count
    for key, population in populations.items():
        count = size * members if key == "person" else size
        if key == "marital_unit":
            count = size * (1 + dependent_count)
        population.count = count
        population.ids = np.arange(count)
        if key == "person":
            continue
        membership = np.repeat(np.arange(size), members)
        if key == "marital_unit":
            roles = np.r_[
                np.zeros(adult_count, dtype=int), np.arange(1, dependent_count + 1)
            ]
            membership = (
                np.arange(size)[:, None] * (1 + dependent_count) + roles
            ).ravel()
        population.members_entity_id = membership
        population.members_role = np.full(size * members, population.entity.MEMBER)
    return Simulation(tax_benefit_system=system, populations=populations)


def _person_inputs(
    wages: FloatArray,
    contributions: FloatArray,
    ordinary_income: FloatArray,
    rental_income: FloatArray,
    investment_income: FloatArray,
    gains: FloatArray,
    qualified_dividends: FloatArray,
    social_security: FloatArray,
    ages: tuple[int, int],
    alive: tuple[bool, bool],
    dependent_ages: tuple[int, ...],
    inflation: FloatArray,
    rent: FloatArray,
) -> dict[str, NDArray[Any]]:
    """Map only living people into each household and keep wages per earner."""
    live = np.flatnonzero(alive)
    size = len(wages)
    members = len(live) + len(dependent_ages)
    people = np.zeros((size, members))
    people[:, : len(live)] = wages[:, live] / inflation[:, None]
    saving = np.zeros_like(people)
    saving[:, : len(live)] = contributions[:, live] / inflation[:, None]
    inputs: dict[str, NDArray[Any]] = {
        "age": np.tile([ages[index] for index in live] + list(dependent_ages), size),
        "is_tax_unit_head": np.tile(np.arange(members) == 0, size),
        "is_tax_unit_spouse": np.tile(
            (np.arange(members) == 1) & (len(live) == 2), size
        ),
        "is_tax_unit_dependent": np.tile(np.arange(members) >= len(live), size),
        "employment_income": people.ravel(),
        "traditional_401k_contributions": saving.ravel(),
        "rental_income_would_be_qualified": np.zeros(size * members, dtype=bool),
    }
    for name, value in {
        "taxable_private_pension_income": ordinary_income,
        "rental_income": rental_income,
        "taxable_interest_income": investment_income,
        "long_term_capital_gains": gains,
        "qualified_dividend_income": qualified_dividends,
        "social_security_retirement": social_security,
        "rent": rent,
    }.items():
        amount = np.zeros_like(people)
        amount[:, 0] = value / inflation
        inputs[name] = amount.ravel()
    return inputs


def _result(
    simulation: Any,
    inflation: FloatArray,
    members: int,
    carryovers: tuple[FloatArray, FloatArray, FloatArray],
) -> ForecastTaxResult:
    """Sum employee payroll over people and restore nominal dollars once."""
    size = len(inflation)

    def household(variable: str) -> FloatArray:
        amount = np.asarray(simulation.calculate(variable, POLICY_YEAR), dtype=float)
        if simulation.tax_benefit_system.variables[variable].entity.key == "person":
            amount = amount.reshape(size, members).sum(axis=1)
        return amount * inflation

    payroll = sum(
        (
            household(variable)
            for variable in (
                "employee_social_security_tax",
                "employee_medicare_tax",
                "additional_medicare_tax",
                "ca_employee_state_disability_insurance_contribution",
                "wa_employee_paid_leave_contribution",
                "wa_employee_long_term_care_contribution",
            )
        ),
        np.zeros(size),
    )
    return ForecastTaxResult(
        federal_income_tax=household("income_tax"),
        state_income_tax=household("state_income_tax"),
        employee_payroll_tax=payroll,
        taxable_social_security=household("tax_unit_taxable_social_security"),
        capital_loss_carryforward=carryovers[0],
        ca_capital_loss_carryforward=carryovers[1],
        wa_capital_loss_carryforward=carryovers[2],
    )


def _state_gains(
    simulation: Any, monetary: dict[str, FloatArray], inflation: FloatArray, state: str
) -> None:
    """Apply state-specific carryovers before any state tax dependencies evaluate."""
    gains = monetary["gains"] / inflation
    if state == "CA":
        federal = np.maximum(
            gains - monetary["capital_loss_carryforward"] / inflation, -3000
        )
        california = np.maximum(
            gains
            + (monetary["ca_hsa_gains"] - monetary["ca_capital_loss_carryforward"])
            / inflation,
            -3000,
        )
        difference = california - federal + monetary["ca_hsa_income"] / inflation
        # Schedule CA adjusts federal AGI for the separately calculated loss.
        subtractions = simulation.calculate("ca_agi_subtractions", POLICY_YEAR)
        simulation.set_input(
            "ca_agi_subtractions",
            POLICY_YEAR,
            subtractions + np.maximum(-difference, 0),
        )
        simulation.set_input("ca_additions", POLICY_YEAR, np.maximum(difference, 0))
    else:
        p = (
            _tax_system()
            .parameters(str(POLICY_YEAR))
            .gov.states.wa.tax.income.capital_gains
        )
        taxable = np.maximum(
            gains
            - monetary["wa_capital_loss_carryforward"] / inflation
            - p.deductions.standard,
            0,
        )
        simulation.set_input(
            "wa_capital_gains_tax", POLICY_YEAR, p.rate.incremental.calc(taxable)
        )


def _loss_carryovers(
    simulation: Any,
    monetary: dict[str, FloatArray],
    inflation: FloatArray,
    wages: FloatArray,
    contributions: FloatArray,
    state: str,
    joint: bool,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Apply IRS/CA loss worksheets, preserving deductions unused at low income.

    References: IRS Schedule D capital loss carryover worksheet and California
    Schedule D (540) instructions. All modeled gains and losses are long term.
    """
    gains = monetary["gains"]
    federal_loss = np.maximum(monetary["capital_loss_carryforward"] - gains, 0)
    ca_loss = np.maximum(
        monetary["ca_capital_loss_carryforward"] - gains - monetary["ca_hsa_gains"], 0
    )
    annual_deduction = np.minimum(federal_loss, 3000 * inflation)
    # The worksheet requires taxable income *before* flooring it at zero.
    unclipped_income = (
        simulation.calculate("adjusted_gross_income", POLICY_YEAR)
        - simulation.calculate("taxable_income_deductions", POLICY_YEAR)
        - simulation.calculate("exemptions", POLICY_YEAR)
    ) * inflation
    used = np.minimum(
        annual_deduction, np.maximum(unclipped_income + annual_deduction, 0)
    )
    federal_carry = federal_loss - used
    # California asks new residents to recompute prior carryovers as though they
    # had always resided there, so maintain this counterfactual while in WA too.
    ca_deduction = (
        _tax_system()
        .parameters(str(POLICY_YEAR))
        .gov.states.ca.tax.income.deductions.standard.amount[
            "JOINT" if joint else "SINGLE"
        ]
    )
    ca_income_before_loss = (
        (wages - contributions).sum(axis=1)
        + monetary["ordinary_income"]
        + monetary["rental_income"]
        + monetary["investment_income"]
        + monetary["qualified_dividends"]
        + monetary["ca_hsa_income"]
        - ca_deduction * inflation
    )
    ca_used = np.minimum(
        np.minimum(ca_loss, 3000 * inflation), np.maximum(ca_income_before_loss, 0)
    )
    ca_carry = ca_loss - ca_used
    new_losses = np.maximum(-gains, 0)
    wa_pool = monetary["wa_capital_loss_carryforward"] + (
        new_losses if state == "WA" else 0
    )
    all_losses = monetary["capital_loss_carryforward"] + new_losses
    wa_carry = np.minimum(
        federal_carry, np.maximum(wa_pool - (all_losses - federal_carry), 0)
    )
    return federal_carry, ca_carry, wa_carry


def yearly_tax(
    *,
    wages: ArrayLike,
    contributions: ArrayLike,
    ordinary_income: ArrayLike = 0,
    rental_income: ArrayLike = 0,
    investment_income: ArrayLike = 0,
    gains: ArrayLike = 0,
    qualified_dividends: ArrayLike = 0,
    social_security: ArrayLike = 0,
    ca_hsa_income: ArrayLike = 0,
    ca_hsa_gains: ArrayLike = 0,
    capital_loss_carryforward: ArrayLike = 0,
    ca_capital_loss_carryforward: ArrayLike = 0,
    wa_capital_loss_carryforward: ArrayLike = 0,
    ages: tuple[int, int],
    alive: tuple[bool, bool] = (True, True),
    state: str,
    inflation: ArrayLike = 1,
    dependent_ages: tuple[int, ...] = (),
    eligible_care_expense: ArrayLike = 0,
    ca_renter_credit_eligible: bool = False,
    rent: ArrayLike = 0,
    wa_cares_exempt: tuple[bool, bool] = (False, False),
) -> ForecastTaxResult:
    """Calculate independent paths using the frozen-real-2026 tax policy.

    Args:
        wages: Nominal gross wages, including taxable equity pay, shape (paths, 2).
        contributions: Actual pretax 401(k) contributions with the same shape.
        ordinary_income: Taxable pensions and eligible pretax withdrawals per path.
        rental_income: Reviewed nonnegative taxable rental profit, without QBI deduction.
        investment_income: Taxable interest and nonqualified dividends per path.
        gains: Net realized long-term capital gains per path, possibly negative.
        qualified_dividends: Qualified dividends per path.
        social_security: Combined gross Social Security benefits per path.
        ca_hsa_income: Interest and dividends inside HSAs, taxable only by CA.
        ca_hsa_gains: Realized HSA gains using CA basis, taxable only by CA.
        capital_loss_carryforward: Unused federal long-term losses, nominal dollars.
        ca_capital_loss_carryforward: Separately tracked CA long-term losses.
        wa_capital_loss_carryforward: WA-allocated portion of federal unused losses.
        ages: Ages at year end; only living adults form the current tax unit.
        alive: Whether each original adult is alive in the modeled year.
        state: Full-year residence, CA or WA.
        inflation: Price level relative to the policy's 2026 dollars, per path.
        dependent_ages: Qualifying children, with no income, aged zero through 17.
        eligible_care_expense: Annual expenses qualifying for dependent care credit.
        ca_renter_credit_eligible: Whether modeled rent qualifies for CA's credit.
        rent: Annual rent, used only for the explicitly selected CA renter credit.
        wa_cares_exempt: Per-adult approved WA Cares exemptions.

    Returns:
        Federal, state, payroll and taxable Social Security arrays in nominal dollars.

    Raises:
        ValueError: For unsupported state, nonfinite inputs, invalid shapes or saving.
        RuntimeError: If installed tax rules do not match the application's pins.

    The caller applies retirement access rules and carries account balances. A
    survivor files single immediately; no estate, final joint return or qualifying
    surviving spouse treatment is inferred. Carryovers are nominal account state;
    do not inflate them between years. The caller resets losses at death unless it
    can attribute them to the surviving owner.
    """
    wages = np.asarray(wages, dtype=float)
    contributions = np.asarray(contributions, dtype=float)
    if wages.ndim != 2 or wages.shape[1] != 2 or wages.shape != contributions.shape:
        message = "Wages and contributions must have shape (paths, 2)."
        raise ValueError(message)
    if (
        not len(wages)
        or not np.isfinite(wages).all()
        or not np.isfinite(contributions).all()
    ):
        message = (
            "Wages and contributions must be finite and include at least one path."
        )
        raise ValueError(message)
    if state not in {"CA", "WA"}:
        message = "Forecast taxes require CA or WA and two original adult roles."
        raise ValueError(message)
    if any(
        living and (age < 18 or age > 130)
        for age, living in zip(ages, alive, strict=True)
    ) or any(age < 0 or age > 17 for age in dependent_ages):
        message = "Unsupported adult or dependent age for forecast taxes."
        raise ValueError(message)
    size = len(wages)
    price = _vector(inflation, size, "Inflation")
    if (price <= 0).any():
        message = "Inflation price levels must be positive."
        raise ValueError(message)
    if (
        (wages < 0).any()
        or (contributions < 0).any()
        or (contributions > np.minimum(wages, 24_500 * price[:, None]) + 0.001).any()
    ):
        message = (
            "Forecast pretax saving must fit wages and the indexed 2026 base limit."
        )
        raise ValueError(message)
    if (wages[:, np.logical_not(alive)] != 0).any():
        message = "Deceased adults cannot receive forecast employment wages."
        raise ValueError(message)
    monetary = {
        name: _vector(value, size, name)
        for name, value in {
            "ordinary_income": ordinary_income,
            "rental_income": rental_income,
            "investment_income": investment_income,
            "gains": gains,
            "qualified_dividends": qualified_dividends,
            "social_security": social_security,
            "ca_hsa_income": ca_hsa_income,
            "ca_hsa_gains": ca_hsa_gains,
            "capital_loss_carryforward": capital_loss_carryforward,
            "ca_capital_loss_carryforward": ca_capital_loss_carryforward,
            "wa_capital_loss_carryforward": wa_capital_loss_carryforward,
            "eligible_care_expense": eligible_care_expense,
            "rent": rent,
        }.items()
    }
    if any(
        (value < 0).any()
        for name, value in monetary.items()
        if name not in {"gains", "ca_hsa_gains"}
    ):
        message = "Income and expenses must be nonnegative except net capital gains."
        raise ValueError(message)
    if not any(alive):
        return ForecastTaxResult(*(np.zeros(size) for _ in range(7)))
    if (
        monetary["wa_capital_loss_carryforward"]
        > monetary["capital_loss_carryforward"] + 0.001
    ).any():
        message = "WA-allocated loss carryovers cannot exceed federal loss carryovers."
        raise ValueError(message)
    with _ENGINE_LOCK, np.errstate(divide="ignore", invalid="ignore"):
        simulation = _simulation(size, sum(alive), len(dependent_ages))
        person = _person_inputs(
            wages,
            contributions,
            **{
                key: monetary[key] - monetary["capital_loss_carryforward"]
                if key == "gains"
                else monetary[key]
                for key in (
                    "ordinary_income",
                    "rental_income",
                    "investment_income",
                    "gains",
                    "qualified_dividends",
                    "social_security",
                )
            },
            ages=ages,
            alive=alive,
            dependent_ages=dependent_ages,
            inflation=price,
            rent=monetary["rent"] if ca_renter_credit_eligible else np.zeros(size),
        )
        for name, value in person.items():
            simulation.set_input(name, POLICY_YEAR, value)
        for name, value in {
            "state_code": np.full(size, state),
            "filing_status": np.full(size, "JOINT" if all(alive) else "SINGLE"),
            "tax_unit_itemizes": np.zeros(size, dtype=bool),
            "ca_itemized_deductions": np.zeros(size),
            "tax_unit_childcare_expenses": monetary["eligible_care_expense"] / price,
        }.items():
            simulation.set_input(name, POLICY_YEAR, value)
        _state_gains(simulation, monetary, price, state)
        members = sum(alive) + len(dependent_ages)
        if any(wa_cares_exempt):
            variable = "wa_employee_long_term_care_contribution"
            care = (
                simulation.calculate(variable, POLICY_YEAR)
                .reshape(size, members)
                .copy()
            )
            for column, original in enumerate(np.flatnonzero(alive)):
                if wa_cares_exempt[original]:
                    care[:, column] = 0
            simulation.set_input(variable, POLICY_YEAR, care.ravel())
        carryovers = _loss_carryovers(
            simulation, monetary, price, wages, contributions, state, all(alive)
        )
        return _result(simulation, price, members, carryovers)
