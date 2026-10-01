"""Meaning, completeness and independence of planning inputs, without a projection."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from financial_model.model import evaluate_scenario
from financial_model.planning import (
    INPUT_ADAPTER,
    AdultWork,
    AssetMix,
    Budget,
    EconomicAssumptions,
    Exploration,
    Growth,
    InvestmentAssumptions,
    LongevityAssumptions,
    PlanningScenario,
    SavedPlanningDocument,
    SpendingAdjustment,
    SpendingAssumptions,
    TimeAnchor,
    WorkAssumptions,
    WorkPhase,
    copy_annual_inputs,
    missing_details,
    replace_profile,
    replace_wealth,
    select_profile,
)
from financial_model.schemas import Money, SavedScenario
from financial_model.wealth import AccountPosition, SavedWealthSnapshot, WealthSnapshot


@pytest.mark.parametrize(
    "model",
    [
        WorkAssumptions,
        SpendingAssumptions,
        InvestmentAssumptions,
        EconomicAssumptions,
        LongevityAssumptions,
        PlanningScenario,
    ],
)
def test_partial_inputs_round_trip_without_results(model):
    inputs = model(name="Synthetic")
    document = SavedPlanningDocument(inputs=inputs)
    assert (
        SavedPlanningDocument.model_validate_json(document.model_dump_json())
        == document
    )
    assert missing_details(inputs)
    assert "result" not in document.model_dump()
    with pytest.raises(ValidationError):
        INPUT_ADAPTER.validate_python(inputs.model_dump() | {"unrecognized": True})


def test_unknown_zero_and_negative_growth_have_distinct_meanings():
    unknown = WorkPhase()
    zero = WorkPhase(cash_pay=0, real_growth={"central": "-0.01"})
    assert unknown.cash_pay is None
    assert zero.cash_pay == 0
    assert zero.real_growth.central == Decimal("-0.01")
    assert '"cash_pay":"0"' in zero.model_dump_json()
    assert WorkAssumptions(name="Later", money_year=2070).money_year == 2070
    for value in ("-1", "NaN", "Infinity"):
        with pytest.raises(ValidationError):
            Exploration[Growth](central=value)


@pytest.mark.parametrize(
    "values",
    [{"lower": 0}, {"central": 1, "upper": 2}, {"central": 1, "lower": 2, "upper": 3}],
)
def test_ranges_require_ordered_complete_bounds(values):
    with pytest.raises(ValidationError):
        Exploration[Money](**values)
    assert Exploration[Money](central=1, lower=0, upper=2).upper == 2


def test_employment_intervals_retirement_and_contribution_basis():
    AdultWork(
        slot=1,
        name="A",
        retirement={"central": 2050},
        phases=(
            WorkPhase(start=2026, end=2040),
            WorkPhase(start=2040, status="working"),
        ),
    )
    with pytest.raises(ValidationError, match="overlap"):
        AdultWork(
            slot=1, name="A", phases=(WorkPhase(start=2026), WorkPhase(start=2040))
        )
    with pytest.raises(ValidationError, match="retirement"):
        AdultWork(
            slot=1,
            name="A",
            retirement={"central": 2040},
            phases=(WorkPhase(start=2040, status="working"),),
        )
    with pytest.raises(ValidationError, match="after"):
        WorkPhase(start=2040, end=2039)
    with pytest.raises(ValidationError, match="pay fraction"):
        WorkPhase(contribution_basis="real", contribution_rate="0.1")
    with pytest.raises(ValidationError, match="not-working"):
        WorkPhase(status="not_working", cash_pay=100)


def test_time_anchors_resolve_only_with_required_dates():
    assert TimeAnchor(kind="both", offset=2).resolve((2040, 2045)) == 2047
    assert TimeAnchor(kind="both").resolve((2040, None)) is None
    assert TimeAnchor(kind="adult_1", offset=-5).resolve((2040, None)) == 2035
    assert TimeAnchor(year=2050).resolve((None, None)) == 2050
    with pytest.raises(ValidationError):
        TimeAnchor(kind="both", year=2040)


def test_spending_conflicts_partial_schedules_and_survivor():
    with pytest.raises(ValidationError, match="same effective time"):
        SpendingAssumptions(
            name="Conflict",
            replacements=(
                {"label": "A", "when": {"year": 2040}},
                {"label": "B", "when": {"year": 2040}},
            ),
        )
    with pytest.raises(ValidationError, match="One-time"):
        SpendingAdjustment(label="Move", frequency="once", end={"year": 2040})
    with pytest.raises(ValidationError, match="Essential"):
        Budget(amount={"central": 100}, essential=101)
    spending = SpendingAssumptions(
        name="Spending",
        money_year=2026,
        baseline={"amount": {"central": 100}},
        adjustments=(
            {
                "label": "Reduction",
                "start": {"year": 2030},
                "end": {"year": 2035},
                "amount": {"central": -101},
            },
        ),
    )
    assert any("negative at 2030" in d for d in missing_details(spending))
    assert any("Survivor" in d for d in missing_details(spending))


def test_investment_allocations_are_complete_and_dates_ordered():
    with pytest.raises(ValidationError, match="100%"):
        AssetMix(equities="0.8", fixed_income="0.3", cash=0)
    AssetMix(equities="0.8", fixed_income="0.2", cash=0)
    with pytest.raises(ValidationError, match="increasing"):
        InvestmentAssumptions(
            name="Bad",
            policy="gradual",
            checkpoints=({"when": {"year": 2040}}, {"when": {"year": 2030}}),
        )
    partial = InvestmentAssumptions(
        name="Future", policy="gradual", checkpoints=({"when": {"kind": "both"}},)
    )
    assert any("timing unresolved" in d for d in missing_details(partial))
    assert any("checkpoints" in d for d in missing_details(partial))


@pytest.mark.parametrize(
    "matrix",
    [
        [[1, 0.9, 0.9, 0], [0.9, 1, -0.9, 0], [0.9, -0.9, 1, 0], [0, 0, 0, 1]],
        [[1, 0.2, 0, 0], [0.3, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
        [[1, 2, 0, 0], [2, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
        [[0, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
    ],
)
def test_economy_rejects_invalid_correlation(matrix):
    with pytest.raises(ValidationError):
        EconomicAssumptions(name="Invalid", correlation=matrix)


def test_economy_optional_statistics_remain_unknown():
    assert EconomicAssumptions(name="Unknown").correlation is None
    assert EconomicAssumptions(name="Unknown").volatility == (None,) * 4
    valid = EconomicAssumptions(
        name="Synthetic",
        correlation=[[1 if i == j else 0 for j in range(4)] for i in range(4)],
    )
    assert valid.correlation[0][1] == 0
    with pytest.raises(ValidationError):
        EconomicAssumptions(name="Bad", volatility=(-1, None, None, None))


def test_lifespan_sampling_intent_and_age_validation():
    requested = LongevityAssumptions(name="Sample", mode="sample")
    assert any("Mortality table" in d for d in missing_details(requested))
    assert (
        LongevityAssumptions(
            name="Long",
            adults=(
                {"slot": 1, "name": "A", "age": {"central": 100}},
                {"slot": 2, "name": "B"},
            ),
        )
        .adults[0]
        .age.central
        == 100
    )
    with pytest.raises(ValidationError):
        LongevityAssumptions(
            name="Bad",
            mode="sample",
            adults=(
                {"slot": 1, "name": "A", "age": {"central": 95}},
                {"slot": 2, "name": "B"},
            ),
        )


def test_explicit_annual_copy_conserves_inputs_without_double_counting(scenario):
    data = scenario.model_dump(mode="json")
    data["living_costs"] = {
        "rent": 2000,
        "childcare": 500,
        "other": 1000,
        "eligible_care_expense": 5000,
    }
    data["dependents"] = [{"age": 5}]
    inputs = type(scenario).model_validate(data)
    source = SavedScenario(inputs=inputs, result=evaluate_scenario(inputs))
    original = source.model_dump_json()
    work, spending, investments = copy_annual_inputs(source)
    assert work.adults[0].phases[0].cash_pay == 100000
    assert work.adults[0].phases[0].end == 2026
    assert work.adults[0].retirement.central is None
    assert spending.baseline.amount.central == 42000
    assert not spending.adjustments
    assert investments.saving == source.inputs.allocation_plan
    assert work.annual_source.input_digest == inputs.digest()
    assert source.model_dump_json() == original
    assert "taxes" not in work.model_dump()


def test_plan_copies_are_stable_and_replacement_clears_alignment():
    source = SavedPlanningDocument(inputs=WorkAssumptions(name="Source"))
    plan = PlanningScenario(
        name="Plan", components=(select_profile(source),), work_adults=(1, 2)
    )
    newer = SavedPlanningDocument(
        id=source.id, revision=2, inputs=WorkAssumptions(name="Edited")
    )
    assert plan.components[0].inputs.name == "Source"
    replaced = replace_profile(plan, newer)
    assert replaced.components[0].inputs.name == "Edited"
    assert replaced.work_adults is None
    assert plan.work_adults == (1, 2)
    with pytest.raises(ValidationError):
        PlanningScenario(
            name="Bad", components=(select_profile(source), select_profile(source))
        )
    with pytest.raises(ValidationError):
        select_profile(SavedPlanningDocument(inputs=plan))


def test_wealth_bindings_are_local_and_replacing_clears_them():
    account = AccountPosition(label="Account", reported_value=100)
    wealth = SavedWealthSnapshot(
        snapshot=WealthSnapshot(as_of=date(2026, 9, 27), accounts=(account,))
    )
    plan = PlanningScenario(
        name="Plan",
        wealth=wealth,
        owners=({"row_id": account.id, "owner": "joint"},),
        portfolio_accounts=(account.id,),
        reserved_cash=0,
    )
    replaced = replace_wealth(plan, wealth)
    assert replaced.wealth == wealth
    assert not replaced.owners
    assert not replaced.portfolio_accounts
    assert replaced.reserved_cash is None
    with pytest.raises(ValidationError, match="selected Wealth"):
        PlanningScenario(name="Bad", wealth=wealth, owners=({"row_id": uuid4()},))
    with pytest.raises(ValidationError, match="selected Wealth"):
        PlanningScenario(name="Bad", portfolio_accounts=(account.id,))


def test_combination_checks_resolve_ranges_people_timing_and_money_years():
    work = SavedPlanningDocument(
        inputs=WorkAssumptions(
            name="Work",
            money_year=2026,
            adults=(
                {"slot": 1, "name": "A", "retirement": {"central": 2040}},
                {"slot": 2, "name": "B", "retirement": {"central": 2045}},
            ),
        )
    )
    spending = SavedPlanningDocument(
        inputs=SpendingAssumptions(
            name="Spend",
            money_year=2025,
            replacements=(
                {"label": "One", "when": {"kind": "both"}},
                {"label": "Two", "when": {"year": 2045}},
            ),
        )
    )
    longevity = SavedPlanningDocument(
        inputs=LongevityAssumptions(
            name="Life",
            adults=(
                {"slot": 1, "name": "A", "age": {"central": 60}},
                {"slot": 2, "name": "B"},
            ),
        )
    )
    plan = PlanningScenario(
        name="Checks",
        components=tuple(select_profile(d) for d in (work, spending, longevity)),
        work_adults=(1, 2),
        longevity_adults=(1, 2),
        household={
            "reference_year": 2026,
            "adults": ({"name": "A", "age": 65}, {"name": "B", "age": 62}),
        },
    )
    details = missing_details(plan)
    assert any("reference years differ" in d for d in details)
    assert any("same year" in d for d in details)
    assert any("precedes starting age" in d for d in details)
    assert not any("replacement date needs input" in d for d in details)
