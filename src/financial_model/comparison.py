"""Named scenario tables rendered from validated calculation snapshots."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from html import escape
from typing import Literal
from uuid import UUID

from financial_model.allocations import fund_cash_goals
from financial_model.scenario_display import ScenarioDisplay, scenario_displays
from financial_model.schemas import (
    ZERO,
    Goal,
    PersonInputs,
    SavedScenario,
    ScenarioResult,
)

NumberFormat = Literal["money", "number", "price", "percent"]
Period = Literal["Monthly", "Annual"]
EFFECTIVE_TAX_RATE_DEFINITION = (
    "Federal and state income taxes plus employee payroll taxes divided by salary, "
    "bonus, and vested RSUs. Includes taxes funded from vested shares. "
    "Income tax includes any modeled AMT."
)


def period_divisor(period: Period) -> Decimal:
    """Convert annual flows to the selected display period."""
    return Decimal(12 if period == "Monthly" else 1)


@dataclass(frozen=True)
class _Row:
    """Display values with their units, emphasis, and optional deficit treatment."""

    label: str
    values: Sequence[Decimal | str | None]
    format: NumberFormat = "money"
    total: bool = False
    deficit: bool = False
    shortfalls: Sequence[Decimal] | None = None


def _number(value: Decimal, format: NumberFormat = "money") -> str:
    """Preserve per-share precision and avoid displaying negative rounded zero."""
    if format == "percent":
        rounded = (value * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        sign = "\N{MINUS SIGN}" if rounded < 0 else ""
        return f"{sign}{abs(rounded):.1f}%"
    precision = Decimal("0.000001") if format == "price" else Decimal(1)
    rounded = value.quantize(precision, rounding=ROUND_HALF_UP)
    sign = "\N{MINUS SIGN}" if rounded < 0 else ""
    amount = (
        f"{abs(rounded):,.6f}".rstrip("0").rstrip(".")
        if format == "price"
        else f"{abs(rounded):,.0f}"
    )
    return f"{sign}{'' if format == 'number' else '$'}{amount}"


def _cell(row: _Row, index: int, period: Period) -> str:
    """Format a scenario value in the selected period, preserving unknowns."""
    value = row.values[index]
    if not isinstance(value, Decimal):
        label = (
            value
            if value is not None
            else {"price": "Not entered", "percent": "N/A"}.get(
                row.format, "Not estimated"
            )
        )
        return f'<td><span class="fm-missing">{escape(label)}</span></td>'
    if row.format in ("money", "number"):
        value /= period_divisor(period)
    kind = ' class="fm-deficit"' if row.deficit and value < 0 else ""
    shortfall = ""
    if row.shortfalls and row.shortfalls[index]:
        amount = row.shortfalls[index] / period_divisor(period)
        shortfall = f'<small class="fm-goal-shortfall" aria-label="Cash shortfall">{_number(-amount)}</small>'
    return f"<td><span{kind}>{_number(value, row.format)}</span>{shortfall}</td>"


def _table(
    displays: tuple[ScenarioDisplay, ...],
    groups: dict[str, list[_Row]],
    caption: str,
    period: Period = "Annual",
) -> str:
    """Render aligned, scrollable columns with sticky labels and explicit totals."""
    rows = []
    for group, values in groups.items():
        if group:
            rows.append(
                f'<tr class="fm-group"><th scope="row">{escape(group)}</th>'
                f'<td colspan="{len(displays)}"></td></tr>'
            )
        for row in values:
            kind = ' class="fm-total"' if row.total else ""
            rows.append(
                f'<tr{kind}><th scope="row">{escape(row.label)}</th>'
                + "".join(_cell(row, index, period) for index in range(len(displays)))
                + "</tr>"
            )
    headings = "".join(f'<th scope="col">{display.html()}</th>' for display in displays)
    return (
        f'<div class="fm-table-scroll" tabindex="0" role="region" aria-label="{escape(caption)}">'
        f'<table class="fm-table" style="min-width:{240 + 160 * len(displays)}px">'
        f"<caption>{escape(caption)}</caption>"
        f'<thead><tr><th scope="col">{period} amounts</th>{headings}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _definitions(items: dict[str, str], title: str = "Definitions") -> str:
    """Keep explanations available without repeating them above every number."""
    rows = "".join(
        f"<dt>{escape(label)}</dt><dd>{escape(text)}</dd>"
        for label, text in items.items()
    )
    return f'<details class="fm-definitions"><summary>{escape(title)}</summary><dl>{rows}</dl></details>'


def _goal_names(
    documents: tuple[SavedScenario, ...], goals: tuple[Goal, ...]
) -> dict[UUID | None, str]:
    """Align goals by ID and the automatic spending balance by its role."""
    snapshots = {
        row.goal.id: row.goal.name
        for doc in documents
        for row in doc.inputs.allocation_plan.rows
        if row.cash_amount is not None
    }
    spending = {d.inputs.allocation_plan.spending_goal_id for d in documents}
    current = {g.id: g.name for g in goals}
    names = {key: current.get(key, name) for key, name in snapshots.items()}
    names.update(
        {
            key: name
            for key, name in current.items()
            if key not in names and key not in spending
        }
    )
    counts = Counter([*names.values(), "Spending"])
    labels: dict[UUID | None, str] = {
        key: f"{name} · {str(key)[:6]}" if counts[name] > 1 else name
        for key, name in names.items()
    }
    labels[None] = "Spending"
    return labels


def _goal_values(
    documents: tuple[SavedScenario, ...], goal_id: UUID | None, field: str
) -> list[Decimal]:
    """Use zero for absent goals; combine remainder rows in the spending column."""
    values = []
    for doc in documents:
        spending_id = doc.inputs.allocation_plan.spending_goal_id
        if goal_id == spending_id:
            values.append(ZERO)
            continue
        source_id = spending_id if goal_id is None else goal_id
        values.append(
            next(
                (
                    getattr(row, field)
                    for row in doc.result.allocations.rows
                    if row.goal_id == source_id
                ),
                ZERO,
            )
        )
    return values


def cash_surplus(result: ScenarioResult) -> Decimal:
    """Cash after taxes, employee contributions, and expenses, before investing."""
    return result.cash_added + result.option_cost


def remaining_cash(document: SavedScenario) -> Decimal:
    """Cash left after funding goals in order, or a deficit before goal funding."""
    return fund_cash_goals(
        document.inputs.allocation_plan, document.result.cash_added
    ).remaining


def _cash_groups(
    documents: tuple[SavedScenario, ...], goals: tuple[Goal, ...]
) -> dict[str, list[_Row]]:
    """Show cash deductions once, without subtracting the residual cash itself."""
    results = [d.result for d in documents]
    funding = [
        fund_cash_goals(d.inputs.allocation_plan, d.result.cash_added)
        for d in documents
    ]
    savings = []
    for key, name in _goal_names(documents, goals).items():
        if key is None:
            continue
        funded = [
            next((row.funded for row in f.rows if row.goal_id == key), ZERO)
            for f in funding
        ]
        shortfalls = [
            next((row.shortfall for row in f.rows if row.goal_id == key), ZERO)
            for f in funding
        ]
        savings.append(_Row(name, funded, shortfalls=shortfalls))
    groups = {
        "Cash income": [_Row("Salary and bonus", [r.gross_cash for r in results])],
        "Taxes": [
            _Row(
                "Income and payroll taxes paid from cash",
                [-r.cash_funded_tax for r in results],
            ),
            _Row(
                "Effective tax rate", [r.effective_tax_rate for r in results], "percent"
            ),
        ],
        "Retirement contributions": [
            _Row("Employee 401(k) contributions", [-r.employee_401k for r in results]),
        ],
        "Living expenses": [
            *[
                _Row(
                    label,
                    [-12 * getattr(d.inputs.living_costs, field) for d in documents],
                )
                for label, field in (
                    ("Rent", "rent"),
                    ("Childcare", "childcare"),
                    ("Other living expenses", "other"),
                )
            ],
            _Row(
                "Total living expenses", [-r.living_cost for r in results], total=True
            ),
            _Row(
                "Cash surplus / deficit",
                [cash_surplus(r) for r in results],
                total=True,
                deficit=True,
            ),
        ],
    }
    if any(r.option_cost for r in results):
        groups["Option exercises"] = [
            _Row("Option exercise cost", [-r.option_cost for r in results])
        ]
    groups["Goal allocations"] = [
        *savings,
        _Row(
            "Spending",
            [f.remaining for f in funding],
            total=True,
            deficit=True,
        ),
    ]
    return groups


def cash_flow_html(
    documents: tuple[SavedScenario, ...],
    displays: tuple[ScenarioDisplay, ...] | None = None,
    *,
    goals: tuple[Goal, ...] = (),
    period: Period = "Annual",
) -> str:
    """Reconcile cash income to expenses, planned saving, and the remaining balance."""
    displays = displays or scenario_displays(documents)
    return (
        '<div class="fm-comparison"><h3>Cash overview</h3>'
        '<p class="fm-note">Income, expenses, goal allocations, and cash available to spend.</p>'
        + _table(
            displays,
            _cash_groups(documents, goals),
            "Cash overview",
            period,
        )
        + _definitions(
            {
                "Cash surplus / deficit": "Salary and bonus less cash-funded taxes, employee 401(k) contributions, and living expenses. Before additional savings or option exercise costs.",
                "Goal allocations": "Positive amounts show funded cash. Red negative amounts beneath a goal show its cash shortfall.",
                "Spending": "Cash left after option exercise costs and funded goal allocations. Positive amounts are available to spend; negative amounts mean income cannot cover existing expenses and commitments. Unfunded goal amounts are shown beside each goal.",
                "Living expenses": "Rent, childcare, and other expenses. Include insurance and other unitemized payroll deductions once. These budget entries do not reduce taxable wages.",
                "Effective tax rate": EFFECTIVE_TAX_RATE_DEFINITION,
            }
        )
        + "</div>"
    )


def tax_html(
    documents: tuple[SavedScenario, ...],
    displays: tuple[ScenarioDisplay, ...] | None = None,
    *,
    period: Period = "Annual",
) -> str:
    """Reconcile estimated taxes to taxes funded from shares and cash."""
    displays = displays or scenario_displays(documents)
    results = [d.result for d in documents]
    tax_rows = [
        _Row("Federal income tax", [r.taxes.federal_income_tax for r in results]),
        _Row("State income tax", [r.taxes.state_income_tax for r in results]),
        _Row("Employee payroll taxes", [r.taxes.employee_payroll_tax for r in results]),
        _Row("Total estimated taxes", [r.taxes.total for r in results], total=True),
        _Row("Effective tax rate", [r.effective_tax_rate for r in results], "percent"),
        _Row("Taxes funded from vested shares", [-r.rsu_tax_funding for r in results]),
        _Row(
            "Income and payroll taxes paid from cash",
            [r.cash_funded_tax for r in results],
            total=True,
        ),
    ]
    return (
        '<div class="fm-comparison"><h3>Tax breakdown</h3>'
        '<p class="fm-note">Income and payroll taxes, funded from vested shares and cash.</p>'
        + _table(displays, {"": tax_rows}, "Estimated taxes", period)
        + _definitions({"Effective tax rate": EFFECTIVE_TAX_RATE_DEFINITION})
        + "</div>"
    )


def goal_breakdown_html(
    documents: tuple[SavedScenario, ...],
    displays: tuple[ScenarioDisplay, ...] | None = None,
    *,
    goals: tuple[Goal, ...] = (),
    period: Period = "Annual",
) -> str:
    """Show each goal's funded sources, requested cash, and unmet cash funding."""
    displays = displays or scenario_displays(documents)
    funding = [
        fund_cash_goals(d.inputs.allocation_plan, d.result.cash_added)
        for d in documents
    ]
    groups = {}
    for key, name in _goal_names(documents, goals).items():
        plans = [
            next(
                (
                    r
                    for r in d.inputs.allocation_plan.rows
                    if (
                        r.cash_amount is None
                        if key is None
                        else r.goal.id == key and r.cash_amount is not None
                    )
                ),
                None,
            )
            for d in documents
        ]
        cash = [next((r for r in f.rows if r.goal_id == key), None) for f in funding]
        spending = [p is not None and p.cash_amount is None for p in plans]
        funded = [
            r.funded if r else max(f.remaining, ZERO) if residual else ZERO
            for r, f, residual in zip(cash, funding, spending, strict=True)
        ]
        employee = [
            d.result.employee_401k
            if plan is not None
            and plan.goal.id == d.inputs.allocation_plan.retirement_goal_id
            else ZERO
            for d, plan in zip(documents, plans, strict=True)
        ]
        employer = [
            d.result.employer_retirement
            if plan is not None
            and plan.goal.id == d.inputs.allocation_plan.retirement_goal_id
            else ZERO
            for d, plan in zip(documents, plans, strict=True)
        ]
        rsus = _goal_values(documents, key, "rsus")
        options = _goal_values(documents, key, "option_cost")
        rows = []
        if not all(spending):
            priorities = [
                next((str(i) for i, r in enumerate(f.rows, 1) if r.goal_id == key), "—")
                for f in funding
            ]
            rows.extend(
                [
                    _Row("Cash funding priority", priorities),
                    _Row(
                        "Planned cash allocation",
                        [r.planned if r else ZERO for r in cash],
                    ),
                    _Row(
                        "Cash shortfall",
                        [-r.shortfall if r else ZERO for r in cash],
                        deficit=True,
                    ),
                ]
            )
        rows.extend(
            [
                _Row("Cash allocated", funded),
                _Row("Employee 401(k) contributions", employee),
                _Row("Employer 401(k) contributions", employer),
                _Row("Shares retained from RSUs", rsus),
            ]
        )
        if any(options):
            rows.append(_Row("Shares from option exercises (at cost)", options))
        rows.append(
            _Row(
                "Total funded",
                [
                    sum(values, ZERO)
                    for values in zip(
                        funded, employee, employer, rsus, options, strict=True
                    )
                ],
                total=True,
            )
        )
        if all(spending):
            rows = [
                _Row(
                    "Cash balance",
                    [f.remaining for f in funding],
                    total=True,
                    deficit=True,
                ),
                *[
                    row
                    for row in rows
                    if row.label
                    in (
                        "Employee 401(k) contributions",
                        "Employer 401(k) contributions",
                        "Shares retained from RSUs",
                        "Shares from option exercises (at cost)",
                    )
                    and any(row.values)
                ],
            ]
        groups[name] = rows
    return (
        '<div class="fm-comparison"><h3>Goal breakdown</h3>'
        '<p class="fm-note">Cash is funded in priority order. Contributions and retained shares keep their designated goals.</p>'
        + _table(displays, groups, "Goal funding by source", period)
        + _definitions(
            {
                "Cash funding priority": "Fixed cash goals are funded from top to bottom in each scenario's Savings goals editor. An unfunded goal does not reduce the cash available to later goals below zero. Remaining cash is available for spending.",
                "Total funded": "Funded cash plus assigned employee and employer 401(k) contributions, retained RSUs, and exercised shares at cost. These are allocations from this year's income, not account balances. Retained shares and 401(k) assets are not spendable cash. Unexercised options are excluded.",
                "Cash shortfall": "Planned cash less funded cash. A deficit from expenses or option exercises is shown separately in Spending; the model does not borrow or sell additional shares to cover it.",
            }
        )
        + "</div>"
    )


