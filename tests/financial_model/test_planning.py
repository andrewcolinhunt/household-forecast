"""Meaning, completeness and independence of planning inputs, without a projection."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from financial_model.model import evaluate_scenario
from financial_model.planning import (
    INPUT_ADAPTER,
    AdultRetirement,
    AdultWork,
    EconomicAssumptions,
    ExpenseLevel,
    ExpenseSchedule,
    Exploration,
    Growth,
    InvestmentAssumptions,
    PlanningChild,
    PlanningHousehold,
    PlanningScenario,
    RetirementAssumptions,
    SavedPlanningDocument,
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
from financial_model.wealth import (
    AccountPosition,
    AssetMix,
    SavedWealthSnapshot,
    WealthSnapshot,
)


@pytest.mark.parametrize(
    "model",
    [
        WorkAssumptions,
        SpendingAssumptions,
        InvestmentAssumptions,
        EconomicAssumptions,
        RetirementAssumptions,
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
        phases=(
            WorkPhase(start=2026, end=2040),
            WorkPhase(start=2040, status="working"),
        ),
    )
    with pytest.raises(ValidationError, match="overlap"):
        AdultWork(
            slot=1, name="A", phases=(WorkPhase(start=2026), WorkPhase(start=2040))
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


def test_expense_schedules_reject_ambiguous_dates_and_negative_amounts():
    with pytest.raises(ValidationError, match="increase"):
        ExpenseSchedule(
            label="Care",
            levels=[
                {"when": {"year": 2040}},
                {"when": {"year": 2040}},
            ],
        )
    with pytest.raises(ValidationError, match="One-time"):
        ExpenseSchedule(label="Tuition", frequency="once", end={"year": 2040})
    with pytest.raises(ValidationError):
        ExpenseLevel(amount={"central": -1})
    with pytest.raises(ValidationError, match="increase"):
        ExpenseSchedule(
            label="Care", levels=[{"when": {"year": 2040}}], end={"year": 2039}
        )
    item = ExpenseSchedule(label="Care")
    with pytest.raises(ValidationError, match="unique"):
        SpendingAssumptions(name="Duplicate", expenses=(item, item))


def test_one_aggregate_budget_is_complete_without_itemization():
    spending = SpendingAssumptions(
        name="Spending",
        money_year=2026,
        expenses=[
            {
                "label": "All living costs",
                "levels": [{"when": {"year": 2026}, "amount": {"central": 100}}],
            }
        ],
    )
    assert not missing_details(spending)
    assert any(
        "amounts need input" in d
        for d in missing_details(SpendingAssumptions(name="Draft"))
    )
    assert any(
        "use zero explicitly" in d
        for d in missing_details(SpendingAssumptions(name="Empty", expenses=[]))
    )
    for removed in ("baseline", "replacements", "adjustments"):
        with pytest.raises(ValidationError):
            SpendingAssumptions.model_validate(spending.model_dump() | {removed: []})


def test_age_references_survive_renaming_reordering_and_resolve_missing_facts():
    child = PlanningChild(name="Child", birth_year=2026)
    other = PlanningChild(name="Sibling", birth_year=2024)
    household = PlanningHousehold(
        reference_year=2026,
        adults=[{"name": "A", "age": 40}, {"name": "B", "age": 38}],
        children=[child, other],
    )
    anchor = TimeAnchor(kind="age", person=str(child.id), age=5)
    assert anchor.resolve((None, None), household) == 2031
    changed = household.model_copy(
        update={"children": (other, child.model_copy(update={"name": "Renamed"}))}
    )
    assert anchor.resolve((None, None), changed) == 2031
    assert (
        TimeAnchor(kind="age", person="adult_1", age=65).resolve(
            (None, None), household
        )
        == 2051
    )
    assert (
        TimeAnchor(kind="survivor").resolve((2040, 2045), household, (2060, 2070))
        == 2061
    )
    assert (
        anchor.resolve((None, None), household.model_copy(update={"children": ()}))
        is None
    )
    assert (
        TimeAnchor(kind="age", person="adult_1", age=65).resolve((None, None)) is None
    )
    for fields in (
        {"kind": "age", "person": "adult_1", "age": 5, "year": 2030},
        {"kind": "year", "age": 5},
        {"kind": "age", "offset": 1},
    ):
        with pytest.raises(ValidationError):
            TimeAnchor(**fields)


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


def test_economy_explicit_missing_or_custom_statistics_are_preserved():
    missing = EconomicAssumptions(
        name="Unknown", volatility=(None,) * 4, correlation=None
    )
    assert missing.correlation is None
    assert missing.volatility == (None,) * 4
    assert EconomicAssumptions.model_validate_json(missing.model_dump_json()) == missing
    valid = EconomicAssumptions(
        name="Synthetic",
        correlation=[[1 if i == j else 0 for j in range(4)] for i in range(4)],
    )
    assert valid.correlation[0][1] == 0
    with pytest.raises(ValidationError):
        EconomicAssumptions(name="Bad", volatility=(-1, None, None, None))


def test_existing_scenarios_keep_new_forecast_facts_unknown():
    retirement = RetirementAssumptions(name="Existing", money_year=2026)
    old = retirement.model_dump(mode="json")
    for adult in old["adults"]:
        for field in (
            "social_security",
            "claiming_age",
            "pension",
            "pension_start_age",
            "pension_survivor_fraction",
        ):
            adult.pop(field)
    loaded = RetirementAssumptions.model_validate(old)
    assert loaded.adults[0].social_security is None
    assert loaded.adults[0].pension is None
    economy = EconomicAssumptions(name="Existing")
    assert economy.stock_dividend_yield is None
    assert economy.bond_interest_yield is None


def test_benefits_survive_plan_copy_and_person_mapping():
    source = SavedPlanningDocument(
        inputs=RetirementAssumptions(
            name="Benefits",
            adults=(
                AdultRetirement(
                    slot=1,
                    name="B",
                    social_security=30000,
                    claiming_age=70,
                    pension=20000,
                    pension_start_age=60,
                    pension_survivor_fraction="0.5",
                ),
                AdultRetirement(slot=2, name="A", social_security=0, pension=0),
            ),
        )
    )
    copied = select_profile(source).model_copy(update={"adult_mapping": (2, 1)})
    plan = SavedPlanningDocument(
        inputs=PlanningScenario(name="Plan", components=(copied,))
    )
    restored = SavedPlanningDocument.model_validate_json(plan.model_dump_json())
    retirement = restored.inputs.components[0]
    assert retirement.adult_mapping == (2, 1)
    assert retirement.inputs.adults[0].social_security == 30000
    assert retirement.inputs.adults[0].pension_survivor_fraction == Decimal("0.5")
    assert retirement.inputs.adults[1].pension == 0


@pytest.mark.parametrize(
    "fields",
    [
        {"claiming_age": 61},
        {"pension_survivor_fraction": "1.01"},
    ],
)
def test_retirement_forecast_inputs_validate_benefits(fields):
    with pytest.raises(ValidationError):
        AdultRetirement(slot=1, name="A", **fields)


def test_lifespan_sampling_intent_and_age_validation():
    requested = RetirementAssumptions(name="Sample", mode="sample")
    assert any("Mortality table" in d for d in missing_details(requested))
    assert (
        RetirementAssumptions(
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
        RetirementAssumptions(
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
    assert "retirement" not in work.adults[0].model_dump()
    assert spending.expenses[0].levels[0].amount.central == 42000
    assert len(spending.expenses) == 1
    assert investments.saving == source.inputs.allocation_plan
    assert work.annual_source.input_digest == inputs.digest()
    assert source.model_dump_json() == original
    assert "taxes" not in work.model_dump()


def test_plan_copies_are_stable_and_replacement_clears_alignment():
    source = SavedPlanningDocument(inputs=WorkAssumptions(name="Source"))
    plan = PlanningScenario(
        name="Plan",
        components=(
            select_profile(source).model_copy(update={"adult_mapping": (1, 2)}),
        ),
    )
    newer = SavedPlanningDocument(
        id=source.id, revision=2, inputs=WorkAssumptions(name="Edited")
    )
    assert plan.components[0].inputs.name == "Source"
    replaced = replace_profile(plan, newer)
    assert replaced.components[0].inputs.name == "Edited"
    assert replaced.components[0].adult_mapping is None
    assert plan.components[0].adult_mapping == (1, 2)
    with pytest.raises(ValidationError):
        PlanningScenario(
            name="Bad", components=(select_profile(source), select_profile(source))
        )
    with pytest.raises(ValidationError):
        select_profile(SavedPlanningDocument(inputs=plan))


def test_wealth_replacement_keeps_investment_choices_and_reserve():
    account = AccountPosition(
        label="Account", balance=100, invested_in="cash", tax_treatment="taxable"
    )
    wealth = SavedWealthSnapshot(
        snapshot=WealthSnapshot(as_of=date(2026, 9, 27), accounts=(account,))
    )
    investments = select_profile(
        SavedPlanningDocument(
            inputs=InvestmentAssumptions(name="Invest", reserved_cash=10)
        )
    )
    plan = PlanningScenario(name="Plan", wealth=wealth, components=(investments,))
    newer = SavedWealthSnapshot(
        snapshot=WealthSnapshot(as_of=date(2026, 10, 1), accounts=(account,))
    )
    replaced = replace_wealth(plan, newer)
    assert replaced.wealth == newer
    assert replaced.components == plan.components
    assert plan.wealth == wealth
    assert not any(
        "owner" in d.lower() or "Choose the accounts" in d
        for d in missing_details(replaced)
    )
    for removed in ({"owners": []}, {"portfolio_accounts": []}, {"reserved_cash": 0}):
        with pytest.raises(ValidationError):
            PlanningScenario.model_validate(plan.model_dump() | removed)


def test_investment_reserve_is_checked_against_all_known_starting_cash():
    investments = select_profile(
        SavedPlanningDocument(
            inputs=InvestmentAssumptions(name="Invest", reserved_cash=101)
        )
    )
    wealth = SavedWealthSnapshot(
        snapshot=WealthSnapshot(
            as_of=date(2026, 1, 1),
            accounts=(AccountPosition(label="Cash", balance=100, invested_in="cash"),),
        )
    )
    plan = PlanningScenario(name="Plan", wealth=wealth, components=(investments,))
    assert "Investments: cash reserve exceeds recorded cash." in missing_details(plan)
    unknown = wealth.model_copy(
        update={
            "snapshot": wealth.snapshot.model_copy(
                update={
                    "accounts": (
                        *wealth.snapshot.accounts,
                        AccountPosition(label="Unknown", balance=1000),
                    )
                }
            )
        }
    )
    assert "Investments: cash reserve exceeds recorded cash." not in missing_details(
        replace_wealth(plan, unknown)
    )


def test_combination_checks_resolve_ranges_people_timing_and_money_years():
    work = SavedPlanningDocument(
        inputs=WorkAssumptions(
            name="Work",
            money_year=2026,
            adults=(
                {"slot": 1, "name": "A"},
                {"slot": 2, "name": "B"},
            ),
        )
    )
    spending = SavedPlanningDocument(
        inputs=SpendingAssumptions(
            name="Spend",
            money_year=2025,
            expenses=(
                {
                    "label": "Living",
                    "levels": (
                        {"when": {"kind": "both"}},
                        {"when": {"year": 2045}},
                    ),
                },
            ),
        )
    )
    longevity = SavedPlanningDocument(
        inputs=RetirementAssumptions(
            name="Life",
            adults=(
                {
                    "slot": 1,
                    "name": "A",
                    "age": {"central": 60},
                    "retirement": {"central": 2040},
                },
                {"slot": 2, "name": "B", "retirement": {"central": 2045}},
            ),
        )
    )
    plan = PlanningScenario(
        name="Checks",
        components=tuple(
            select_profile(d).model_copy(
                update={
                    "adult_mapping": (1, 2)
                    if d.inputs.kind in {"work", "retirement"}
                    else None
                }
            )
            for d in (work, spending, longevity)
        ),
        household={
            "reference_year": 2026,
            "adults": ({"name": "A", "age": 65}, {"name": "B", "age": 62}),
        },
    )
    details = missing_details(plan)
    assert any("reference years differ" in d for d in details)
    assert any("boundaries must increase" in d for d in details)
    assert any("precedes starting age" in d for d in details)
    assert not any("replacement date needs input" in d for d in details)


def test_retirement_owns_dates_and_lifespans_without_spending_overrides():
    retirement = RetirementAssumptions(
        name="Retire earlier",
        money_year=2026,
        adults=(
            {
                "slot": 1,
                "name": "A",
                "retirement": {"central": 2040},
                "age": {"central": 95},
            },
            {
                "slot": 2,
                "name": "B",
                "retirement": {"central": 2045},
                "age": {"central": 100},
            },
        ),
    )
    assert not missing_details(retirement)
    for field in ("only_adult_1", "only_adult_2", "both_retired", "survivor"):
        with pytest.raises(ValidationError):
            RetirementAssumptions.model_validate(
                retirement.model_dump() | {field: {"amount": {"central": 100}}}
            )
    with pytest.raises(ValidationError):
        AdultWork(slot=1, name="A", retirement={"central": 2040})


def test_economy_has_net_returns_without_fee_horizon_or_rate_notes():
    economy = EconomicAssumptions(
        name="Net",
        inflation={"central": "0.025"},
        equities={"central": "0.06"},
        fixed_income={"central": "0.045"},
        cash={"central": "0.03"},
    )
    assert not missing_details(economy)
    assert economy.return_basis == "nominal_compound_after_fees_before_tax"
    for removed in (
        {"fees": "0.01"},
        {"forecast_years": 10},
        {"inflation": {"central": "0.025", "notes": "Essay"}},
    ):
        with pytest.raises(ValidationError):
            EconomicAssumptions.model_validate(economy.model_dump() | removed)


def test_plan_varies_independent_categories_and_preserves_person_mappings():
    def selection(inputs, mapping=None):
        return select_profile(SavedPlanningDocument(inputs=inputs)).model_copy(
            update={"adult_mapping": mapping}
        )

    work = [
        selection(WorkAssumptions(name=name), (1, 2)) for name in ("Job A", "Job B")
    ]
    spending = [
        selection(SpendingAssumptions(name=name))
        for name in ("Spend A", "Spend B", "Spend C")
    ]
    retirement = selection(RetirementAssumptions(name="Retire"), (2, 1))
    fixed = [
        retirement,
        selection(InvestmentAssumptions(name="Invest")),
        selection(EconomicAssumptions(name="Economy")),
    ]
    plan = PlanningScenario(name="Compare", components=(*work, *spending, *fixed))
    assert plan.combination_count == 6
    cases = list(plan.combinations())
    assert len(cases) == 6
    assert {
        (c.components[0].inputs.name, c.components[1].inputs.name) for c in cases
    } == {(w.inputs.name, s.inputs.name) for w in work for s in spending}
    assert all(
        next(c for c in case.components if c.inputs.kind == "retirement") == retirement
        for case in cases
    )
    changed = replace_profile(
        plan, SavedPlanningDocument(inputs=WorkAssumptions(name="Job C"))
    )
    assert changed.combination_count == 3
    assert plan.combination_count == 6


def test_plan_checks_each_alternative_and_uses_retirement_mapping():
    investments = SavedPlanningDocument(
        inputs=InvestmentAssumptions(
            name="Glide",
            policy="gradual",
            checkpoints=({"when": {"year": 2040}}, {"when": {"kind": "adult_1"}}),
        )
    )
    choices = []
    for year in (2030, 2050):
        profile = RetirementAssumptions(
            name=f"Retire {year}",
            adults=(
                {"slot": 1, "name": "B", "retirement": {"central": 2060}},
                {"slot": 2, "name": "A", "retirement": {"central": year}},
            ),
        )
        choices.append(
            select_profile(SavedPlanningDocument(inputs=profile)).model_copy(
                update={"adult_mapping": (2, 1)}
            )
        )
    plan = PlanningScenario(
        name="Mapped", components=(select_profile(investments), *choices)
    )
    assert any("checkpoints must increase" in d for d in missing_details(plan))
    later = plan.model_copy(
        update={"components": (select_profile(investments), choices[1])}
    )
    assert not any("checkpoints must increase" in d for d in missing_details(later))
