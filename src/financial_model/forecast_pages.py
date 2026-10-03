"""Forecast setup, uncertainty curves, and inspectable saved household runs."""

from __future__ import annotations

import csv
import io
from typing import Any

import psycopg
import streamlit as st

from financial_model import input_forms as forms
from financial_model.forecast_models import (
    ForecastRequest,
    ForecastSettings,
    SavedForecast,
)
from financial_model.forecast_paths import parse_history_csv
from financial_model.forecast_storage import (
    create_forecast,
    import_forecast_backup,
    list_forecasts,
)
from financial_model.forecast_tax import FORECAST_TAX_ASSUMPTION
from financial_model.planning import PlanningScenario, SavedPlanningDocument
from financial_model.planning_storage import list_planning_documents

_MODES = {
    "fixed": "Fixed assumptions",
    "monte_carlo": "Monte Carlo",
    "historical": "Historical replay",
    "bootstrap": "Historical blocks",
    "stress": "Adverse scenario",
}


def plan_options(
    records: tuple[SavedPlanningDocument, ...],
) -> dict[str, PlanningScenario]:
    """Expand saved named alternatives without expanding numerical ranges."""
    options = {}
    for record in records:
        plan = record.inputs
        if not isinstance(plan, PlanningScenario):
            continue
        varying = {
            component.inputs.kind
            for component in plan.components
            if sum(c.inputs.kind == component.inputs.kind for c in plan.components) > 1
        }
        for index, combination in enumerate(plan.combinations()):
            name = plan.name
            if varying:
                names = " / ".join(
                    c.inputs.name
                    for c in combination.components
                    if c.inputs.kind in varying
                )
                name = f"{plan.name[:32]} · {index + 1}: {names}"[:80]
            options[f"{record.id}:{index}"] = combination.model_copy(
                update={"name": name}
            )
    return options


def _open_run(document: SavedForecast) -> None:
    """Open exactly the saved assumptions without resolving current Plan revisions."""
    st.session_state["forecast_document"] = document
    st.session_state["forecast_loaded_request"] = document.request
    st.session_state["forecast_settings"] = document.request.settings.model_dump(
        mode="json"
    )
    st.session_state["forecast_name"] = {"name": document.request.name}
    st.session_state["forecast_selected"] = [
        f"loaded:{i}" for i in range(len(document.request.plans))
    ]
    st.session_state["forecast_selection_buffer"] = {
        "selected": st.session_state["forecast_selected"]
    }
    st.session_state["forecast_epoch"] = st.session_state.get("forecast_epoch", 0) + 1
    st.session_state.pop("forecast_calculation", None)


def _saved_runs(dsn: str | None) -> None:
    """Open saved or downloaded runs even when input libraries are unavailable."""
    with st.expander("Open a saved forecast"):
        if dsn:
            try:
                records = list_forecasts(dsn)
            except psycopg.errors.UndefinedTable:
                st.info(
                    "Forecast storage needs the updated scripts/schema.sql. JSON downloads still work."
                )
                records = ()
            except ValueError as error:
                st.error(str(error))
                records = ()
            except psycopg.Error:
                st.error(
                    "Saved forecasts could not be read. Try again or open a downloaded JSON file."
                )
                records = ()
            if records:
                selected = st.selectbox(
                    "Saved run",
                    records,
                    format_func=lambda item: f"{item.request.name} · {item.created_at:%Y-%m-%d %H:%M} UTC",
                )
                if st.button("Open saved run"):
                    _open_run(selected)
                    st.rerun()
        uploaded = st.file_uploader("Forecast JSON", type="json", key="forecast_upload")
        if uploaded is not None and st.button("Open forecast JSON"):
            try:
                _open_run(import_forecast_backup(uploaded.getvalue()))
            except ValueError as error:
                st.error(str(error))
            else:
                st.rerun()


def _plans(dsn: str | None) -> dict[str, PlanningScenario]:
    """Read saved combinations and any explicitly opened frozen request."""
    records = tuple(st.session_state.get("planning_records", {}).get("plan", ()))
    if dsn:
        try:
            records = list_planning_documents(dsn, "plan")
        except (psycopg.Error, ValueError):
            st.error(
                "Saved Plans could not be read. An opened forecast remains available."
            )
    options = plan_options(records)
    if loaded := st.session_state.get("forecast_loaded_request"):
        options.update({f"loaded:{i}": plan for i, plan in enumerate(loaded.plans)})
    return options


