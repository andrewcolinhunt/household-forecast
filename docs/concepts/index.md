# How the Annual Model Works

The model follows one household through one calendar year. It separates cash,
retirement contributions, vested RSUs, and purchased option shares. Average
monthly cash flow is annual cash flow divided by 12; it does not
predict paycheck timing or withholding.

## Goals and cash

A goal describes an asset's purpose. Its asset type still describes how it is held.
Cash and retirement accounts can both fund Retirement, but retain different access
rules. Goals are shared across scenarios; allocations belong to each scenario.

```text
cash available = cash pay − cash-funded taxes − employee 401(k)
                 − option purchase cost − annual living costs
cash remainder = max(cash available − fixed cash allocations, 0)
funding gap = max(fixed cash allocations − cash available, 0)
net additions = planned goal additions − funding gap
```

One goal receives the cash remainder. Retained RSUs are split by percentages;
retirement contributions and exercised shares each have a goal destination.
RSU splits conserve every cent, assigning rounding pennies by largest fractional
remainder and then stable goal ID. Assignments do not change taxes or net additions.

Unfunded plans remain visible. The model does not silently cut allocations,
withdraw existing assets, sell extra shares, or borrow. A goal named Available to
spend still holds cash; spending belongs in expenses.

## Access and value certainty

Liquidity describes how readily an asset can be used or sold. Its value can
still change even if it is easy to sell. The app keeps these distinctions visible:

| Category | Access | Value shown |
| --- | --- | --- |
| Cash | Available without selling shares or withdrawing retirement funds | Net cash added after expenses, retirement contributions, and ISO exercise costs |
| Retirement | Withdrawal restrictions apply; employer plan vesting is not modeled | Contributions, before any future withdrawal taxes |
| RSU shares | Shares delivered at vesting; sale restrictions may apply | Estimated vesting value after taxes, subject to price changes |
| Exercised option shares | Shares owned; ability to sell is not recorded | Exercise-date value, not guaranteed sale proceeds |
| Unexercised options | Rights to buy shares; exercise needs funding and sale may be unavailable | An uncertain estimate shown separately from saving totals |

Cash available can be negative. Planned cash allocations less their funding gap
equal this amount. Annual additions are not existing balances, and combined goal
amounts are not all available to spend.

The model does not record public/private company status or trading restrictions.
It assumes RSU shares are delivered at vesting and that enough can be sold to
cover their taxes. It does not model private RSUs with delayed settlement or no
sale available for tax funding. No share holdings are counted as available cash.

See FINRA's explanations of [liquidity and investment risk](https://www.finra.org/investors/investing/investing-basics/risk)
and [employee stock awards](https://syndication.finra.org/content/questions-employees-should-ask-about-stock-awards).

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

## Option compensation

An optional award record describes ISO or NSO options vesting this year. Its
estimate is `vesting shares × max(assumed share value − exercise price, 0)`.
A missing share value means **Not estimated**, not zero. A zero spread means
there is no value above exercise cost at the assumed price; it does not value
the option's remaining time or potential future appreciation.

This record is informational. It is separate from an exercise event and does
not enter wages, taxes, saving targets, or net saving. It represents one annual
vesting estimate per adult, not a full grant schedule or vested-option
inventory. NSO exercises, dispositions, and option-pricing models are excluded.

## Net saving from income

The headline annual measure is:

```text
net saved from income = cash pay + vested RSUs + employer retirement
                        − total taxes − annual living costs
```

It includes cash surplus or shortfall, pretax retirement contributions, and
retained RSUs. Planned contributions can be high even when cash is short, so the
app shows both contributions and the shortfall. Moving cash into a 401(k),
additional saving, or option shares does not itself increase this total.

ISO exercise spread can come from options earned in earlier years. It is
excluded from net saving; exercise-related taxes remain an expense. Acquired
shares and their cash cost appear separately in **Goal allocations**. The
reconciliation adds back the exercise cost to cash remaining because that cash
was moved into shares rather than spent on living costs.

Goal funding counts exercised shares at purchase cost, excluding exercise spread.
It excludes existing balances and investment growth. Pretax retirement amounts
still have future withdrawal taxes; assigning them to a goal does not remove those
obligations.

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