def _people(
    documents: tuple[SavedScenario, ...],
) -> list[tuple[str, list[PersonInputs | None]]]:
    """Align adults by name and occurrence rather than household input order."""
    households = []
    counts: Counter[str] = Counter()
    for document in documents:
        seen: Counter[str] = Counter()
        people = {}
        for adult in document.inputs.adults:
            seen[adult.name] += 1
            people[adult.name, seen[adult.name]] = adult
        households.append(people)
        counts |= seen
    keys = dict.fromkeys(key for people in households for key in people)
    return [
        (
            f"{name} ({occurrence})" if counts[name] > 1 else name,
            [people.get((name, occurrence)) for people in households],
        )
        for name, occurrence in keys
    ]


def _option_rows(people: list[PersonInputs | None]) -> list[_Row]:
    """Keep absent awards, unknown estimates, quantities, and prices distinct."""
    values: list[tuple[Decimal | str | None, ...]] = []
    for person in people:
        if person is None or person.option_award is None:
            values.append(("—",) * 7)
            continue
        award = person.option_award
        values.append(
            (
                award.kind,
                Decimal(award.vesting_shares),
                award.strike,
                award.assumed_share_value,
                award.vesting_shares * award.assumed_share_value
                if award.assumed_share_value is not None
                else None,
                award.vesting_shares * award.strike,
                award.exercise_spread,
            )
        )
    fields: list[tuple[str, NumberFormat]] = [
        ("Option type", "money"),
        ("Options vesting", "number"),
        ("Exercise price per share", "price"),
        ("Assumed share value", "price"),
        ("Share value if exercised", "money"),
        ("Exercise cost", "money"),
        ("Estimated intrinsic value", "money"),
    ]
    return [
        _Row(label, [v[index] for v in values], format)
        for index, (label, format) in enumerate(fields)
    ]