def _selection(
    options: dict[str, PlanningScenario], scope: str
) -> tuple[PlanningScenario, ...]:
    """Keep selected alternatives across page navigation and explicit run opens."""
    buffer = st.session_state.setdefault(
        "forecast_selection_buffer",
        {"selected": st.session_state.get("forecast_selected", ())},
    )
    buffer["selected"] = [key for key in buffer["selected"] if key in options]
    key = f"forecast_select_{scope}"
    selected = st.multiselect(
        "Plans to compare (up to four)",
        list(options),
        default=buffer["selected"],
        max_selections=4,
        format_func=lambda item: options[item].name
        + (" · saved run" if item.startswith("loaded:") else ""),
        key=key,
        on_change=forms.capture,
        args=(buffer, "selected", key),
    )
    st.session_state["forecast_selected"] = selected
    return tuple(options[item] for item in selected)


def _assumptions() -> None:
    """Explain fixed model conventions without asking for duplicate household facts."""
    with st.expander("How this forecast works"):
        st.caption(
            "Financial inputs come from each saved Plan. Edit them in Scenarios, "
            "update the Plan's copies, and save the Plan before running again."
        )
        st.caption(
            "Recurring income and spending are prorated from the Wealth snapshot date. "
            "One-time expenses listed for the opening year are planned expenses at year end. "
            "Remove expenses that have already happened from the Plan."
        )
        st.caption(FORECAST_TAX_ASSUMPTION)
        st.caption(
            "Withdrawal policy: required distributions first, then cash, taxable investments, eligible pretax accounts, and Roth funds. Retirement access starts at 60; Roth withdrawals are assumed qualified. Pretax accounts are fully taxable, and retirement accounts pass to the surviving spouse. Taxable sales use proportional average basis and long-term gains, without a basis step-up on death. Stocks use the broad-market return path, including retained employer shares; concentrated-stock risk is not modeled. Annual 401(k) contributions use the regular limit; catch-up contributions are not modeled."
        )


def _scenario_links() -> None:
    """Send missing-input work to its owning category while retaining forecast setup."""
    st.caption(
        "Complete missing inputs in Scenarios. After editing a source, update its copy "
        "in the Plan and save the Plan."
    )
    with st.container(horizontal=True):
        for category in ("Plans", "Wealth", "Retirement", "Economy"):
            if st.button(f"Open {category}", key=f"forecast_open_{category}"):
                st.session_state["pending_category"] = category
                st.session_state["workspace_target"] = "library"
                st.session_state.setdefault("workspace_views", {})[category] = "library"
                st.switch_page("src/financial_model/pages/scenarios.py")


def _settings(scope: str) -> dict[str, Any]:
    """Collect reproducible simulation settings and dated historical evidence."""
    data: dict[str, Any] = st.session_state.setdefault(
        "forecast_settings", ForecastSettings().model_dump(mode="json")
    )
    forms.choice(
        data, "mode", "Forecast method", tuple(_MODES), f"mode_{scope}", labels=_MODES
    )
    if data["mode"] in {"monte_carlo", "bootstrap"}:
        forms.number(
            data,
            "count",
            "Simulated futures",
            f"count_{scope}",
            integer=True,
            minimum=1,
            maximum=10000,
        )
        forms.number(
            data,
            "seed",
            "Random seed",
            f"seed_{scope}",
            integer=True,
            maximum=2**32 - 1,
        )
    if data["mode"] == "monte_carlo":
        st.caption(
            "Uses each Plan's Economy central compound returns and annual simple-return standard deviations. Correlations describe log gross returns. Complete these in Economy; numerical low/high ranges are separate alternatives. Matching scenarios share annual random shocks."
        )
    if data["mode"] in {"historical", "bootstrap"}:
        st.caption(
            "Upload joint annual decimal rates, after investment fees: year,equities,fixed_income,cash,inflation. Years must be consecutive. Historical replay requires enough years for the full forecast; blocks sample consecutive stretches without wrapping."
        )
        history = st.file_uploader(
            "Joint annual history CSV", type="csv", key=f"history_{scope}"
        )
        if history is not None:
            text = history.getvalue().decode("utf-8-sig")
            data["history"] = parse_history_csv(text)
            data["history_start_year"] = int(
                next(csv.DictReader(io.StringIO(text)))["year"]
            )
        forms.text(
            data,
            "history_source",
            "History source, retrieval date, and fee convention",
            f"history_source_{scope}",
            multiline=True,
        )
        if not data["history_source"].strip():
            msg = "Enter the history source, retrieval date, and fee convention before running historical scenarios."
            raise ValueError(msg)
        st.caption(f"{len(data['history'])} annual observations retained in this run.")
        if data["mode"] == "bootstrap":
            forms.number(
                data,
                "block_years",
                "Consecutive years per block",
                f"blocks_{scope}",
                integer=True,
                minimum=1,
                maximum=30,
            )
    if data["mode"] == "stress":
        forms.choice(
            data,
            "stress",
            "Adverse scenario",
            ("early_loss", "persistent_inflation"),
            f"stress_{scope}",
            labels={
                "early_loss": "Early stock losses: -40%, -20%, 0%",
                "persistent_inflation": "At least 8% inflation for ten years",
            },
        )
        st.caption(
            "Illustrative stresses have no assigned probability; other rates retain their central assumptions."
        )
    return data


