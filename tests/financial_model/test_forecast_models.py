"""Forecast documents preserve unknowns and reject misleading saved comparisons."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from financial_model.forecast_models import (
    ForecastPoint,
    ForecastRequest,
    ForecastSettings,
    SavedForecast,
)
from financial_model.planning import PlanningScenario


@pytest.fixture
def request_input():
    plan = PlanningScenario(
        name="Example",
        household={"reference_year": 2026},
        wealth={"snapshot": {"as_of": date(2026, 1, 1)}},
    )
    return ForecastRequest(plans=(plan,))


@pytest.fixture
def point_input():
    return {
        "year": 2026,
        "lower": -100,
        "median": 0,
        "upper": 100,
        "accessible_median": 0,
        "cash_median": 0,
        "debt_median": 0,
        "shortfall_probability": 0.5,
        "reserve_breach_probability": 1,
        "lower_difference": 0,
        "median_difference": 0,
        "upper_difference": 0,
    }


def test_forecast_request_allows_residence_choices(request_input):
    original = request_input.plans[0]
    alternative = original.model_dump()
    alternative["household"]["residence"] = [{"year": 2026, "location": "WA"}]
    request = ForecastRequest(
        plans=(original, PlanningScenario.model_validate(alternative)),
    )
    assert len(request.plans) == 2


def test_forecast_request_allows_child_count_identity_and_birth_year_choices(
    request_input,
):
    plans = [request_input.plans[0]]
    for years in ((2027,), (2029,), (2027, 2029)):
        data = request_input.plans[0].model_dump()
        data["household"]["children"] = [
            {"id": uuid4(), "name": f"Child {index}", "birth_year": year}
            for index, year in enumerate(years, start=1)
        ]
        plans.append(PlanningScenario.model_validate(data))
    request = ForecastRequest(plans=tuple(plans))
    assert [len(plan.household.children) for plan in request.plans] == [0, 1, 1, 2]
    assert request.plans[1].household.children[0].birth_year == 2027
    assert request.plans[2].household.children[0].birth_year == 2029


@pytest.mark.parametrize(
    "difference",
    ["wealth", "adult_age", "adult_name", "adult_order", "reference_year", "tax_fact"],
)
def test_forecast_request_rejects_unfair_comparison(request_input, difference):
    original = request_input.plans[0]
    alternative = original.model_dump()
    if difference == "wealth":
        alternative["wealth"]["snapshot"]["as_of"] = date(2026, 2, 1)
    elif difference == "adult_age":
        alternative["household"]["adults"][0]["age"] = 40
    elif difference == "adult_name":
        alternative["household"]["adults"][0]["name"] = "Someone else"
    elif difference == "adult_order":
        alternative["household"]["adults"] = alternative["household"]["adults"][::-1]
    elif difference == "reference_year":
        alternative["household"]["reference_year"] = 2027
    else:
        alternative["household"]["adults"][0]["wa_cares_exempt"] = True
    with pytest.raises(ValidationError):
        ForecastRequest(
            plans=(original, PlanningScenario.model_validate(alternative)),
        )


@pytest.mark.parametrize("mode", ["historical", "bootstrap"])
def test_forecast_settings_need_history_provenance(mode):
    with pytest.raises(ValidationError, match="their source"):
        ForecastSettings(mode=mode, history=((0.1, 0.02, 0, 0.02),))


@pytest.mark.parametrize("bad_rate", [-1, float("nan"), float("inf")])
def test_forecast_settings_reject_invalid_history_rates(bad_rate):
    with pytest.raises(ValidationError):
        ForecastSettings(history=((bad_rate, 0, 0, 0),))


@pytest.mark.parametrize(
    "changes",
    [{"lower": 101}, {"median": float("inf")}, {"shortfall_probability": 1.1}],
)
def test_forecast_point_rejects_invalid_results(point_input, changes):
    with pytest.raises(ValidationError):
        ForecastPoint(**(point_input | changes))


def test_saved_forecast_round_trip_and_missing_result(request_input, point_input):
    summary = {
        "name": "Example",
        "money_year": 2026,
        "retirement_years": (2040, 2042),
        "points": (point_input,),
        "funded_fraction": 0.5,
        "first_shortfall_year": 2026,
        "mean_unfunded_total": 100,
        "mean_unfunded_years": 0.5,
        "paths": 2,
        "example": ({"year": 2026, "closing_wealth": -100},),
    }
    document = SavedForecast(
        numpy_version="test",
        tax_engine_version="test",
        request=request_input,
        summaries=(summary,),
    )
    assert SavedForecast.model_validate_json(document.model_dump_json()) == document
    for changes in ({"summaries": []}, {"model_version": "unknown"}):
        with pytest.raises(ValidationError):
            SavedForecast.model_validate(document.model_dump() | changes)
