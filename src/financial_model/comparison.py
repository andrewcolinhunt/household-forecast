"""Named scenario comparisons rendered from validated calculation snapshots."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from html import escape

from financial_model.schemas import ZERO, SavedScenario, ScenarioResult


def _money(value: Decimal) -> str:
    """Format whole dollars without displaying a negative rounded zero."""
    rounded = value.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    sign = "\N{MINUS SIGN}" if rounded < 0 else ""
    return f"{sign}${abs(rounded):,.0f}"


def scenario_names(documents: tuple[SavedScenario, ...]) -> tuple[str, ...]:
    """Distinguish repeated names using their positions in the comparison."""
    names = tuple(document.inputs.name for document in documents)
    return tuple(
        f"{name} ({index + 1})" if names.count(name) > 1 else name
        for index, name in enumerate(names)
    )


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
    names: tuple[str, ...], groups: dict[str, list[tuple[str, list[Decimal]]]]
) -> str:
    """Render a scrollable comparison with sticky row labels and named columns."""
    rows = []
    for group, values in groups.items():
        if group:
            rows.append(
                f'<tr class="fm-group"><th scope="row">{escape(group)}</th>'
                f'<td colspan="{len(names)}"></td></tr>'
            )
        rows.extend(
            f'<tr><th scope="row">{escape(label)}</th>'
            + "".join(f"<td>{_money(value)}</td>" for value in amounts)
            + "</tr>"
            for label, amounts in values
        )
    headings = "".join(
        f'<th scope="col"><span class="fm-swatch fm-scenario-{index}"></span>{escape(name)}</th>'
        for index, name in enumerate(names)
    )
    return (
        '<div class="fm-table-scroll" tabindex="0" role="region" aria-label="Scenario amounts">'
        f'<table class="fm-table" style="min-width:{200 + 170 * len(names)}px">'
        f'<thead><tr><th scope="col">Amount</th>{headings}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _series(
    names: tuple[str, ...],
    values: list[Decimal],
    low: Decimal,
    high: Decimal,
    *,
    noun: str | None = None,
    skip: int | None = None,
) -> str:
    """Render labeled bars in selection order with one color per scenario."""
    rows = []
    for index, (name, value) in enumerate(zip(names, values, strict=True)):
        if index == skip:
            continue
        amount = _money(value)
        if noun is not None:
            rounded = value.quantize(Decimal(1), rounding=ROUND_HALF_UP)
            amount = (
                f"{_money(abs(value))} {'more' if value > 0 else 'less'} {noun}"
                if rounded
                else "No change"
            )
        rows.append(
            '<div class="fm-series-row"><div class="fm-series-label">'
            f'<span><span class="fm-swatch fm-scenario-{index}"></span>{escape(name)}</span>'
            f"<strong>{escape(amount)}</strong></div>"
            f"{_bar(value, low, high, f'fm-scenario-{index}')}</div>"
        )
    return f'<div class="fm-series">{"".join(rows)}</div>'


def _savings_rows(result: ScenarioResult) -> dict[str, Decimal]:
    """Separate cash saving, retirement, retained stock, and purchased shares."""
    return {
        "Your 401(k)s": result.employee_401k,
        "Employer retirement": result.employer_retirement,
        "Other personal savings": result.extra_savings,
        "Stock kept after taxes": result.retained_rsus,
        "Purchased option shares": result.option_value,
    }


def overview_html(
    documents: tuple[SavedScenario, ...],
    names: tuple[str, ...] | None = None,
) -> str:
    """Compare each scenario's monthly cash, planned saving, and annual assets."""
    names = names or scenario_names(documents)
    results = [document.result for document in documents]
    metrics = [
        (
            "Cash left after spending & saving / month",
            [r.monthly_remaining_cash for r in results],
        ),
        (
            "Savings & investments / year",
            [r.total_savings_and_assets - r.annual_remaining_cash for r in results],
        ),
        (
            "Assets added if all leftover cash is kept / year",
            [r.total_savings_and_assets for r in results],
        ),
    ]
    sections = []
    for index, (label, values) in enumerate(metrics):
        note = '<p class="fm-note">Excludes leftover cash.</p>' if index == 1 else ""
        sections.append(
            f'<section class="fm-outcome"><h4>{escape(label)}</h4>'
            f"{_series(names, values, *_domain(values))}{note}</section>"
        )
    savings = [_savings_rows(result) for result in results]
    detail = _table(
        names,
        {
            "": [
                (label, [rows[label] for rows in savings])
                for label in savings[0]
                if any(rows[label] for rows in savings)
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


def cash_flow_html(
    documents: tuple[SavedScenario, ...],
    names: tuple[str, ...] | None = None,
) -> str:
    """Show grouped monthly cash reconciliation in one column per scenario."""
    monthly = [_monthly_groups(document) for document in documents]
    groups = {
        group: [
            (label, [rows[group][label] for rows in monthly])
            for label in values
            if label != "Option purchases"
            or any(rows[group][label] for rows in monthly)
        ]
        for group, values in monthly[0].items()
    }
    return (
        '<div class="fm-comparison"><h3>Average monthly cash flow</h3>'
        f"{_table(names or scenario_names(documents), groups)}"
        '<p class="fm-note">Annual amounts ÷ 12, rounded. Retained stock and employer retirement appear in Overview.</p></div>'
    )


def component_differences(
    reference: ScenarioResult, other: ScenarioResult
) -> dict[str, Decimal]:
    """Return each component's own difference, with higher costs also positive."""
    return {
        "Salary + bonus": other.gross_cash - reference.gross_cash,
        "Stock compensation": other.gross_rsus - reference.gross_rsus,
        "Taxes": other.taxes.total - reference.taxes.total,
        "Living costs": other.living_cost - reference.living_cost,
        "Employer retirement": other.employer_retirement
        - reference.employer_retirement,
        "Option purchases": other.option_cost - reference.option_cost,
        "Purchased option shares": other.option_value - reference.option_value,
    }


def _difference_scale(extent: Decimal) -> str:
    """Label the symmetric annual-dollar scale shared by component rows."""
    return (
        '<div class="fm-scale"><span>' + _money(-extent) + "</span>"
        '<span class="fm-scale-middle">$0</span>'
        f"<span>{_money(extent)}</span></div>"
    )


def component_differences_html(
    documents: tuple[SavedScenario, ...],
    reference_index: int = 0,
    names: tuple[str, ...] | None = None,
) -> str:
    """Compare independent annual components and show net asset change separately."""
    names = names or scenario_names(documents)
    reference = documents[reference_index]
    differences = [
        component_differences(reference.result, document.result)
        for document in documents
    ]
    net = [
        document.result.total_savings_and_assets
        - reference.result.total_savings_and_assets
        for document in documents
    ]
    extent = max(abs(value) for rows in differences for value in rows.values())
    scale_extent = max(extent, *(abs(value) for value in net)) or Decimal(1)
    nouns = {
        "Salary + bonus": "income",
        "Stock compensation": "stock compensation",
        "Taxes": "tax",
        "Living costs": "spending",
        "Employer retirement": "employer retirement",
        "Option purchases": "in option costs",
        "Purchased option shares": "in option shares",
    }
    optional = {"Stock compensation", "Option purchases", "Purchased option shares"}
    sections = []
    if extent:
        for label, noun in nouns.items():
            values = [rows[label] for rows in differences]
            if label in optional and not any(values):
                continue
            sections.append(
                f'<section class="fm-outcome"><h4>{escape(label)}</h4>'
                f"{_series(names, values, -scale_extent, scale_extent, noun=noun, skip=reference_index)}</section>"
            )
        sections.append(_difference_scale(scale_extent))
    else:
        sections.append("<p>No component differences.</p>")
    net_chart = _series(
        names, net, -scale_extent, scale_extent, noun="in assets", skip=reference_index
    )
    return (
        '<div class="fm-comparison fm-components">'
        f"<p>Annual differences from {escape(names[reference_index])}. Right means more; left means less.</p>"
        f'{"".join(sections)}<section class="fm-net"><h3>Net difference in annual assets added</h3>'
        f'{net_chart}<p class="fm-note">Income and employer retirement add to assets; taxes and spending reduce them. '
        "Assumes leftover cash is saved; includes retirement and retained shares.</p></section></div>"
    )
