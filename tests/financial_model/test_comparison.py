"""Scenario tables and accounting boundaries for synthetic households."""

from __future__ import annotations

import re
from decimal import Decimal
from html import escape, unescape

import pytest

from financial_model.comparison import (
    _number,
    cash_flow_html,
    cash_surplus,
    equity_html,
    goal_breakdown_html,
    remaining_cash,
    tax_html,
)
from financial_model.model import evaluate_scenario
from financial_model.schemas import OptionAward, SavedScenario, Scenario

RENDERERS = (cash_flow_html, equity_html, tax_html, goal_breakdown_html)


def cells(html: str, label: str) -> list[str]:
    """Read each scenario's visible value from a named row."""
    row = re.search(rf'<th scope="row">{re.escape(escape(label))}</th>(.*?)</tr>', html)
    assert row is not None, label
    return [
        " ".join(unescape(re.sub(r"<[^>]+>", " ", value)).split())
        for value in re.findall(r"<td>(.*?)</td>", row[1])
    ]


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


@pytest.mark.parametrize("count", [1, 2, 3, 4])
@pytest.mark.parametrize("render", RENDERERS)
def test_tables_include_every_selected_scenario_in_order(
    documents, count, render
) -> None:
    selected = documents[:count]
    snapshots = [document.model_dump() for document in selected]
    html = render(selected)
    assert [document.model_dump() for document in selected] == snapshots
    for table in re.findall(r"<table.*?</table>", html):
        assert table.count('scope="col"') == count + 1
        assert [table.index(document.inputs.name) for document in selected] == sorted(
            table.index(document.inputs.name) for document in selected
        )
    if count < 4:
        assert documents[-1].inputs.name not in html
    assert 'tabindex="0"' in html
    assert "<caption>" in html
    assert 'class="fm-table-scroll"' in html
    assert 'class="fm-delta"' not in html
    assert 'class="fm-track"' not in html


@pytest.mark.parametrize(("period", "divisor"), [("Annual", 1), ("Monthly", 12)])
def test_cash_flow_reconciles_without_deducting_remaining_cash(
    documents, period, divisor
):
    html = cash_flow_html(documents, period=period)
    for index, d in enumerate(documents):
        r = d.result
        surplus = r.gross_cash - r.cash_funded_tax - r.employee_401k - r.living_cost
        balance = surplus - r.option_cost - d.inputs.allocation_plan.fixed_cash
        assert cash_surplus(r) == surplus
        assert remaining_cash(d) == balance
        for label, value in (
            ("Salary and bonus", r.gross_cash),
            ("Income and payroll taxes paid from cash", -r.cash_funded_tax),
            ("Employee 401(k) contributions", -r.employee_401k),
            ("Total living expenses", -r.living_cost),
            ("Cash surplus / deficit", surplus),
            ("Option exercise cost", -r.option_cost),
            ("Spending", balance),
        ):
            assert cells(html, label)[index] == _number(value / divisor)
    assert remaining_cash(documents[0]) > 0
    main = html.split("<details")[0]
    assert "Available to spend</th>" not in main
    assert (
        main.index("Living expenses")
        < main.index("Cash surplus / deficit")
        < main.index("Option exercise cost")
        < main.index("Goal allocations")
    )
    assert "Option exercise cost</th>" not in cash_flow_html(documents[:1])
    assert "Employer 401(k) contributions</th>" not in main
    assert "Shares retained from RSUs</th>" not in main


@pytest.mark.parametrize(("period", "divisor"), [("Annual", 1), ("Monthly", 12)])
def test_rsu_income_taxes_shares_and_goals_reconcile(documents, period, divisor):
    html = equity_html(documents, period=period)
    for i, d in enumerate(documents):
        r = d.result
        for label, value in (
            ("RSU value at vesting", r.gross_rsus),
            ("Taxes funded from vested shares", -r.rsu_tax_funding),
            ("Shares retained from RSUs", r.retained_rsus),
            ("Retirement", r.retained_rsus),
            ("Exercise cost", r.option_cost),
            ("Share value at exercise", r.option_value),
        ):
            assert cells(html, label)[i] == _number(value / divisor)
        assert r.gross_rsus - r.rsu_tax_funding == r.retained_rsus
        assert sum(a.rsus for a in r.allocations.rows) == r.retained_rsus
    assert "Option exercises" in html
    assert "Total allocated to goals" not in html
    assert "Net additions to assets" not in html


