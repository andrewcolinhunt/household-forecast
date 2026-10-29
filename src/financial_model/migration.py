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
