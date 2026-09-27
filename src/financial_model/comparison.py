"""Named scenario tables rendered from validated calculation snapshots."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from html import escape
from typing import Literal

from financial_model.schemas import PersonInputs, SavedScenario

NumberFormat = Literal["money", "number", "price"]


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
    precision = Decimal("0.000001") if format == "price" else Decimal(1)
    rounded = value.quantize(precision, rounding=ROUND_HALF_UP)
    sign = "\N{MINUS SIGN}" if rounded < 0 else ""
    amount = (
        f"{abs(rounded):,.6f}".rstrip("0").rstrip(".")
        if format == "price"
        else f"{abs(rounded):,.0f}"
    )
    return f"{sign}{'' if format == 'number' else '$'}{amount}"


def scenario_names(documents: tuple[SavedScenario, ...]) -> tuple[str, ...]:
    """Distinguish repeated names using their positions in the comparison."""
    names = tuple(document.inputs.name for document in documents)
    return tuple(
        f"{name} ({index + 1})" if names.count(name) > 1 else name
        for index, name in enumerate(names)
    )


def _cell(row: _Row, index: int, reference: int | None) -> str:
    """Add neutral differences only for pairs of known numeric amounts."""
    value = row.values[index]
    if not isinstance(value, Decimal):
        label = (
            value
            if value is not None
            else ("Not entered" if row.format == "price" else "Not estimated")
        )
        return f'<td><span class="fm-missing">{escape(label)}</span></td>'
    difference = ""
    baseline = row.values[reference] if reference is not None else None
    if reference != index and isinstance(baseline, Decimal):
        delta = value - baseline
        amount = _number(delta, row.format)
        if amount in ("$0", "0"):
            amount = "No change"
        elif delta > 0:
            amount = f"+{amount}"
        difference = f'<span class="fm-delta">{amount}</span>'
    kind = ' class="fm-deficit"' if row.deficit and value < 0 else ""
    return f"<td><span{kind}>{_number(value, row.format)}</span>{difference}</td>"


def _table(
    names: tuple[str, ...],
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
                f'<td colspan="{len(names)}"></td></tr>'
            )
        for row in values:
            kind = ' class="fm-total"' if row.total else ""
            rows.append(
                f'<tr{kind}><th scope="row">{escape(row.label)}</th>'
                + "".join(_cell(row, index, reference) for index in range(len(names)))
                + "</tr>"
            )
    headings = "".join(
        f'<th scope="col">{escape(name)}'
        + ('<span class="fm-reference">Reference</span>' if index == reference else "")
        + "</th>"
        for index, name in enumerate(names)
    )
    return (
        f'<div class="fm-table-scroll" tabindex="0" role="region" aria-label="{escape(caption)}">'
        f'<table class="fm-table" style="min-width:{240 + 160 * len(names)}px">'
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


def overview_html(
    documents: tuple[SavedScenario, ...],
    names: tuple[str, ...] | None = None,
    reference_index: int | None = None,
) -> str:
    """Summarize monthly cash and annual saving without combining their units."""
    results = [document.result for document in documents]
    annual = [
        _Row("Net cash added", [r.cash_added for r in results], deficit=True),
        _Row(
            "Retirement contributions",
            [r.employee_401k + r.employer_retirement for r in results],
        ),
        _Row("RSUs retained after tax", [r.retained_rsus for r in results]),
    ]
    if any(r.option_cost for r in results):
        annual.append(
            _Row("Cash moved into option shares", [r.option_cost for r in results])
        )
    annual.append(
        _Row(
            "Net saved from income",
            [r.net_saved_from_income for r in results],
            total=True,
            deficit=True,
        )
    )
    groups = {
        "Per month": [
            _Row(
                "Cash surplus / shortfall",
                [r.monthly_remaining_cash for r in results],
                total=True,
                deficit=True,
            )
        ],
        "Per year": annual,
    }
    return (
        '<div class="fm-comparison"><h3>Overview</h3>'
        '<p class="fm-note">Cash remaining each month and amounts added to assets each year.</p>'
        + _table(
            names or scenario_names(documents),
            groups,
            reference_index,
            "Monthly cash and annual saving",
        )
        + _definitions(
            {
                "Cash surplus / shortfall": "Cash left after taxes, living costs, planned saving, and option exercises. Negative amounts require additional cash.",
                "Net cash added": "Planned cash saving plus the annual cash surplus or shortfall. Assumes surplus is kept.",
                "Retirement contributions": "Your 401(k) plus employer contributions. Access is restricted; employer plan vesting is not modeled.",
                "RSUs retained after tax": "Estimated value of shares kept after vesting taxes. Value can change and sales may be restricted.",
                "Net saved from income": "Sum of the annual rows above. Combines cash, retirement, and estimated share value; it is not all spendable cash. Excludes investment growth and option gains.",
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
    names: tuple[str, ...] | None = None,
    reference_index: int | None = None,
) -> str:
    """Separate annual cash, RSUs, retirement, and uncertain option awards."""
    names = names or scenario_names(documents)
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
        names,
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
            names,
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
        + _table(names, groups, reference_index, "Annual household compensation")
        + f'<details class="fm-breakdown"><summary>By person</summary>{by_person}</details>'
        + options
        + _definitions(
            {
                "RSUs": "Share value at vesting before tax. Retained shares are shown in Saving & equity; value and sale availability can change.",
                "Employer contributions": "In addition to salary. Access is restricted; employer plan vesting is not modeled. Other employer benefits are not modeled.",
                "Option values": "Annual vesting, not the full multiyear grant. Share value if exercised is the option count times assumed share value. Cost to exercise is the count times exercise price. Value above cost is floored at zero and excludes tax.",
                "Option awards": "Estimates do not enter cash, taxes, or saving totals. A $0 estimate excludes future appreciation. Enter actual qualifying ISO exercises separately in the editor.",
            }
        )
        + "</div>"
    )


def saving_html(
    documents: tuple[SavedScenario, ...],
    names: tuple[str, ...] | None = None,
    reference_index: int | None = None,
) -> str:
    """Reconcile annual saving, keeping option purchases and gains distinct."""
    names = names or scenario_names(documents)
    results = [document.result for document in documents]
    groups = {
        "Cash": [
            _Row("Additional cash saving", [r.extra_savings for r in results]),
            _Row(
                "Cash surplus / shortfall",
                [r.annual_remaining_cash for r in results],
                deficit=True,
            ),
            _Row(
                "Net cash added",
                [r.cash_added for r in results],
                total=True,
                deficit=True,
            ),
        ],
        "Retirement": [
            _Row("Your 401(k)s", [r.employee_401k for r in results]),
            _Row("Employer contributions", [r.employer_retirement for r in results]),
        ],
        "Shares": [_Row("RSUs retained after tax", [r.retained_rsus for r in results])],
    }
    if any(r.option_cost for r in results):
        groups["Shares"].append(
            _Row("Cash moved into option shares", [r.option_cost for r in results])
        )
    groups["Total"] = [
        _Row(
            "Net saved from income",
            [r.net_saved_from_income for r in results],
            total=True,
            deficit=True,
        )
    ]
    option_detail = ""
    if any(adult.options.shares for d in documents for adult in d.inputs.adults):
        option_detail = (
            '<section class="fm-section"><h3>Exercised ISOs</h3>'
            '<p class="fm-note">Cash moved into shares this year. Exercise gains are excluded from net saving above.</p>'
            + _table(
                names,
                {
                    "": [
                        _Row("Cash paid to exercise", [r.option_cost for r in results]),
                        _Row(
                            "Shares acquired at exercise value",
                            [r.option_value for r in results],
                        ),
                        _Row(
                            "Value above exercise cost",
                            [r.option_value - r.option_cost for r in results],
                        ),
                    ]
                },
                reference_index,
                "Annual ISO purchases",
            )
            + "</section>"
        )
    return (
        '<div class="fm-comparison"><h3>Annual saving &amp; equity</h3>'
        '<p class="fm-note">Additions this year. Existing balances and investment growth are excluded.</p>'
        + _table(names, groups, reference_index, "Annual saving reconciliation")
        + option_detail
        + _definitions(
            {
                "Additional cash saving": "Planned cash saving from the savings goal in the editor, after employee 401(k) contributions. It reduces monthly cash remaining.",
                "Net cash added": "Additional cash saving plus the cash surplus or shortfall. This is a subtotal, not an extra contribution. Assumes surplus is kept.",
                "Retirement": "Your 401(k) plus employer contributions. Access is restricted; future withdrawals may be taxed. Employer plan vesting is not modeled.",
                "Shares": "Retained RSUs are estimated after vesting taxes. Exercised ISO shares are valued at exercise. Values can change and sales may be restricted.",
                "Net saved from income": "Net cash added + retirement + retained RSUs + cash moved into option shares. Equivalently, salary + RSUs + employer retirement minus taxes minus living costs. Combines cash and assets with restricted access; it is not all spendable cash.",
            }
        )
        + "</div>"
    )


def _monthly_groups(document: SavedScenario) -> dict[str, dict[str, Decimal]]:
    """Use cash-funded taxes only, keeping retained equity out of monthly cash."""
    result, costs = document.result, document.inputs.living_costs
    return {
        "Money in": {"Salary + bonus": result.gross_cash / 12},
        "Taxes & living costs": {
            "Taxes paid from cash": -result.cash_funded_tax / 12,
            "Rent": -costs.rent,
            "Childcare budget": -costs.childcare,
            "Other living costs": -costs.other,
        },
        "Money set aside": {
            "Your 401(k)s": -result.employee_401k / 12,
            "Additional cash saving": -result.extra_savings / 12,
            "ISO exercise cost": -result.option_cost / 12,
        },
        "Remaining": {"Cash surplus / shortfall": result.monthly_remaining_cash},
    }


def cash_flow_html(
    documents: tuple[SavedScenario, ...],
    names: tuple[str, ...] | None = None,
    reference_index: int | None = None,
) -> str:
    """Show grouped monthly cash reconciliation in one column per scenario."""
    monthly = [_monthly_groups(document) for document in documents]
    groups = {
        group: [
            _Row(
                label,
                [rows[group][label] for rows in monthly],
                total=group == "Remaining",
                deficit=group == "Remaining",
            )
            for label in values
            if label != "ISO exercise cost"
            or any(rows[group][label] for rows in monthly)
        ]
        for group, values in monthly[0].items()
    }
    return (
        '<div class="fm-comparison"><h3>Average monthly cash flow</h3>'
        '<p class="fm-note">Annual amounts ÷ 12. Minus signs show cash going out.</p>'
        + _table(
            names or scenario_names(documents),
            groups,
            reference_index,
            "Average monthly cash flow",
        )
        + _definitions(
            {
                "Monthly averages": "Rounded to whole dollars; actual paycheck timing varies. Retained shares and employer retirement are not cash income.",
                "Taxes paid from cash": "Excludes taxes funded from vested RSUs. The remaining RSUs are shown in Saving & equity.",
                "Additional cash saving": "Planned cash saving from the savings goal in the editor, after employee 401(k) contributions.",
                "Cash surplus / shortfall": "Cash left after the rows above. A shortfall requires additional cash; saving is not reduced and shares are not sold automatically.",
            }
        )
        + "</div>"
    )
