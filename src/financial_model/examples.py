"""Build a fictional, editable household without reading any local user data.

Rounded, invented amounts illustrate the app's features.
"""

from __future__ import annotations

from contextlib import closing
from datetime import date

from financial_model.database import SessionDatabase
from financial_model.model import evaluate_scenario
from financial_model.planning import (
    HouseholdAssumptions,
    PlanningScenario,
    SavedPlanningDocument,
    SpendingAssumptions,
    household_from_defaults,
    replace_household,
    select_profile,
)
from financial_model.planning_storage import create_planning_document
from financial_model.schemas import (
    AllocationPlan,
    Household,
    SavedScenario,
    Scenario,
    default_allocation_plan,
)
from financial_model.storage import (
    create_scenario,
    save_household,
)
from financial_model.wealth import SavedWealthSnapshot
from financial_model.wealth_storage import create_wealth_snapshot

_ADULTS = (("Alex", 40, 55000, 3000), ("Sam", 38, 40000, 2000))
_NOTE = "Fictional example with rounded, illustrative assumptions. Edit before use."


def _wealth() -> SavedWealthSnapshot:
    """Return modest savings and a home with an explicitly modeled mortgage."""
    wealth = SavedWealthSnapshot.model_validate(
        {
            "snapshot": {
                "name": "Example starting balances",
                "as_of": date(2026, 1, 1),
                "notes": _NOTE,
                "accounts": [
                    {
                        "label": "Emergency savings",
                        "balance": 20000,
                        "tax_treatment": "taxable",
                        "invested_in": "cash",
                    },
                    *[
                        {
                            "label": f"{name} retirement",
                            "balance": balance,
                            "tax_treatment": "tax_deferred",
                            "invested_in": "mixed",
                            "mix": {"equities": ".7", "fixed_income": ".3", "cash": 0},
                            "owner": slot,
                        }
                        for slot, (name, balance) in enumerate(
                            (("Alex", 60000), ("Sam", 40000)), 1
                        )
                    ],
                ],
                "debts": [
                    {
                        "label": "Home mortgage",
                        "amount_owed": 220000,
                        "annual_payment": 18000,
                        "interest_rate": ".045",
                        "payment_frequency": "monthly",
                    }
                ],
                "properties": [
                    {
                        "label": "Primary home",
                        "market_value": 300000,
                        "annual_appreciation": ".025",
                    }
                ],
            }
        }
    )
    property_position = wealth.snapshot.properties[0].model_copy(
        update={"mortgage_id": wealth.snapshot.debts[0].id}
    )
    return wealth.model_copy(
        update={
            "snapshot": wealth.snapshot.model_copy(
                update={"properties": (property_position,)}
            )
        }
    )


def _profiles(saving: AllocationPlan) -> tuple[SavedPlanningDocument, ...]:
    """Return complete shared work, retirement, investment and economy inputs."""
    profiles: list[dict[str, object]] = [
        {
            "kind": "work",
            "name": "Two steady incomes",
            "money_year": 2026,
            "rsu_taxes_from_shares": True,
            "adults": [
                {
                    "slot": slot,
                    "name": name,
                    "phases": [
                        {
                            "start": 2026,
                            "status": "working",
                            "cash_pay": pay,
                            "real_growth": {"central": 0},
                            "employee_401k": contribution,
                            "employer_retirement_rate": ".03",
                        }
                    ],
                    "vesting": [{"start": 2026, "end": 2100, "amount": 0}],
                }
                for slot, (name, _, pay, contribution) in enumerate(_ADULTS, 1)
            ],
        },
        {
            "kind": "retirement",
            "name": "Retire at 65",
            "money_year": 2026,
            "adults": [
                {
                    "slot": slot,
                    "name": name,
                    "retirement": {"central": 2026 + 65 - age},
                    "age": {"central": 90},
                    "social_security": benefit,
                    "claiming_age": 67,
                    "pension": 0,
                }
                for slot, ((name, age, _, _), benefit) in enumerate(
                    zip(_ADULTS, (22000, 18000), strict=True), 1
                )
            ],
        },
        {
            "kind": "investments",
            "name": "Steady allocation",
            "money_year": 2026,
            "saving": saving,
            "policy": "constant",
            "rebalancing": "annual",
            "reserved_cash": 15000,
            "checkpoints": [
                {
                    "when": {"year": 2026},
                    "mix": {"equities": ".7", "fixed_income": ".25", "cash": ".05"},
                }
            ],
        },
        {
            "kind": "economy",
            "name": "Illustrative long-term outlook",
            "inflation": {"central": ".025"},
            "equities": {"central": ".06"},
            "fixed_income": {"central": ".035"},
            "cash": {"central": ".02"},
            "stock_dividend_yield": ".015",
            "bond_interest_yield": ".035",
        },
    ]
    return tuple(
        SavedPlanningDocument.model_validate({"inputs": profile | {"notes": _NOTE}})
        for profile in profiles
    )


