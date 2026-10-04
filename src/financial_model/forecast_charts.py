"""Charts of one realized forecast ledger, without additional calculations."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from financial_model.planning import PlanningScenario, RetirementAssumptions

type LedgerRow = dict[str, float | int]
type ChartRow = dict[str, float | int | str]

_INCOME = {
    "cash_pay": "Cash pay",
    "social_security": "Social Security",
    "pensions": "Pensions",
    "spending_planned": "Planned spending",
    "tax_paid": "Paid taxes",
    "income": "All external income",
}
_BALANCES = {
    "assets": "Total assets",
    "net_worth": "Net wealth",
    "accessible": "Accessible, before tax",
    "taxable": "Taxable accounts",
    "tax_deferred": "Pretax accounts",
    "roth": "Roth accounts",
    "hsa": "HSA",
}
_CHANGES = (
    ("External income", "income", 1),
    ("Investment return", "investment_gain", 1),
    ("Paid spending", "spending_paid", -1),
    ("Paid taxes", "tax_paid", -1),
    ("Debt payments", "debt_payment", -1),
)


def plan_events(plan: PlanningScenario) -> list[ChartRow]:
    """Read dated assumptions using the saved Retirement person mapping.

    Planning lifetimes end after the entered age's final year. These markers
    describe saved inputs and the age-60 access convention, not forecast results.
    """
    reference_year = _reference_year(plan)
    selected = next(
        (c for c in plan.components if isinstance(c.inputs, RetirementAssumptions)),
        None,
    )
    if selected is None:
        message = "Saved Plan is missing Retirement inputs."
        raise ValueError(message)
    if selected.adult_mapping is None:
        message = "Saved Retirement inputs need a reviewed person mapping."
        raise ValueError(message)
    retirement = cast(RetirementAssumptions, selected.inputs)
    dates: dict[int, list[str]] = {}
    for slot, detail in zip(selected.adult_mapping, retirement.adults, strict=True):
        person = plan.household.adults[slot - 1]
        if person.age is None:
            message = f"Saved household is missing {person.name}'s age."
            raise ValueError(message)
        if detail.age.central is None or detail.retirement.central is None:
            message = f"Saved Retirement inputs need {person.name}'s retirement year and planning age."
            raise ValueError(message)
        birth = reference_year - person.age
        events = [
            (detail.retirement.central, f"{person.name} retires"),
            (birth + 60, f"{person.name}: retirement account access at 60"),
            (
                birth + detail.age.central + 1,
                f"After {person.name}'s planning age {detail.age.central}",
            ),
        ]
        if detail.social_security:
            events.append(
                (birth + detail.claiming_age, f"{person.name}: Social Security age")
            )
        if detail.pension:
            events.append(
                (birth + detail.pension_start_age, f"{person.name}: pension age")
            )
        for year, label in events:
            dates.setdefault(year, []).append(label)
    return [{"year": year, "event": " · ".join(dates[year])} for year in sorted(dates)]


def _series(
    ledger: Sequence[LedgerRow], fields: dict[str, str], real: bool, *, flows: bool
) -> list[ChartRow]:
    """Deflate flows at their opening price and balances at their closing price."""
    values: list[ChartRow] = []
    opening_price = 1.0
    for row in ledger:
        divisor = (opening_price if flows else row["price_index"]) if real else 1
        values.extend(
            {"year": row["year"], "series": label, "value": row[key] / divisor}
            for key, label in fields.items()
        )
        opening_price = row["price_index"]
    return values


def _line_spec(
    values: list[ChartRow],
    title: str,
    *,
    events: Sequence[ChartRow] = (),
    percent: bool = False,
) -> dict[str, Any]:
    years = [int(row["year"]) for row in values]
    year_axis = {
        "field": "year",
        "type": "quantitative",
        "scale": {"zero": False},
        "axis": {"title": "Year", "format": "d", "tickMinStep": 1},
    }
    return {
        "height": 270,
        "layer": [
            {
                "data": {"values": values},
                "mark": {"type": "line", "point": {"size": 16}},
                "encoding": {
                    "x": year_axis,
                    "y": {
                        "field": "value",
                        "type": "quantitative",
                        "title": title,
                        "axis": {"format": ".0%" if percent else "~s"},
                    },
                    "color": {
                        "field": "series",
                        "type": "nominal",
                        "title": None,
                        "legend": {"orient": "top", "symbolType": "stroke"},
                    },
                    "tooltip": [
                        {"field": "year", "type": "quantitative", "format": "d"},
                        {"field": "series", "type": "nominal", "title": "Series"},
                        {
                            "field": "value",
                            "type": "quantitative",
                            "title": title,
                            "format": ".2%" if percent else "$,.2f",
                        },
                    ],
                },
            },
            {
                "data": {
                    "values": [
                        e
                        for e in events
                        if min(years) <= cast(int, e["year"]) <= max(years)
                    ]
                },
                "mark": {"type": "rule", "strokeDash": [3, 4], "opacity": 0.35},
                "encoding": {
                    "x": year_axis,
                    "tooltip": [{"field": "year", "format": "d"}, {"field": "event"}],
                },
            },
        ],
    }


def _units(real: bool, money_year: int) -> str:
    return f"{money_year} dollars" if real else "Nominal dollars"


def _reference_year(plan: PlanningScenario) -> int:
    if plan.household.reference_year is None:
        message = "Saved Plan is missing its reference year."
        raise ValueError(message)
    return plan.household.reference_year


def income_spec(
    plan: PlanningScenario, ledger: Sequence[LedgerRow], real: bool
) -> dict[str, Any]:
    """Show annual inflows above zero and planned spending and paid taxes below."""
    cash_flows = [
        row
        | {"spending_planned": -row["spending_planned"], "tax_paid": -row["tax_paid"]}
        for row in ledger
    ]
    spec = _line_spec(
        _series(cash_flows, _INCOME, real, flows=True),
        _units(real, _reference_year(plan)) + " · annual flows",
        events=plan_events(plan),
    )
    spec["layer"].append(
        {
            "data": {"values": [{}]},
            "mark": {"type": "rule", "color": "#888888", "strokeWidth": 1.5},
            "encoding": {"y": {"datum": 0, "type": "quantitative"}},
        }
    )
    return spec


def waterfall_spec(row: LedgerRow, real: bool, *, money_year: int) -> dict[str, Any]:
    """Reconcile assets using one common closing price for every bridge term.

    Contributions and withdrawals are internal transfers. Only paid costs leave
    assets; outstanding debt and tax liabilities belong in net wealth instead.
    """
    divisor = row["price_index"] if real else 1
    running = row["opening_assets"] / divisor
    values: list[ChartRow] = [
        {
            "step": "Opening assets",
            "start": 0,
            "end": running,
            "amount": running,
            "kind": "Balance",
        }
    ]
    for label, key, sign in _CHANGES:
        amount = sign * row[key] / divisor
        values.append(
            {
                "step": label,
                "start": running,
                "end": running + amount,
                "amount": amount,
                "kind": "Added" if amount >= 0 else "Used",
            }
        )
        running += amount
    values.append(
        {
            "step": "Closing assets",
            "start": 0,
            "end": row["assets"] / divisor,
            "amount": row["assets"] / divisor,
            "kind": "Balance",
        }
    )
    return {
        "height": 280,
        "data": {"values": values},
        "mark": "bar",
        "encoding": {
            "y": {
                "field": "step",
                "type": "nominal",
                "title": None,
                "sort": [v["step"] for v in values],
            },
            "x": {
                "field": "start",
                "type": "quantitative",
                "title": _units(real, money_year)
                + (" · year-end prices" if real else ""),
                "axis": {"format": "~s"},
            },
            "x2": {"field": "end"},
            "color": {"field": "kind", "type": "nominal", "title": None},
            "tooltip": [
                {"field": "step", "title": "Component"},
                {"field": "amount", "title": "Amount", "format": "$,.2f"},
                {"field": "end", "title": "Balance after", "format": "$,.2f"},
            ],
        },
    }


def waterfall_residual(row: LedgerRow, real: bool) -> float:
    """Return unexplained asset change in the same units as the asset bridge."""
    expected = row["opening_assets"] + sum(row[key] * sign for _, key, sign in _CHANGES)
    return (row["assets"] - expected) / (row["price_index"] if real else 1)


def _group_accounts(plan: PlanningScenario, row: LedgerRow) -> LedgerRow:
    if plan.wealth is None:
        message = "Saved Plan is missing its opening Wealth snapshot."
        raise ValueError(message)
    groups: LedgerRow = dict.fromkeys(("taxable", "tax_deferred", "roth", "hsa"), 0.0)
    for account in plan.wealth.snapshot.accounts:
        key = f"Account: {account.label} ({str(account.id)[:8]})"
        if account.tax_treatment != "unknown":
            groups[account.tax_treatment] += row[key]
    groups["taxable"] += row["Forecast cash"] + row["Forecast investments"]
    groups["tax_deferred"] += (
        row["Adult 1 contributions"] + row["Adult 2 contributions"]
    )
    return row | groups


def balances_spec(
    plan: PlanningScenario, ledger: Sequence[LedgerRow], real: bool
) -> dict[str, Any]:
    """Show net wealth, assets, accessibility, and balances by tax treatment."""
    return _line_spec(
        _series(
            [_group_accounts(plan, row) for row in ledger], _BALANCES, real, flows=False
        ),
        _units(real, _reference_year(plan)) + " · year-end balances",
        events=plan_events(plan),
    )


def returns_spec(ledger: Sequence[LedgerRow]) -> dict[str, Any]:
    """Plot annual nominal economic rates, including the opening year's full rates."""
    return _line_spec(
        _series(
            ledger,
            {
                "equity_return": "Stocks",
                "bond_return": "Bonds",
                "cash_return": "Cash",
                "inflation": "Inflation",
            },
            False,
            flows=False,
        ),
        "Annual return / inflation",
        percent=True,
    )


def future_spending_spec(
    ledger: Sequence[LedgerRow], real: bool, *, money_year: int
) -> dict[str, Any]:
    """Keep spending, withdrawals, and shortfalls on one realized future."""
    return _line_spec(
        _series(
            ledger,
            {
                "spending_planned": "Planned spending",
                "withdrawals": "Account withdrawals",
                "unfunded": "Unpaid expenses",
            },
            real,
            flows=True,
        ),
        _units(real, money_year) + " · annual flows",
    )
