# Model Reference

All public models live in `financial_model.schemas`. Submitted models are
immutable, reject unknown fields, and serialize with Pydantic JSON methods.
Money inputs are nonnegative finite decimals with at most two decimal places.
Share prices allow six decimal places; shares are nonnegative whole numbers.

| Type | Content |
| --- | --- |
| `PersonInputs` | Name, optional employer, explicit not-working flag, year-end age, annual cash pay/RSUs, employee 401(k), employer contribution rate, WA Cares exemption, ISO event, optional option award |
| `OptionAward` | ISO/NSO, shares vesting this year, exercise price, optional assumed share value; informational only |
| `ISOExercise` | Qualifying shares exercised and held, strike price, exercise-date fair market value |
| `DependentInputs` | Qualifying child's year-end age |
| `LivingCosts` | Monthly rent/childcare/other; annual eligible care expense; CA renter eligibility |
| `Scenario` | Optional custom label (`name`), year, state, exactly two adults, children, costs, allocation plan, notes, confirmed household facts |
| `TaxResult` | Federal/state income tax, employee payroll components, included AMT detail, coverage and engine versions |
| `ScenarioResult` | Annual asset sources, goal allocations and funding gap, tax result, input digest and calculation provenance |
| `HouseholdPerson` | Shared name and age defaults, without employment or compensation |
| `Household` | Family defaults, reference year, qualifying children, filing status, and revision |
| `Goal` | Shared UUID, editable name, revision |
| `GoalAllocation` | Goal snapshot, fixed annual cash or `None` for remainder, RSU rate |
| `AllocationPlan` | Goal rows in cash funding priority order and retirement/option destinations |
| `GoalAddition` | One goal's cash, retirement, retained RSUs and option purchase cost |
| `AllocationResult` | Planned goal additions and cash funding gap |
| `GoalCashFunding` | Goal ID, planned cash, funded cash, and derived shortfall |
| `CashFunding` | Funded fixed goals in priority order and signed remaining cash |
| `SavedScenario` | Schema version, UUID, revision, update timestamp, inputs and matching result |

## Calculate a scenario

```python
from financial_model.model import evaluate_scenario
from financial_model.schemas import PersonInputs, Scenario

scenario = Scenario(
    name="Synthetic example",
    tax_year=2025,
    state="WA",
    adults=(
        PersonInputs(name="Adult A", age=40, cash_pay=100000),
        PersonInputs(name="Adult B", age=38, cash_pay=100000),
    ),
    assumptions_confirmed=True,
)
result = evaluate_scenario(scenario)
print(result.cash_added / 12)
```

`evaluate_scenario(Scenario) -> ScenarioResult` needs neither Streamlit nor a
database. `calculate_taxes(Scenario) -> TaxResult` is the single tax entry point;
its keyword flags for excluding RSUs/ISO spread serve internal attribution.

## Interpret results

| Field | Meaning |
| --- | --- |
| `cash_added` | Cash after taxes, employee 401(k), living costs and option purchases, before goal allocation |
| `investments_added` | Employee/employer retirement, retained RSUs and option shares at cost |
| `allocations.planned_total` | All requested goal additions, including unfunded cash |
| `allocations.funding_gap` | Fixed cash allocations minus cash available, floored at zero |
| `allocations.net_total` | Planned total less funding gap |
| `rsu_tax_funding` | Incremental tax paid from vested RSUs |
| `retained_rsus` | Gross vested RSUs less their tax funding |
| `option_value` | Purchased shares at exercise-date FMV, shown separately from goal funding |
| `net_saved_from_income` | Cash pay + RSUs + employer retirement − taxes − living costs; equals allocation net total |
| `taxes.total` | Income taxes plus employee payroll taxes; AMT already included |
| `effective_tax_rate` | Federal/state income and employee payroll taxes / gross cash and RSU wages; includes RSU-funded and ISO-related taxes; `None` for zero wages |

See [calculation assumptions](../concepts/index.md) for formulas and coverage.

The shared Monthly / Annual display setting applies to all result tables.
Monthly views divide annual money flows and vesting counts by 12.
Per-share prices and tax rates are unchanged. Display conversion
does not modify stored inputs or results. Rounding can cause small visible
differences. Goals require exactly one cash remainder and complete RSU
percentages when RSUs are present. Result validation checks source conservation
and verifies allocations against the stored plan.

