"""Annual cash and asset accounting independent of the UI and persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from financial_model import __version__
from financial_model.allocations import allocate_sources
from financial_model.schemas import ZERO, Scenario, ScenarioResult, TaxResult
from financial_model.taxes import calculate_taxes


def _rsu_tax_funding(scenario: Scenario, taxes: TaxResult) -> Decimal:
    """Allocate incremental wage tax to a sale of at most all vested RSUs."""
    rsus = sum((adult.rsu_vesting for adult in scenario.adults), ZERO)
    if not rsus or not scenario.rsu_taxes_from_shares:
        return ZERO
    without_options = (
        calculate_taxes(scenario, include_iso=False)
        if any(adult.options.spread for adult in scenario.adults)
        else taxes
    )
    cash_only = calculate_taxes(scenario, include_rsus=False, include_iso=False)
    return min(rsus, max(without_options.total - cash_only.total, ZERO))


def evaluate_scenario(scenario: Scenario) -> ScenarioResult:
    """Evaluate annual cash flow, taxes, retirement saving, and acquired assets.

    Args:
        scenario: Submitted household inputs within supported tax coverage.

    Returns:
        A reconciled, timestamped result identifying its exact inputs.
    """
    taxes = calculate_taxes(scenario)
    funding = _rsu_tax_funding(scenario, taxes)
    amounts = _annual_amounts(scenario)
    cash_tax = taxes.total - funding
    retained_rsus = amounts["gross_rsus"] - funding
    remaining = (
        amounts["gross_cash"]
        - cash_tax
        - amounts["employee_401k"]
        - amounts["option_cost"]
        - amounts["living_cost"]
    )
    return ScenarioResult.model_validate(
        dict(
            **amounts,
            input_digest=scenario.digest(),
            tax_year=scenario.tax_year,
            state=scenario.state,
            calculated_at=datetime.now(UTC),
            app_version=__version__,
            taxes=taxes,
            rsu_tax_funding=funding,
            retained_rsus=retained_rsus,
            cash_funded_tax=cash_tax,
            cash_added=remaining,
            allocations=allocate_sources(
                scenario.allocation_plan,
                cash=remaining,
                retirement=amounts["employee_401k"] + amounts["employer_retirement"],
                rsus=retained_rsus,
                option_cost=amounts["option_cost"],
            ),
        )
    )


def _annual_amounts(scenario: Scenario) -> dict[str, Decimal]:
    """Aggregate the two adults' amounts without introducing tax assumptions."""
    adults = scenario.adults
    return {
        "gross_cash": sum((adult.cash_pay for adult in adults), ZERO),
        "gross_rsus": sum((adult.rsu_vesting for adult in adults), ZERO),
        "employee_401k": sum((adult.employee_401k for adult in adults), ZERO),
        "employer_retirement": sum(
            (adult.employer_retirement for adult in adults), ZERO
        ),
        "option_cost": sum((adult.options.cost for adult in adults), ZERO),
        "option_value": sum((adult.options.value for adult in adults), ZERO),
        "living_cost": scenario.living_costs.annual_total,
    }
