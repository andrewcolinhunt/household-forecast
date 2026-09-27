"""Scenario tables, accounting boundaries, and differences for synthetic households."""

from __future__ import annotations

import re
from decimal import Decimal
from html import escape, unescape

import pytest

from financial_model.comparison import (
    _monthly_groups,
    _number,
    cash_flow_html,
    compensation_html,
    overview_html,
    saving_html,
)
from financial_model.model import evaluate_scenario
from financial_model.schemas import OptionAward, SavedScenario, Scenario

RENDERERS = (overview_html, cash_flow_html, compensation_html, saving_html)


def cells(html: str, label: str) -> list[str]:
    """Read each scenario's visible value and optional delta from a named row."""
    row = re.search(rf'<th scope="row">{re.escape(escape(label))}</th>(.*?)</tr>', html)
    assert row is not None, label
    return [
        unescape(re.sub(r"<[^>]+>", " ", value)).strip()
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
    html = render(selected)
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


def test_overview_separates_monthly_cash_from_annual_assets(documents) -> None:
    html = overview_html(documents)
    assert html.index("Per month") < html.index("Per year")
    for index, document in enumerate(documents):
        result = document.result
        assert cells(html, "Cash surplus / shortfall")[index] == _number(
            result.monthly_remaining_cash
        )
        assert cells(html, "Net cash added")[index] == _number(result.cash_added)
        assert cells(html, "Retirement contributions")[index] == _number(
            result.employee_401k + result.employer_retirement
        )
        assert cells(html, "Net saved from income")[index] == _number(
            result.net_saved_from_income
        )
    assert cells(html, "Cash moved into option shares") == [
        "$0",
        "$3,000",
        "$3,000",
        "$3,000",
    ]
    assert "Cash moved into option shares</th>" not in overview_html(documents[:1])


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
    assert cells(html, "ISO exercise cost") == [
        "$0",
        "\N{MINUS SIGN}$250",
        "\N{MINUS SIGN}$250",
        "\N{MINUS SIGN}$250",
    ]
    assert "ISO exercise cost</th>" not in cash_flow_html(documents[:1])


def test_saving_reconciles_income_and_excludes_option_spread(documents) -> None:
    html = saving_html(documents)
    assert "How this total adds up" not in html
    for index, document in enumerate(documents):
        r = document.result
        assert (
            r.net_saved_from_income
            == r.gross_cash
            + r.gross_rsus
            + r.employer_retirement
            - r.taxes.total
            - r.living_cost
        )
        assert (
            r.net_saved_from_income
            == r.cash_added
            + r.employee_401k
            + r.employer_retirement
            + r.retained_rsus
            + r.option_cost
        )
        assert cells(html, "Net saved from income")[index] == _number(
            r.net_saved_from_income
        )
        assert cells(html, "Net cash added")[index] == _number(
            r.extra_savings + r.annual_remaining_cash
        )
    assert cells(html, "Value above exercise cost") == [
        "$0",
        "$5,000",
        "$5,000",
        "$5,000",
    ]
    assert "Exercised ISOs" in html
    assert "not all spendable cash" in html


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
    html = compensation_html(awards)
    assert cells(html, "Options vesting / year") == ["10,000"] * 4
    assert cells(html, "Exercise price / share") == ["$2.123456"] * 4
    assert cells(html, "Assumed value / share") == ["Not entered", "$0", "$8", "$2"]
    assert cells(html, "Cost to exercise") == ["$21,235"] * 4
    assert cells(html, "Share value if exercised") == [
        "Not estimated",
        "$0",
        "$80,000",
        "$20,000",
    ]
    assert cells(html, "Value above exercise cost") == [
        "Not estimated",
        "$0",
        "$58,765",
        "$0",
    ]
    assert "&lt;Alex &amp; Sam&gt;" in html
    assert "<Alex" not in html
    assert "No option awards entered" in compensation_html(documents)
    assert "Options vesting / year" not in saving_html(awards)
    # One scenario has the person but no award; another has no matching person.
    no_award = awards[0].inputs.adults[0].model_copy(update={"option_award": None})
    absent = awards[0].model_copy(
        update={
            "inputs": awards[0].inputs.model_copy(
                update={"adults": (no_award, awards[0].inputs.adults[1])}
            )
        }
    )
    mixed = compensation_html((awards[0], absent, documents[1]), reference_index=0)
    assert cells(mixed, "Share value if exercised") == ["Not estimated", "—", "—"]


def test_option_differences_preserve_counts_and_per_share_units(documents) -> None:
    awards = (
        with_award(documents[0], 8),
        with_award(documents[1], 9, Decimal("2.123457"), 12000),
    )
    html = compensation_html(awards, reference_index=0)
    assert cells(html, "Options vesting / year") == ["10,000", "12,000  +2,000"]
    assert cells(html, "Exercise price / share") == [
        "$2.123456",
        "$2.123457  +$0.000001",
    ]
    assert cells(html, "Assumed value / share") == ["$8", "$9  +$1"]
    assert cells(html, "Option type") == ["NSO", "NSO"]
    unknown = compensation_html(
        (with_award(documents[0], None), awards[1]), reference_index=0
    )
    assert cells(unknown, "Assumed value / share") == ["Not entered", "$9"]


@pytest.mark.parametrize("reference", range(4))
def test_differences_keep_row_units_reference_and_column_order(
    documents, reference
) -> None:
    for render, label, amounts in (
        (compensation_html, "Salary + bonus", [d.result.gross_cash for d in documents]),
        (
            cash_flow_html,
            "Salary + bonus",
            [d.result.gross_cash / 12 for d in documents],
        ),
        (cash_flow_html, "Rent", [-d.inputs.living_costs.rent for d in documents]),
        (
            overview_html,
            "Net saved from income",
            [d.result.net_saved_from_income for d in documents],
        ),
        (saving_html, "Net cash added", [d.result.cash_added for d in documents]),
    ):
        html = render(documents, reference_index=reference)
        assert (
            f'{documents[reference].inputs.name}<span class="fm-reference">Reference</span>'
            in html
        )
        values = cells(html, label)
        assert values[reference] == _number(amounts[reference])
        for index, amount in enumerate(amounts):
            if index == reference:
                continue
            delta = amount - amounts[reference]
            expected = (
                "No change"
                if not delta
                else ("+" if delta > 0 else "") + _number(delta)
            )
            assert values[index] == f"{_number(amount)}  {expected}"
    if reference == 0:
        assert cells(compensation_html(documents, reference_index=0), "Salary + bonus")[
            1
        ].endswith("+$20,000")
        assert cells(cash_flow_html(documents, reference_index=0), "Salary + bonus")[
            1
        ].endswith("+$1,667")
        assert cells(cash_flow_html(documents, reference_index=0), "Rent")[1].endswith(
            "\N{MINUS SIGN}$2,000"
        )


def test_people_align_by_name_when_adult_order_changes(documents) -> None:
    first = documents[1]
    reversed_people = first.model_copy(
        update={
            "inputs": first.inputs.model_copy(
                update={"adults": tuple(reversed(first.inputs.adults))}
            )
        }
    )
    html = compensation_html((first, reversed_people), reference_index=0)
    detail = html.split("<summary>By person</summary>")[1]
    assert cells(detail, "Salary + bonus") == ["$120,000", "$120,000  No change"]
    assert detail.index("Adult A") < detail.index("Adult B")
    duplicate = first.model_copy(
        update={
            "inputs": first.inputs.model_copy(
                update={
                    "adults": tuple(
                        a.model_copy(update={"name": "Sam"})
                        for a in first.inputs.adults
                    )
                }
            )
        }
    )
    html = compensation_html((duplicate, duplicate))
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
    for render in (overview_html, cash_flow_html, saving_html):
        assert 'class="fm-deficit"' in render((document,))
        assert "\N{MINUS SIGN}$" in render((document,))


@pytest.mark.parametrize(
    ("value", "expected"),
    [("-0.4", "$0"), ("0.4", "$0"), ("-0.5", "\N{MINUS SIGN}$1"), ("0.5", "$1")],
)
def test_money_rounding_avoids_negative_zero(value, expected) -> None:
    assert _number(Decimal(value)) == expected


@pytest.mark.parametrize("render", RENDERERS)
def test_identical_values_show_no_change_and_escape_duplicate_names(
    documents, render
) -> None:
    name = '<img src=x onerror="alert(1)">'
    first = documents[0].model_copy(
        update={"inputs": documents[0].inputs.model_copy(update={"name": name})}
    )
    html = render((first,) * 4, reference_index=2)
    assert "<img" not in html
    assert "No change" in html
    assert "+$0" not in html
    for index in range(4):
        assert f"{escape(name)} ({index + 1})" in html
