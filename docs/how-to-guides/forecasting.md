(run-forecast)=
# Compare Future Wealth

Use **Forecast** to compare one to four saved Plan combinations through both
people's planning lifetimes. Each comparison uses the same opening Wealth
snapshot and matching economic futures.

## Prepare and run a comparison

1. Complete financial inputs in **Scenarios**:
   - **Wealth** owns account balances, retirement-account owners, taxable cost
     basis, and debt payment terms.
   - **Retirement** owns retirement dates, planning ages, living budgets,
     Social Security, and pensions.
   - **Economy** owns total returns, inflation, dividend and interest yields,
     and simulation volatility and correlation. New Economy scenarios include
     simulation defaults; review or edit them under **Advanced simulation settings**.
   Use zero for a known zero and leave unknown facts blank. Choose **No pension**
   when appropriate; pension timing is then unnecessary.
2. Select these saved scenarios, plus Work and Spending, in a combined Plan
   under **Scenarios → Plans**. Review its forecast readiness. When a source
   changes, choose **Update this copy** or **Update Wealth copy**, then save the
   Plan. Other Plans retain their own copies.
3. Open **Forecast** and select up to four combinations. Categories with saved
   alternatives produce separate choices. The first selected combination is
   the baseline for the difference chart. Compared choices must share their
   dated accounts, debts, and household demographics; residence may differ.
4. Start with **Fixed assumptions** to check your cash flows. Then choose
   **Monte Carlo** for uncertainty bands. The default is 10,000 simulated
   futures. Select **Run forecast** and allow the annual tax calculations to
   finish. Missing inputs link back to Scenarios.

For an older Economy scenario with blank simulation inputs, select **Use
recommended simulation settings** under **Advanced simulation settings**, then
save. This sets annual volatility to 18 percentage points for stocks, 6 for bonds,
1 for cash returns, and 2 for inflation, with a documented correlation preset.
It leaves your expected returns, exploration ranges, and income yields unchanged.
Update the Economy copy in each Plan you want to use with these settings.

Lower and upper exploration ranges describe alternative long-term outlooks.
Monte Carlo uses the central outlook plus annual volatility and correlations;
the forecast calculates its 10th–90th percentile band from the resulting balances.

Separate health-expense modeling is deferred. Include any health costs you want
in the forecast in your household living budgets. HSA balances remain invested
and count toward total wealth, but do not fund spending. California HSA
investments still need a state cost basis in Wealth.

Dividend and interest yields are parts of total returns, not additional growth.
Debt payments are modeled separately from living costs; exclude them from your
spending budgets.

Forecasting currently starts from a 2026 snapshot with amounts expressed in
2026 purchasing power. A part-year snapshot receives only the remaining year's
recurring activity. One-time expenses listed for that opening year are planned
expenses at year end. Remove expenses that have already happened from the Plan;
there is no separate opening-year setting.

## Read the results

The main chart shows median net wealth and the middle 80% of modeled outcomes
at each year end, in 2026 purchasing power. Retirement dates appear as vertical
marks. The band is neither a worst-case boundary nor a lifetime guarantee.
The time axis has three aligned numeric rows: year and both people's year-end
ages, with row labels on the left. Ages use the household saved with the forecast.
Hover over a median line for exact ages and balances. The full-width chart keeps
a nearly square shape as the window changes. Its legend sits inside the upper-left
corner, with solid samples matching the median lines.

Read the funding summary and annual table alongside the chart. Positive total
wealth can coexist with a spending shortfall when money remains in inaccessible
retirement accounts. Accessible balances are gross of future withdrawal taxes;
HSA funds are excluded from accessible balances. Cash means taxable
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
contains the selected Plan copies, including Wealth and all financial assumptions,
simulation settings, seed, model versions, summary results, and example ledger.
Later library edits do not change it. Rerunning creates a separate result.

Open a saved forecast to review its frozen results. Rerun its saved inputs to
inspect other individual paths. Exact reproduction requires the same model and
numerical dependencies recorded by the run. JSON downloads also work when
database saving is unavailable; imports are limited to 10 MB.

For an existing database, its owner must reapply `scripts/schema.sql` and grant
the runtime role `SELECT` and `INSERT` on `forecast_runs`. This creates an
additional table and updates its version constraint without rewriting household
inputs. New runs use forecast format and model version 3. Version 1 and 2 runs remain
stored but cannot be opened by this version; their inputs must be recreated in
Scenarios before running a new forecast.

See [forecast methodology and limits](#forecast-method) for the accounting and
tax assumptions behind the results.
