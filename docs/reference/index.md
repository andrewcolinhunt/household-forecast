# Model Reference

All public models live in `financial_model.schemas`. Submitted models are
immutable, reject unknown fields, and serialize with Pydantic JSON methods.
Money inputs are nonnegative finite decimals with at most two decimal places.
Share prices allow six decimal places; shares are nonnegative whole numbers.

| Type | Content |
| --- | --- |
| `PersonInputs` | Name, optional employer, explicit not-working flag, year-end age, annual cash pay/RSUs, employee 401(k), savings/employer rates, WA Cares exemption, ISO event, optional option award |
| `OptionAward` | ISO/NSO, shares vesting this year, exercise price, optional assumed share value; informational only |
| `ISOExercise` | Qualifying shares exercised and held, strike price, exercise-date fair market value |
| `DependentInputs` | Qualifying child's year-end age |
| `LivingCosts` | Monthly rent/childcare/other; annual eligible care expense; CA renter eligibility |
| `Scenario` | Optional custom label (`name`), year, state, exactly two adults, children, costs, notes, confirmed household facts |
| `TaxResult` | Federal/state income tax, employee payroll components, included AMT detail, coverage and engine versions |
| `ScenarioResult` | Annual cash/savings/assets, monthly cash, tax result, input digest and calculation provenance |
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
print(result.monthly_remaining_cash)
```

`evaluate_scenario(Scenario) -> ScenarioResult` needs neither Streamlit nor a
database. `calculate_taxes(Scenario) -> TaxResult` is the single tax entry point;
its keyword flags for excluding RSUs/ISO spread serve internal attribution.

## Interpret results

| Field | Meaning |
| --- | --- |
| `annual_remaining_cash` | Cash after taxes, personal saving, option purchases, and living costs |
| `monthly_remaining_cash` | Annual remaining cash / 12, rounded to cents |
| `cash_added` | Derived net cash retained: additional cash saving + annual remaining cash, including any deficit; excludes all shares and retirement |
| `investments_added` | Employee and employer retirement + retained RSUs + option purchases at cost; excludes option gains and investment growth |
| `extra_savings` | Personal savings goal beyond employee 401(k), funded from cash |
| `rsu_tax_funding` | Incremental tax paid from vested RSUs |
| `retained_rsus` | Gross vested RSUs less their tax funding |
| `option_value` | Purchased shares at exercise-date FMV |
| `total_savings_and_assets` | Remaining cash + personal/employer saving + retained RSUs + purchased shares |
| `net_saved_from_income` | Shown as Total saved: `cash_added + investments_added`, or cash pay + RSUs + employer retirement − taxes − living costs; excludes option exercise spread |
| `taxes.total` | Income taxes plus employee payroll taxes; AMT already included |
| `effective_tax_rate` | Federal/state income and employee payroll taxes / gross cash and RSU wages; includes RSU-funded and ISO-related taxes; `None` for zero wages |

See [calculation assumptions](../concepts/index.md) for formulas and coverage.

**Cash left after saving** uses `monthly_remaining_cash` (or
`annual_remaining_cash` in annual details). **Cash saved / month** uses
`cash_added / 12`, including planned cash saving. Display rounding can cause
small differences when multiplying visible monthly amounts by 12. Saving goals
allocate cash between set-aside and remaining amounts; they do not change total
cash retained. Negative cash retained reduces total saving even when investment
contributions are positive.

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

Schema version 1 is the only accepted backup format. Unknown versions and
unsupported facts fail validation. There is no general Excel import
or revision-history API in this version.

Absent `PersonInputs.option_award` and `PersonInputs.employer` values, and a false
`not_working` flag, are omitted from JSON. Existing saved scenarios and backups
therefore retain their original input digest without a data migration. The
net-saving property derives from existing result fields; it is not a new stored
calculation or tax rule.