def equity_html(
    documents: tuple[SavedScenario, ...],
    displays: tuple[ScenarioDisplay, ...] | None = None,
    *,
    goals: tuple[Goal, ...] = (),
    period: Period = "Annual",
) -> str:
    """Reconcile RSU shares and show option awards separately from exercised shares."""
    displays = displays or scenario_displays(documents)
    results = [d.result for d in documents]
    names = _goal_names(documents, goals)
    rsu_goals = [
        _Row(name, _goal_values(documents, key, "rsus")) for key, name in names.items()
    ]
    groups = {
        "RSU income": [_Row("RSU value at vesting", [r.gross_rsus for r in results])],
        "Taxes": [
            _Row(
                "Taxes funded from vested shares", [-r.rsu_tax_funding for r in results]
            )
        ],
        "Retained shares": [
            _Row(
                "Shares retained from RSUs",
                [r.retained_rsus for r in results],
                total=True,
            )
        ],
    }
    groups["Goal allocations"] = rsu_goals
    people = _people(documents)
    awards = {
        name: _option_rows(adults)
        for name, adults in people
        if any(p and p.option_award for p in adults)
    }
    html = (
        '<div class="fm-comparison"><h3>Equity overview</h3>'
        '<p class="fm-note">RSU vesting, taxes funded from shares, and shares retained by savings goal.</p>'
        + _table(displays, groups, "RSUs", period)
        + '<section class="fm-section"><h3>Stock options</h3>'
        '<p class="fm-note">Vesting during the selected year. These estimates do not change cash flow. Enter ISO exercises separately.</p>'
        + (
            _table(
                displays,
                awards,
                "Stock option vesting and exercise estimates",
                period,
            )
            if awards
            else '<p class="fm-note">No option awards entered.</p>'
        )
        + "</section>"
    )
    if any(p and p.options.shares for _, adults in people for p in adults):
        html += (
            '<section class="fm-section"><h3>Option exercises</h3>'
            '<p class="fm-note">ISO shares exercised and held. Exercise cost is also shown in Cash overview.</p>'
            + _table(
                displays,
                {
                    "": [
                        _Row(
                            "Shares acquired",
                            [
                                Decimal(sum(a.options.shares for a in d.inputs.adults))
                                for d in documents
                            ],
                            "number",
                        ),
                        _Row("Exercise cost", [r.option_cost for r in results]),
                        _Row(
                            "Share value at exercise", [r.option_value for r in results]
                        ),
                        _Row(
                            "Savings goal",
                            [
                                names[
                                    None
                                    if d.inputs.allocation_plan.option_goal_id
                                    == d.inputs.allocation_plan.spending_goal_id
                                    else d.inputs.allocation_plan.option_goal_id
                                ]
                                for d in documents
                            ],
                        ),
                    ]
                },
                "ISO exercises and retained shares",
                period,
            )
            + "</section>"
        )
    return (
        html
        + _definitions(
            {
                "RSUs": "The model assumes shares are delivered at vesting and enough can be sold to fund their incremental taxes. Retained shares are valued at vesting; they are not cash. Savings goals identify their intended use without selling or transferring them.",
                "Stock options": "Vesting counts describe this year's vesting, not the full grant or all vested options. Monthly counts and values are annual averages. Per-share prices are unchanged. Unexercised awards are excluded from cash flow and funded savings.",
                "Estimated intrinsic value": "Options vesting multiplied by the positive difference between assumed share value and exercise price. Excludes taxes and time value; it is not sale proceeds. A zero intrinsic value does not mean the option has no potential value. A sale may not be possible.",
                "Option exercises": "Exercise cost purchases shares; it is not a living expense. Share value uses exercise-date fair market value, not sale proceeds. Any modeled exercise tax is included in the tax calculation. Unexercised award estimates do not affect taxes.",
            }
        )
        + "</div>"
    )