@pytest.mark.parametrize(("period", "divisor"), [("Annual", 1), ("Monthly", 12)])
def test_tax_breakdown_reconciles_taxes_and_funding(documents, period, divisor):
    html = tax_html(documents, period=period)
    for index, document in enumerate(documents):
        r = document.result
        for label, value in (
            ("Federal income tax", r.taxes.federal_income_tax),
            ("State income tax", r.taxes.state_income_tax),
            ("Employee payroll taxes", r.taxes.employee_payroll_tax),
            ("Total estimated taxes", r.taxes.total),
            ("Taxes funded from vested shares", -r.rsu_tax_funding),
            ("Income and payroll taxes paid from cash", r.cash_funded_tax),
        ):
            assert cells(html, label)[index] == _number(value / divisor)
        assert (
            r.taxes.federal_income_tax
            + r.taxes.state_income_tax
            + r.taxes.employee_payroll_tax
            == r.taxes.total
        )
        assert r.taxes.total - r.rsu_tax_funding == r.cash_funded_tax
        assert cells(html, "Effective tax rate")[index] == _number(
            r.effective_tax_rate, "percent"
        )
    cash = cash_flow_html(documents, period=period)
    equity = equity_html(documents, period=period)
    assert "<summary>Tax breakdown</summary>" not in cash
    assert "<summary>Income by person</summary>" not in cash
    assert "<summary>Remaining cash and funding shortfalls</summary>" not in cash
    assert "<summary>RSU income by person</summary>" not in equity


@pytest.mark.parametrize(
    ("rent", "saving", "option_cost"),
    [(20000, 0, 0), (20000, 1000, 0), (0, 200000, 0), (0, 0, 200000)],
)
def test_cash_deficit_and_savings_shortfall_have_distinct_causes(
    scenario, rent, saving, option_cost
):
    data = scenario.model_dump(mode="json")
    data["living_costs"]["rent"] = rent
    data["allocation_plan"]["rows"][0]["cash_amount"] = saving
    data["adults"][0]["options"].update(
        shares=int(bool(option_cost)), strike=option_cost, fair_market_value=option_cost
    )
    inputs = Scenario.model_validate(data)
    d = SavedScenario(inputs=inputs, result=evaluate_scenario(inputs))
    html = cash_flow_html((d,))
    deficit = max(-cash_surplus(d.result), Decimal(0))
    investment_shortfall = d.result.allocations.funding_gap - deficit
    assert cells(html, "Cash surplus / deficit") == [_number(cash_surplus(d.result))]
    assert cells(html, "Spending") == [_number(min(d.result.cash_added, Decimal(0)))]
    assert remaining_cash(d) == min(d.result.cash_added, Decimal(0))
    if saving:
        assert 'aria-label="Cash shortfall"' in html
    assert 'class="fm-deficit"' in html or 'class="fm-goal-shortfall"' in html
    if rent:
        assert deficit > 0
    else:
        assert deficit == 0
        assert investment_shortfall > 0


def with_award(document, value, strike=Decimal("2.123456"), shares=10000):
    adult = document.inputs.adults[0].model_copy(
        update={
            "name": "<Alex & Sam>",
            "option_award": OptionAward(
                kind="NSO",
                vesting_shares=shares,
                strike=strike,
                assumed_share_value=value,
            ),
        }
    )
    return document.model_copy(
        update={
            "inputs": document.inputs.model_copy(
                update={"adults": (adult, document.inputs.adults[1])}
            )
        }
    )


