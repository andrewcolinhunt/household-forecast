"""Scenario tables, accounting boundaries, and differences for synthetic households."""

from __future__ import annotations

import re
from decimal import Decimal
from html import escape, unescape

import pytest

from financial_model.comparison import (
    _monthly_groups,
    _number,
    allocation_html,
    cash_flow_html,
    compensation_html,
    overview_html,
)
from financial_model.model import evaluate_scenario
from financial_model.schemas import OptionAward, SavedScenario, Scenario

RENDERERS = (overview_html, cash_flow_html, compensation_html, allocation_html)


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
            assert table.count('scope="col"') == 6
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


def test_overview_reconciles_goals_and_funding_gaps(documents) -> None:
    html = overview_html(documents)
    for index, document in enumerate(documents):
        result = document.result
        for label, value in (
            ("Planned additions", result.allocations.planned_total),
            ("Less funding gap", -result.allocations.funding_gap),
            ("Net additions", result.net_saved_from_income),
        ):
            assert cells(html, label)[index] == _number(value)
        assert cells(html, "Effective tax rate")[index] == _number(
            result.effective_tax_rate, "percent"
        )
        assert result.allocations.net_total == result.net_saved_from_income
    assert "Excludes existing wealth" in html


def test_monthly_groups_reconcile_cash_for_every_scenario(documents) -> None:
    for document in documents:
        groups = _monthly_groups(document)
        total = sum(
            value
            for group, rows in groups.items()
            if group != "Cash available for goals"
            for value in rows.values()
        )
        assert abs(total * 12) < Decimal("0.00001")
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
        assert cells(html, "Net additions")[index] == _number(r.net_saved_from_income)
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
    assert "Options vesting / year" not in allocation_html(awards)
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
            "Net additions",
            [d.result.net_saved_from_income for d in documents],
        ),
        (
            allocation_html,
            "Net additions",
            [d.result.net_saved_from_income for d in documents],
        ),
    ):
        html = render(documents, reference_index=reference)
        assert (
            f'<span class="fm-scenario-name">{documents[reference].inputs.name}</span>'
            in html
        )
        headings = re.findall(r'<th scope="col">(.*?)</th>', html)
        assert '<span class="fm-reference">Reference</span>' in headings[reference + 1]
        assert sum('class="fm-reference"' in heading for heading in headings[:5]) == 1
        assert "California · 2025" in html
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
    for render in (overview_html, cash_flow_html, allocation_html):
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
    html = overview_html((first, second), goals=(renamed, other))
    assert cells(html, "Travel") == ["$0", "$1,200"]
    assert cells(html, "Future")[0] == _number(first.result.allocations.rows[0].total)
    assert "Retirement</th>" not in html
    assert first.inputs.allocation_plan.rows[0].goal.name == "Retirement"
