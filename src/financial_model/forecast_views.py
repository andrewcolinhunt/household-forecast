"""Inspectable forecast breakdowns and explicitly requested sensitivity runs."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

import streamlit as st

from financial_model import forecast_charts as charts
from financial_model import input_forms as forms
from financial_model.forecast_models import SavedForecast
from financial_model.planning import PlanningScenario

if TYPE_CHECKING:
    from financial_model.forecast import ForecastCalculation
    from financial_model.forecast_sensitivity import SensitivityResult


def _view_state(document: SavedForecast) -> dict[str, Any]:
    """Keep shared selections independently of lazily mounted tab widgets."""
    state = cast(dict[str, Any], st.session_state.get("forecast_detail_state", {}))
    if state.get("run_id") != str(document.id):
        summary = document.summaries[0]
        first, last = summary.points[0].year, summary.points[-1].year
        state = {
            "run_id": str(document.id),
            "scenario": 0,
            "path": 1,
            "year": max(first, min(last, *summary.retirement_years)),
            "sensitivity_year": max(first, min(last, max(summary.retirement_years))),
            "real": True,
        }
        st.session_state["forecast_detail_state"] = state
        st.session_state["forecast_sensitivity_results"] = {}
    return state


def _select_plan(document: SavedForecast, state: dict[str, Any]) -> int:
    """Select a frozen Plan by index, including when names are duplicated."""
    key = f"forecast_detail_plan_{document.id}"
    if len(document.summaries) > 1:
        state["scenario"] = st.selectbox(
            "Plan to inspect",
            range(len(document.summaries)),
            index=state["scenario"],
            format_func=lambda index: f"{index + 1}. {document.summaries[index].name}",
            key=key,
            on_change=forms.capture,
            args=(state, "scenario", key),
        )
    else:
        st.caption(document.summaries[0].name)
    return int(state["scenario"])


def _year_label(plan: PlanningScenario, year: int) -> str:
    """Label calendar years with the saved household's ages."""
    household = plan.household
    ages = " / ".join(
        f"{adult.name} {adult.age + year - household.reference_year}"
        for adult in household.adults
        if adult.age is not None and household.reference_year is not None
    )
    return f"{year} · {ages}" if ages else str(year)


def _select_year(
    document: SavedForecast, state: dict[str, Any], *, sensitivity: bool = False
) -> int:
    """Preserve a year inside the selected Plan's modeled horizon."""
    index = state["scenario"]
    years = [point.year for point in document.summaries[index].points]
    field = "sensitivity_year" if sensitivity else "year"
    prefix = "sensitivity" if sensitivity else "detail"
    key = f"forecast_{prefix}_year_{document.id}"
    state[field] = max(years[0], min(years[-1], state[field]))
    if key in st.session_state and st.session_state[key] not in years:
        st.session_state[key] = state[field]
    state[field] = st.selectbox(
        "Inspect year",
        years,
        index=years.index(state[field]),
        format_func=lambda year: _year_label(document.request.plans[index], year),
        key=key,
        on_change=forms.capture,
        args=(state, field, key),
    )
    return int(state[field])


def _path_controls(document: SavedForecast, state: dict[str, Any]) -> None:
    """Use the same actual future and dollar convention across breakdown tabs."""
    summary = document.summaries[state["scenario"]]
    state["path"] = min(state["path"], summary.paths)
    if summary.paths > 1:
        key = f"forecast_detail_path_{document.id}"
        if st.session_state.get(key, 1) > summary.paths:
            st.session_state[key] = state["path"]
        state["path"] = st.number_input(
            "Future number",
            min_value=1,
            max_value=summary.paths,
            value=state["path"],
            step=1,
            key=key,
            on_change=forms.capture,
            args=(state, "path", key),
        )
    key = f"forecast_detail_dollars_{document.id}"
    state["real"] = st.segmented_control(
        "Dollar display",
        (True, False),
        default=state["real"],
        required=True,
        format_func=lambda real: f"{summary.money_year} purchasing power"
        if real
        else "Nominal dollars",
        key=key,
        on_change=forms.capture,
        args=(state, "real", key),
    )


