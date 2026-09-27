"""Scenario identity stays separate from metadata and transient session status."""

from __future__ import annotations

from html import escape

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


def test_duplicate_names_use_context_before_numeric_markers(scenario) -> None:
    original = SavedScenario(inputs=scenario, result=evaluate_scenario(scenario))
    data = scenario.model_dump(mode="json")
    data["state"] = "WA"
    state = original.model_copy(update={"inputs": Scenario.model_validate(data)})
    data = scenario.model_dump(mode="json")
    data["adults"][0]["employer"] = "Acme"
    employer = original.model_copy(update={"inputs": Scenario.model_validate(data)})
    distinct = scenario_displays((original, state, employer))
    assert len({display.label for display in distinct}) == 3
    assert all(display.duplicate is None for display in distinct)
    duplicates = scenario_displays((original, original))
    assert [display.duplicate for display in duplicates] == [1, 2]
    assert '<span class="fm-scenario-duplicate"> (1)</span>' in duplicates[0].html()
    assert all(display.inputs.name == scenario.name for display in duplicates)
    statuses = scenario_displays(
        (original, original), ("Last calculated draft", "Kept snapshot")
    )
    assert all(display.duplicate is None for display in statuses)
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