def _run(request: ForecastRequest) -> None:
    """Calculate only after an explicit user action and preserve the previous run on failure."""
    from financial_model.forecast import calculate_forecast

    progress = st.progress(0.0, text="Preparing household forecast…")

    def report(message: str, fraction: float) -> None:
        progress.progress(fraction, text=message)

    try:
        calculation = calculate_forecast(request, progress=report)
    except (ValueError, RuntimeError) as error:
        st.error(str(error))
        return
    finally:
        progress.empty()
    st.session_state["forecast_calculation"] = calculation
    st.session_state["forecast_document"] = calculation.document


def _chart(document: SavedForecast, *, differences: bool = False) -> None:
    """Plot pointwise percentile intervals and median curves on a shared dollar scale."""
    suffix = "_difference" if differences else ""
    household = document.request.plans[0].household
    birth_years = [
        household.reference_year - adult.age
        if household.reference_year is not None and adult.age is not None
        else None
        for adult in household.adults
    ]
    age_labels = [
        f"format(datum.value - {birth_year}, 'd')" if birth_year is not None else "'—'"
        for birth_year in birth_years
    ]
    values = [
        {
            "scenario": f"{index + 1}. {summary.name}",
            "year": point.year,
            "lower": getattr(point, "lower" + suffix),
            "median": getattr(point, "median" + suffix),
            "upper": getattr(point, "upper" + suffix),
            **{
                f"age_{i}": point.year - birth_year if birth_year is not None else None
                for i, birth_year in enumerate(birth_years)
            },
        }
        for index, summary in enumerate(document.summaries)
        for point in summary.points
    ]
    marks = [
        {"scenario": f"{index + 1}. {summary.name}", "year": year}
        for index, summary in enumerate(document.summaries)
        for year in set(summary.retirement_years)
    ]
    encoding: dict[str, Any] = {
        "x": {
            "field": "year",
            "type": "quantitative",
            "scale": {"zero": False},
            "axis": {
                "title": ["Year", *[f"{adult.name} age" for adult in household.adults]],
                "titleX": -24,
                "titleY": 12,
                "titleAngle": 0,
                "titleAlign": "right",
                "titleBaseline": "top",
                "titleFontSize": 12,
                "titleFontWeight": "normal",
                "titleLineHeight": 18,
                "labelExpr": "[format(datum.value, 'd'), "
                + ", ".join(age_labels)
                + "]",
                "labelAngle": 0,
                "labelFontSize": 12,
                "labelLineHeight": 18,
                "labelBaseline": "top",
                "labelPadding": 12,
                "labelFlush": False,
                "tickSize": 0,
                "tickCount": {"expr": "max(2, floor(width / 130))"},
                "tickMinStep": 1,
                "grid": False,
            },
        },
        "color": {
            "field": "scenario",
            "type": "nominal",
            "title": "Plan",
            "legend": None,
        },
    }
    st.vega_lite_chart(
        {
            "layer": [
                {
                    "data": {"values": values},
                    "mark": {"type": "area", "opacity": 0.12},
                    "encoding": encoding
                    | {
                        "y": {
                            "field": "lower",
                            "type": "quantitative",
                            "title": "Difference ($)"
                            if differences
                            else "Net wealth ($)",
                            "axis": {"format": "~s"},
                        },
                        "y2": {"field": "upper"},
                    },
                },
                {
                    "data": {"values": values},
                    "mark": {"type": "line", "strokeWidth": 2.5},
                    "encoding": encoding
                    | {
                        "color": encoding["color"]
                        | {
                            "legend": {
                                "title": None,
                                "orient": "top-left",
                                "direction": "vertical",
                                "offset": 12,
                                "rowPadding": 8,
                                "labelFontSize": 12,
                                "labelLimit": {
                                    "expr": "max(120, min(300, width - 48))"
                                },
                                "symbolType": "stroke",
                                "symbolOpacity": 1,
                                "symbolStrokeWidth": 2.5,
                                "symbolSize": 400,
                                "symbolDash": [],
                            }
                        },
                        "y": {"field": "median", "type": "quantitative"},
                        "tooltip": [
                            {"field": "scenario"},
                            {"field": "year", "format": "d"},
                            *[
                                {
                                    "field": f"age_{i}",
                                    "title": f"{adult.name} age",
                                    "format": "d",
                                }
                                for i, adult in enumerate(household.adults)
                            ],
                            {"field": "median", "format": "$,.0f"},
                            {"field": "lower", "format": "$,.0f"},
                            {"field": "upper", "format": "$,.0f"},
                        ],
                    },
                },
                {
                    "data": {"values": marks},
                    "mark": {"type": "rule", "strokeDash": [3, 4], "opacity": 0.3},
                    "encoding": encoding
                    | {
                        "tooltip": [
                            {"field": "scenario"},
                            {"field": "year", "title": "Retirement", "format": "d"},
                        ]
                    },
                },
            ],
            "resolve": {"legend": {"color": "independent"}},
            "params": [{"name": "height", "expr": "width * 0.9"}],
            "autosize": {"type": "fit-x", "contains": "padding", "resize": True},
        },
        width="stretch",
    )


