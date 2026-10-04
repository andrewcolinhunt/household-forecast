"""Forecast setup, uncertainty curves, and inspectable saved household runs."""

from __future__ import annotations

from typing import Any

import psycopg
import streamlit as st

from financial_model import input_forms as forms
from financial_model.forecast_models import (
    ForecastRequest,
    ForecastSettings,
    SavedForecast,
)
from financial_model.forecast_storage import (
    create_forecast,
    import_forecast_backup,
    list_forecasts,
    list_historical_forecasts,
)
from financial_model.forecast_tax import FORECAST_TAX_ASSUMPTION
from financial_model.planning import PlanningScenario, SavedPlanningDocument
from financial_model.planning_storage import list_planning_documents

_MODES = {
    "fixed": "Fixed assumptions",
    "monte_carlo": "Simulated returns",
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
                historical = list_historical_forecasts(dsn)
            except psycopg.errors.UndefinedTable:
                st.info(
                    "Forecast storage needs the updated scripts/schema.sql. JSON downloads still work."
                )
                records = ()
                historical = ()
            except ValueError as error:
                st.error(str(error))
                records = ()
                historical = ()
            except psycopg.Error:
                st.error(
                    "Saved forecasts could not be read. Try again or open a downloaded JSON file."
                )
                records = ()
                historical = ()
            if records:
                selected = st.selectbox(
                    "Saved run",
                    records,
                    format_func=lambda item: f"{item.request.name} · {item.created_at:%Y-%m-%d %H:%M} UTC",
                )
                if st.button("Open saved run"):
                    _open_run(selected)
                    st.rerun()
            if historical:
                st.caption(
                    "Earlier model versions remain available as frozen JSON. "
                    "Their inputs and results are preserved and cannot be rerun with this model."
                )
                archived = st.selectbox(
                    "Historical run",
                    historical,
                    format_func=lambda item: f"{' / '.join(item.names)} · {item.created_at:%Y-%m-%d %H:%M} UTC · model {item.model_version}",
                )
                st.download_button(
                    "Download historical forecast",
                    archived.to_json(),
                    file_name=f"forecast-{archived.id}.json",
                    mime="application/json",
                )
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
    st.caption(
        "Financial inputs come from saved Plans. To change them, edit the source "
        "in Scenarios, update its copy in the Plan, and save the Plan."
    )
    with st.expander("Model assumptions and limits"):
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
    """Set up fixed assumptions or reproducible simulated returns."""
    data: dict[str, Any] = st.session_state.setdefault(
        "forecast_settings", ForecastSettings().model_dump(mode="json")
    )
    key = f"mode_{scope}"
    methods = ("fixed", "monte_carlo")
    if data["mode"] not in methods:
        data = ForecastSettings(
            mode="fixed", count=data["count"], seed=data["seed"]
        ).model_dump(mode="json")
        st.session_state["forecast_settings"] = data
        if key in st.session_state:
            st.session_state[key] = data["mode"]
    data["mode"] = st.segmented_control(
        "Forecast method",
        methods,
        default=data["mode"],
        required=True,
        format_func=_MODES.__getitem__,
        key=key,
        on_change=forms.capture,
        args=(data, "mode", key),
    )
    st.caption(
        {
            "fixed": "Use each Plan's expected returns and inflation every year.",
            "monte_carlo": "Simulate different annual returns and inflation using each Plan's Economy assumptions (Monte Carlo).",
        }[data["mode"]]
    )
    if data["mode"] == "monte_carlo":
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
            help_text="Keep this number unchanged to repeat the same simulated futures.",
        )
        st.caption(
            "Compared Plans share the same economic shocks. Low/high input ranges "
            "are separate choices; they do not define the simulated uncertainty."
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


def _chart(document: SavedForecast) -> None:
    """Plot pointwise percentile intervals and median curves on a shared dollar scale."""
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
            "lower": point.lower,
            "median": point.median,
            "upper": point.upper,
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
                            "title": "Net wealth ($)",
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
            "params": [{"name": "height", "expr": "max(280, min(420, width * 0.65))"}],
            "autosize": {"type": "fit-x", "contains": "padding", "resize": True},
        },
        width="stretch",
    )


