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


def _cell(row: _Row, index: int, reference: int | None) -> str:
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
                    _cell(row, index, reference) for index in range(len(displays))
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
        f'<thead><tr><th scope="col">Amount</th>{headings}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _definitions(items: dict[str, str]) -> str:
    """Keep explanations available without repeating them above every number."""
    rows = "".join(
        f"<dt>{escape(label)}</dt><dd>{escape(text)}</dd>"
        for label, text in items.items()
    )
    return f'<details class="fm-definitions"><summary>Definitions</summary><dl>{rows}</dl></details>'


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


def overview_html(
    documents: tuple[SavedScenario, ...],
    displays: tuple[ScenarioDisplay, ...] | None = None,
    reference_index: int | None = None,
    *,
    goals: tuple[Goal, ...] = (),
) -> str:
    """Compare planned goal additions and reconcile any unfunded cash."""
    results = [doc.result for doc in documents]
    groups = {
        "Goals · per year": [
            _Row(name, _goal_values(documents, goal_id, "total"))
            for goal_id, name in _goal_names(documents, goals).items()
        ],
        "Total · per year": [
            _Row("Planned additions", [r.allocations.planned_total for r in results]),
            _Row(
                "Less funding gap",
                [-r.allocations.funding_gap for r in results],
                deficit=True,
            ),
            _Row(
                "Net additions",
                [r.net_saved_from_income for r in results],
                total=True,
                deficit=True,
            ),
        ],
        "Taxes": [
            _Row(
                "Effective tax rate", [r.effective_tax_rate for r in results], "percent"
            )
        ],
    }
    return (
        '<div class="fm-comparison"><h3>Annual goal funding</h3>'
        '<p class="fm-note">Planned additions by purpose. A funding gap means the plan needs more cash.</p>'
        + _table(
            displays or scenario_displays(documents),
            groups,
            reference_index,
            "Annual goal funding",
        )
        + _definitions(
            {
                "Planned additions": "Cash, retirement contributions, retained RSUs, and option purchases at cost, each assigned once. Shares retain their value risk and access restrictions.",
                "Net additions": "Planned additions less the cash funding gap. Excludes existing wealth, investment growth, and option gains. Allocating money does not spend it.",
                "Effective tax rate": "Federal and state income taxes plus employee payroll taxes divided by gross salary, bonus, and vested RSUs. Includes RSU-funded and ISO exercise taxes. N/A at zero wages. Differences use percentage points (pp).",
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
        ("Options vesting / year", "number"),
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
        "Annual compensation by person",
    )
    awards = {
        name: _option_rows(adults)
        for name, adults in people
        if any(p and p.option_award for p in adults)
    }
    options = (
        '<section class="fm-section"><h3>Options</h3>'
        '<p class="fm-note">Annual vesting at the assumed share value, before tax. A sale may not be possible.</p>'
        + _table(
            displays,
            awards,
            reference_index,
            "Annual option vesting and exercise estimates",
        )
        if awards
        else '<p class="fm-note">No option awards entered.</p>'
    )
    if awards:
        options += "</section>"
    return (
        '<div class="fm-comparison"><h3>Annual compensation</h3>'
        '<p class="fm-note">Before taxes and your own retirement contributions.</p>'
        + _table(displays, groups, reference_index, "Annual household compensation")
        + f'<details class="fm-breakdown"><summary>By person</summary>{by_person}</details>'
        + options
        + _definitions(
            {
                "RSUs": "Share value at vesting before tax. Retained shares are shown in Goal allocations; value and sale availability can change.",
                "Employer contributions": "In addition to salary. Access is restricted; employer plan vesting is not modeled. Other employer benefits are not modeled.",
                "Option values": "Annual vesting, not the full multiyear grant. Share value if exercised is the option count times assumed share value. Cost to exercise is the count times exercise price. Value above cost is floored at zero and excludes tax.",
                "Option awards": "Estimates do not enter cash, taxes, or saving totals. A $0 estimate excludes future appreciation. Enter actual qualifying ISO exercises separately in the editor.",
            }
        )
        + "</div>"
    )


def allocation_html(
    documents: tuple[SavedScenario, ...],
    displays: tuple[ScenarioDisplay, ...] | None = None,
    reference_index: int | None = None,
    *,
    goals: tuple[Goal, ...] = (),
) -> str:
    """Show each goal's sources without treating all goal assets as accessible cash."""
    displays = displays or scenario_displays(documents)
    names = _goal_names(documents, goals)
    if len(documents) == 1:
        result = documents[0].result
        rows = "".join(
            f'<tr><th scope="row">{escape(names[row.goal_id])}</th>'
            + "".join(
                f"<td>{_number(getattr(row, field))}</td>"
                for field in ("cash", "retirement", "rsus", "option_cost", "total")
            )
            + "</tr>"
            for row in result.allocations.rows
        )
        tables = (
            '<div class="fm-table-scroll" tabindex="0"><table class="fm-table" style="min-width:900px"><caption>Annual goal allocations by asset type</caption><thead><tr><th scope="col">Goal</th><th scope="col">Cash</th><th scope="col">Retirement accounts</th><th scope="col">Retained RSUs</th><th scope="col">Option shares at cost</th><th scope="col">Total planned</th></tr></thead><tbody>'
            + rows
            + "</tbody></table></div>"
        )
    else:
        tables = _table(
            displays,
            {
                "Goals": [
                    _Row(name, _goal_values(documents, goal_id, "total"), total=True)
                    for goal_id, name in names.items()
                ]
            },
            reference_index,
            "Annual goal allocations",
        )
    for goal_id, name in names.items():
        detail = [
            _Row(label, _goal_values(documents, goal_id, field))
            for label, field in (
                ("Cash", "cash"),
                ("Retirement accounts", "retirement"),
                ("Retained RSUs", "rsus"),
                ("Option shares at cost", "option_cost"),
            )
        ]
        if len(documents) > 1:
            tables += (
                f'<details class="fm-breakdown"><summary>{escape(name)} · asset breakdown</summary>'
                + _table(
                    displays,
                    {"": detail},
                    reference_index,
                    f"{name}: annual asset breakdown",
                )
                + "</details>"
            )
    totals = {
        "Reconciliation": [
            _Row(
                "Planned additions",
                [d.result.allocations.planned_total for d in documents],
            ),
            _Row(
                "Less funding gap",
                [-d.result.allocations.funding_gap for d in documents],
                deficit=True,
            ),
            _Row(
                "Net additions",
                [d.result.net_saved_from_income for d in documents],
                total=True,
                deficit=True,
            ),
        ]
    }
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
        '<div class="fm-comparison"><h3>Annual goal allocations</h3>'
        '<p class="fm-note">Purpose and asset type. Shares are not available cash; retirement withdrawals may be restricted.</p>'
        + tables
        + _table(displays, totals, reference_index, "Goal allocation reconciliation")
        + _definitions(
            {
                "Funding gap": "Goal amounts are planned. Subtract missing cash funding to reconcile net additions; no reserve withdrawal or share sale is assumed.",
                "Option shares": "Allocated at purchase cost. Exercise spread and unexercised options are excluded from goal funding. Values can change and sales may be restricted.",
            }
        )
        + "</div>"
    )


def _monthly_groups(
    document: SavedScenario, goals: tuple[Goal, ...] = ()
) -> dict[str, dict[str, Decimal]]:
    """Separate spending from goal transfers, including the named cash remainder."""
    result, costs = document.result, document.inputs.living_costs
    names = _goal_names((document,), goals)
    return {
        "Money in": {"Salary + bonus": result.gross_cash / 12},
        "Taxes & living costs": {
            "Taxes paid from cash": -result.cash_funded_tax / 12,
            "Rent": -costs.rent,
            "Childcare budget": -costs.childcare,
            "Other living costs": -costs.other,
        },
        "Transfers into assets": {
            "Your 401(k)s": -result.employee_401k / 12,
            "ISO exercise cost": -result.option_cost / 12,
        },
        "Cash available for goals": {
            "Available before allocation": result.cash_added / 12
        },
        "Cash allocations": {
            names[row.goal_id]: -row.cash / 12 for row in result.allocations.rows
        },
        "Funding": {"Additional cash needed": result.allocations.funding_gap / 12},
    }


def cash_flow_html(
    documents: tuple[SavedScenario, ...],
    displays: tuple[ScenarioDisplay, ...] | None = None,
    reference_index: int | None = None,
    *,
    goals: tuple[Goal, ...] = (),
) -> str:
    """Show monthly cash sources, uses, goal destinations, and funding needs."""
    # Use union goal names so different plans align by identity, not row position.
    names = _goal_names(documents, goals)
    monthly = [
        _monthly_groups(
            d, tuple(Goal(id=key, name=name) for key, name in names.items())
        )
        for d in documents
    ]
    groups = {}
    for group in monthly[0]:
        labels = list(dict.fromkeys(label for rows in monthly for label in rows[group]))
        groups[group] = [
            _Row(
                label,
                [rows[group].get(label, ZERO) for rows in monthly],
                total=group in ("Cash available for goals", "Funding"),
                deficit=group == "Cash available for goals",
            )
            for label in labels
            if label != "ISO exercise cost"
            or any(rows[group].get(label, ZERO) for rows in monthly)
        ]
    return (
        '<div class="fm-comparison"><h3>Average monthly cash flow</h3>'
        '<p class="fm-note">Annual amounts ÷ 12. Goal allocations remain your assets.</p>'
        + _table(
            displays or scenario_displays(documents),
            groups,
            reference_index,
            "Average monthly cash flow",
        )
        + _definitions(
            {
                "Taxes paid from cash": "Excludes taxes funded from vested RSUs; retained shares are allocated separately.",
                "Cash allocations": "Fixed amounts plus the remainder assigned to its named goal. A funding gap is additional cash needed, not an automatic withdrawal.",
                "Monthly averages": "Rounded to whole dollars. Retirement and retained shares are not cash income. Available before allocation is a subtotal.",
            }
        )
        + "</div>"
    )
