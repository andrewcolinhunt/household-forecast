"""Allocate existing annual financial sources without changing their value."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from financial_model.schemas import (
    ZERO,
    AllocationPlan,
    AllocationResult,
    GoalAddition,
)


def allocate_sources(
    plan: AllocationPlan,
    *,
    cash: Decimal,
    retirement: Decimal,
    rsus: Decimal,
    option_cost: Decimal,
) -> AllocationResult:
    """Assign sources once, retaining planned amounts when cash is insufficient.

    Args:
        plan: Validated goal destinations and allocations.
        cash: Signed cash after taxes, living costs, 401(k), and option purchases.
        retirement: Employee and employer contributions together.
        rsus: Value of shares retained after their tax funding.
        option_cost: Cash transferred into exercised shares, excluding gains.

    Returns:
        Goal additions and the funding gap needed to realize the cash plan.
    """
    if rsus and sum((r.rsu_rate for r in plan.rows), ZERO) != 1:
        msg = "Allocate 100% of retained RSUs across your goals."
        raise ValueError(msg)
    exact = [rsus * row.rsu_rate for row in plan.rows]
    split = [v.quantize(Decimal("0.01"), rounding=ROUND_DOWN) for v in exact]
    # Largest remainders preserve every cent without favoring the last goal.
    pennies = int((rsus - sum(split, ZERO)) * 100)
    order = sorted(
        range(len(split)),
        key=lambda i: (-(exact[i] - split[i]), str(plan.rows[i].goal.id)),
    )
    for index in order[:pennies]:
        split[index] += Decimal("0.01")
    remainder = max(cash - plan.fixed_cash, ZERO)
    return AllocationResult(
        funding_gap=max(plan.fixed_cash - cash, ZERO),
        rows=tuple(
            GoalAddition(
                goal_id=row.goal.id,
                cash=remainder if row.cash_amount is None else row.cash_amount,
                retirement=retirement
                if row.goal.id == plan.retirement_goal_id
                else ZERO,
                rsus=split[index],
                option_cost=option_cost if row.goal.id == plan.option_goal_id else ZERO,
            )
            for index, row in enumerate(plan.rows)
        ),
    )