Cash overview, Equity overview, Tax breakdown, and Goal breakdown render the
same saved annual snapshot. `cash_surplus(result)` adds option exercise cost back
to `cash_added`, giving cash after taxes, employee 401(k), and living expenses.

`fund_cash_goals(plan, cash)` allocates available cash to fixed goals in stored row
order. Each receives the lesser of its requested cash and the positive cash still
available. The `cash_amount=None` row receives the remainder and is displayed as
Spending. The editor keeps that row last and does not expose a
remainder-destination selector. `remaining_cash(document)` returns the balance
after funding fixed goals; it can be negative only when cash is negative before
those goals are funded.

Funded cash plus the signed remainder equals `cash_added`. Unfunded goal cash plus
an expense/commitment deficit equals `allocations.funding_gap`. The saved
`AllocationResult` preserves requested amounts; `CashFunding` derives funded
amounts without changing the snapshot, taxes, or net additions. Existing saved
row order supplies initial priority, so no database migration is required.

All household goals appear in the views, including unused goals with zero amounts.
Names align by ID. Goal breakdown separates planned/funded cash from employee and
employer 401(k) contributions, retained RSUs, and exercised shares at cost. It uses
`retirement_goal_id` for contributions and `option_goal_id` for exercised shares.
Unexercised options and exercise gains do not enter goal funding. Retained shares
and retirement contributions are never used to cover cash shortfalls.

Effective tax rate uses gross wages before 401(k) deductions, excludes employer
contributions and option values from its denominator, and may be negative when
refundable credits exceed taxes. AMT is already included in income taxes.

`OptionAward.exercise_spread` returns the nonnegative estimated value above
exercise cost for this year's vesting, rounded to cents. It returns `None` when
the assumed share value is unknown. Award inputs do not change calculated
taxes or cash flows, but are included in the scenario's input digest when present.

`PersonInputs.employer` is an optional name of up to 80 characters. Leading and
trailing whitespace is removed; blank text becomes `None`. `not_working` is a
strict boolean that defaults to `False`. An employer and `not_working=True`
cannot both be set. Neither field changes compensation, tax calculations, or
saving, and neither is inferred from salary. Changes are included in the input
digest and require a matching calculation snapshot when saved.

`Scenario.name` defaults to an empty string and allows up to 80 characters.
An empty or whitespace-only label uses the state as its display title; the
display title is not stored as an input. Existing named documents retain their
digests. Changing a label follows the same calculation/save process as other
inputs. Scenario UUIDs identify records regardless of their labels.

`financial_model.scenario_display` supplies shared titles, selector labels, and
HTML. It compares matching headings against variable household assumptions,
retains exact input precision, and displays only the fields needed to distinguish
unique assumption sets. Identical sets use saved timestamps and record IDs.
Display context does not change saved inputs or calculation outputs.

## Persistence

`financial_model.storage` provides `list_scenarios`, `load_scenario`,
`create_scenario`, `update_scenario`, and `delete_scenario`. Each call uses a short
transaction. Updates and deletions require the revision previously loaded and
raise `RevisionConflictError` if the record changed or no longer exists. `import_backup(bytes) -> Scenario` validates a saved document and
returns only the inputs for fresh calculation.

Schema and model version 2 replace individual saving rates with goal allocations.
Version-one records require the explicit preview/apply migration; version-one
backups require reviewed `legacy_destinations` in `import_backup`. Unknown versions
and unsupported inputs fail validation. There is no general Excel importer.

`list_goals`, `create_goal`, `update_goal`, and `delete_goal` manage the shared
catalogue. Renames and deletions check revisions; referenced goals cannot be deleted.
Snapshots retain their original names, while views prefer current catalogue names.
`resolve_goal_conflicts` applies reviewed import choices and remaps destinations
by UUID; equal names alone never merge identities.

`load_household` and `save_household` manage the singleton household defaults.
Revision zero means not yet saved; writes increment the revision and reject stale
updates. `Household.scenario_defaults()` copies family facts into a new input buffer.
It does not copy a calculation, confirmation, employment, or allocation plan. Existing
scenario documents remain independent of later household-default changes.
