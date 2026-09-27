"""Scenario identity stays separate from metadata and transient session status."""

from __future__ import annotations

from html import escape
from uuid import uuid4

import pytest

from financial_model.model import evaluate_scenario
from financial_model.scenario_display import (
    ScenarioDisplay,
    employer_label,
    scenario_displays,
)
from financial_model.schemas import PersonInputs, SavedScenario, Scenario


def test_missing_employer_is_not_inferred_from_compensation() -> None:
    person = PersonInputs(name="Alex", age=32, cash_pay=0)
    assert employer_label(person) == "Employer not entered"
    assert employer_label(person, compact=True) == "Not entered"
    assert (
        employer_label(person.model_copy(update={"not_working": True})) == "Not working"
    )
    assert employer_label(person.model_copy(update={"employer": "Acme"})) == "Acme"


def test_scenario_display_separates_name_location_people_and_status(scenario) -> None:
    data = scenario.model_dump(mode="json")
    data["name"] = "Both working"
    data["adults"][0]["employer"] = "Acme"
    inputs = Scenario.model_validate(data)
    display = ScenarioDisplay(inputs, "Last calculated draft")
    html = display.html()
    assert '<span class="fm-scenario-name">Both working</span>' in html
    assert '<span class="fm-scenario-location">California · 2025</span>' in html
    assert "Adult A: Acme" in html
    assert "Adult B: Employer not entered" in html
    assert '<span class="fm-scenario-status">Last calculated draft</span>' in html
    assert (
        display.label
        == "Both working · CA · 2025 · Acme / Not entered · Last calculated draft"
    )
    assert '<h3 class="fm-scenario-name">Both working</h3>' in display.html(
        heading=True
    )
    assert inputs.name == "Both working"


def test_scenario_display_escapes_every_user_field(scenario) -> None:
    attack = '<img src=x onerror="alert(1)">'
    data = scenario.model_dump(mode="json")
    data["name"] = attack
    data["adults"][0].update(name=attack, employer=attack)
    display = ScenarioDisplay(Scenario.model_validate(data), attack)
    assert "<img" not in display.html()
    assert display.html().count(escape(attack)) == 4


def test_duplicate_names_use_context_then_saved_record_details(scenario) -> None:
    original = SavedScenario(inputs=scenario, result=evaluate_scenario(scenario))
    data = scenario.model_dump(mode="json")
    data["state"] = "WA"
    state = original.model_copy(update={"inputs": Scenario.model_validate(data)})
    data = scenario.model_dump(mode="json")
    data["adults"][0]["employer"] = "Acme"
    employer = original.model_copy(update={"inputs": Scenario.model_validate(data)})
    distinct = scenario_displays((original, state, employer))
    assert len({display.label for display in distinct}) == 3
    assert all(
        not display.differences and not display.record_label for display in distinct
    )
    copy = original.model_copy(update={"id": uuid4()})
    duplicates = scenario_displays((original, copy))
    assert all(display.differences == ("Same assumptions",) for display in duplicates)
    assert len({display.label for display in duplicates}) == 2
    assert (
        '<details class="fm-scenario-record"><summary>Saved details</summary>'
        in duplicates[0].html()
    )
    assert "Saved " in duplicates[0].html()
    assert all(display.inputs.name == scenario.name for display in duplicates)
    statuses = scenario_displays(
        (original, original), ("Last calculated draft", "Kept snapshot")
    )
    assert all(display.title == scenario.name for display in statuses)
    assert all(display.record_label is None for display in statuses)
    assert len({display.label for display in statuses}) == 2