def _inspect(document: SavedForecast) -> None:
    """Show an individual annual ledger, reconstructing only when explicitly requested."""
    with st.expander("Inspect an individual future"):
        scenario = st.selectbox(
            "Scenario",
            range(len(document.summaries)),
            format_func=lambda index: document.summaries[index].name,
            key=f"inspect_scenario_{document.id}",
        )
        summary = document.summaries[scenario]
        path = st.number_input(
            "Future number",
            min_value=1,
            max_value=summary.paths,
            value=1,
            step=1,
            key=f"inspect_path_{document.id}_{scenario}",
        )
        if document.request.settings.mode == "historical":
            start = document.request.settings.history_start_year
            if start is not None:
                st.caption(
                    f"This replay begins with the observed economic year {start + int(path) - 1}."
                )
        calculation = st.session_state.get("forecast_calculation")
        if calculation is not None and calculation.document.id == document.id:
            ledger = calculation.inspect(scenario, int(path) - 1)
        elif path == 1:
            ledger = list(summary.example)
        else:
            st.info("Rerun the saved inputs to reconstruct other individual futures.")
            ledger = []
        st.caption(
            "Annual nominal dollars, with reference-year values where labeled. Transfers stay inside the household; an unfunded expense remains a shortfall."
        )
        st.dataframe(ledger, hide_index=True, width="stretch")


