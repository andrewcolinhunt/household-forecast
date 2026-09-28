"""Exact asset assignment and explicit unfunded cash plans."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from financial_model.allocations import allocate_sources
from financial_model.schemas import (
    AllocationPlan,
    Goal,
    GoalAllocation,
    Scenario,
    default_allocation_plan,
)


def plan_with_cash(amount: str) -> AllocationPlan:
    plan = default_allocation_plan()
    return AllocationPlan.model_validate(
        plan.model_dump()
        | {
            "rows": (
                plan.rows[0].model_copy(update={"cash_amount": Decimal(amount)}),
                plan.rows[1],
            )
        }
    )


@pytest.mark.parametrize(
    ("cash", "fixed", "remainder", "gap"),
    [
        ("100", "30", "70", "0"),
        ("100", "100", "0", "0"),
        ("100", "120", "0", "20"),
        ("-100", "30", "0", "130"),
        ("0", "0", "0", "0"),
        ("0.01", "0", "0.01", "0"),
    ],
)
def test_allocation_sources_reconcile(cash, fixed, remainder, gap):
    result = allocate_sources(
        plan_with_cash(fixed),
        cash=Decimal(cash),
        retirement=Decimal(10),
        rsus=Decimal(20),
        option_cost=Decimal(5),
    )
    assert result.rows[0].cash == Decimal(fixed)
    assert result.rows[1].cash == Decimal(remainder)
    assert result.funding_gap == Decimal(gap)
    assert result.net_total == Decimal(cash) + 35
    assert result.rows[0].retirement == 10
    assert result.rows[0].option_cost == 5


def test_rsu_rounding_conserves_pennies_independent_of_row_order():
    goals = [Goal(id=UUID(int=i), name=f"Goal {i}") for i in (1, 2, 3)]
    rows = tuple(
        GoalAllocation(
            goal=g, cash_amount=None if i == 0 else Decimal(0), rsu_rate=Decimal(rate)
        )
        for i, (g, rate) in enumerate(
            zip(goals, (".3333", ".3333", ".3334"), strict=True)
        )
    )
    plan = AllocationPlan(
        rows=rows, retirement_goal_id=goals[0].id, option_goal_id=goals[1].id
    )
    outputs = []
    for ordered in (rows, tuple(reversed(rows))):
        result = allocate_sources(
            plan.model_copy(update={"rows": ordered}),
            cash=Decimal(0),
            retirement=Decimal(0),
            rsus=Decimal("0.02"),
            option_cost=Decimal(0),
        )
        outputs.append({r.goal_id: r.rsus for r in result.rows})
        assert sum(r.rsus for r in result.rows) == Decimal("0.02")
    assert (
        outputs[0]
        == outputs[1]
        == {goals[0].id: Decimal(".01"), goals[1].id: 0, goals[2].id: Decimal(".01")}
    )


@pytest.mark.parametrize(
    "change",
    [
        "negative",
        "duplicate",
        "no_remainder",
        "two_remainders",
        "missing_destination",
        "over_rsus",
    ],
)
def test_plan_rejects_incomplete_or_ambiguous_assignments(change):
    raw = default_allocation_plan().model_dump(mode="json")
    if change == "negative":
        raw["rows"][0]["cash_amount"] = "-1"
    elif change == "duplicate":
        raw["rows"][1]["goal"] = raw["rows"][0]["goal"]
    elif change == "no_remainder":
        raw["rows"][1]["cash_amount"] = "0"
    elif change == "two_remainders":
        raw["rows"][0]["cash_amount"] = None
    elif change == "missing_destination":
        raw["option_goal_id"] = str(UUID(int=1))
    else:
        raw["rows"][1]["rsu_rate"] = "0.01"
    with pytest.raises(ValidationError):
        AllocationPlan.model_validate(raw)


def test_scenario_requires_complete_rsu_assignment(scenario):
    raw = scenario.model_dump(mode="json")
    raw["adults"][0]["rsu_vesting"] = "100"
    raw["allocation_plan"]["rows"][0]["rsu_rate"] = "0.5"
    with pytest.raises(ValidationError, match="100%"):
        Scenario.model_validate(raw)