def _ledger(
    document: SavedForecast, state: dict[str, Any]
) -> list[dict[str, float | int]]:
    """Read one complete path; saved summaries contain only Future 1."""
    calculation = cast(
        "ForecastCalculation | None", st.session_state.get("forecast_calculation")
    )
    if calculation is not None and calculation.document.id == document.id:
        return calculation.inspect(state["scenario"], state["path"] - 1)
    if state["path"] == 1:
        return list(document.summaries[state["scenario"]].example)
    st.info("Rerun these inputs to reconstruct other individual futures.")
    return []


def _breakdown_specs(
    view: str,
    plan: PlanningScenario,
    ledger: list[dict[str, float | int]],
    real: bool,
    money_year: int,
) -> list[tuple[str, dict[str, Any]]]:
    """Build every spec before rendering so incomplete saved detail fails clearly."""
    if view == "Cash flow":
        return [
            ("Income and spending", charts.income_spec(plan, ledger, real)),
        ]
    return [
        (
            "Wealth, accessible money, and accounts",
            charts.balances_spec(plan, ledger, real),
        ),
        ("Returns and inflation", charts.returns_spec(ledger)),
        (
            "Spending and withdrawals",
            charts.future_spending_spec(ledger, real, money_year=money_year),
        ),
    ]


def forecast_breakdown(document: SavedForecast, view: str) -> None:
    """Render accepted accounting charts from the selected saved-run path."""
    state = _view_state(document)
    index = _select_plan(document, state)
    _path_controls(document, state)
    plan, summary = document.request.plans[index], document.summaries[index]
    st.caption(
        f"Future {state['path']} of {summary.paths}. Every chart follows this same "
        "complete future, not a path assembled from yearly medians."
    )
    if document.request.settings.mode == "historical":
        start = document.request.settings.history_start_year
        if start is not None:
            st.caption(
                f"Historical replay starts in observed year {start + state['path'] - 1}."
            )
    ledger = _ledger(document, state)
    if not ledger:
        if state["path"] == 1:
            st.info(
                "Annual detail is unavailable in this saved run. Rerun these inputs to reconstruct it."
            )
        return
    try:
        specs = _breakdown_specs(view, plan, ledger, state["real"], summary.money_year)
    except (KeyError, ValueError, StopIteration):
        st.info(
            "Annual detail is incomplete in this saved run. Rerun these inputs to reconstruct it."
        )
        return
    st.caption(
        "Real-dollar flows use start-of-year prices; balances "
        "use year-end prices. Opening-year flows begin on the saved Wealth date. "
        "Accessible balances are before withdrawal taxes."
    )
    for title, spec in specs:
        st.markdown(f"#### {title}")
        st.vega_lite_chart(spec, width="stretch")
    if view == "Cash flow":
        year = _select_year(document, state)
        st.markdown("#### Annual asset waterfall")
        try:
            row = next(row for row in ledger if row["year"] == year)
            spec = charts.waterfall_spec(
                row, state["real"], money_year=summary.money_year
            )
            residual = charts.waterfall_residual(row, state["real"])
        except (KeyError, ValueError, StopIteration):
            st.info(
                "Waterfall detail is incomplete. Rerun these inputs to reconstruct it."
            )
            return
        st.vega_lite_chart(spec, width="stretch")
        st.caption(
            f"Unexplained difference: ${residual:,.2f}. Real-dollar waterfall values all use year-end prices. Transfers and employee contributions stay inside assets."
        )
    with st.expander("Annual ledger"):
        st.caption("All unadjusted monetary columns below are nominal dollars.")
        st.dataframe(ledger, hide_index=True, width="stretch")


@st.cache_data(max_entries=8, show_spinner=False)
def _calculate_sensitivity(
    plan_json: str, _progress: Callable[[str, float], None]
) -> SensitivityResult:
    """Cache fixed sensitivity by exact frozen inputs, excluding the UI callback."""
    from financial_model.forecast_sensitivity import calculate_sensitivity

    return calculate_sensitivity(
        PlanningScenario.model_validate_json(plan_json), progress=_progress
    )


def _sensitivity_rows(result: SensitivityResult, year: int) -> list[dict[str, Any]]:
    """Rank valid endpoint pairs by the largest absolute wealth departure."""
    baseline = next(
        point.wealth_real for point in result.baseline.annual if point.year == year
    )
    rows: list[dict[str, Any]] = []
    for driver in result.drivers:
        if driver.low.status != "ok" or driver.high.status != "ok":
            continue
        pair: list[dict[str, Any]] = []
        for direction, case in (
            ("Lower input", driver.low),
            ("Higher input", driver.high),
        ):
            wealth = next(
                point.wealth_real for point in case.annual if point.year == year
            )
            pair.append(
                {
                    "Driver": driver.key,
                    "Assumption": driver.label,
                    "Tested range": driver.range_label,
                    "Input": direction,
                    "Setting": case.label,
                    "Change": wealth - baseline,
                    "Net wealth": wealth,
                }
            )
        size = max(abs(row["Change"]) for row in pair)
        rows.extend(row | {"size": size} for row in pair)
    return sorted(rows, key=lambda row: -row["size"])


