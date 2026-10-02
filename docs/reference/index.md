# Model Reference

Annual and household models live in `financial_model.schemas`; wealth inventory
models live in `financial_model.wealth`. Submitted models are
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
| `Scenario` | Optional custom label (`name`), year, state, exactly two adults, children, costs, allocation plan, RSU tax funding choice, notes, confirmed household facts |
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

## Wealth snapshot models

The models in `financial_model.wealth` reuse immutable values and decimal money
constraints. Saving them does not run a projection or tax calculation.

| Type | Content |
| --- | --- |
| `AssetMix` | Equities, fixed-income, and cash fractions totaling exactly 1; shared with future allocation targets |
| `AccountPosition` | Local UUID, label, nullable balance, tax treatment, `invested_in`, and optional mixed allocation |
| `DebtPosition` | Local UUID, label, nullable amount owed |
| `WealthSnapshot` | Name, one `as_of` date, USD currency, optional note, accounts, debts |
| `SavedWealthSnapshot` | `document_type="wealth_snapshot"`, schema version 2, UUID, revision, save timestamp, and snapshot |

Tax treatment is `unknown`, `taxable`, `tax_deferred`, `roth`, or `hsa`.
`invested_in` is `unknown`, `cash`, `stocks`, `bonds`, or `mixed`. Only `mixed`
allows an entered `AssetMix`; a missing split stays unknown. The `allocation`
property resolves simple choices to stock/bond/cash fractions.

Balances are counted once. `assets`, `liabilities`, and `net_recorded` are derived
from entered amounts. `asset_classes` splits balances by the selected mix and
keeps unclassified amounts under `unknown`. `has_balances` distinguishes unknown
amounts from explicit zeros. `missing_details` asks for balances, tax treatment,
and investment mix; known zero balances need no classification.

Amounts serialize as decimal strings; unknown is `null`. IDs must be distinct
across accounts and debts. Saved copies retain independent contents. Owners,
security identifiers, nested holdings, per-account dates, and account-type
classifications are not part of this schema. Household pooling does not model
person-specific retirement withdrawals. Cost basis is not yet captured.

## Planning input models

`financial_model.planning` contains immutable inputs for future planning. None of
these models calculates financial outcomes or calls the annual tax engine.

| Type | Content |
| --- | --- |
| `Exploration[T]` | Nullable central value, optional paired lower/upper alternatives |
| `TimeAnchor` | Calendar year, or offset from household adult 1, adult 2 or both retirements |
| `WorkAssumptions` | Money year, two ordered adult work slots, employment phases, vesting, dated option events and RSU tax funding |
| `SpendingAssumptions` | Money year, baseline budget, replacement budgets, dated additions/reductions |
| `InvestmentAssumptions` | Money year, optional `AllocationPlan`, constant/gradual target mix, checkpoints, rebalancing and nullable `reserved_cash` |
| `EconomicAssumptions` | Inflation and compound returns after fees and before tax; optional annual volatility and correlation |
| `RetirementAssumptions` | Two adult slots with retirement years and planning ages; one-person, both-retired and after-death household budgets; optional mortality-sampling intent |
| `PlanningHousehold` | Copied names, reference-year ages, children by birth year, residence changes and personal tax elections |
| `ProfileSelection` | Source UUID/revision/name plus an exact validated copy and optional reviewed adult mapping |
| `PlanningScenario` | Named combination with copied Household, saved Wealth, profile selections, reviewed Work/Retirement adult mappings |
| `SavedPlanningDocument` | `document_type="planning_document"`, schema version 3, UUID, revision, aware timestamp and discriminated `inputs` |

The six input kinds are `work`, `spending`, `investments`, `economy`, `retirement`,
and `plan`. Household is embedded, not a seventh library. Nested Plans and duplicate source IDs are rejected. Several selections of the
same kind represent alternatives. `combination_count` counts their cross-product;
`combinations()` yields independent selections without calculating results. The annual document schema stays unchanged.

Money uses finite decimal strings in USD, with at most two decimal places. Blank
is `null`. A money reference year identifies purchasing power; it does not establish
a snapshot date, age reference year or tax year. Nominal vesting and contribution
fields retain their explicit convention. Planning years extend beyond 2026, and
planning ages extend beyond the annual calculator's age-64 boundary.

Growth is a finite fraction greater than -1; allocation fractions stay within
0–1. Ranges require either no bounds or both bounds satisfying lower ≤ central ≤
upper. A range has no probability distribution or weight. Combination checks use
central values; a future analyzer must recheck each explored configuration.

Work and recurring spending intervals are start-inclusive, end-exclusive.
`TimeAnchor.resolve()` performs date resolution only. The `both` anchor uses the
later retirement year; missing required dates remain unresolved. Spending checks
inspect entered change boundaries for negative consumption without creating a
financial projection. Replacements affect only the baseline. Retirement budgets
replace regular costs with the total appropriate to the retired people. A
`RetirementBudget` records an unknown choice, continuation of regular costs, or
a replacement amount. After-death costs take precedence over the two-person
budget; dated extras remain separate.