def test_options_distinguish_absent_unknown_zero_and_positive_values(documents) -> None:
    awards = tuple(
        with_award(d, value)
        for d, value in zip(documents, (None, 0, 8, 2), strict=True)
    )
    html = equity_html(awards)
    assert cells(html, "Options vesting") == ["10,000"] * 4
    assert cells(html, "Exercise price per share") == ["$2.123456"] * 4
    assert cells(html, "Assumed share value") == ["Not entered", "$0", "$8", "$2"]
    assert cells(html, "Exercise cost") == ["$21,235"] * 4
    assert cells(html, "Share value if exercised") == [
        "Not estimated",
        "$0",
        "$80,000",
        "$20,000",
    ]
    assert cells(html, "Estimated intrinsic value") == [
        "Not estimated",
        "$0",
        "$58,765",
        "$0",
    ]
    assert "&lt;Alex &amp; Sam&gt;" in html
    assert "<Alex" not in html
    assert "No option awards entered" in equity_html(documents)
    assert "Options vesting" not in cash_flow_html(awards)
    # One scenario has the person but no award; another has no matching person.
    no_award = awards[0].inputs.adults[0].model_copy(update={"option_award": None})
    absent = awards[0].model_copy(
        update={
            "inputs": awards[0].inputs.model_copy(
                update={"adults": (no_award, awards[0].inputs.adults[1])}
            )
        }
    )
    mixed = equity_html((awards[0], absent, documents[1]))
    assert cells(mixed, "Share value if exercised") == ["Not estimated", "—", "—"]


def test_option_tables_preserve_counts_and_per_share_units(documents) -> None:
    awards = (
        with_award(documents[0], 8),
        with_award(documents[1], 9, Decimal("2.123457"), 12000),
    )
    html = equity_html(awards)
    assert cells(html, "Options vesting") == ["10,000", "12,000"]
    assert cells(html, "Exercise price per share") == [
        "$2.123456",
        "$2.123457",
    ]
    assert cells(html, "Assumed share value") == ["$8", "$9"]
    assert cells(html, "Option type") == ["NSO", "NSO"]
    unknown = equity_html((with_award(documents[0], None), awards[1]))
    assert cells(unknown, "Assumed share value") == ["Not entered", "$9"]


def test_people_align_by_name_when_adult_order_changes(documents) -> None:
    first = with_award(documents[1], 8)
    reversed_people = first.model_copy(
        update={
            "inputs": first.inputs.model_copy(
                update={"adults": tuple(reversed(first.inputs.adults))}
            )
        }
    )
    html = equity_html((first, reversed_people))
    assert cells(html, "Options vesting") == ["10,000", "10,000"]
    assert cells(html, "Share value if exercised") == ["$80,000", "$80,000"]
    duplicate = first.model_copy(
        update={
            "inputs": first.inputs.model_copy(
                update={
                    "adults": tuple(
                        a.model_copy(
                            update={
                                "name": "Sam",
                                "option_award": first.inputs.adults[0].option_award,
                            }
                        )
                        for a in first.inputs.adults
                    )
                }
            )
        }
    )
    html = equity_html((duplicate, duplicate))
    assert "Sam (1)" in html
    assert "Sam (2)" in html


def test_cash_deficits_remain_visible_with_positive_retirement(scenario) -> None:
    inputs = scenario.model_copy(
        update={
            "living_costs": scenario.living_costs.model_copy(
                update={"rent": Decimal(20000)}
            )
        }
    )
    document = SavedScenario(inputs=inputs, result=evaluate_scenario(inputs))
    assert document.result.cash_added < 0
    for render in (cash_flow_html,):
        assert 'class="fm-deficit"' in render((document,))
        assert "\N{MINUS SIGN}$" in render((document,))


@pytest.mark.parametrize(
    ("value", "expected"),
    [("-0.4", "$0"), ("0.4", "$0"), ("-0.5", "\N{MINUS SIGN}$1"), ("0.5", "$1")],
)
def test_money_rounding_avoids_negative_zero(value, expected) -> None:
    assert _number(Decimal(value)) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0.23770458", "23.8%"), ("-0.0004", "0.0%"), ("-0.005", "\N{MINUS SIGN}0.5%")],
)
def test_percent_rounding_avoids_negative_zero(value, expected) -> None:
    assert _number(Decimal(value), "percent") == expected


