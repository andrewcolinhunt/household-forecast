(run-forecast)=
# Compare Future Wealth

Use **Forecast** to compare one to four saved Plan combinations through both
people's planning lifetimes. Each comparison uses the same opening Wealth
snapshot and matching economic futures.

## Prepare and run a comparison

1. Save your Work, Spending, Retirement, Investments, and Economy inputs, then
   select them in a combined Plan under **Scenarios → Plans**. Enter both
   planning ages and a budget for the surviving person. Retirement means the
   first calendar year without employment income.
2. Open **Forecast** and select up to four combinations. Categories with saved
   alternatives produce separate choices. The first selected combination is
   the baseline for the difference chart. Compared choices must share their
   dated accounts, debts, and household demographics; residence may differ.
3. Complete the forecast details. Enter owners for retirement accounts, taxable
   cost basis, debt payment terms, retirement benefits, and additional retirement
   health costs. Use zero for a known zero; leave unknown facts blank until you
   can supply them. These details do not change the saved Wealth inventory.
4. If you have an HSA, enter the part of those health costs eligible for payment
   from the available HSA accounts. For California scenarios, also enter the
   HSA's California cost basis. Health costs here are **additional to** the
   saved living budget; do not enter the same expense twice.
5. Enter stock dividend and bond interest yields. These describe the taxable
   distribution component of total returns; the model does not add them again
   to investment growth. Review and confirm the displayed assumptions.
6. Start with **Fixed assumptions** to check your cash flows. Then choose
   **Monte Carlo** for uncertainty bands. Complete all four volatilities and
   the correlation matrix in the Economy profile first, then replace its frozen
   copy in the combined Plan and save that Plan. The default
   is 10,000 simulated futures. Select **Run forecast** and allow the annual
   tax calculations to finish.

Forecasting currently starts from a 2026 snapshot with amounts expressed in
2026 purchasing power. A part-year snapshot receives only the remaining year's
recurring activity. If an expense occurs once in that calendar year, explicitly
choose whether it has already happened or remains to be paid.

## Read the results

The main chart shows median net wealth and the middle 80% of modeled outcomes
at each year end, in 2026 purchasing power. Retirement dates appear as vertical
marks. The band is neither a worst-case boundary nor a lifetime guarantee.

Read the funding summary and annual table alongside the chart. Positive total
wealth can coexist with a spending shortfall when money remains in inaccessible
retirement accounts. Accessible balances are gross of future withdrawal taxes;
HSA funds are reserved for eligible modeled health costs. Cash means taxable
cash available to the household.

The difference chart compares each alternative with the baseline **within the
same simulated future**, then summarizes those differences. Differences stop
when either comparison horizon ends. Inspect an individual path to see nominal
account balances and the annual reconciliation. The saved example is **Future 1**,
not an invented path through each year's median.

## Check adverse and historical outcomes

**Adverse scenario** applies an illustrative stock decline or sustained high
inflation. These scenarios have no assigned probability.

For **Historical replay** or **Historical blocks**, upload a CSV with consecutive
calendar years and this exact header:

```csv
year,equities,fixed_income,cash,inflation
2024,0.08,0.03,0.02,0.025
2025,-0.12,0.04,0.02,0.03
```

These rows illustrate the format; they are not observed market data. Use decimal
annual total returns after fees, with inflation for the same periods, and record
the source, retrieval date, and fee convention. Replay needs a history at least
as long as the forecast and evaluates every complete consecutive window. Blocks
sample consecutive stretches jointly, with replacement, without wrapping from
the last historical year to the first. Neither mode changes history to match
the central economic outlook.

## Save and reproduce a run

Save the forecast to the shared database or download its JSON file. A saved run
contains the selected Plan copies, Wealth snapshot, supplemental details,
economic settings, seed, model versions, summary results, and example ledger.
Later library edits do not change it. Rerunning creates a separate result.

Open a saved forecast to review its frozen results. Rerun its saved inputs to
inspect other individual paths. Exact reproduction requires the same model and
numerical dependencies recorded by the run. JSON downloads also work when
database saving is unavailable; imports are limited to 10 MB.

For an existing database, its owner must reapply `scripts/schema.sql` and grant
the runtime role `SELECT` and `INSERT` on `forecast_runs`. This creates an
additional table without rewriting existing household inputs.

See [forecast methodology and limits](#forecast-method) for the accounting and
tax assumptions behind the results.
