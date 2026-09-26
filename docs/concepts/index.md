# How the Annual Model Works

The model follows one household through one calendar year. It separates cash,
retirement contributions, vested RSUs, and purchased option shares. Average
monthly remaining cash is the annual cash balance divided by 12; it does not
predict paycheck timing or withholding.

## Saving and cash

Each adult's personal savings goal is a percentage of cash compensation plus
vested RSUs. The employee's traditional 401(k) contribution counts toward that
goal. Personal saving is at least the 401(k) contribution; any remainder is an
after-tax allocation funded from cash. Employer retirement contributions are
additional and do not reduce cash pay.

```text
personal saving = max(goal rate × (cash pay + RSUs), employee 401(k))
additional saving = personal saving − employee 401(k)
remaining cash = cash pay − cash-funded taxes − personal saving
                 − option purchase cost − annual living costs
```

Remaining cash can be negative. The app does not automatically sell shares or
borrow to cover a shortfall.

## RSUs and ISO shares

RSUs count as wages once, at vesting. The model assumes enough vested shares are
sold to pay the incremental taxes attributable to RSUs. It calculates taxes on
cash wages alone, then on cash plus RSUs, holding other facts fixed and excluding
ISO spread from both calculations. The difference, bounded between zero and
gross RSUs, is funded from RSUs. Remaining tax, including ISO-related AMT, is
funded from cash.

ISO exercise-and-hold uses cash equal to shares × strike price. Purchased shares
are valued at shares × exercise-date fair market value. Positive exercise
spread enters federal and California AMT. Later share sales, changing values,
and AMT credit carryforwards are outside the model.

Total savings and assets added combines remaining cash, personal saving,
employer retirement, retained RSUs, and acquired option shares. Purchasing a
share moves cash into an asset; the model does not count both as new wealth.
The total is a planning measure, not spendable cash or a retirement forecast.

## Supported tax facts

The supported household is married filing jointly, with adults aged 18–64,
full-year US residence and employment in CA or WA, and employment-authorized
Social Security numbers. Qualifying children are aged 0–17, have no income, and
live with the household throughout the year. Disability and senior tax cases
are excluded.

Supported income is W-2 cash pay and RSUs, plus qualifying ISO exercise-and-hold.
Traditional 401(k) contributions use the base annual limit: $23,500 in 2025 and
$24,500 in 2026. Employee plus employer contributions must fit the annual plan
limit and compensation. Employer contributions are an estimate entered as a
percentage of cash pay, not an employer-plan formula.

The engine applies standard deductions, supported child/dependent credits,
eligible work-related childcare expenses, income taxes, AMT, and employee
payroll taxes. Qualifying California renter status and per-adult WA Cares
exemptions are explicit inputs. AMT detail is already included in income tax.
Refundable tax balances may be negative.

Excluded facts include itemization, health-benefit deductions, health savings or flexible spending accounts, Roth or
catch-up contributions, business/investment income, stock sales, existing AMT
credits, part-year residence, and work across state lines. A full-year CA-versus-WA
comparison does not model the year of an actual move.

| Year | Washington | California |
| --- | --- | --- |
| 2025 | Tested reference cases | Tested reference cases; corrected 1.2% SDI |
| 2026 | Tested reference cases | Income-tax parameters projected in the pinned engine; 1.3% SDI |

PolicyEngine US 2.15.1 and Core 3.32.7 run inside the app. Scoped corrections
connect ISO spread to federal and CA AMT and correct 2025 CA SDI. WA Cares
exemptions preserve the nonexempt spouse's premium. Synthetic reference cases
and their primary sources are in `tests/fixtures/tax_cases.json`.

## Saved scenarios

Pydantic validates submitted inputs, calculations, database documents, and JSON
backups. Each saved result identifies its inputs and model/tax versions.
PostgreSQL stores inputs and results together. A revision check rejects a save
if another session already changed that record. Local drafts remain available
for a new copy or backup.

Old results are labeled for recalculation. Comparisons require the same tax
year and current model versions. Recalculating does not save automatically.
Imported backups provide inputs for a new calculation, never an overwrite of
an existing record.

See [usage instructions](../how-to-guides/index.md) and the
[data model reference](../reference/index.md).