def test_tax_rates_preserve_percentages_and_skip_zero_wages(
    documents, monkeypatch
) -> None:
    from financial_model import model
    from financial_model.schemas import TaxResult

    base = documents[0].inputs
    tax_data = documents[0].result.taxes.model_dump()
    for key, value in tax_data.items():
        if isinstance(value, Decimal):
            tax_data[key] = Decimal(0)
    versions = []
    for income, tax in ((200000, 40000), (200000, 50000), (200000, -1000), (0, -1000)):
        inputs = base.model_copy(
            update={
                "adults": tuple(
                    adult.model_copy(update={"cash_pay": Decimal(income) / 2})
                    for adult in base.adults
                )
            }
        )
        taxes = TaxResult.model_validate(tax_data | {"federal_income_tax": tax})
        monkeypatch.setattr(
            model, "calculate_taxes", lambda *args, taxes=taxes, **kwargs: taxes
        )
        versions.append(SavedScenario(inputs=inputs, result=evaluate_scenario(inputs)))
    html = cash_flow_html(tuple(versions))
    assert cells(html, "Effective tax rate") == [
        "20.0%",
        "25.0%",
        "\N{MINUS SIGN}0.5%",
        "N/A",
    ]


@pytest.mark.parametrize("render", RENDERERS)
def test_identical_values_escape_duplicate_names(documents, render) -> None:
    name = '<img src=x onerror="alert(1)">'
    first = documents[0].model_copy(
        update={"inputs": documents[0].inputs.model_copy(update={"name": name})}
    )
    html = render((first,) * 4)
    assert "<img" not in html
    assert 'class="fm-delta"' not in html
    assert "+$0" not in html
    assert f'<span class="fm-scenario-name">{escape(name)}</span>' in html
    assert "Same assumptions" in html
    assert "Saved " in html
    assert "fm-scenario-duplicate" not in html


def test_shared_goal_names_align_by_id_and_missing_goals_use_zero(documents):
    from uuid import uuid4

    from financial_model.schemas import Goal

    first = documents[0]
    retirement = first.inputs.allocation_plan.rows[0].goal
    renamed = retirement.model_copy(update={"name": "Future"})
    other = Goal(id=uuid4(), name="Travel")
    data = documents[1].inputs.model_dump(mode="json")
    data["allocation_plan"]["rows"].append(
        {"goal": other.model_dump(mode="json"), "cash_amount": "1200", "rsu_rate": "0"}
    )
    data["allocation_plan"]["rows"].reverse()
    inputs = Scenario.model_validate(data)
    second = SavedScenario(inputs=inputs, result=evaluate_scenario(inputs))
    html = cash_flow_html((first, second), goals=(renamed, other))
    assert cells(html, "Travel") == ["$0", "$1,200"]
    retirement = goal_breakdown_html((first, second), goals=(renamed, other))
    assert '<th scope="row">Future</th>' in retirement
    assert "Retirement</th>" not in html
    assert first.inputs.allocation_plan.rows[0].goal.name == "Retirement"


@pytest.mark.parametrize(("period", "divisor"), [("Annual", 1), ("Monthly", 12)])
def test_retirement_breakdown_reuses_sources_without_double_deductions(
    documents, period, divisor
):
    detail = goal_breakdown_html(documents, period=period)
    assert "<h3>Retirement savings</h3>" not in cash_flow_html(documents, period=period)
    for i, d in enumerate(documents):
        r = d.result
        goal = r.allocations.rows[0]
        for label, value in (
            ("Employee 401(k) contributions", r.employee_401k),
            ("Employer 401(k) contributions", r.employer_retirement),
            ("Cash allocated", goal.cash),
            ("Shares retained from RSUs", goal.rsus),
            ("Shares from option exercises (at cost)", goal.option_cost),
            ("Total funded", goal.total),
        ):
            assert cells(detail, label)[i] == _number(value / divisor)
        assert (
            r.employee_401k
            + r.employer_retirement
            + goal.cash
            + goal.rsus
            + goal.option_cost
            == goal.total
        )


def test_remainder_is_spending_regardless_of_its_saved_name(
    documents,
):
    data = documents[1].inputs.model_dump(mode="json")
    data["allocation_plan"]["rows"][0]["cash_amount"] = None
    data["allocation_plan"]["rows"][1]["cash_amount"] = "0"
    inputs = Scenario.model_validate(data)
    d = SavedScenario(inputs=inputs, result=evaluate_scenario(inputs))
    html = cash_flow_html((d,))
    assert cells(html, "Spending") == [_number(d.result.cash_added)]
    detail = goal_breakdown_html((d,))
    spending = detail.split('<tr class="fm-group"><th scope="row">Spending</th>')[1]
    assert cells(spending, "Cash balance") == [_number(d.result.cash_added)]


