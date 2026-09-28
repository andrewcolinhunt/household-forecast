"""Scenario tables, accounting boundaries, and differences for synthetic households."""

from __future__ import annotations

import re
from decimal import Decimal
from html import escape, unescape

import pytest

from financial_model.comparison import (
    _number,
    allocation_html,
    compensation_html,
    overview_html,
)
from financial_model.model import evaluate_scenario
from financial_model.schemas import OptionAward, SavedScenario, Scenario

RENDERERS = (overview_html, compensation_html, allocation_html)


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
        if (
            render is allocation_html
            and count == 1
            and "Annual goal allocations by asset type" in table
        ):
            assert table.count('scope="col"') == 7
            continue
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


def test_goal_totals_reconcile_assets_and_cash_shortfalls(documents) -> None:
    html = allocation_html(documents)
    for index, document in enumerate(documents):
        result = document.result
        for label, value in (
            ("Total allocated to goals", result.allocations.planned_total),
            ("Less cash shortfall", -result.allocations.funding_gap),
            ("Net additions to assets", result.net_saved_from_income),
        ):
            assert cells(html, label)[index] == _number(value)
        assert cells(overview_html(documents), "Effective tax rate")[index] == _number(
            result.effective_tax_rate, "percent"
        )
        assert result.allocations.net_total == result.net_saved_from_income
    assert "Excludes existing wealth" in html


@pytest.mark.parametrize(("period", "divisor"), [("Annual", 1), ("Monthly", 12)])
def test_overview_reconciles_cash_for_every_scenario(
    documents, period, divisor
) -> None:
    html = overview_html(documents, period=period)
    for index, document in enumerate(documents):
        r = document.result
        cash_allocated = sum(row.cash for row in r.allocations.rows)
        after_deductions = r.gross_cash - r.cash_funded_tax - r.employee_401k
        assert after_deductions - r.living_cost - r.option_cost == r.cash_added
        assert r.cash_added - cash_allocated == -r.allocations.funding_gap
        for label, value in (
            ("Gross salary + bonus", r.gross_cash),
            ("Taxes paid from cash", -r.cash_funded_tax),
            ("Your 401(k) contributions", -r.employee_401k),
            ("Cash after taxes and 401(k)", after_deductions),
            ("Cash available before goals", r.cash_added),
            ("Cash shortfall", -r.allocations.funding_gap),
        ):
            assert cells(html, label)[index] == _number(value / divisor)
    assert cells(html, "ISO share purchases") == [
        _number(-d.result.option_cost / divisor) for d in documents
    ]
    assert "ISO share purchases</th>" not in overview_html(documents[:1])
    assert (
        html.index("Gross salary + bonus")
        < html.index("Cash after taxes and 401(k)")
        < html.index("Rent")
        < html.index("Cash available before goals")
        < html.index("Cash to Retirement")
        < html.index("Cash shortfall")
    )
    assert "Employer contributions</th>" not in html
    assert "Retained RSUs</th>" not in html


