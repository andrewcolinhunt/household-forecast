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
from financial_model.schemas import ZERO, Goal, PersonInputs, SavedScenario

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


def _cash_groups(
    documents: tuple[SavedScenario, ...],
    goals: tuple[Goal, ...] = (),
) -> dict[str, list[_Row]]:
    """Reconcile annual cash pay to goal transfers and any unfunded balance."""
    results = [d.result for d in documents]
    costs = [d.inputs.living_costs for d in documents]
    living = [
        _Row(label, [-12 * getattr(c, field) for c in costs])
        for label, field in (
            ("Rent", "rent"),
            ("Childcare budget", "childcare"),
            ("Other living costs", "other"),
        )
    ]
    if any(r.option_cost for r in results):
        living.append(_Row("ISO share purchases", [-r.option_cost for r in results]))
    return {
        "Pay and deductions": [
            _Row("Gross salary + bonus", [r.gross_cash for r in results]),
            _Row("Taxes paid from cash", [-r.cash_funded_tax for r in results]),
            _Row("Your 401(k) contributions", [-r.employee_401k for r in results]),
            _Row(
                "Cash after taxes and 401(k)",
                [r.gross_cash - r.cash_funded_tax - r.employee_401k for r in results],
                total=True,
                deficit=True,
            ),
        ],
        "Cash after expenses": [
            *living,
            _Row(
                "Cash available before goals",
                [r.cash_added for r in results],
                total=True,
                deficit=True,
            ),
        ],
        "Cash allocations": [
            *[
                _Row(
                    f"Cash to {name}",
                    [-v for v in _goal_values(documents, key, "cash")],
                )
                for key, name in _goal_names(documents, goals).items()
            ],
            _Row(
                "Cash shortfall",
                [-r.allocations.funding_gap for r in results],
                total=True,
                deficit=True,
            ),
        ],
    }


def _goal_totals(documents: tuple[SavedScenario, ...]) -> dict[str, list[_Row]]:
    """Reconcile all asset forms without presenting them as take-home cash."""
    return {
        "Totals": [
            _Row(
                "Total allocated to goals",
                [d.result.allocations.planned_total for d in documents],
            ),
            _Row(
                "Less cash shortfall",
                [-d.result.allocations.funding_gap for d in documents],
                deficit=True,
            ),
            _Row(
                "Net additions to assets",
                [d.result.net_saved_from_income for d in documents],
                total=True,
                deficit=True,
            ),
        ]
    }