def _results(document: SavedForecast, dsn: str | None) -> None:
    """Present uncertainty, accessible funds, and funding failures together."""
    st.subheader(document.request.name)
    st.caption(
        f"Saved inputs · {document.created_at:%Y-%m-%d %H:%M} UTC · model {document.model_version} · {document.request.settings.mode.replace('_', ' ')}"
    )
    years = ", ".join(
        str(year) for year in sorted({s.money_year for s in document.summaries})
    )
    st.caption(
        f"Balances in {years} purchasing power. Lines are medians; shading is the 10th-90th percentile at each year. The band does not bound an entire lifetime or represent worst cases. Dashed lines mark retirement."
    )
    _chart(document)
    st.dataframe(
        [
            {
                "Plan": s.name,
                "Futures": s.paths,
                "Futures fully funded through lifetime (%)": round(
                    s.funded_fraction * 100, 1
                ),
                "First year with any shortfall": s.first_shortfall_year,
                "Mean total unfunded ($, real)": round(s.mean_unfunded_total),
                "Mean unfunded years": round(s.mean_unfunded_years, 1),
            }
            for s in document.summaries
        ],
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "Funding statistics retain every future, including failed paths. A positive total balance can coexist with inaccessible funds and unpaid expenses."
    )
    with st.expander("Accessible funds, cash, debt, and annual shortfalls"):
        st.caption(
            "Each row is one calendar year; percentages describe simulated futures in that year. Accessible balances are gross, before withdrawal taxes. Cash means taxable cash; restricted account cash stays inside its account."
        )
        st.dataframe(
            [
                {
                    "Plan": s.name,
                    "Year": p.year,
                    "Accessible median, gross ($)": p.accessible_median,
                    "Taxable cash median ($)": p.cash_median,
                    "Debt median ($)": p.debt_median,
                    "Futures with unfunded expense (%)": p.shortfall_probability * 100,
                    "Reserve breach (%)": p.reserve_breach_probability * 100,
                }
                for s in document.summaries
                for p in s.points
            ],
            hide_index=True,
            width="stretch",
        )
    if len(document.summaries) > 1:
        with st.expander("Difference from the first Plan"):
            st.caption(
                f"Compared with {document.summaries[0].name} within matching economic futures, before taking percentiles."
            )
            _chart(document, differences=True)
    _inspect(document)
    st.download_button(
        "Download forecast JSON",
        document.model_dump_json(indent=2),
        file_name=f"forecast-{document.id}.json",
        mime="application/json",
    )
    saved_ids = st.session_state.setdefault("forecast_saved_ids", set())
    if (
        st.button("Save forecast", disabled=not dsn or str(document.id) in saved_ids)
        and dsn
    ):
        try:
            create_forecast(dsn, document)
        except psycopg.errors.UniqueViolation:
            st.info("This exact run is already saved.")
            saved_ids.add(str(document.id))
        except psycopg.errors.UndefinedTable:
            st.error(
                "Apply the updated scripts/schema.sql before saving forecasts. Download the JSON to keep this run now."
            )
        except psycopg.Error:
            st.error(
                "This forecast could not be saved. Your calculated run is still here; download a JSON copy or try again."
            )
        else:
            saved_ids.add(str(document.id))
            st.success("Forecast saved with its exact inputs and results.")


def forecast_page() -> None:
    """Set up, run, compare, inspect, and retain household forecasts."""
    from financial_model.app import _database_url
    from financial_model.forecast import forecast_issues

    st.title("Forecast")
    st.caption(
        "Compare household choices from the same dated financial position through the planning lifetimes."
    )
    dsn = _database_url()
    _saved_runs(dsn)
    scope = str(st.session_state.get("forecast_epoch", 0))
    options = _plans(dsn)
    request = None
    if options:
        plans = _selection(options, scope)
        if plans:
            names = st.session_state.setdefault(
                "forecast_name", {"name": "Scenario forecast"}
            )
            forms.text(names, "name", "Forecast name", f"forecast_name_{scope}")
            _assumptions()
            try:
                settings = _settings(scope)
                request = ForecastRequest(
                    name=names["name"],
                    plans=plans,
                    settings=ForecastSettings.model_validate(settings),
                )
                issues = forecast_issues(request)
            except (ValueError, UnicodeDecodeError) as error:
                st.error(str(error))
                request, issues = None, ()
            if issues:
                with st.expander(f"{len(issues)} scenario inputs need attention"):
                    for issue in issues:
                        st.write(f"• {issue}")
                _scenario_links()
            if (
                st.button(
                    "Run forecast",
                    type="primary",
                    disabled=request is None or bool(issues),
                )
                and request is not None
            ):
                _run(request)
    else:
        st.info(
            "Save a combined Plan with Work, Spending, Investments, Economy, and Retirement choices, or open a forecast JSON above."
        )
    if document := st.session_state.get("forecast_document"):
        if request is not None and request != document.request:
            st.info(
                "The chart below is the previous run. Run the edited inputs to update it."
            )
        if st.button("Rerun saved inputs"):
            _run(document.request)
            document = st.session_state["forecast_document"]
        _results(document, dsn)