def test_allocations_reconcile_income_and_exclude_option_spread(documents) -> None:
    html = allocation_html(documents)
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
        assert r.net_saved_from_income == r.cash_added + r.investments_added
        assert cells(html, "Net additions to assets")[index] == _number(
            r.net_saved_from_income
        )
        assert cells(html, "Retirement")[index] == _number(r.allocations.rows[0].total)
    assert cells(html, "Exercise spread excluded above") == [
        "$0",
        "$5,000",
        "$5,000",
        "$5,000",
    ]
    assert "Exercised options" in html


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
    assert cells(html, "Options vesting") == ["10,000"] * 4
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
    assert "Options vesting" not in allocation_html(awards)
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
    assert cells(html, "Options vesting") == ["10,000", "12,000  +2,000"]
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
@pytest.mark.parametrize(("period", "divisor"), [("Annual", 1), ("Monthly", 12)])
def test_differences_keep_row_units_reference_and_column_order(
    documents, reference, period, divisor
) -> None:
    for render, label, annual in (
        (compensation_html, "Salary + bonus", [d.result.gross_cash for d in documents]),
        (
            overview_html,
            "Gross salary + bonus",
            [d.result.gross_cash for d in documents],
        ),
        (overview_html, "Rent", [-12 * d.inputs.living_costs.rent for d in documents]),
        (
            allocation_html,
            "Net additions to assets",
            [d.result.net_saved_from_income for d in documents],
        ),
    ):
        html = render(documents, reference_index=reference, period=period)
        headings = re.findall(r'<th scope="col">(.*?)</th>', html)
        assert '<span class="fm-reference">Reference</span>' in headings[reference + 1]
        values = cells(html, label)
        amounts = [v / divisor for v in annual]
        for index, amount in enumerate(amounts):
            delta = amount - amounts[reference]
            expected = (
                "No change"
                if not delta
                else ("+" if delta > 0 else "") + _number(delta)
            )
            assert values[index] == (
                _number(amount)
                if index == reference
                else f"{_number(amount)}  {expected}"
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
    for render in (overview_html, allocation_html):
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


def test_tax_rate_differences_use_percentage_points_and_skip_zero_wages(
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
    html = overview_html(tuple(versions), reference_index=0)
    assert cells(html, "Effective tax rate") == [
        "20.0%",
        "25.0%  +5.0 pp",
        "\N{MINUS SIGN}0.5%  \N{MINUS SIGN}20.5 pp",
        "N/A",
    ]
    html = overview_html(tuple(versions), reference_index=3)
    assert cells(html, "Effective tax rate") == [
        "20.0%",
        "25.0%",
        "\N{MINUS SIGN}0.5%",
        "N/A",
    ]
    html = overview_html((versions[0], versions[0]), reference_index=0)
    assert cells(html, "Effective tax rate") == ["20.0%", "20.0%  No change"]


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
    html = allocation_html((first, second), goals=(renamed, other))
    assert cells(html, "Travel") == ["$0", "$1,200"]
    assert cells(html, "Future")[0] == _number(first.result.allocations.rows[0].total)
    assert "Retirement</th>" not in html
    assert first.inputs.allocation_plan.rows[0].goal.name == "Retirement"


@pytest.mark.parametrize(("period", "divisor"), [("Annual", 1), ("Monthly", 12)])
def test_goal_breakdowns_separate_employee_employer_cash_and_shares(
    documents, period, divisor
):
    document = documents[1]
    r = document.result
    total = r.allocations.rows[0]
    expected = [
        total.cash,
        r.employee_401k,
        r.employer_retirement,
        total.rsus,
        total.option_cost,
        total.total,
    ]
    assert sum(expected[:-1]) == expected[-1]
    single = allocation_html((document,), period=period)
    assert cells(single, "Retirement") == [_number(v / divisor) for v in expected]
    multiple = allocation_html((document, documents[0]), period=period)
    detail = multiple.split("Retirement · funding breakdown</summary>")[1].split(
        "</details>"
    )[0]
    for label, value in zip(
        (
            "Cash",
            "Your 401(k) contributions",
            "Employer contributions",
            "Retained RSUs",
            "Option shares at cost",
        ),
        expected[:-1],
        strict=True,
    ):
        assert cells(detail, label)[0] == _number(value / divisor)


def test_monthly_options_scale_vesting_values_but_not_per_share_prices(documents):
    awards = (
        with_award(documents[0], 8, Decimal("2.123456"), 12000),
        with_award(documents[1], None),
    )
    html = compensation_html(awards, period="Monthly", reference_index=0)
    assert cells(html, "Options vesting")[0] == "1,000"
    assert cells(html, "Exercise price / share")[0] == "$2.123456"
    assert cells(html, "Assumed value / share") == ["$8", "Not entered"]
    assert cells(html, "Share value if exercised") == ["$8,000", "Not estimated"]
    assert cells(html, "Cost to exercise")[0] == "$2,123"
    assert cells(html, "Value above exercise cost")[0] == "$5,877"
    assert "Annual amounts" not in html
