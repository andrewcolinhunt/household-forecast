"""Explicit conversion of version-one backups into goal allocation drafts."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import TypeAdapter

from financial_model.schemas import (
    ZERO,
    AllocationPlan,
    GoalAllocation,
    PersonInputs,
    Rate,
    Scenario,
    ScenarioResult,
    dollars,
)


def convert_legacy_inputs(
    document: dict[str, Any], *, destinations: AllocationPlan
) -> Scenario:
    """Convert an old saving target after its goal destinations have been reviewed.

    Args:
        document: A version-one scenario backup; its results are not imported.
        destinations: Reviewed RSU, contribution, option, and remainder destinations.

    Returns:
        New validated inputs with the exact former additional annual saving amount.
    """
    if document.get("schema_version") != 1:
        msg = "Only version-one scenarios need this migration."
        raise ValueError(msg)
    data = dict(document["inputs"])
    adults = []
    additional = ZERO
    for raw in data["adults"]:
        person = dict(raw)
        rate: Decimal = TypeAdapter(Rate).validate_python(person.pop("savings_rate"))
        adult = PersonInputs.model_validate(person)
        additional += max(dollars(rate * adult.wages) - adult.employee_401k, ZERO)
        adults.append(adult)
    rows = []
    for row in destinations.rows:
        amount = row.cash_amount
        if row.goal.id == destinations.retirement_goal_id and amount is not None:
            amount = additional
        rows.append(
            GoalAllocation(goal=row.goal, cash_amount=amount, rsu_rate=row.rsu_rate)
        )
    data.update(
        adults=tuple(adults),
        allocation_plan=destinations.model_copy(update={"rows": tuple(rows)}),
    )
    return Scenario.model_validate(data)


def verify_legacy_totals(old: dict[str, Any], result: ScenarioResult) -> None:
    """Fail migration if goal assignment changes any financial source or tax."""
    for key in (
        "gross_cash",
        "gross_rsus",
        "employee_401k",
        "employer_retirement",
        "option_cost",
        "option_value",
        "living_cost",
        "rsu_tax_funding",
        "retained_rsus",
        "cash_funded_tax",
    ):
        if Decimal(str(old[key])) != getattr(result, key):
            msg = f"Migration changed {key}; review before applying."
            raise ValueError(msg)
    if old["taxes"] != result.taxes.model_dump(mode="json"):
        # JSON amounts may use different equivalent decimal spellings.
        from financial_model.schemas import TaxResult

        if TaxResult.model_validate(old["taxes"]) != result.taxes:
            msg = "Migration changed taxes; review before applying."
            raise ValueError(msg)
    net = (
        Decimal(str(old["total_savings_and_assets"]))
        - Decimal(str(old["option_value"]))
        + Decimal(str(old["option_cost"]))
    )
    if net != result.net_saved_from_income:
        msg = "Migration changed total net additions."
        raise ValueError(msg)