def _sensitivity_spec(rows: list[dict[str, Any]], money_year: int) -> dict[str, Any]:
    """Compare low and high tested inputs on a common change-in-wealth scale."""
    values = [
        row | {"Label": f"{row['Driver']}|{row['Assumption']}|{row['Tested range']}"}
        for row in rows
    ]
    order = list(dict.fromkeys(row["Label"] for row in values))
    return {
        "data": {"values": values},
        "mark": {"type": "bar"},
        "height": {"step": 64},
        "encoding": {
            "y": {
                "field": "Label",
                "type": "nominal",
                "sort": order,
                "axis": {
                    "title": None,
                    "labelLimit": 200,
                    "labelLineHeight": 16,
                    "labelExpr": "slice(split(datum.label, '|'), 1)",
                },
            },
            "yOffset": {"field": "Input", "sort": ["Lower input", "Higher input"]},
            "x": {
                "field": "Change",
                "type": "quantitative",
                "title": f"Change in net wealth ({money_year} dollars)",
                "axis": {"format": "~s"},
            },
            "color": {
                "field": "Input",
                "type": "nominal",
                "sort": ["Lower input", "Higher input"],
                "legend": {"title": None, "orient": "top"},
            },
            "tooltip": [
                {"field": name} for name in ("Assumption", "Tested range", "Setting")
            ]
            + [{"field": name, "format": "$,.0f"} for name in ("Change", "Net wealth")],
        },
        "autosize": {"type": "fit-x", "contains": "padding", "resize": True},
    }


def forecast_sensitivity(document: SavedForecast) -> None:
    """Calculate influence only on request, then inspect any year without reruns."""
    state = _view_state(document)
    index = _select_plan(document, state)
    year = _select_year(document, state, sensitivity=True)
    plan = document.request.plans[index]
    plan_json = plan.model_dump_json()
    results = st.session_state["forecast_sensitivity_results"]
    st.caption(
        "Fixed assumptions, one input at a time. Compares recurring spending ±20%, each retirement year ±2 years, stock returns and inflation ±1 percentage point, cash pay ±10%, and opening assets ±10%."
    )
    if st.button(
        "Run sensitivity analysis", key=f"forecast_sensitivity_run_{document.id}"
    ):
        progress = st.progress(0.0, text="Preparing sensitivity analysis…")

        def report(message: str, fraction: float) -> None:
            progress.progress(fraction, text=message)

        try:
            results[plan_json] = _calculate_sensitivity(plan_json, report)
        except (ValueError, RuntimeError) as error:
            st.error(str(error))
        finally:
            progress.empty()
    if plan_json not in results:
        st.info(
            "Run sensitivity analysis to rank the assumptions in this saved Plan. It calculates a fixed baseline and up to fourteen variations."
        )
        return
    result = results[plan_json]
    baseline = next(
        point.wealth_real for point in result.baseline.annual if point.year == year
    )
    st.markdown("#### Which assumptions move the forecast most?")
    st.caption(
        f"End of {year} · baseline net wealth ${baseline:,.0f} in {result.money_year} dollars. Sorted by the largest change within the tested ranges; this is not a ranking of probabilities."
    )
    rows = _sensitivity_rows(result, year)
    if rows:
        st.vega_lite_chart(_sensitivity_spec(rows, result.money_year), width="stretch")
    for driver in result.drivers:
        for case in (driver.low, driver.high):
            if case.status != "ok":
                st.warning(
                    f"{driver.label} — {case.label}: {case.error}. This pair is not ranked."
                )
    with st.expander("Tested values and effects"):
        st.dataframe(
            [
                {
                    key: value
                    for key, value in row.items()
                    if key not in {"Driver", "size"}
                }
                for row in rows
            ],
            hide_index=True,
            width="stretch",
        )
        for driver in result.drivers:
            st.caption(f"{driver.label}: {driver.description}")
