# Model Reference

All public models live in `financial_model.schemas`. Submitted models are
immutable, reject unknown fields, and serialize with Pydantic JSON methods.
Money inputs are nonnegative finite decimals with at most two decimal places.
Share prices allow six decimal places; shares are nonnegative whole numbers.

| Type | Content |
| --- | --- |
| `PersonInputs` | Name, year-end age, annual cash pay/RSUs, employee 401(k), savings/employer rates, WA Cares exemption, ISO event |
| `DependentInputs` | Qualifying child's year-end age |
| `LivingCosts` | Monthly rent/childcare/other; annual eligible care expense; CA renter eligibility |
| `Scenario` | Name, year, state, exactly two adults, children, costs, notes, confirmed household facts |
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
| `extra_savings` | Personal savings goal beyond employee 401(k), funded from cash |
| `rsu_tax_funding` | Incremental tax paid from vested RSUs |
| `retained_rsus` | Gross vested RSUs less their tax funding |
| `option_value` | Purchased shares at exercise-date FMV |
| `total_savings_and_assets` | Remaining cash + personal/employer saving + retained RSUs + purchased shares |
| `taxes.total` | Income taxes plus employee payroll taxes; AMT already included |
| `effective_tax_rate` | Total tax / gross cash and RSU wages; `None` for zero wages |

See [calculation assumptions](../concepts/index.md) for formulas and coverage.

## Persistence

`financial_model.storage` provides `list_scenarios`, `load_scenario`,
`create_scenario`, and `update_scenario`. Each call uses a short transaction.
Updates require the revision previously loaded and raise `RevisionConflictError`
when stale. `import_backup(bytes) -> Scenario` validates a saved document and
returns only the inputs for fresh calculation.

Schema version 1 is the only accepted backup format. Unknown versions and
unsupported facts fail validation. There is no general Excel import, deletion,
or revision-history API in this version.
