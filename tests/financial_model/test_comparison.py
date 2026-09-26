"""Multi-scenario display and component differences using synthetic households."""

from __future__ import annotations

import re
from decimal import Decimal
from html import escape

import pytest

from financial_model.comparison import (
    _monthly_groups,
    cash_flow_html,
    component_differences,
    component_differences_html,
    overview_html,
)
from financial_model.model import evaluate_scenario
from financial_model.schemas import SavedScenario, Scenario


@pytest.fixture
def documents(scenario: Scenario) -> tuple[SavedScenario, ...]:
    """Vary wages, RSUs, retirement, expenses, and option purchases in four plans."""
    result = [SavedScenario(inputs=scenario, result=evaluate_scenario(scenario))]
    for index, cash in enumerate((120000, 80000, 160000), start=1):
        data = scenario.model_dump(mode="json")
        data["name"] = f"Synthetic option {index}"
        data["adults"][0].update(
            cash_pay=cash,
            rsu_vesting=24000,
            employee_401k=12000,
            employer_retirement_rate="0.05",
        )
        data["adults"][0]["options"].update(shares=1000, strike=3, fair_market_value=8)
        data["living_costs"].update(rent=2000, other=3000)
        inputs = Scenario.model_validate(data)
        result.append(SavedScenario(inputs=inputs, result=evaluate_scenario(inputs)))
    return tuple(result)


def test_component_differences_reconcile_without_cumulative_totals(documents) -> None:
    left, right = documents[:2]
    amounts = component_differences(left.result, right.result)
    assert amounts["Salary + bonus"] == 20000
    assert amounts["Stock compensation"] == 24000
    assert amounts["Living costs"] == 60000
    assert amounts["Employer retirement"] == 6000
    assert amounts["Option purchases"] == 3000
    assert amounts["Purchased option shares"] == 8000
    assert amounts["Taxes"] == right.result.taxes.total - left.result.taxes.total
    net = (
        amounts["Salary + bonus"]
        + amounts["Stock compensation"]
        - amounts["Taxes"]
        - amounts["Living costs"]
        + amounts["Employer retirement"]
        - amounts["Option purchases"]
        + amounts["Purchased option shares"]
    )
    assert (
        net
        == right.result.total_savings_and_assets - left.result.total_savings_and_assets
    )
    assert component_differences(right.result, left.result) == {
        name: -value for name, value in amounts.items()
    }


@pytest.mark.parametrize("count", [2, 3, 4])
def test_comparison_views_include_every_selected_scenario_in_order(
    documents, count
) -> None:
    selected = documents[:count]
    for render in (overview_html, cash_flow_html, component_differences_html):
        html = render(selected)
        for document in selected:
            assert document.inputs.name in html
        assert documents[-1].inputs.name not in html if count < 4 else True
    overview = overview_html(selected)
    assert overview.count('class="fm-track"') == count * 3
    table = cash_flow_html(selected)
    assert table.count('scope="col"') == count + 1
    assert [table.index(document.inputs.name) for document in selected] == sorted(
        table.index(document.inputs.name) for document in selected
    )
    assert 'tabindex="0"' in table
    assert 'class="fm-table-scroll"' in table


def test_monthly_groups_reconcile_cash_for_every_scenario(documents) -> None:
    for document in documents:
        groups = _monthly_groups(document)
        total = sum(
            value
            for group, rows in groups.items()
            if group != "Remaining"
            for value in rows.values()
        )
        assert abs(total * 12 - document.result.annual_remaining_cash) < Decimal(
            "0.00001"
        )
        assert (
            groups["Taxes & living costs"]["Taxes paid from cash"]
            == -document.result.cash_funded_tax / 12
        )
    html = cash_flow_html(documents)
    assert "Option purchases" in html
    assert "\N{MINUS SIGN}$250" in html


def test_overview_html_separates_leftover_cash_from_savings(documents) -> None:
    html = overview_html(documents)
    for document in documents:
        planned = (
            document.result.total_savings_and_assets
            - document.result.annual_remaining_cash
        ).quantize(Decimal(1))
        assert f"${planned:,.0f}" in html
    assert "Purchased option shares" in html
    assert "Excludes leftover cash" in html


@pytest.mark.parametrize("reference", range(4))
def test_component_bars_use_own_values_and_keep_scenario_colors(
    documents, reference
) -> None:
    html = component_differences_html(documents, reference)
    assert f"Annual differences from {documents[reference].inputs.name}" in html
    assert f'class="fm-swatch fm-scenario-{reference}"' not in html
    for index in range(4):
        if index != reference:
            assert f'class="fm-swatch fm-scenario-{index}"' in html
    for position, width in re.findall(r'style="left:([\d.]+)%;width:([\d.]+)%"', html):
        assert 0 <= Decimal(position) <= 50
        assert Decimal(position) + Decimal(width) <= Decimal("100.000001")
    assert "Running total" not in html
    assert "Net difference in annual assets added" in html
    if reference == 0:
        assert "$20,000 more income" in html
        assert "$20,000 less income" in html
        assert "$60,000 more spending" in html
        assert "more tax" in html
        assert html.index('class="fm-net"') > html.index('class="fm-scale"')


def test_component_differences_identical_values_show_zero_state(documents) -> None:
    left = documents[0]
    html = component_differences_html((left, left))
    assert "No component differences" in html
    assert "No change" in html
    assert 'class="fm-scale"' not in html


@pytest.mark.parametrize(
    "render", [overview_html, cash_flow_html, component_differences_html]
)
def test_comparison_html_escapes_names_and_distinguishes_all_duplicates(
    documents, render
) -> None:
    name = '<img src=x onerror="alert(1)">'
    renamed = tuple(
        document.model_copy(
            update={"inputs": document.inputs.model_copy(update={"name": name})}
        )
        for document in documents
    )
    html = render(renamed)
    assert "<img" not in html
    for index in range(4):
        assert f"{escape(name)} ({index + 1})" in html
