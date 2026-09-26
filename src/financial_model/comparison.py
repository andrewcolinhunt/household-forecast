"""Named scenario comparisons rendered from validated calculation snapshots."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from html import escape

from financial_model.schemas import ZERO, SavedScenario, ScenarioResult


def _money(value: Decimal, *, signed: bool = False) -> str:
    """Format whole dollars without displaying a negative rounded zero."""
    rounded = value.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    sign = "\N{MINUS SIGN}" if rounded < 0 else "+" if signed and rounded > 0 else ""
    return f"{sign}${abs(rounded):,.0f}"


def _names(left: SavedScenario, right: SavedScenario) -> tuple[str, str]:
    """Retain scenario names while distinguishing snapshots with equal names."""
    names = left.inputs.name, right.inputs.name
    if names[0] == names[1]:
        return f"{names[0]} (comparison)", f"{names[1]} (current)"
    return names


def _domain(values: list[Decimal]) -> tuple[Decimal, Decimal]:
    """Include zero and all values on a shared nonempty scale."""
    low, high = min(ZERO, *values), max(ZERO, *values)
    return low, high if high != low else Decimal(1)


def _bar(value: Decimal, low: Decimal, high: Decimal, kind: str) -> str:
    """Draw signed values from a visible zero on the shared scale."""
    zero = (ZERO - low) / (high - low) * 100
    position = (value - low) / (high - low) * 100
    start, width = min(zero, position), abs(position - zero)
    return (
        '<div class="fm-track" aria-hidden="true">'
        f'<span class="fm-fill {kind}" style="left:{start:.6f}%;width:{width:.6f}%"></span>'
        f'<span class="fm-zero" style="left:clamp(0px,{zero:.6f}%,calc(100% - 1px))"></span>'
        "</div>"
    )


def _table(
    names: tuple[str, str], groups: dict[str, list[tuple[str, Decimal, Decimal]]]
) -> str:
    """Render grouped numeric rows with separate, escaped scenario headers."""
    rows = []
    for group, values in groups.items():
        if group:
            rows.append(
                f'<tr class="fm-group"><th colspan="3">{escape(group)}</th></tr>'
            )
        rows.extend(
            f'<tr><th scope="row">{escape(label)}</th>'
            f"<td>{_money(a)}</td><td>{_money(b)}</td></tr>"
            for label, a, b in values
        )
    return (
        '<table class="fm-table"><thead><tr><th scope="col">Amount</th>'
        f'<th scope="col">{escape(names[0])}</th>'
        f'<th scope="col">{escape(names[1])}</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _savings_rows(result: ScenarioResult) -> dict[str, Decimal]:
    """Separate cash saving, retirement, retained stock, and purchased shares."""
    return {
        "Your 401(k)s": result.employee_401k,
        "Employer retirement": result.employer_retirement,
        "Other personal savings": result.extra_savings,
        "Stock kept after taxes": result.retained_rsus,
        "Purchased option shares": result.option_value,
    }


def overview_html(left: SavedScenario, right: SavedScenario) -> str:
    """Compare spendable cash and annual asset accumulation without mixing them."""
    names, a, b = _names(left, right), left.result, right.result
    metrics = [
        (
            "Cash left after spending & saving / month",
            a.monthly_remaining_cash,
            b.monthly_remaining_cash,
            "month",
        ),
        (
            "Savings & investments / year",
            a.total_savings_and_assets - a.annual_remaining_cash,
            b.total_savings_and_assets - b.annual_remaining_cash,
            "year",
        ),
        (
            "Assets added if all leftover cash is kept / year",
            a.total_savings_and_assets,
            b.total_savings_and_assets,
            "year",
        ),
    ]
    sections = []
    for index, (label, first, second, period) in enumerate(metrics):
        low, high = _domain([first, second])
        pairs = "".join(
            f"<div><span>{escape(name)}</span><strong>{_money(value)}</strong>"
            f"{_bar(value, low, high, color)}</div>"
            for name, value, color in zip(
                names, (first, second), ("fm-left", "fm-right"), strict=True
            )
        )
        difference = second - first
        delta = (
            f"{escape(names[1])}: {_money(abs(difference))} "
            f"{'more' if difference > 0 else 'less'} per {period}"
            if difference
            else "No difference"
        )
        note = '<p class="fm-note">Excludes leftover cash.</p>' if index == 1 else ""
        sections.append(
            f'<section class="fm-outcome"><h4>{escape(label)}</h4>'
            f'<div class="fm-pair">{pairs}</div><p>{delta}</p>{note}</section>'
        )
    first_savings, second_savings = _savings_rows(a), _savings_rows(b)
    detail = _table(
        names,
        {
            "": [
                (label, value, second_savings[label])
                for label, value in first_savings.items()
                if value or second_savings[label]
            ]
        },
    )
    return (
        f'<div class="fm-comparison">{"".join(sections)}'
        "<details><summary>Savings & investments breakdown / year</summary>"
        f'{detail}<p class="fm-note">Excludes leftover cash. Includes pretax retirement and retained shares.</p>'
        "</details></div>"
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
            "Other personal savings": -result.extra_savings / 12,
            "Option purchases": -result.option_cost / 12,
        },
        "Remaining": {"Cash left over": result.monthly_remaining_cash},
    }


def cash_flow_html(left: SavedScenario, right: SavedScenario) -> str:
    """Show a grouped monthly cash reconciliation for both scenarios."""
    a, b = _monthly_groups(left), _monthly_groups(right)
    groups = {
        group: [
            (label, value, b[group][label])
            for label, value in values.items()
            if label != "Option purchases" or value or b[group][label]
        ]
        for group, values in a.items()
    }
    return (
        '<div class="fm-comparison"><h3>Average monthly cash flow</h3>'
        f"{_table(_names(left, right), groups)}"
        '<p class="fm-note">Annual amounts ÷ 12, rounded. Retained stock and employer retirement appear in Overview.</p></div>'
    )


def difference_steps(
    left: ScenarioResult, right: ScenarioResult
) -> list[tuple[str, Decimal, Decimal]]:
    """Return signed adjustments and running totals that reconcile asset growth."""
    changes = [
        ("Salary + bonus", right.gross_cash - left.gross_cash),
        ("Stock compensation", right.gross_rsus - left.gross_rsus),
        ("Taxes", left.taxes.total - right.taxes.total),
        ("Living costs", left.living_cost - right.living_cost),
        ("Employer retirement", right.employer_retirement - left.employer_retirement),
        ("Option purchases", left.option_cost - right.option_cost),
        ("Purchased option shares", right.option_value - left.option_value),
    ]
    total = ZERO
    rows = []
    optional = {"Stock compensation", "Option purchases", "Purchased option shares"}
    for label, change in changes:
        total += change
        if change or label not in optional:
            rows.append((label, change, total))
    return rows


def difference_html(left: SavedScenario, right: SavedScenario) -> str:
    """Render the approved running-total chart, including negative differences."""
    names = _names(left, right)
    steps = difference_steps(left.result, right.result)
    heading = f"<h3>{escape(names[1])} &minus; {escape(names[0])}</h3>"
    if not any(change for _, change, _ in steps):
        return f'<div class="fm-comparison">{heading}<p>No difference in annual assets added.</p></div>'
    low, high = _domain([total for _, _, total in steps])
    rows = []
    for index, (label, change, total) in enumerate(steps):
        adjustment = f" ({_money(change, signed=True)})" if index else ""
        color = "fm-right" if index == len(steps) - 1 else "fm-left"
        rows.append(
            '<div class="fm-step"><div class="fm-step-label">'
            f'<span>{escape(label)}<span class="fm-adjustment">{adjustment}</span></span>'
            f"<strong>{_money(total)}</strong></div>{_bar(total, low, high, color)}</div>"
        )
    middle = ZERO if low < 0 < high else (low + high) / 2
    position = (middle - low) / (high - low) * 100
    middle_tick = (
        f'<span class="fm-scale-middle" style="left:{position:.6f}%">{_money(middle)}</span>'
        if 20 <= position <= 80
        else ""
    )
    scale = (
        '<div class="fm-scale">'
        f"<span>{_money(low)}</span>{middle_tick}"
        f"<span>{_money(high)}</span></div>"
    )
    return (
        f'<div class="fm-comparison">{heading}<p class="fm-note">{right.inputs.tax_year} · Annual difference ($)</p>'
        "<p>Each bar shows the difference after that adjustment. The final bar is the difference in assets retained.</p>"
        '<div class="fm-columns"><span>Change</span><span>Running total</span></div>'
        f"{''.join(rows)}{scale}"
        '<p class="fm-note">Assumes all leftover cash is saved. Includes retirement and retained shares.</p></div>'
    )