Portfolio mixes use equities, fixed income and cash, totaling exactly 1. A gradual
policy records an interpolation choice; no portfolio path is computed yet.
Within each pair of dated targets, let `t` be elapsed time divided by the interval
length, from 0 to 1. The saved choices specify the fraction of the allocation
change completed:

| Stored choice | Intended fraction of change |
| --- | --- |
| `linear_then_hold` (default) | `t` |
| `log_then_hold` | `log(1 + 3*t) / log(4)` |
| `exponential_then_hold` | `(4**t - 1) / 3` |

The fixed curvature avoids another user input. These are modeling choices, not
industry-prescribed paths. Each asset's target is its starting fraction plus the
chosen fraction of the change to its ending fraction. Each curve reaches both
endpoints exactly; the final target is held after the final date. Existing saved
linear choices remain unchanged, and constant policies do not use this field.

Economy returns use `nominal_compound_after_fees_before_tax`, distinct
from arithmetic means of annual random returns. Volatility and correlation order
is equities, fixed income, cash, inflation. Entered correlation matrices must be
4×4, finite, symmetric, diagonal 1, bounded by [-1, 1], and positive semidefinite.
A missing matrix remains unknown.

`missing_details(inputs)` reports capture omissions and combination conflicts.
`select_profile()` and `replace_profile()` copy validated contents. Replacing Work
or Retirement clears adult alignment. `replace_wealth()` changes starting balances
without changing Investment scenarios or their reserves. All Wealth accounts are
included automatically. Cash reserved outside the allocation remains part of
Wealth and is not added to assets. When all account mixes and balances are known,
combination checks flag a reserve larger than recorded cash.

`copy_annual_inputs()` returns unsaved Work, Spending and Investments profiles with
annual ID, revision, year and input digest as provenance. It copies one year of
compensation and vesting, multiplies monthly consumption by 12, and copies saving
instructions. Annual calculated taxes, surplus and saving totals are excluded.

`financial_model.planning_storage` provides category-filtered
`list_planning_documents`, `load_planning_document`, `create_planning_document`,
`update_planning_document`, and `delete_planning_document`. The `planning_documents`
table stores canonical JSONB documents. Updates require a matching ID, revision and
kind; deleted records cannot be recreated by update. Embedded source and goal
identities are provenance, not foreign-key dependencies or permission to recreate
catalogue records.

`import_planning_backup(bytes) -> PlanningInputs` validates the declared envelope,
version, payload and 1 MB limit, then discards the outer identity/revision. Embedded
copies and their provenance survive; the import does not populate source libraries.

### Migrate earlier planning inputs

Version-one planning documents require an explicit migration before this app can
read them. Run `scripts/migrate_planning_retirement.py preview DIRECTORY` with the
database-owner connection in `FINANCIAL_MODEL_DATABASE_URL`. The private directory
contains originals, settings, and validated proposals. `apply DIRECTORY` locks the
table, checks that nothing changed, and updates documents and constraints in one
transaction. Other financial tables are not modified.

Optional `--settings FILE` supplies a JSON `money_year` and `retirement_rows`
(source UUIDs mapped to reviewed row indices). These identify undated retirement
budgets without guessing from labels. Ambiguous retirement timing stops conversion.
Originals remain available for recovery. Version-one backups are not imported
through the current editor.

Known fees convert all return assumptions with `(1 + return) * (1 - fee) - 1`.
When no fee was recorded, numeric rates stay unchanged and a note asks the user to
review their new after-fee meaning. Retirement dates and spending move into new
Retirement records as needed, including independent copies inside Plans. Person
mappings are retained explicitly. If the Wealth library is still at schema 1,
follow this with `scripts/migrate_wealth.py`; it preserves already upgraded
Planning documents.

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
| `rsu_tax_funding` | Incremental tax paid from vested RSUs when `rsu_taxes_from_shares` is true; zero otherwise |
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

`list_wealth_snapshots`, `load_wealth_snapshot`, `create_wealth_snapshot`,
`update_wealth_snapshot`, and `delete_wealth_snapshot` store complete validated
documents in `wealth_snapshots`. Listing sorts by financial date, then save time.
Creation requires revision 1. Updates and deletions use an atomic revision check;
updates also preserve the financial date. Use a new identity for another date.
No snapshot operation modifies scenarios, household defaults, or goal assignments.

`import_wealth_backup(bytes) -> WealthSnapshot` validates the wealth document
type and schema, enforces the 1 MB limit, and returns contents without database
identity. The UI opens those contents as an unsaved draft. Backups and PostgreSQL
use the same document shape. Future saved projections must embed the exact wealth
and scenario inputs used, rather than resolving mutable library IDs on display.
