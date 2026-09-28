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


@pytest.mark.parametrize(
    ("cash", "funded", "remaining", "shortfalls"),
    [
        ("200", ("100", "80"), "20", ("0", "0")),
        ("130", ("100", "30"), "0", ("0", "50")),
        ("70", ("70", "0"), "0", ("30", "80")),
        ("0", ("0", "0"), "0", ("100", "80")),
        ("-30", ("0", "0"), "-30", ("100", "80")),
        ("100.01", ("100", "0.01"), "0", ("0", "79.99")),
    ],
)
def test_cash_goals_fund_in_order_and_conserve_cash(
    cash, funded, remaining, shortfalls
):
    from financial_model.allocations import fund_cash_goals

    base = plan_with_cash("100")
    travel = GoalAllocation(goal=Goal(name="Travel"), cash_amount=80)
    plan = base.model_copy(update={"rows": (base.rows[0], base.rows[1], travel)})
    snapshot = plan.model_dump()
    funding = fund_cash_goals(plan, Decimal(cash))
    assert [r.funded for r in funding.rows] == list(map(Decimal, funded))
    assert [r.shortfall for r in funding.rows] == list(map(Decimal, shortfalls))
    assert funding.remaining == Decimal(remaining)
    assert sum(r.funded for r in funding.rows) + funding.remaining == Decimal(cash)
    planned = allocate_sources(
        plan,
        cash=Decimal(cash),
        retirement=Decimal(10),
        rsus=Decimal(20),
        option_cost=Decimal(5),
    )
    assert (
        sum(r.shortfall for r in funding.rows) + max(-funding.remaining, 0)
        == planned.funding_gap
    )
    assert plan.model_dump() == snapshot


def test_cash_priority_changes_funding_without_changing_other_assets():
    from financial_model.allocations import fund_cash_goals

    base = plan_with_cash("100")
    travel = GoalAllocation(goal=Goal(name="Travel"), cash_amount=80)
    plan = base.model_copy(update={"rows": (base.rows[0], travel, base.rows[1])})
    reversed_plan = plan.model_copy(
        update={"rows": (travel, base.rows[0], base.rows[1])}
    )
    original = fund_cash_goals(plan, Decimal(130))
    reordered = fund_cash_goals(reversed_plan, Decimal(130))
    assert [(r.goal_id, r.funded) for r in original.rows] == [
        (base.rows[0].goal.id, 100),
        (travel.goal.id, 30),
    ]
    assert [(r.goal_id, r.funded) for r in reordered.rows] == [
        (travel.goal.id, 80),
        (base.rows[0].goal.id, 50),
    ]
    assert original.remaining == reordered.remaining == 0
    results = [
        allocate_sources(
            p,
            cash=Decimal(130),
            retirement=Decimal(10),
            rsus=Decimal(20),
            option_cost=Decimal(5),
        )
        for p in (plan, reversed_plan)
    ]
    assert {r.goal_id: r for r in results[0].rows} == {
        r.goal_id: r for r in results[1].rows
    }
