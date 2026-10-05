"""Readable money and cursor inspection across forecast series."""

from __future__ import annotations

from copy import deepcopy

import altair as alt
import pytest

from financial_model import forecast_charts as charts


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "$0.00"),
        (12.345, "$12.35"),
        (1000, "$1.00k"),
        (12500, "$12.50k"),
        (1234567, "$1.23M"),
        (-1234567, "-$1.23M"),
        (1250000000, "$1.25B"),
        (1250000000000, "$1.25T"),
    ],
)
def test_format_money_preserves_two_decimals_at_readable_scale(
    value: float, expected: str
) -> None:
    assert charts.format_money(value) == expected


@pytest.mark.parametrize("percent", [False, True])
def test_line_hover_inspects_every_series_for_nearest_year(percent: bool) -> None:
    values: list[charts.ChartRow] = [
        {"year": year, "series": label, "value": value}
        for year in (2030, 2031)
        for label, value in (("First", 1.234567), ("Second", -0.123456))
    ]
    original = deepcopy(values)
    spec = charts._line_spec(
        values,
        "Annual return" if percent else "Dollars",
        percent=percent,
        events=[{"year": 2031, "event": "Casey retires"}],
    )
    alt.LayerChart.from_dict(spec, validate=True)
    hover = spec["layer"][-1]
    rule, hits = hover["layer"]
    selection = hits["params"][0]["select"]
    assert selection["fields"] == ["year"]
    assert selection["on"] == "pointerover"
    assert selection["clear"] == "pointerout"
    assert rule["mark"]["type"] == "rule"
    assert rule["encoding"]["opacity"]["condition"]["empty"] is False
    assert rule["encoding"]["opacity"]["value"] == 0
    assert [field["title"] for field in hits["encoding"]["tooltip"]] == [
        "Year",
        "First",
        "Second",
        "Milestone",
    ]
    rows = hover["data"]["values"]
    assert rows[0] == {
        "year": 2030,
        "series_0": "123.46%" if percent else "$1.23",
        "series_1": "-12.35%" if percent else "-$0.12",
        "hover_start": 2030,
        "hover_end": 2030.5,
        "event": "—",
    }
    assert rows[1]["event"] == "Casey retires"
    assert rows[0]["hover_end"] == rows[1]["hover_start"]
    assert values == original


def test_year_hover_keeps_one_year_inspectable_and_missing_series_explicit() -> None:
    spec = charts.year_hover_layer(
        [{"year": 2030, "first": "$1.23M"}],
        {"field": "year", "type": "quantitative"},
        [{"field": "first"}, {"field": "second"}],
    )
    row = spec["data"]["values"][0]
    assert row["hover_start"] < 2030 < row["hover_end"]
    assert row["first"] == "$1.23M"
    assert row["second"] == "—"