def _overview(document: SavedForecast) -> None:
    """Summarize the saved run before inspecting any individual future."""
    years = ", ".join(
        str(year) for year in sorted({s.money_year for s in document.summaries})
    )
    single_future = all(s.paths == 1 for s in document.summaries)
    st.markdown("#### Net wealth over time")
    st.caption(
        f"Balances use {years} purchasing power. Each line shows one projected future. "
        "Dashed lines mark retirement."
        if single_future
        else f"Balances use {years} purchasing power. Lines show the median; shading covers "
        "the 10th to 90th percentiles for each year, not the worst and best possible "
        "lifetimes. Dashed lines mark retirement."
    )
    _chart(document)
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


def _results(document: SavedForecast, dsn: str | None) -> None:
    """Group overview and accepted breakdowns around the same frozen run."""
    from financial_model.forecast_views import forecast_breakdown, forecast_sensitivity

    st.subheader(document.request.name)
    st.caption(
        f"Run on {document.created_at:%Y-%m-%d %H:%M} UTC · {_MODES[document.request.settings.mode]}"
    )
    labels = ("Overview", "Cash flow", "Simulated future", "Sensitivity")
    tabs = st.tabs(labels, key=f"forecast_result_tab_{document.id}", on_change="rerun")
    for label, tab in zip(labels, tabs, strict=True):
        if not tab.open:
            continue
        with tab:
            if label == "Overview":
                _overview(document)
            elif label == "Sensitivity":
                forecast_sensitivity(document)
            else:
                forecast_breakdown(document, label)
    st.markdown("#### Keep this forecast")
    if not dsn:
        st.caption(
            "Database saving is unavailable. Download a copy to reopen this forecast later."
        )
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
        except psycopg.Error as error:
            outdated_schema = isinstance(error, psycopg.errors.CheckViolation) and (
                error.diag.constraint_name
                in {
                    "forecast_runs_document_check",
                    "forecast_runs_document_check1",
                    "forecast_runs_schema_version",
                }
            )
            st.error(
                "The database needs an update before it can save this forecast. "
                "Your calculated run is still here. Apply the latest scripts/schema.sql, "
                "then try Save forecast again."
                if outdated_schema
                else "This forecast could not be saved. Your calculated run is still here; download a JSON copy or try again."
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
        "See how saved Plans affect wealth and the ability to pay expenses through retirement."
    )
    dsn = _database_url()
    _saved_runs(dsn)
    with st.expander(
        "Set up forecast", expanded="forecast_document" not in st.session_state
    ):
        scope = str(st.session_state.get("forecast_epoch", 0))
        options = _plans(dsn)
        request = None
        if options:
            plans = _selection(options, scope)
            if plans:
                names = st.session_state.setdefault(
                    "forecast_name", {"name": "Retirement forecast"}
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
                except ValueError as error:
                    st.error(str(error))
                    request, issues = None, ()
                if issues:
                    with st.container(border=True):
                        st.markdown("#### Complete these inputs before running")
                        for issue in issues:
                            st.markdown(f"- {issue}")
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
                st.info("Choose at least one saved Plan to set up a forecast.")
        else:
            st.info(
                "Save a combined Plan with Work, Spending, Investments, Economy, and Retirement choices, or open a forecast JSON above."
            )
    if document := st.session_state.get("forecast_document"):
        st.subheader("Results")
        if options and request != document.request:
            st.info(
                "These results are from the previous run. Complete the setup and run "
                "the forecast to apply your changes."
            )
        if st.button("Rerun these inputs"):
            _run(document.request)
            document = st.session_state["forecast_document"]
        _results(document, dsn)
