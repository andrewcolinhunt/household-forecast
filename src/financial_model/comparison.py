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


def _metric_sections(
    names: tuple[str, ...], metrics: list[tuple[str, list[Decimal], str]]
) -> str:
    """Show separately defined amounts using the same scenario bars throughout."""
    sections = []
    for label, values, definition in metrics:
        sections.append(
            f'<section class="fm-outcome"><h4>{escape(label)}</h4>'
            f'<p class="fm-note">{escape(definition)}</p>'
            f"{_series(names, values, *_domain(values))}</section>"
        )
    return f'<div class="fm-metrics">{"".join(sections)}</div>'


def _option_awards(documents: tuple[SavedScenario, ...], names: tuple[str, ...]) -> str:
    """Keep uncertain option value separate from compensation paid and cash saving."""
    cards = []
    for index, (document, name) in enumerate(zip(documents, names, strict=True)):
        awards = []
        for adult in document.inputs.adults:
            if award := adult.option_award:
                spread = award.exercise_spread
                value = _money(spread) if spread is not None else "Not estimated"
                price = (
                    f"${award.assumed_share_value:,.6f}".rstrip("0").rstrip(".")
                    if award.assumed_share_value is not None
                    else "Not entered"
                )
                strike = f"${award.strike:,.6f}".rstrip("0").rstrip(".")
                awards.append(
                    f'<div class="fm-award"><p>{escape(adult.name)} · {award.kind}</p>'
                    f"<dl><dt>Options vesting this year</dt><dd>{award.vesting_shares:,}</dd>"
                    f"<dt>Exercise price / share</dt><dd>{strike}</dd>"
                    f"<dt>Assumed share value</dt><dd>{price}</dd>"
                    f"<dt>Value above exercise cost</dt><dd>{value}</dd></dl></div>"
                )
        cards.append(
            '<section class="fm-option-card">'
            f'<h4><span class="fm-swatch fm-scenario-{index}"></span>{escape(name)}</h4>'
            f"{''.join(awards) or '<p>No award entered.</p>'}</section>"
        )
    return (
        '<section class="fm-options"><h3>Options · uncertain value</h3>'
        '<p class="fm-note">Annual vesting, not the full multiyear grant. Estimated value '
        "above exercise cost is before tax, not spendable cash. Exercise requires funding; a sale may not be possible. "
        "A $0 estimate excludes potential future appreciation.</p>"
        f'<div class="fm-metrics">{"".join(cards)}</div></section>'
    )


def compensation_html(
    documents: tuple[SavedScenario, ...], names: tuple[str, ...] | None = None
) -> str:
    """Separate cash pay, vested RSUs, employer contributions, and option awards."""
    names = names or scenario_names(documents)
    results = [document.result for document in documents]
    metrics = [
        (
            "Salary + bonus / year",
            [r.gross_cash for r in results],
            "Cash pay before taxes and saving.",
        ),
        (
            "RSUs vesting / year",
            [r.gross_rsus for r in results],
            "Estimated share value before tax. Shares are not cash; value and sale availability can change.",
        ),
        (
            "Employer retirement / year",
            [r.employer_retirement for r in results],
            "Employer contributions, in addition to your salary.",
        ),
    ]
    options = _option_awards(documents, names)
    return (
        f'<div class="fm-comparison">{_metric_sections(names, metrics)}{options}'
        '<p class="fm-note">Option awards do not enter cash, taxes, or net saving. '
        "Only qualifying ISO exercise-and-hold taxes are modeled. Other employer benefits are not modeled.</p></div>"
    )