def _spending(name: str, ordinary: int) -> SpendingAssumptions:
    """Keep the mortgage in Wealth so the forecast does not count it twice."""
    return SpendingAssumptions.model_validate(
        {
            "name": name,
            "money_year": 2026,
            "notes": "Mortgage payments are modeled separately in Wealth. " + _NOTE,
            "expenses": [
                {
                    "label": label,
                    "category": category,
                    "levels": [{"when": {"year": 2026}, "amount": {"central": amount}}],
                    **({"end": {"year": 2034}} if category == "children" else {}),
                }
                for label, category, amount in (
                    ("Ordinary living", "ordinary", ordinary),
                    ("Home upkeep, taxes and insurance", "other", 6000),
                    ("Healthcare", "healthcare", 6000),
                    ("Child activities and care", "children", 3000),
                )
            ],
        }
    )


def _annual(name: str, ordinary: int, saving: AllocationPlan) -> SavedScenario:
    """Include all household cash expenses in the single-year comparison."""
    inputs = Scenario.model_validate(
        {
            "name": name,
            "tax_year": 2026,
            "state": "WA",
            "adults": [
                {
                    "name": name,
                    "age": age,
                    "cash_pay": pay,
                    "employee_401k": contribution,
                    "employer_retirement_rate": ".03",
                }
                for name, age, pay, contribution in _ADULTS
            ],
            "dependents": [{"age": 10}],
            "living_costs": {"other": (ordinary + 6000 + 6000 + 3000 + 18000) // 12},
            "allocation_plan": saving,
            "notes": "Annual living costs include the mortgage cash payment. " + _NOTE,
            "assumptions_confirmed": True,
        }
    )
    return SavedScenario(inputs=inputs, result=evaluate_scenario(inputs))


def example_database() -> bytes:
    """Return a fresh SQLite example with two annual comparisons and two Plans.

    Each call creates independent records in a temporary database.
    No personal databases, files, environment settings or credentials are read.
    """
    with closing(SessionDatabase()) as database:
        household = Household.model_validate(
            {
                "tax_year": 2026,
                "adults": [{"name": name, "age": age} for name, age, _, _ in _ADULTS],
                "dependents": [{"age": 10}],
            }
        )
        save_household(database.path, household)
        people = household_from_defaults(household)
        people = people.model_copy(
            update={
                "adults": tuple(
                    a.model_copy(update={"wa_cares_exempt": False})
                    for a in people.adults
                )
            }
        )
        family = SavedPlanningDocument(
            inputs=HouseholdAssumptions.model_validate(
                {
                    "name": "Example household",
                    "notes": _NOTE,
                    **people.model_dump(exclude={"residence"}),
                }
            )
        )
        create_planning_document(database.path, family)
        wealth = create_wealth_snapshot(database.path, _wealth())
        saving = default_allocation_plan()
        profiles = _profiles(saving)
        for profile in profiles:
            create_planning_document(database.path, profile)
        for name, ordinary in (("Current spending", 24000), ("Lower spending", 21000)):
            spending = create_planning_document(
                database.path, SavedPlanningDocument(inputs=_spending(name, ordinary))
            )
            plan = PlanningScenario.model_validate(
                {
                    "name": name,
                    "notes": _NOTE,
                    "wealth": wealth,
                    "household": people.model_dump()
                    | {"residence": [{"year": 2026, "location": "WA"}]},
                    "components": [
                        select_profile(profile).model_copy(
                            update={
                                "adult_mapping": (1, 2)
                                if profile.inputs.kind in {"work", "retirement"}
                                else None
                            }
                        )
                        for profile in (*profiles, spending)
                    ],
                }
            )
            create_planning_document(
                database.path,
                SavedPlanningDocument(inputs=replace_household(plan, family)),
            )
            create_scenario(database.path, _annual(name, ordinary, saving))
        return database.export()
