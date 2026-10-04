(run-forecast)=
# Compare Future Wealth

Use **Forecast** to compare one to four saved Plan combinations through both
people's planning lifetimes. Each comparison uses the same opening Wealth
snapshot and matching economic futures.

## Prepare and run a comparison

1. Complete financial inputs in **Scenarios**:
   - **Wealth** owns account balances, retirement-account owners, taxable cost
     basis, and debt payment terms.
   - **Spending** owns all household expense schedules, including children,
     healthcare, care, retirement, and survivor spending.
   - **Retirement** owns retirement dates, planning ages, Social Security,
     and pensions.
   - **Economy** owns total returns, inflation, dividend and interest yields,
     and simulation volatility and correlation. New Economy assumptions include
     simulation defaults; review or edit them under **Advanced simulation settings**.
   Use zero for a known zero and leave unknown facts blank. Choose **No pension**
   when appropriate; pension timing is then unnecessary.
2. Select these saved assumptions, plus Work, Spending, and Investments, in a combined Plan
   under **Scenarios → Plans**. Review its forecast readiness. When a source
   changes, choose **Update this copy** or **Update Wealth copy**, then save the
   Plan. Other Plans retain their own copies. Use **Plan view → Timeline** to
   review dated changes together and edit this Plan's inputs. A category filter
   changes the view; it does not create another copy of the expense.
3. Open **Forecast → Set up forecast** and select up to four combinations.
   Categories with saved alternatives produce separate choices. Compared choices must share their
   dated accounts, debts, and household demographics; residence may differ.
4. Start with **Fixed assumptions** to check your cash flows. Then choose
   **Simulated returns** (Monte Carlo) for uncertainty bands. The default is 10,000 simulated
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

Enter healthcare and care in **Spending**, either inside an overall household
budget or as separate named expenses. Include the premiums and out-of-pocket
costs the household expects to pay. The app does not calculate Medicare,
insurance subsidies, illness, or care needs. HSA balances remain invested and
count toward total wealth, but do not fund spending. California HSA investments
still need a state cost basis in Wealth.

Dividend and interest yields are parts of total returns, not additional growth.
Debt payments are modeled separately from living costs; exclude them from your
spending budgets.

Forecasting currently starts from a 2026 snapshot with amounts expressed in
2026 purchasing power. A part-year snapshot receives only the remaining year's
recurring activity. One-time expenses listed for that opening year are planned
expenses at year end. Remove expenses that have already happened from the Plan;
there is no separate opening-year setting.

## Set up changing expenses

1. In **Spending**, use the initial ordinary-living expense for your total
   household budget, or select **Add an expense** for a separately budgeted cost.
   Name it, choose a category, and describe what it includes and excludes.
2. Enter an amount and its start date. **Add spending phase** sets a new annual
   amount for that same expense. Select a calendar year, an adult's retirement,
   both adults' retirement, or the first year after one person dies. In a Plan's
   Timeline, you can also link a date to a child's age using the Plan's household
   details. Adult age links are available in both editors.
3. Use **Specify an end date** for temporary costs. The end is the first year
   excluded. A one-time expense has one amount and date, with no end date.
4. Enter amounts in the profile's money year. Leave **Growth above inflation**
   at zero for constant purchasing power. Growth runs from the money year for
   every phase; inflation is applied separately once.
5. If the new expense is already inside another budget, use **Separate costs
   already included in an expense**. Review the before, remaining, separated,
   and combined amounts before **Apply reviewed split**. This splits every
   phase and range by the chosen percentage, keeping dates and growth unchanged.
   Review both schedules afterward if the costs need different timing.

For children, a few phases such as childcare, school years, and college support
may be sufficient. Add a work phase if parental leave or reduced hours changes
income. Adding a child to Household does not create expenses. For healthcare and
care, compare reasonable amounts and durations in named Plans rather than treating
an entered range as a probability distribution.

A Plan with multiple alternatives offers a selector for each category in its
Timeline. Select the combination you want to review before editing. Those edits
update only the selected Plan copy. Save the Plan before running a forecast.

## Read the results

Use the four result tabs:

- **Overview** compares net wealth at each year end, in 2026 purchasing power.
  Fixed assumptions show one projected future; simulated returns show the median
  and middle 80% of modeled outcomes. Retirement dates appear as vertical marks,
  and the time axis includes both people's ages. Open the annual table below the
  chart for accessible funds, cash, debt, and shortfalls.
- **Cash flow** shows income above zero and planned spending and paid taxes below
  zero. Use **Inspect year** beside the asset waterfall to reconcile that year's
  opening assets, income, investment returns, and paid costs with closing assets.
  The unexplained difference should be near zero. Transfers and employee
  contributions remain inside assets.
- **Simulated future** follows one actual future across net wealth, total and
  accessible assets, account balances by tax treatment, returns, inflation,
  spending, withdrawals, and unpaid expenses.
- **Sensitivity** shows which assumptions most affect wealth in a selected year.
  Choose a Plan and **Inspect year**, then select **Run sensitivity analysis**.
  It changes one input at a time against a fixed baseline and ranks the changes
  in net wealth within the displayed ranges. The ranking depends on those ranges
  and the selected year; it does not describe probabilities.

In Cash flow and Simulated future, use **Dollar display** to switch units. Runs
with multiple Plans or futures also offer **Plan to inspect** and **Future number**.
These selections carry between the two tabs, whose charts follow the same complete
future. Open **Annual ledger** for the nominal amounts. The saved example is
**Future 1**, not a path through each year's median.

Positive total wealth can coexist with a spending shortfall when money remains
in inaccessible accounts. Accessible balances are before withdrawal taxes and
exclude HSA funds. Cash means taxable cash available to the household. The
Overview band is neither a worst-case boundary nor a lifetime guarantee.

In purchasing-power mode, annual flows use start-of-year prices and balances use
year-end prices. Every waterfall term uses the same year-end prices so its
amounts still reconcile. Recurring opening-year flows start on the saved Wealth date.

## Save and reproduce a run

Save the forecast to the shared database or download its JSON file. A saved run
contains the selected Plan copies, including Wealth and all financial assumptions,
simulation settings, seed, model versions, summary results, and example ledger.
Later library edits do not change it. Rerunning creates a separate result.

Open a saved forecast to review its frozen results. Choose **Rerun these inputs** to
inspect other individual paths. Exact reproduction requires the same model and
numerical dependencies recorded by the run. JSON downloads also work when
database saving is unavailable; imports are limited to 10 MB.

Previously saved runs in the current format remain viewable. Rerunning an older
method starts a new setup with **Fixed assumptions**.

New Plans and forecasts use format version 4. Older forecasts remain stored in
the historical inventory for download; they cannot be rerun by the changed model.
Do not overwrite a historical result to make its inputs fit the new format.

For version 3 Plans, the database owner should create and review a migration
preview with `pixi run --locked python scripts/migrate_household_timeline.py preview DIRECTORY`,
optionally supplying `--settings FILE`, then run the same command with `apply DIRECTORY`.
The private backup retains original records, settings, proposed inputs, and an
annual spending comparison. Converted expense dates are fixed calendar years;
review any intended retirement or age links explicitly. Converted expenses stop
at the old planning horizon; review their ends when testing longer lives. Reusable profiles need
their reviewed Plan context to resolve the old budget rules. Ambiguous inputs
must be resolved before conversion.

See [forecast methodology and limits](#forecast-method) for the accounting and
tax assumptions behind the results.
