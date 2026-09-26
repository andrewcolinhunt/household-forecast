"""Comparison accounting and display boundaries using synthetic households."""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal

import pytest

from financial_model.comparison import (
    _monthly_groups,
    cash_flow_html,
    difference_html,
    difference_steps,
    overview_html,
)
from financial_model.model import evaluate_scenario
from financial_model.schemas import SavedScenario, Scenario


@pytest.fixture
def documents(scenario: Scenario) -> tuple[SavedScenario, SavedScenario]:
    """Include different wages, RSUs, retirement, expenses, and ISO purchases."""
    left = SavedScenario(inputs=scenario, result=evaluate_scenario(scenario))
    data = scenario.model_dump(mode="json")
    data["name"] = "Synthetic change"
    data["adults"][0].update(
        cash_pay=120000,
        rsu_vesting=24000,
        employee_401k=12000,
        employer_retirement_rate="0.05",
    )
    data["adults"][0]["options"].update(shares=1000, strike=3, fair_market_value=8)
    data["living_costs"].update(rent=2000, other=3000)
    right_inputs = Scenario.model_validate(data)
    right = SavedScenario(inputs=right_inputs, result=evaluate_scenario(right_inputs))
    return left, right


def test_difference_steps_reconciles_all_asset_sources(
    documents: tuple[SavedScenario, SavedScenario],
) -> None:
    left, right = documents
    steps = difference_steps(left.result, right.result)
    amounts = {label: change for label, change, _ in steps}
    assert amounts["Salary + bonus"] == 20000
    assert amounts["Stock compensation"] == 24000
    assert amounts["Living costs"] == -60000
    assert amounts["Employer retirement"] == 6000
    assert amounts["Option purchases"] == -3000
    assert amounts["Purchased option shares"] == 8000
    assert (
        steps[-1][2]
        == right.result.total_savings_and_assets - left.result.total_savings_and_assets
    )
    reversed_steps = difference_steps(right.result, left.result)
    assert [
        (label, -change, -total) for label, change, total in steps
    ] == reversed_steps


def test_monthly_groups_reconciles_cash_and_option_purchases(
    documents: tuple[SavedScenario, SavedScenario],
) -> None:
    _, document = documents
    groups = _monthly_groups(document)
    total = sum(
        value
        for group, rows in groups.items()
        if group != "Remaining"
        for value in rows.values()
    )
    assert abs(total * 12 - document.result.annual_remaining_cash) < Decimal("0.00001")
    assert groups["Money set aside"]["Option purchases"] == -250
    assert (
        groups["Taxes & living costs"]["Taxes paid from cash"]
        == -document.result.cash_funded_tax / 12
    )
    html = cash_flow_html(*documents)
    assert "Option purchases" in html
    assert "\N{MINUS SIGN}$250" in html


def test_overview_html_separates_leftover_cash_from_savings(
    documents: tuple[SavedScenario, SavedScenario],
) -> None:
    left, right = documents
    html = overview_html(left, right)
    for document in documents:
        planned = (
            document.result.total_savings_and_assets
            - document.result.annual_remaining_cash
        ).quantize(Decimal(1))
        assert f"${planned:,.0f}" in html
    assert "Purchased option shares" in html
    assert "Excludes leftover cash" in html


@pytest.mark.parametrize("reverse", [False, True])
def test_difference_html_mixed_sign_bars_fit_shared_scale(
    documents: tuple[SavedScenario, SavedScenario], reverse: bool
) -> None:
    left, right = reversed(documents) if reverse else documents
    html = difference_html(left, right)
    geometry = re.findall(r'style="left:([\d.]+)%;width:([\d.]+)%"', html)
    assert len(geometry) == 7
    for position, width in geometry:
        assert Decimal(position) >= 0
        assert Decimal(width) >= 0
        assert Decimal(position) + Decimal(width) <= Decimal("100.000001")
    assert html.index('class="fm-scale"') > html.rindex('class="fm-step"')
    assert "\N{MINUS SIGN}$" in html


def test_difference_html_identical_scenarios_has_no_misleading_scale(
    documents: tuple[SavedScenario, SavedScenario],
) -> None:
    left, _ = documents
    html = difference_html(left, left)
    assert "No difference in annual assets added" in html
    assert 'class="fm-track"' not in html


def test_difference_html_zero_near_endpoint_does_not_overlap_tick(
    documents: tuple[SavedScenario, SavedScenario],
) -> None:
    left, right = documents
    data = right.inputs.model_dump(mode="json")
    data["living_costs"]["other"] = 1000000
    inputs = Scenario.model_validate(data)
    right = SavedScenario(inputs=inputs, result=evaluate_scenario(inputs))
    html = difference_html(left, right)
    assert "fm-scale-middle" not in html
    assert 'class="fm-scale"' in html


@pytest.mark.parametrize("render", [overview_html, cash_flow_html, difference_html])
def test_comparison_html_escapes_names_and_keeps_duplicate_columns(
    documents: tuple[SavedScenario, SavedScenario],
    render: Callable[[SavedScenario, SavedScenario], str],
) -> None:
    left, right = documents
    name = '<img src=x onerror="alert(1)">'
    left = left.model_copy(
        update={"inputs": left.inputs.model_copy(update={"name": name})}
    )
    right = right.model_copy(
        update={"inputs": right.inputs.model_copy(update={"name": name})}
    )
    html = render(left, right)
    assert "<img" not in html
    assert "&lt;img" in html
    assert "(comparison)" in html
    assert "(current)" in html