def test_monthly_options_scale_vesting_values_but_not_per_share_prices(documents):
    awards = (
        with_award(documents[0], 8, Decimal("2.123456"), 12000),
        with_award(documents[1], None),
    )
    html = equity_html(awards, period="Monthly")
    assert cells(html, "Options vesting")[0] == "1,000"
    assert cells(html, "Exercise price per share")[0] == "$2.123456"
    assert cells(html, "Assumed share value") == ["$8", "Not entered"]
    assert cells(html, "Share value if exercised") == ["$8,000", "Not estimated"]
    assert cells(html, "Exercise cost")[0] == "$2,123"
    assert cells(html, "Estimated intrinsic value")[0] == "$5,877"
    assert "Annual amounts" not in html


@pytest.mark.parametrize(("period", "divisor"), [("Annual", 1), ("Monthly", 12)])
def test_goal_views_show_cash_priority_shortfalls_and_unused_goals(
    scenario, period, divisor
):
    from financial_model.schemas import Goal, GoalAllocation

    travel = Goal(name="Travel")
    unused = Goal(name="Emergency fund")
    base = scenario.allocation_plan
    plan = base.model_copy(
        update={
            "rows": (
                base.rows[0].model_copy(update={"cash_amount": Decimal(120000)}),
                GoalAllocation(goal=travel, cash_amount=120000),
                base.rows[1],
            )
        }
    )
    inputs = scenario.model_copy(update={"allocation_plan": plan})
    document = SavedScenario(inputs=inputs, result=evaluate_scenario(inputs))
    second_cash = document.result.cash_added - 120000
    assert 0 < second_cash < 120000
    goals = (unused,)
    cash = cash_flow_html((document,), goals=goals, period=period)
    assert cells(cash, "Retirement") == [_number(Decimal(120000) / divisor)]
    assert cells(cash, "Travel") == [
        f"{_number(second_cash / divisor)} {_number(-(120000 - second_cash) / divisor)}"
    ]
    assert cells(cash, "Emergency fund") == ["$0"]
    assert cells(cash, "Spending") == ["$0"]
    for render in (cash_flow_html, equity_html, goal_breakdown_html):
        html = render((document,), goals=goals, period=period)
        table = html.split("</table>")[0]
        assert (
            table.index(">Retirement</th>")
            < table.index(">Travel</th>")
            < table.index(">Emergency fund</th>")
            < table.index(">Spending</th>")
        )
        if render != goal_breakdown_html:
            assert ">Goal allocations</th>" in table
    detail = goal_breakdown_html((document,), goals=goals, period=period)
    travel_group = detail.split('<tr class="fm-group"><th scope="row">Travel</th>')[1]
    assert cells(travel_group, "Cash funding priority") == ["2"]
    assert cells(travel_group, "Cash allocated") == [_number(second_cash / divisor)]
    assert cells(travel_group, "Cash shortfall") == [
        _number(-(120000 - second_cash) / divisor)
    ]
    assert cells(travel_group, "Total funded") == [_number(second_cash / divisor)]


def test_spending_aligns_by_role_when_imported_goal_ids_differ(documents):
    from financial_model.schemas import Goal

    first = documents[1]
    data = first.inputs.model_dump(mode="json")
    data["name"] = "Imported spending goal"
    data["allocation_plan"]["rows"][0]["rsu_rate"] = "0.5"
    data["allocation_plan"]["rows"][1]["rsu_rate"] = "0.5"
    data["allocation_plan"]["rows"][1]["goal"] = Goal(
        name="Old remainder name"
    ).model_dump(mode="json")
    second_inputs = Scenario.model_validate(data)
    second = SavedScenario(
        inputs=second_inputs, result=evaluate_scenario(second_inputs)
    )
    for render in (cash_flow_html, equity_html, goal_breakdown_html):
        html = render((first, second))
        assert html.count(">Spending</th>") == 1
        assert "Old remainder name" not in html
    assert cells(equity_html((first, second)), "Spending") == [
        "$0",
        _number(second.result.retained_rsus / 2),
    ]
    detail = goal_breakdown_html((first, second)).split(
        '<tr class="fm-group"><th scope="row">Spending</th>'
    )[1]
    assert cells(detail, "Cash balance") == [
        _number(remaining_cash(d)) for d in (first, second)
    ]