def test_display_order_is_consistent_when_adults_are_reversed(scenario) -> None:
    data = scenario.model_dump(mode="json")
    data["adults"][0]["employer"] = "Acme"
    data["adults"][1]["employer"] = "Example"
    original = ScenarioDisplay(Scenario.model_validate(data))
    data["adults"].reverse()
    reordered = ScenarioDisplay(Scenario.model_validate(data))
    assert original.label == reordered.label
    assert original.html() == reordered.html()


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ({"employer": "Acme"}, {"employer": "Example"}),
        ({"employer": "Acme"}, {"not_working": True}),
        ({"not_working": True}, {"employer": "Example"}),
        ({"not_working": True}, {"not_working": True}),
        ({}, {}),
        ({"employer": "Acme"}, {}),
    ],
)
def test_automatic_titles_use_location_without_work_categories(scenario, first, second):
    data = scenario.model_dump(mode="json")
    data["name"] = ""
    data["adults"][0].update(first)
    data["adults"][1].update(second)
    inputs = Scenario.model_validate(data)
    display = ScenarioDisplay(inputs)
    assert display.title == "California"
    assert display.location == "2025"
    assert display.label.count("California") == 1
    data["state"] = "WA"
    assert ScenarioDisplay(Scenario.model_validate(data)).title.startswith("Washington")
    data["name"] = "Higher rent"
    assert ScenarioDisplay(Scenario.model_validate(data)).title == "Higher rent"


def test_matching_titles_show_minimal_real_differences_and_keep_titles(scenario):
    original = SavedScenario(inputs=scenario, result=evaluate_scenario(scenario))
    documents = []
    for rent, saving in [(3500, "0.1"), (4500, "0.1"), (3500, "0.2")]:
        data = scenario.model_dump(mode="json")
        data["living_costs"]["rent"] = rent
        data["adults"][0]["savings_rate"] = saving
        inputs = Scenario.model_validate(data)
        documents.append(original.model_copy(update={"id": uuid4(), "inputs": inputs}))
    displays = scenario_displays(tuple(documents))
    assert len({display.label for display in displays}) == 3
    assert displays[0].differences == ("Rent: $3,500/mo", "Adult A · Saving goal: 10%")
    assert displays[1].differences == ("Rent: $4,500/mo", "Adult A · Saving goal: 10%")
    assert displays[2].differences == ("Rent: $3,500/mo", "Adult A · Saving goal: 20%")
    assert all(display.title == scenario.name for display in displays)
    assert all(display.record_label is None for display in displays)
    assert scenario_displays((documents[0],))[0].title == displays[0].title


def test_difference_details_keep_cents_and_precise_rates(scenario):
    original = SavedScenario(inputs=scenario, result=evaluate_scenario(scenario))
    data = scenario.model_dump(mode="json")
    data["living_costs"]["rent"] = "0.01"
    changed = original.model_copy(update={"inputs": Scenario.model_validate(data)})
    displays = scenario_displays((original, changed))
    assert displays[1].differences == ("Rent: $0.01/mo",)
    data["living_costs"]["rent"] = "0"
    data["adults"][0]["savings_rate"] = "0.100001"
    changed = original.model_copy(update={"inputs": Scenario.model_validate(data)})
    displays = scenario_displays((original, changed))
    assert displays[1].differences == ("Adult A · Saving goal: 10.0001%",)


def test_differences_cover_option_awards_and_nonmonetary_inputs(scenario):
    original = SavedScenario(inputs=scenario, result=evaluate_scenario(scenario))
    for updates, expected in [
        ({"age": 41}, "Adult A · Age: 41"),
        (
            {"option_award": {"kind": "ISO", "vesting_shares": 100}},
            "Adult A · Option award: ISO",
        ),
        (
            {"options": {"shares": 12, "strike": "0.85", "fair_market_value": "0.85"}},
            "Adult A · ISOs exercised: 12",
        ),
    ]:
        data = scenario.model_dump(mode="json")
        data["adults"][0].update(updates)
        changed = original.model_copy(update={"inputs": Scenario.model_validate(data)})
        displays = scenario_displays((original, changed))
        assert displays[1].differences == (expected,)
        assert "Same assumptions" not in displays[1].html()


def test_assumptions_ignore_adult_order_and_notes(scenario):
    original = SavedScenario(inputs=scenario, result=evaluate_scenario(scenario))
    data = scenario.model_dump(mode="json")
    data["adults"].reverse()
    data["notes"] = "A note only"
    changed = original.model_copy(
        update={"id": uuid4(), "inputs": Scenario.model_validate(data)}
    )
    assert all(
        display.differences == ("Same assumptions",)
        for display in scenario_displays((original, changed))
    )