def saving_html(
    documents: tuple[SavedScenario, ...], names: tuple[str, ...] | None = None
) -> str:
    """Separate annual contributions and equity, then reconcile net saving."""
    names = names or scenario_names(documents)
    results = [document.result for document in documents]
    metrics = [
        (
            "Cash · net added / year",
            [r.cash_added for r in results],
            "Available cash: planned cash saving plus surplus or shortfall. Assumes surplus is kept.",
        ),
        (
            "Shares · retained RSUs / year",
            [r.retained_rsus for r in results],
            "Estimated value after vesting taxes. Value can change; sale restrictions may apply.",
        ),
        (
            "Retirement · your 401(k) / year",
            [r.employee_401k for r in results],
            "Restricted access. Pretax contributions from salary; future withdrawals may be taxed.",
        ),
        (
            "Retirement · employer / year",
            [r.employer_retirement for r in results],
            "Restricted access. Employer contributions, counted as yours; plan vesting is not modeled.",
        ),
    ]
    option_detail = ""
    if any(adult.options.shares for d in documents for adult in d.inputs.adults):
        option_detail = (
            '<section class="fm-options"><h3>Shares · exercised ISOs</h3>'
            + _table(
                names,
                {
                    "": [
                        ("Cash paid to exercise", [r.option_cost for r in results]),
                        (
                            "Shares acquired at exercise value",
                            [r.option_value for r in results],
                        ),
                        (
                            "Value above exercise cost",
                            [r.option_value - r.option_cost for r in results],
                        ),
                    ]
                },
            )
            + '<p class="fm-note">Shares owned, valued at exercise. Sale availability and current value are unknown. Exercise moves cash into shares; the spread is excluded from net saving.</p></section>'
        )
    reconciliation = _table(
        names,
        {
            "": [
                ("Your 401(k) contributions", [r.employee_401k for r in results]),
                ("Employer retirement", [r.employer_retirement for r in results]),
                ("Additional cash saving", [r.extra_savings for r in results]),
                ("RSUs retained after tax", [r.retained_rsus for r in results]),
                (
                    "Cash surplus / shortfall",
                    [r.annual_remaining_cash for r in results],
                ),
                *(
                    [
                        (
                            "Cash moved into option shares",
                            [r.option_cost for r in results],
                        )
                    ]
                    if any(r.option_cost for r in results)
                    else []
                ),
                ("Net saved from income", [r.net_saved_from_income for r in results]),
            ]
        },
    )
    values = [r.net_saved_from_income for r in results]
    awards = (
        _option_awards(documents, names)
        if any(adult.option_award for d in documents for adult in d.inputs.adults)
        else ""
    )
    return (
        '<div class="fm-comparison"><p class="fm-note">Planned additions this year, separated by access and value certainty. Existing balances are not included.</p>'
        f"{_metric_sections(names, metrics)}{option_detail}{awards}"
        '<section class="fm-net"><h3>Net saved from income / year</h3>'
        '<p class="fm-note">Combined cash, retirement, and estimated RSU value, not spendable cash. '
        "Includes cash shortfalls. Excludes option award values, exercise spread, and investment growth.</p>"
        f"{_series(names, values, *_domain(values))}"
        f'<details><summary>How this total adds up</summary><p class="fm-note">Salary + bonus + RSUs + employer retirement &minus; taxes &minus; living costs. Cash paid for option shares is an asset transfer.</p>{reconciliation}</details></section></div>'
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
) -> str:
    """Show grouped monthly cash reconciliation in one column per scenario."""
    monthly = [_monthly_groups(document) for document in documents]
    groups = {
        group: [
            (label, [rows[group][label] for rows in monthly])
            for label in values
            if label != "ISO exercise cost"
            or any(rows[group][label] for rows in monthly)
        ]
        for group, values in monthly[0].items()
    }
    names = names or scenario_names(documents)
    values = [d.result.monthly_remaining_cash for d in documents]
    return (
        '<div class="fm-comparison"><h3>Monthly cash surplus / shortfall</h3>'
        '<p class="fm-note">After taxes, living costs, planned saving, and option exercises. Negative means more cash is needed.</p>'
        f"{_series(names, values, *_domain(values))}"
        '<section class="fm-options"><h3>Average monthly cash flow</h3>'
        f"{_table(names, groups)}</section>"
        '<p class="fm-note">Annual amounts ÷ 12, rounded; not paycheck timing. Retained shares and employer retirement are not cash income.</p></div>'
    )


def component_differences(
    reference: ScenarioResult, other: ScenarioResult
) -> dict[str, Decimal]:
    """Return each component's own difference, with higher costs also positive."""
    return {
        "Salary + bonus": other.gross_cash - reference.gross_cash,
        "RSUs vesting": other.gross_rsus - reference.gross_rsus,
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
        document.result.net_saved_from_income - reference.result.net_saved_from_income
        for document in documents
    ]
    extent = max(abs(value) for rows in differences for value in rows.values())
    scale_extent = max(extent, *(abs(value) for value in net)) or Decimal(1)
    nouns = {
        "Salary + bonus": "income",
        "RSUs vesting": "in vested RSUs",
        "Taxes": "tax",
        "Living costs": "spending",
        "Employer retirement": "employer retirement",
        "Option purchases": "in option costs",
        "Purchased option shares": "in option shares",
    }
    optional = {"RSUs vesting", "Option purchases", "Purchased option shares"}
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
        names,
        net,
        -scale_extent,
        scale_extent,
        noun="saved from income",
        skip=reference_index,
    )
    return (
        '<div class="fm-comparison fm-components">'
        f"<p>Annual differences from {escape(names[reference_index])}. Right means more; left means less.</p>"
        f'{"".join(sections)}<section class="fm-net"><h3>Difference in annual net saving</h3>'
        f'{net_chart}<p class="fm-note">Income and employer retirement add to assets; taxes and spending reduce them. '
        "Includes cash shortfalls. Option exercise costs and acquired shares affect cash and holdings; their spread is excluded from this total.</p></section></div>"
    )
