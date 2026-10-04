"""Sensitivity comparisons keep calendar years, endpoint direction, and ranges."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import altair as alt
import pytest

from financial_model import forecast_views as views
from financial_model.forecast_models import ForecastExpense, ForecastSummary
from financial_model.forecast_sensitivity import (
    SensitivityCase,
    SensitivityDriver,
    SensitivityPoint,
    SensitivityResult,
)


@pytest.mark.parametrize(("real", "expected"), [(True, 500), (False, 550)])
def test_expense_rows_preserve_item_identity_and_flow_dollars(real, expected):
    first, second = uuid4(), uuid4()
    summary = ForecastSummary.model_construct(
        expenses=tuple(
            ForecastExpense(
                year=year,
                item_id=item,
                label="Coverage",
                category=category,
                frequency="recurring",
                base_amount=amount,
                real_growth=0.02,
                real_amount=amount * 1.02,
            )
            for year, item, category, amount in (
                (2026, first, "healthcare", 1000),
                (2027, first, "healthcare", 500),
                (2027, second, "care", 100),
            )
        )
    )
    ledger = [
        {"year": 2026, "price_index": 1.1, f"expense_{first}": 1000},
        {
            "year": 2027,
            "price_index": 1.21,
            f"expense_{first}": 550,
            f"expense_{second}": 110,
        },
    ]
    rows = views._expense_rows(summary, ledger, 2027, real)
    assert [row["Category"] for row in rows] == ["Healthcare", "Care"]
    assert [row["Planned amount"] for row in rows] == pytest.approx(
        [expected, expected / 5]
    )
    assert [row["Base amount"] for row in rows] == [500, 100]
    assert [row["Annual real amount"] for row in rows] == [510, 102]


def case(label: str, first: float, later: float) -> SensitivityCase:
    """Use distinct calendar years and deliberately different accessible balances."""
    return SensitivityCase(
        label=label,
        status="ok",
        annual=(
            SensitivityPoint(2030, first, 99999),
            SensitivityPoint(2040, later, 88888),
        ),
    )


@pytest.fixture
def sensitivity() -> SensitivityResult:
    return SensitivityResult(
        plan_name="Synthetic household",
        money_year=2026,
        baseline=case("Fixed baseline", 1000, 2000),
        drivers=(
            SensitivityDriver(
                key="spending",
                label="Recurring spending",
                range_label="±20%",
                description="Synthetic recurring spending endpoints",
                low=case("80% of spending", 600, 2100),
                high=case("120% of spending", 1400, 1800),
            ),
            SensitivityDriver(
                key="inflation",
                label="Inflation",
                range_label="1%-3%",
                description="Synthetic inflation endpoints",
                low=case("1% inflation", 900, 1400),
                high=case("3% inflation", 1100, 2400),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("year", "order", "changes"),
    [
        (2030, ["Recurring spending", "Inflation"], [-400, 400, -100, 100]),
        (2040, ["Inflation", "Recurring spending"], [-600, 400, 100, -200]),
    ],
)
def test_sensitivity_rows_rank_absolute_effect_in_selected_calendar_year(
    sensitivity: SensitivityResult,
    year: int,
    order: list[str],
    changes: list[float],
) -> None:
    rows = views._sensitivity_rows(sensitivity, year)
    assert [row["Assumption"] for row in rows] == [
        name for name in order for _ in range(2)
    ]
    assert [row["Change"] for row in rows] == changes
    baseline = 1000 if year == 2030 else 2000
    assert [row["Net wealth"] for row in rows] == [
        baseline + change for change in changes
    ]


def test_sensitivity_rows_keep_input_direction_when_wealth_effects_invert(
    sensitivity: SensitivityResult,
) -> None:
    rows = [
        row
        for row in views._sensitivity_rows(sensitivity, 2040)
        if row["Assumption"] == "Recurring spending"
    ]
    assert [(row["Input"], row["Setting"], row["Change"]) for row in rows] == [
        ("Lower input", "80% of spending", 100),
        ("Higher input", "120% of spending", -200),
    ]
    assert all(row["Tested range"] == "±20%" for row in rows)


@pytest.mark.parametrize("invalid_side", ["low", "high"])
def test_sensitivity_rows_exclude_whole_pair_when_one_endpoint_is_invalid(
    sensitivity: SensitivityResult, invalid_side: str
) -> None:
    invalid = SensitivityCase(
        label="Unavailable endpoint", status="invalid", error="Invalid timing"
    )
    driver = replace(sensitivity.drivers[0], **{invalid_side: invalid})
    result = replace(sensitivity, drivers=(driver, sensitivity.drivers[1]))
    rows = views._sensitivity_rows(result, 2040)
    assert [row["Assumption"] for row in rows] == ["Inflation", "Inflation"]
    assert [row["Change"] for row in rows] == [-600, 400]


def test_sensitivity_spec_validates_and_displays_assumptions_with_tested_ranges(
    sensitivity: SensitivityResult,
) -> None:
    rows = views._sensitivity_rows(sensitivity, 2040)
    original = deepcopy(rows)
    spec = views._sensitivity_spec(rows, sensitivity.money_year)
    alt.Chart.from_dict(spec, validate=True)
    label_field = spec["encoding"]["y"]["field"]
    values = spec["data"]["values"]
    for row in values:
        assert row["Assumption"] in row[label_field]
        assert row["Tested range"] in row[label_field]
    assert "2026 dollars" in spec["encoding"]["x"]["title"]
    assert [row["Input"] for row in values] == [
        "Lower input",
        "Higher input",
        "Lower input",
        "Higher input",
    ]
    assert rows == original
