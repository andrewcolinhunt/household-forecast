"""Named scenario tables rendered from validated calculation snapshots."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from html import escape
from typing import Literal
from uuid import UUID

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


def _cell(row: _Row, index: int, reference: int | None, period: Period) -> str:
    """Add neutral differences only for pairs of known numeric amounts."""
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
    difference = ""
    baseline = row.values[reference] if reference is not None else None
    if row.format in ("money", "number"):
        value /= period_divisor(period)
        if isinstance(baseline, Decimal):
            baseline /= period_divisor(period)
    if reference != index and isinstance(baseline, Decimal):
        delta = value - baseline
        amount = _number(delta, row.format)
        if amount in ("$0", "0", "0.0%"):
            amount = "No change"
        elif delta > 0:
            amount = f"+{amount}"
        if row.format == "percent":
            amount = amount.replace("%", " pp")
        difference = f'<span class="fm-delta">{amount}</span>'
    kind = ' class="fm-deficit"' if row.deficit and value < 0 else ""
    return f"<td><span{kind}>{_number(value, row.format)}</span>{difference}</td>"


def _table(
    displays: tuple[ScenarioDisplay, ...],
    groups: dict[str, list[_Row]],
    reference: int | None,
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
                + "".join(
                    _cell(row, index, reference, period)
                    for index in range(len(displays))
                )
                + "</tr>"
            )
    headings = "".join(
        f'<th scope="col">{display.html()}'
        + ('<span class="fm-reference">Reference</span>' if index == reference else "")
        + "</th>"
        for index, display in enumerate(displays)
    )
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
) -> dict[UUID, str]:
    """Align purposes by stable identity, using current names when available."""
    snapshots = {
        row.goal.id: row.goal.name
        for doc in documents
        for row in doc.inputs.allocation_plan.rows
    }
    current = {g.id: g.name for g in goals}
    names = {key: current.get(key, name) for key, name in snapshots.items()}
    counts = Counter(names.values())
    return {
        key: f"{name} · {str(key)[:6]}" if counts[name] > 1 else name
        for key, name in names.items()
    }


def _goal_values(
    documents: tuple[SavedScenario, ...], goal_id: UUID, field: str
) -> list[Decimal]:
    """Use zero for a goal absent from a scenario, retaining selected column order."""
    return [
        next(
            (
                getattr(row, field)
                for row in doc.result.allocations.rows
                if row.goal_id == goal_id
            ),
            ZERO,
        )
        for doc in documents
    ]


def cash_surplus(result: ScenarioResult) -> Decimal:
    """Cash after taxes, employee contributions, and expenses, before investing."""
    return result.cash_added + result.option_cost


def remaining_cash(document: SavedScenario) -> Decimal:
    """Signed cash balance after planned savings and option exercise costs."""
    return document.result.cash_added - document.inputs.allocation_plan.fixed_cash


def _cash_groups(
    documents: tuple[SavedScenario, ...], goals: tuple[Goal, ...]
) -> dict[str, list[_Row]]:
    """Show cash deductions once, without subtracting the residual cash itself."""
    results = [d.result for d in documents]
    savings = []
    for key, name in _goal_names(documents, goals).items():
        amounts = [
            next(
                (
                    row.cash_amount or ZERO
                    for row in d.inputs.allocation_plan.rows
                    if row.goal.id == key
                ),
                ZERO,
            )
            for d in documents
        ]
        if any(amounts):
            savings.append(_Row(name, [-v for v in amounts]))
    if any(r.option_cost for r in results):
        savings.append(_Row("Option exercise cost", [-r.option_cost for r in results]))
    if not savings:
        savings.append(_Row("Additional savings", [ZERO for _ in documents]))
    return {
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
        "Planned savings and investments": [
            *savings,
            _Row(
                "Remaining cash / funding shortfall",
                [remaining_cash(d) for d in documents],
                total=True,
                deficit=True,
            ),
        ],
    }


def _retirement_rows(
    documents: tuple[SavedScenario, ...], goals: tuple[Goal, ...]
) -> list[_Row]:
    """Break down the goal receiving retirement contributions without merging liquidity."""
    names = _goal_names(documents, goals)
    additions = [
        next(
            row
            for row in d.result.allocations.rows
            if row.goal_id == d.inputs.allocation_plan.retirement_goal_id
        )
        for d in documents
    ]
    return [
        _Row(
            "Savings goal",
            [names[d.inputs.allocation_plan.retirement_goal_id] for d in documents],
        ),
        _Row(
            "Employee 401(k) contributions", [d.result.employee_401k for d in documents]
        ),
        _Row(
            "Employer retirement contributions",
            [d.result.employer_retirement for d in documents],
        ),
        _Row("Additional savings", [a.cash for a in additions]),
        _Row("Shares retained from RSUs", [a.rsus for a in additions]),
        *(
            [
                _Row(
                    "Shares from option exercises (at cost)",
                    [a.option_cost for a in additions],
                )
            ]
            if any(a.option_cost for a in additions)
            else []
        ),
        _Row("Planned retirement savings", [a.total for a in additions], total=True),
    ]


def cash_flow_html(
    documents: tuple[SavedScenario, ...],
    displays: tuple[ScenarioDisplay, ...] | None = None,
    reference_index: int | None = None,
    *,
    goals: tuple[Goal, ...] = (),
    period: Period = "Annual",
) -> str:
    """Reconcile cash income to expenses, planned saving, and the remaining balance."""
    displays = displays or scenario_displays(documents)
    results = [d.result for d in documents]
    tax_rows = [
        _Row("Federal income tax", [r.taxes.federal_income_tax for r in results]),
        _Row("State income tax", [r.taxes.state_income_tax for r in results]),
        _Row("Employee payroll taxes", [r.taxes.employee_payroll_tax for r in results]),
        _Row("Total estimated taxes", [r.taxes.total for r in results], total=True),
        _Row("Taxes funded from vested shares", [-r.rsu_tax_funding for r in results]),
        _Row(
            "Income and payroll taxes paid from cash",
            [r.cash_funded_tax for r in results],
            total=True,
        ),
    ]
    names = _goal_names(documents, goals)
    destinations = [
        next(
            names[row.goal.id]
            for row in d.inputs.allocation_plan.rows
            if row.cash_amount is None
        )
        for d in documents
    ]
    return (
        '<div class="fm-comparison"><h3>Cash flow</h3>'
        '<p class="fm-note">Income, expenses, planned savings, and remaining cash.</p>'
        + _table(
            displays,
            _cash_groups(documents, goals),
            reference_index,
            "Cash flow",
            period,
        )
        + '<details class="fm-breakdown"><summary>Income by person</summary>'
        + _table(
            displays,
            {
                name: [
                    _Row("Salary and bonus", [p.cash_pay if p else "—" for p in adults])
                ]
                for name, adults in _people(documents)
            },
            reference_index,
            "Cash income by person",
            period,
        )
        + '</details><details class="fm-breakdown"><summary>Tax breakdown</summary>'
        + _table(displays, {"": tax_rows}, reference_index, "Estimated taxes", period)
        + '</details><details class="fm-breakdown"><summary>Remaining cash and funding shortfalls</summary>'
        + _table(
            displays,
            {
                "": [
                    _Row("Remaining cash designated for", destinations),
                    _Row(
                        "Cash-flow deficit",
                        [-max(-cash_surplus(r), ZERO) for r in results],
                        deficit=True,
                    ),
                    _Row(
                        "Savings and investment shortfall",
                        [
                            -(r.allocations.funding_gap - max(-cash_surplus(r), ZERO))
                            for r in results
                        ],
                        deficit=True,
                    ),
                ]
            },
            reference_index,
            "Cash funding details",
            period,
        )
        + '</details><section class="fm-section"><h3>Retirement savings</h3>'
        '<p class="fm-note">Includes contributions above and retained shares. Planned amounts may exceed available funding.</p>'
        + _table(
            displays,
            {"": _retirement_rows(documents, goals)},
            reference_index,
            "Retirement savings",
            period,
        )
        + "</section>"
        + _definitions(
            {
                "Cash surplus / deficit": "Salary and bonus less cash-funded taxes, employee 401(k) contributions, and living expenses. Before additional savings or option exercise costs.",
                "Remaining cash / funding shortfall": "Cash surplus less planned additional savings and option exercise costs. A positive amount remains as cash; a negative amount requires funding. The model assumes no borrowing, reserve withdrawal, or extra share sale. No funding priority among goals is assumed.",
                "Living expenses": "Rent, childcare, and other expenses. Include insurance and other unitemized payroll deductions once. These budget entries do not reduce taxable wages.",
                "Retirement savings": "Contributions and retained assets designated for the goal receiving retirement contributions. This is a breakdown, not another cash deduction. It includes remaining cash only when that goal is its designated destination. Option shares are shown at exercise cost, excluding gains.",
                "Effective tax rate": "Federal and state income taxes plus employee payroll taxes divided by salary, bonus, and vested RSUs. Includes taxes funded from vested shares. Income tax includes any modeled AMT.",
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
    reference_index: int | None = None,
    *,
    goals: tuple[Goal, ...] = (),
    period: Period = "Annual",
) -> str:
    """Reconcile RSU shares and show option awards separately from exercised shares."""
    displays = displays or scenario_displays(documents)
    results = [d.result for d in documents]
    names = _goal_names(documents, goals)
    rsu_goals = [
        _Row(name, values)
        for key, name in names.items()
        if any(values := _goal_values(documents, key, "rsus"))
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
    if rsu_goals:
        groups["Savings goals"] = rsu_goals
    people = _people(documents)
    awards = {
        name: _option_rows(adults)
        for name, adults in people
        if any(p and p.option_award for p in adults)
    }
    html = (
        '<div class="fm-comparison"><h3>Equity compensation</h3>'
        '<p class="fm-note">RSU vesting, taxes funded from shares, and shares retained by savings goal.</p>'
        + _table(displays, groups, reference_index, "RSUs", period)
        + '<details class="fm-breakdown"><summary>RSU income by person</summary>'
        + _table(
            displays,
            {
                name: [
                    _Row(
                        "RSU value at vesting",
                        [p.rsu_vesting if p else "—" for p in adults],
                    )
                ]
                for name, adults in people
            },
            reference_index,
            "RSU income by person",
            period,
        )
        + '</details><section class="fm-section"><h3>Stock options</h3>'
        '<p class="fm-note">Vesting during the selected year. These estimates do not change cash flow. Enter ISO exercises separately.</p>'
        + (
            _table(
                displays,
                awards,
                reference_index,
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
            '<p class="fm-note">ISO shares exercised and held. Exercise cost is also shown in Cash flow.</p>'
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
                                names[d.inputs.allocation_plan.option_goal_id]
                                for d in documents
                            ],
                        ),
                    ]
                },
                reference_index,
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