def overview_html(
    documents: tuple[SavedScenario, ...],
    displays: tuple[ScenarioDisplay, ...] | None = None,
    reference_index: int | None = None,
    *,
    goals: tuple[Goal, ...] = (),
    period: Period = "Annual",
) -> str:
    """Make the cash calculation the primary overview, with a single time basis."""
    displays = displays or scenario_displays(documents)
    groups = _cash_groups(documents, goals)
    groups["Taxes"] = [
        _Row(
            "Effective tax rate",
            [d.result.effective_tax_rate for d in documents],
            "percent",
        )
    ]
    return (
        '<div class="fm-comparison"><h3>Cash flow overview</h3>'
        + _table(displays, groups, reference_index, "Cash flow overview", period)
        + _definitions(
            {
                "Cash after taxes and 401(k)": "Salary and bonus minus taxes paid from cash and your 401(k) contributions. Before any other payroll deductions included in the living-cost budget. Pay and contributions are set in Assumptions → Compensation.",
                "Cash available before goals": "Cash after taxes and 401(k) minus living costs and ISO share purchases. Living costs are rent + childcare + other costs, set in Assumptions → Living costs. Include insurance and any other deductions not entered separately in that budget, once.",
                "Cash shortfall": "Cash available before goals minus all cash allocations. A negative balance is the cash shortfall. Allocations are set in the Allocations tab. If cash is already negative before goals, expenses and deductions exceed cash pay even without additional goal saving. No borrowing, reserve withdrawal, or extra share sale is assumed.",
                "Cash to a goal": "Money set aside, not spent. The remainder goal receives all cash left after fixed allocations. A zero shortfall means the cash plan balances; it does not mean there is no spending money.",
                "Retirement and shares": "Employer contributions and retained RSUs do not enter spendable cash. Goal allocations shows these assets alongside your 401(k) and additional cash, grouped by purpose.",
                "Effective tax rate": "Federal and state income taxes plus employee payroll taxes divided by salary, bonus, and vested RSUs. Includes RSU-funded taxes. Per-share prices and tax rates do not change with the display period.",
            },
            "How these numbers are calculated",
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


def _person_rows(people: list[PersonInputs | None]) -> list[_Row]:
    """Compare annual compensation for the same named person in each scenario."""
    return [
        _Row("Salary + bonus", [p.cash_pay if p else "—" for p in people]),
        _Row("RSUs vesting before tax", [p.rsu_vesting if p else "—" for p in people]),
        _Row(
            "Employer retirement", [p.employer_retirement if p else "—" for p in people]
        ),
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
        ("Exercise price / share", "price"),
        ("Assumed value / share", "price"),
        ("Share value if exercised", "money"),
        ("Cost to exercise", "money"),
        ("Value above exercise cost", "money"),
    ]
    return [
        _Row(label, [v[index] for v in values], format)
        for index, (label, format) in enumerate(fields)
    ]


def compensation_html(
    documents: tuple[SavedScenario, ...],
    displays: tuple[ScenarioDisplay, ...] | None = None,
    reference_index: int | None = None,
    *,
    goals: tuple[Goal, ...] = (),
    period: Period = "Annual",
) -> str:
    """Separate annual cash, RSUs, retirement, and uncertain option awards."""
    displays = displays or scenario_displays(documents)
    results = [document.result for document in documents]
    groups = {
        "Cash": [_Row("Salary + bonus", [r.gross_cash for r in results])],
        "Shares": [_Row("RSUs vesting before tax", [r.gross_rsus for r in results])],
        "Retirement": [
            _Row("Employer contributions", [r.employer_retirement for r in results])
        ],
    }
    people = _people(documents)
    by_person = _table(
        displays,
        {name: _person_rows(adults) for name, adults in people},
        reference_index,
        f"{period} compensation by person",
        period,
    )
    awards = {
        name: _option_rows(adults)
        for name, adults in people
        if any(p and p.option_award for p in adults)
    }
    options = (
        '<section class="fm-section"><h3>Options</h3>'
        '<p class="fm-note">Vesting at the assumed share value, before tax. Per-share prices stay unchanged. A sale may not be possible.</p>'
        + _table(
            displays,
            awards,
            reference_index,
            f"{period} option vesting and exercise estimates",
            period,
        )
        if awards
        else '<p class="fm-note">No option awards entered.</p>'
    )
    if awards:
        options += "</section>"
    return (
        '<div class="fm-comparison"><h3>Compensation</h3>'
        '<p class="fm-note">Before taxes and your own retirement contributions.</p>'
        + _table(
            displays,
            groups,
            reference_index,
            f"{period} household compensation",
            period,
        )
        + f'<details class="fm-breakdown"><summary>By person</summary>{by_person}</details>'
        + options
        + _definitions(
            {
                "RSUs": "Share value at vesting before tax. Retained shares are shown in Goal allocations; value and sale availability can change.",
                "Employer contributions": "In addition to salary. Access is restricted; employer plan vesting is not modeled. Other employer benefits are not modeled.",
                "Option values": "Vesting for the selected period, not the full multiyear grant. Monthly amounts average the year's vesting, including any cliff. Share value if exercised is the option count times assumed share value. Cost to exercise is the count times exercise price. Value above cost is floored at zero and excludes tax.",
                "Option awards": "Estimates do not enter cash, taxes, or saving totals. A $0 estimate excludes future appreciation. Enter actual qualifying ISO exercises separately in the editor.",
            }
        )
        + "</div>"
    )


def _goal_sources(
    documents: tuple[SavedScenario, ...], goal_id: UUID
) -> dict[str, list[Decimal]]:
    """Split account contributions without counting either source twice."""
    return {
        "Cash": _goal_values(documents, goal_id, "cash"),
        "Your 401(k) contributions": [
            d.result.employee_401k
            if d.inputs.allocation_plan.retirement_goal_id == goal_id
            else ZERO
            for d in documents
        ],
        "Employer contributions": [
            d.result.employer_retirement
            if d.inputs.allocation_plan.retirement_goal_id == goal_id
            else ZERO
            for d in documents
        ],
        "Retained RSUs": _goal_values(documents, goal_id, "rsus"),
        "Option shares at cost": _goal_values(documents, goal_id, "option_cost"),
    }


def allocation_html(
    documents: tuple[SavedScenario, ...],
    displays: tuple[ScenarioDisplay, ...] | None = None,
    reference_index: int | None = None,
    *,
    goals: tuple[Goal, ...] = (),
    period: Period = "Annual",
) -> str:
    """Show goal purposes separately from accounts, asset types, and cash needs."""
    displays = displays or scenario_displays(documents)
    names = _goal_names(documents, goals)
    if len(documents) == 1:
        rows = []
        headers = (
            "Cash",
            "Your 401(k)",
            "Employer contributions",
            "Retained RSUs",
            "Option shares at cost",
            "Total allocated",
        )
        for goal_id, name in names.items():
            values = [v[0] for v in _goal_sources(documents, goal_id).values()]
            values += _goal_values(documents, goal_id, "total")
            rows.append(
                f'<tr><th scope="row">{escape(name)}</th>'
                + "".join(
                    f"<td>{_number(v / period_divisor(period))}</td>" for v in values
                )
                + "</tr>"
            )
        tables = (
            '<div class="fm-table-scroll" tabindex="0"><table class="fm-table" style="min-width:1040px">'
            f'<caption>{period} goal allocations by asset type</caption><thead><tr><th scope="col">Goal</th>'
            + "".join(f'<th scope="col">{h}</th>' for h in headers)
            + "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></div>"
        )
    else:
        tables = _table(
            displays,
            {
                "Goals": [
                    _Row(name, _goal_values(documents, key, "total"), total=True)
                    for key, name in names.items()
                ]
            },
            reference_index,
            f"{period} goal allocations",
            period,
        )
        for goal_id, name in names.items():
            detail = [
                _Row(label, values)
                for label, values in _goal_sources(documents, goal_id).items()
            ]
            tables += (
                f'<details class="fm-breakdown"><summary>{escape(name)} · funding breakdown</summary>'
                + _table(
                    displays,
                    {"": detail},
                    reference_index,
                    f"{name}: {period.lower()} funding breakdown",
                    period,
                )
                + "</details>"
            )
    totals = _goal_totals(documents)
    if any(d.result.option_cost or d.result.option_value for d in documents):
        totals["Exercised options"] = [
            _Row(
                "Shares acquired at exercise value",
                [d.result.option_value for d in documents],
            ),
            _Row(
                "Exercise spread excluded above",
                [d.result.option_value - d.result.option_cost for d in documents],
            ),
        ]
    return (
        '<div class="fm-comparison"><h3>Goal allocations</h3>'
        '<p class="fm-note">A goal is a purpose, not an account. Cash and retained assets are shown separately.</p>'
        + tables
        + _table(displays, totals, reference_index, "Goal allocation totals", period)
        + _definitions(
            {
                "Retirement": "Retirement funding can include your 401(k), employer contributions, additional cash, and retained shares. The goal name does not determine the account or when money can be withdrawn.",
                "Cash": "Cash assigned in Allocations, including the remainder. This is additional to your 401(k) contributions and remains unspent until used.",
                "Total allocated to goals": "The sum of cash, your 401(k), employer contributions, retained RSUs, and option shares at purchase cost. Each asset is counted once. Amounts are planned if cash is short.",
                "Net additions to assets": "Total allocated to goals minus the cash shortfall. Excludes existing wealth, investment growth, and option gains. It is not take-home cash or a retirement-readiness measure.",
                "Cash shortfall": "Missing cash funding shown in Overview. No reserve withdrawal, borrowing, or extra share sale is assumed.",
                "Retained shares": "RSUs are valued after vesting-tax funding. Their value can change and sales may be restricted. Option shares are included at purchase cost; exercise spread and unexercised options are excluded from goal funding.",
            }
        )
        + "</div>"
    )
