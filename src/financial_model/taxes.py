"""One local PolicyEngine adapter for the explicitly supported household cases."""

from __future__ import annotations

from functools import cache
from importlib.metadata import version
from threading import RLock
from typing import Any, ClassVar

import numpy as np

from financial_model.schemas import Scenario, TaxResult, dollars

ENGINE_VERSION = "2.15.1"
CORE_VERSION = "3.32.7"
CORRECTION_VERSION = "1"
_ENGINE_LOCK = RLock()


@cache
def _tax_system() -> Any:
    """Initialize the pinned rules once; corrections apply only to this instance."""
    from policyengine_core.reforms import Reform
    from policyengine_us.system import system
    from policyengine_us.variables.gov.irs.tax.federal_income.alternative_minimum_tax.income.amt_income import (
        amt_income as BaseAMTIncome,
    )
    from policyengine_us.variables.gov.states.ca.tax.income.alternative_minimum_tax.amti.ca_amti_adjustments import (
        ca_amti_adjustments as BaseCAAdjustments,
    )

    if (
        version("policyengine-us") != ENGINE_VERSION
        or version("policyengine-core") != CORE_VERSION
    ):
        message = "Tax engine version changed. Validate the reference cases before updating the pins."
        raise RuntimeError(message)

    # PolicyEngine identifies variables by their class names. See IRS Form 6251,
    # 2025 Schedule P (540), and the private evaluation for these disconnected inputs.
    class amt_income(BaseAMTIncome):  # type: ignore[misc]  # Untyped engine extension API.
        adds: ClassVar[list[str]] = [*BaseAMTIncome.adds, "amt_non_agi_income"]

    class ca_amti_adjustments(BaseCAAdjustments):  # type: ignore[misc]
        def formula(tax_unit: Any, period: Any, parameters: Any) -> Any:
            return BaseCAAdjustments.formula(tax_unit, period, parameters) + tax_unit(
                "amt_non_agi_income", period
            )

    class CoverageCorrections(Reform):  # type: ignore[misc]
        def apply(self) -> None:
            self.update_variable(amt_income)
            self.update_variable(ca_amti_adjustments)
            # Core 3.32.7 already clones parameters for this reform. Calling
            # modify_parameters would allocate a second full copy of the rules.
            # https://edd.ca.gov/en/Payroll_Taxes/Rates_and_Withholding
            self.parameters.gov.states.ca.tax.payroll.disability.employee_rate.update(
                period="2025", value=0.012
            )

    return CoverageCorrections(system)


def _people(scenario: Scenario, *, include_rsus: bool) -> dict[str, Any]:
    """Map explicit household roles and wages without duplicating RSUs."""
    year = str(scenario.tax_year)
    people: dict[str, Any] = {}
    for index, adult in enumerate(scenario.adults):
        person = {
            "age": {year: adult.age},
            "employment_income": {
                year: float(adult.wages if include_rsus else adult.cash_pay)
            },
            "traditional_401k_contributions_desired": {
                year: float(adult.employee_401k)
            },
            "is_tax_unit_head": {year: index == 0},
            "is_tax_unit_spouse": {year: index == 1},
            "is_tax_unit_dependent": {year: False},
        }
        if index == 0 and scenario.living_costs.ca_renter_credit_eligible:
            person["rent"] = {year: float(12 * scenario.living_costs.rent)}
        people[f"adult_{index}"] = person
    for index, child in enumerate(scenario.dependents):
        people[f"child_{index}"] = {
            "age": {year: child.age},
            "is_tax_unit_head": {year: False},
            "is_tax_unit_spouse": {year: False},
            "is_tax_unit_dependent": {year: True},
        }
    return people


def _situation(
    scenario: Scenario, *, include_rsus: bool, include_iso: bool
) -> dict[str, Any]:
    """Build the complete PolicyEngine household, including child marital units."""
    year = str(scenario.tax_year)
    people = _people(scenario, include_rsus=include_rsus)
    members = list(people)
    marital_units = {"couple": {"members": ["adult_0", "adult_1"]}}
    marital_units.update(
        {name: {"members": [name]} for name in people if name.startswith("child_")}
    )
    tax_unit = {
        "members": members,
        "filing_status": {year: "JOINT"},
        "tax_unit_itemizes": {year: False},
        "ca_itemized_deductions": {year: 0},
        "amt_non_agi_income": {
            year: float(sum(p.options.spread for p in scenario.adults))
            if include_iso
            else 0.0
        },
        "tax_unit_childcare_expenses": {
            year: float(scenario.living_costs.eligible_care_expense)
        },
    }
    return {
        "people": people,
        "tax_units": {"unit": tax_unit},
        "households": {
            "household": {"members": members, "state_code": {year: scenario.state}}
        },
        "families": {"family": {"members": members}},
        "spm_units": {"spm": {"members": members}},
        "marital_units": marital_units,
    }


def calculate_taxes(
    scenario: Scenario, *, include_rsus: bool = True, include_iso: bool = True
) -> TaxResult:
    """Calculate annual income and employee payroll taxes for supported facts.

    Args:
        scenario: Validated annual household assumptions.
        include_rsus: Include vested wages; false is the internal cash-only comparison.
        include_iso: Include exercise spread; false isolates ordinary compensation tax.

    Returns:
        Taxes including refundable balances and non-additive AMT detail.

    Raises:
        RuntimeError: If installed rules differ from the validated pinned versions.
    """
    # The engine eagerly evaluates inactive np.where branches containing 0/0.
    # Limit suppression to its calculation; Pydantic rejects nonfinite outputs.
    with _ENGINE_LOCK, np.errstate(divide="ignore", invalid="ignore"):
        system = _tax_system()
        from policyengine_us import Simulation

        simulation = Simulation(
            tax_benefit_system=system,
            situation=_situation(
                scenario, include_rsus=include_rsus, include_iso=include_iso
            ),
        )
        if any(adult.wa_cares_exempt for adult in scenario.adults):
            variable = "wa_employee_long_term_care_contribution"
            year = str(scenario.tax_year)
            contributions = simulation.calculate(variable, year).copy()
            for index, adult in enumerate(scenario.adults):
                if adult.wa_cares_exempt:
                    contributions[index] = 0
            # A partial person input would default all other people to zero.
            simulation.set_input(variable, year, contributions)
        return _tax_result(simulation, scenario)


def _tax_result(simulation: Any, scenario: Scenario) -> TaxResult:
    """Normalize each tax component once at the engine boundary."""
    variables = {
        "federal_income_tax": "income_tax",
        "state_income_tax": "state_income_tax",
        "social_security": "employee_social_security_tax",
        "medicare": "employee_medicare_tax",
        "additional_medicare": "additional_medicare_tax",
        "ca_sdi": "ca_employee_state_disability_insurance_contribution",
        "wa_paid_leave": "wa_employee_paid_leave_contribution",
        "wa_cares": "wa_employee_long_term_care_contribution",
        "federal_amt": "alternative_minimum_tax",
        "state_amt": "ca_amt",
        "federal_taxable_income": "taxable_income",
    }
    values = {
        field: dollars(
            float(simulation.calculate(variable, str(scenario.tax_year)).sum())
        )
        for field, variable in variables.items()
    }
    return TaxResult.model_validate(
        {
            **values,
            "engine_version": ENGINE_VERSION,
            "core_version": CORE_VERSION,
            "correction_version": CORRECTION_VERSION,
            "rules_status": (
                "projected_ca_parameters"
                if scenario.tax_year == 2026 and scenario.state == "CA"
                else "verified_parameters"
            ),
        }
    )
