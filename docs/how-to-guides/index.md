# Using and Hosting the App

(database-files)=
## Work with a database file

Each browser session starts with an empty SQLite database. **Open database**
uploads a database you previously downloaded. Opening another database or choosing
**New database** replaces the current session's contents after confirmation.
Uploads are limited to 50 MiB. Invalid or incompatible files leave the current
database unchanged.

1. Open your latest database file, or start with the empty database.
2. Edit assumptions and use the normal **Save** actions in each editor. Save
   forecast runs you want to keep too.
3. Choose **Download database** to keep the updated database on your computer.
4. Open that downloaded file in your next session.

The download includes saved annual scenarios, Household defaults, goals, Wealth,
planning inputs, combined Plans, and saved forecast runs, with their identities and
revisions. It excludes unsaved editor drafts and calculations you have not saved.
Save those first, or use the appropriate JSON backup for a valid individual draft.

Download before closing or refreshing the browser tab, replacing its database,
or restarting the app. A hosted restart or lost browser session can discard the
working copy.
**Save** updates that copy; it does not update the file you originally uploaded
or store a durable database on the host.

Two sessions that open the same file have independent copies. They do not share
changes, detect edits in the other copy, or merge automatically. To share work,
finish editing, download the database, and pass that file to the next person.
Keep earlier downloads if you need recovery points. Store and share them privately
because they contain household financial data.

## Organize assumptions and combine plans

Under **Scenarios**, save the inputs you want to reuse, then combine them in Plans.

| Sub-tab | What you enter |
| --- | --- |
| Plans | Which assumptions to combine or compare in forecasts; existing annual plans |
| Household | Names, ages, children, residence, and personal tax elections |
| Wealth | Dated balances, tax treatment, investment mix, account owners, cost basis, and debt terms |
| Work | Pay, salary growth, contributions, and equity compensation |
| Spending | Regular living costs, changes to that budget, and extra expenses |
| Retirement | Retirement dates, living costs, benefits, lifespans, and costs after one person dies |
| Investments | Saving destinations, cash reserves, and portfolio allocation over time |
| Economy | Inflation, stock/bond/cash returns after fees, taxable yields, and simulation variability |
| Goals | Shared names for saving purposes |

Choose **New** in a category, enter known values, and **Save inputs**. A blank
amount means unknown; zero means a known zero. Dollar amounts use the purchasing
power of the year shown above the form, unless explicitly labeled nominal.

Saved assumptions appear as compact rows. Use the pencil to edit, the copy button
to duplicate, or the trash button to delete. Open an incomplete entry to see the
specific inputs to complete.

Editors keep amounts, dates, and required choices visible together. In Spending
and Retirement, **Edit ranges, essential costs, and notes** shows optional fields;
in Economy, **Edit ranges** shows lower and upper alternatives for all four rates.
Hiding these fields keeps their values. **Remove range** clears both bounds.
Source notes, backups, and advanced settings remain in named expandable sections.

### Enter work, spending, and retirement

Work keeps each person's compensation separate. Salary growth is above inflation:
0% means pay keeps pace with inflation. A work phase includes its start year and
excludes its end year. A blank end continues until the selected retirement year.
Vesting and option exercises have their own schedules under **Dated equity events
(optional)**. Entered schedules open automatically. The **401(k) contributions**
fields stay visible with compensation.

In Spending, **Regular living costs** is the total annual household budget,
excluding taxes and saving. **Change regular living costs** replaces that budget
from a chosen date. **Add an expense** adds a recurring or one-time cost. Add
childcare only if it is not already included in regular living costs.

In Retirement, enter the first full year each person will no longer work. For
periods with only one person retired, choose to continue regular living costs or
enter a different total household budget. Enter the budget for both people retired
separately. These amounts replace regular living costs; extras such as childcare
keep their own end dates.

**Living costs after one person dies** is the remaining person's total household
budget. It can apply before retirement too. Enter it explicitly; the app does not
halve a two-person budget. **Plan through age** sits beside each person's
retirement year, with optional ranges. **Mortality assumptions (advanced)** holds
inputs for a future mortality model; current forecasts require planning ages.

### Enter investments and economic assumptions

In Investments, saving instructions assign new money to goals. Portfolio strategy
sets the mix of stocks, bonds, and cash, totaling 100%. Choose a constant mix or a
**Change the mix over time**: enter a starting mix and a final mix with their dates. Choose
**Linear** for steady change, **Logarithmic** for faster change at the beginning,
or **Exponential** for faster change near the end. Each reaches the final mix on
the finish date and holds it afterward. Extra dated targets use the same shape
between each pair.
Allocation dates can follow either person's retirement or the
later retirement date. Starting balances and their investment mix remain in Wealth.
Enter the cash reserve here: cash kept outside the portfolio allocation, in the
scenario's reference-year dollars. It is already part of Wealth, not an extra asset
or expense. Enter zero if none. All accounts in the chosen snapshot are included.
Each allocation target shows its date or retirement anchor and stock, bond, and
cash percentages together. Saving instructions use
inline up/down arrows to change funding priority without changing amounts.
**Surplus goes to** chooses the goal for cash left after taxes, contributions,
planned spending, cash-reserve top-ups, and fixed cash goals. Choose Retirement to
save all that remains. Retirement is a goal, not a promise that all saving fits
into tax-advantaged accounts. **Forecast** applies these saved instructions.

In Economy, enter inflation and average yearly compound returns. Returns include
inflation and are **after fees, before tax**. Ranges are alternative assumptions,
not probabilities or limits on one-year market movements. Optional simulation
details record volatility (yearly fluctuations) and correlation (how returns move
together). Monte Carlo forecasts require these inputs; fixed forecasts do not.
See [forecast methodology](#forecast-method) for their precise definitions.

### Hold some choices fixed and vary others

Choose **Plans → New combined plan**, select a Wealth snapshot, and choose saved
assumptions from each category's library. **Use these assumptions** adds the
selection to the Plan. Each category shows the inputs currently in the Plan.
One selection holds that category fixed.
**Add alternative** varies it. For example, two Work choices and three Spending
choices make six combinations when Retirement, Investments, and Economy are fixed.
Multiple combinations appear in a preview table. Choose **Save plan**, then **Open
Forecast** to
[calculate selected combinations](#run-forecast).
Numeric ranges remain available for later analysis and are not expanded here.

Use **Edit plan Household** to review family facts and match the people in each
Work and Retirement choice. Household and Wealth remain fixed within that Plan.
Return to Plans and save.

Each choice is a saved copy. Use **Update this copy** to bring a newer source
revision into one selection, or **Update Wealth copy** for starting balances.
Then save the Plan. Its forecast readiness shows what still needs attention.
Editing or deleting saved assumptions does not change
a saved Plan. **Replace selection** replaces all choices in that category;
**Remove** removes one. A new Work or Retirement selection needs its person mapping
reviewed. Replacing Wealth changes the starting balances and keeps the selected
Investment scenarios, including their cash reserves.

The editor lists unknown values and conflicts across the selected
combinations. Saving inputs does not run a projection, tax calculation, or retirement
analysis. Annual calculations remain available separately under Plans.

Drafts survive category changes and visits to Compare within a browser session.
**Discard edits and reload inputs** restores a saved version. Concurrent saves
cannot overwrite a newer revision. Category backups export valid inputs; imports
create a new draft. The import limit is 1 MB.

An annual plan's **Create assumption profiles from this annual plan** action opens
Work, Spending, and Investments drafts. It copies pay and vesting for the recorded
year, converts monthly spending to annual amounts, and copies saving instructions.
It does not copy calculated taxes or cash surplus as income.

## Household and goals

Under Scenarios, **Household → Shared household defaults** holds names and ages for a specified year end, qualifying children's
ages, and the supported filing status. Save these defaults once. New plans copy
them; existing plans, duplicates, imports, and open drafts keep their own facts.
Changing defaults does not update a saved financial calculation.

**Goals** manages shared purposes. Add, rename, or delete unused goals here. Renaming
a goal changes its displayed name across the app, without changing allocations or
financial results. Saved scenario backups retain the names used when saved. A goal
referenced by a saved scenario or an open draft cannot be deleted from this page.
Spending is the automatic cash remainder, separate from editable goals.

## Save wealth snapshots

Open **Wealth → New snapshot**. Enter one date for all balances and an optional
name. Without a name, the date becomes the title.

Add one entry per named account:

| Field | What to enter |
| --- | --- |
| Account | A recognizable name, such as Example checking |
| Balance | The account's total value in USD |
| Tax treatment | Taxable, Pretax retirement, Roth, HSA, or Not entered |
| Invested in | Cash, Stocks, Bonds, Mixed, or Not entered |
| Owner | The person who owns a retirement or HSA account; taxable accounts do not need an owner for forecasting |

**Invested in** determines which return assumptions a forecast uses.
Choose **Mixed** to enter stock, bond, and cash percentages totaling 100%.
Individual holdings and tickers are not needed. A fund's name or its tax treatment
does not establish its investment mix. Accounts with both pretax and Roth balances
need separately named portions when that split is known; do not count the total
again as another row.

Enter amounts without commas or currency symbols, such as `1250.00`. Leave an
unknown balance blank; enter `0` only for a known zero. Incomplete inputs can be
saved. **Inputs still needed for forecasting** lists missing balances, tax treatment, or
investment mix. Empty accounts do not need a tax or investment classification.

Use **Owner** in each retirement or HSA account. Owner slots
refer to the two people in the Plan's household. Cost basis appears directly
where needed for forecasting. California HSA investments
need a separate state cost basis.

Under **Debts**, enter a name, positive amount owed, annual principal-and-interest
payment, interest rate, and payment frequency. For a monthly mortgage, enter
twelve times the monthly principal and interest, excluding escrow, and choose
monthly payments. Debt payments are separate from
living costs, so exclude them from spending budgets. Debt is subtracted once.
**Net recorded wealth** is the recorded assets less debts; it does not certify
that every household asset has been included. Use the optional snapshot note for
anything left out.

Under **Real estate**, add the property's market value and nominal annual
appreciation assumption. Enter monthly rent and property expenses, including
escrow for taxes and insurance, and link its mortgage debt. Do not repeat these
expenses in Spending. Enter the annual taxable rental-profit estimate separately;
rent covering the mortgage does not establish zero taxable profit. Leave unknown
values blank. They can be saved, but must be completed before forecasting.
Property value counts toward wealth and cannot be withdrawn for spending.
Rent, property expenses, and the taxable-profit estimate remain fixed in nominal
dollars through the forecast; there are no automatic sales or vacancies.

All balances are pooled for household planning. Wealth describes the starting
position; future allocation changes and cash reserves belong in **Investments**.

**Save snapshot** saves without calculating taxes. **Edit** corrects the same
dated record. Its date is fixed after saving: use **Duplicate** in Wealth to
record a later date, review all copied balances, and save a new snapshot. A
duplicate remains unsaved until you save it. **Save as new snapshot** also keeps
an independent copy of current edits. Changing one snapshot never changes another.

Edits survive navigation between pages within the browser session. Return through
**Edit** or **Continue editing**. If the record changed or was deleted after the
draft loaded, saving cannot overwrite that change. Save a new copy, or choose **Reload saved
snapshot → Discard edits and reload wealth**. **Refresh** updates the library.

Under **Wealth backups**, download the current valid contents as JSON, including
unsaved edits. This differs from **Download database**, which includes saved records only.
Upload a wealth backup and choose **Load wealth backup as new draft** to restore
its contents without reusing its saved identity. Files are limited to 1 MB;
scenario backups and wealth backups have different readers. Keep exports private.

**Delete** opens a confirmation identifying the saved snapshot. Deletion removes
it from this session's database and clears its draft. A changed revision
cannot be deleted until you refresh and review it again. Revisions detect
competing edits; they do not provide an archive of every previous edit.

Wealth snapshots serve as a saved inventory and can be copied into combined Plans.
Annual scenarios and goal allocations remain independent. Forecasts carry the
starting balances forward using the selected Plans, with owners and cost basis
recorded in Wealth. Future vesting belongs in Work and must not also be counted as opening
account balances. Individual tax-lot accounting and assigning existing assets
to goals remain outside this simplified snapshot.

## Create and edit annual plans

Open **Scenarios → Plans → Annual plans**. Each saved scenario shows its title,
location, tax year, each person's employer, annual cash surplus or deficit,
and cash available for spending after funding goals.

- Choose **New annual plan** to start from scratch, or **Duplicate** to
  start from an existing scenario. A duplicate stays unsaved until you save it.
- Choose **Edit** to open an annual plan. Plans owns its label, year and calculation
  actions. Edit family and residence facts in Household, compensation in Work,
  expenses in Spending, and saving instructions in Investments. The persistent
  banner identifies the annual case; these edits do not change shared defaults.
- Return to **Plans** for **Calculate**, which shows results without saving.
  **Save** calculates and saves the current inputs and matching results.
- **Unsaved changes** identifies edits that are not in the session's database.
  A separate notice identifies results that need recalculation.

Leave **Annual plan name (optional)** blank to use the state as the heading.
Employment details appear beneath it, without an extra work-status category.

Enter a label such as “Higher rent” when useful. Labels need not be unique and
stay as written when inputs change. Clear a label to return to the automatic
title. Existing names remain custom labels until cleared. Duplicate and
**Save as new annual plan** preserve the label, including a blank one.

Enter **Employer** within each person's section. Use a short name for table
headings; leave it blank if unknown. Select **Not working in this annual plan** to
show that status explicitly. A blank employer or zero salary does not mean
someone is not working. Checking the box removes the current employer when you
calculate or save; it does not change any compensation amounts. Set those
separately. Employer details follow the same calculate/save process as other inputs.

Under **Compensation**, toggle **Use vested shares to pay their taxes**:

- **On** (default): vested RSUs cover the taxes attributed to them; the remaining
  shares are retained.
- **Off**: cash pays all taxes and all vested RSUs are retained. This leaves less
  cash for spending and savings goals and can create a cash shortfall.

The setting applies to both adults in this annual plan. Total taxes and the
effective tax rate stay the same. Choose **Calculate** to preview the result or
**Save** to keep it. Duplicate a plan to compare both choices side by side.

Edits stay in your browser session when you switch pages or open another
annual plan. Use its **Edit** button or **Continue editing annual plans** to
return. Save, then choose **Download database** before closing or refreshing the
browser tab.

Reopening a scenario loads a newer saved version when you have no unsaved edits.
If you do have edits, the editor keeps them and shows a warning about the newer
saved version.

If a scenario has changed or been deleted in this database since you loaded it,
your save cannot overwrite that change. Keep your version with **Save as new annual plan**,
or use **Reload saved version → Discard edits and reload** when a saved version still
exists. **Refresh** updates the library without replacing your drafts.

## Set savings goals

While editing an annual plan, open **Investments → Saving instructions**:

1. Choose monthly or annual entry and enter planned cash amounts. Use **Move up**
   to set the funding order. The app stores exact annual amounts and that order.
2. Designate retained RSU shares for goals, totaling 100% when RSUs are present.
3. Assign existing 401(k)/employer contributions and exercised option shares to
   goals. These amounts are calculated from compensation, not entered again.
4. Choose **Calculate**, review the results tabs, then save.

**Add goal to scenario** includes an existing shared goal with zero allocations.
Use **Manage household goals** to create or rename goals on the Goals page.
**Remove** removes an unused allocation row only from this scenario. Reassign its
cash, RSUs, and contribution destinations first, then save the scenario.

Cash goals are funded from top to bottom after taxes, employee 401(k)
contributions, living expenses, and option exercise costs. **Spending**
always comes last and receives leftover cash. Its destination is not editable.
Actual spending still belongs in living expenses.

If there is not enough cash, later goals are partly funded or unfunded. Their
planned amounts stay unchanged. Retained shares and 401(k) contributions keep
their assigned goals; they are not sold or withdrawn to cover cash shortfalls.

## Compare annual plans

Open **Compare annual plans** in the top navigation and check two to four scenarios in the
visible list. The order you check them sets the table's column order. Checked
scenarios show **Column 1**, **Column 2**, and so on; the checklist itself stays
in place. Use the left or right arrow on a checked row to move that plan's column
without changing your selections. Hover over an arrow to see which plan it moves.

Unchecking removes only that comparison column; it does not delete the saved
scenario. Checking it again adds it on the right. With four selected, uncheck
one before adding another. Selections and column order stay in place when you
switch pages. All selected scenarios must use the same tax year and current
calculation rules.

Choose **Monthly** or **Annual** once for all four views. Money amounts
and vesting counts use that period; per-share prices and tax
rates stay unchanged. Monthly values are averages of the modeled year, not a
paycheck or vesting schedule. The period stays selected when you switch pages.

Each view uses a table with the same scenario columns and order. On a narrow
screen, scroll horizontally while row labels stay visible. Expand
**Definitions** for formulas and inputs.

Column headings and cards show the title above year and employer details. State
appears in the details when a custom label replaces the automatic title.
Selectors use compact versions of the same information.

When titles and context match, the app adds enough differing inputs to distinguish
the scenarios, such as rent per month or a goal's cash allocation. Titles stay
stable when the comparison selection changes. Identical assumptions are marked
**Same assumptions**. Expand **Saved details** to see their last-saved date,
with a short record identifier when dates also match. Selectors include those
details to distinguish identical copies. The database does not store creation
dates. Draft and snapshot labels remain separate. Notes do not count as a change
to financial assumptions.

**Cash overview** shows salary and bonus, taxes paid from cash, employee 401(k)
contributions, and living expenses. **Cash surplus / deficit** is the balance
before option exercises and goal allocations. **Goal allocations** lists every
goal, including zero amounts. Funded cash is positive; any shortfall appears
beneath it as a red negative amount.
**Spending** is the final row: positive cash is available to spend;
a negative amount means existing expenses and commitments exceed income.

**Goal breakdown** shows each goal's cash priority, planned cash, funded cash,
and shortfall, followed by employee and employer 401(k) contributions, retained
RSUs, and exercised shares at cost. **Total funded** combines those sources.
These are annual additions, not account balances or retirement projections.
Retirement is listed alongside the other goals, and Spending is last.

Health insurance and other deductions not entered separately belong in the
living-expense budget once. Expense inputs do not reduce taxable wages.

**Effective tax rate** includes federal/state income taxes and employee payroll
taxes, divided by salary, bonus, and vested RSUs. It includes taxes funded from
shares and any modeled ISO exercise tax. It excludes employer contributions and
option values from the denominator. Zero wages show **N/A**. Open the **Tax
breakdown** tab to reconcile total taxes to the portion paid from cash.

**Equity overview** follows RSU value at vesting through taxes funded from
vested shares to shares retained. Its **Goal allocations** section uses the same
goal names and order as Cash overview, including zero amounts. **Stock options** shows
vesting counts, per-share prices, share value if exercised, exercise cost, and
estimated intrinsic value. These award estimates do not enter cash flow or
funded savings. An unknown estimate stays unknown; a dash means no matching
person or award. Modeled **Option exercises** appear separately, with shares
acquired, exercise cost, exercise-date value, and savings goal. Their cost also
appears in Cash overview once.

The editor uses the same four views. This is a one-year budget, not a retirement
projection. Existing asset balances, investment growth, and future retirement
needs are not included.

To compare without saving, calculate in the editor and choose **Compare results**.
The last calculated draft appears as a separate choice. **Keep snapshot** retains
one calculated version while you change and recalculate the draft. Both session
results are labeled; uncalculated edits never replace their inputs.

Dollar totals are rounded to whole dollars; per-share prices retain up to six
decimal places. Calculations retain cents. See
[calculation assumptions](../concepts/index.md) for the meaning of each total.

## Enter option compensation

In each adult's Work section, select **Include a stock option award**. Enter ISO
or NSO, the number vesting this year, and exercise price per
share. Enter an assumed share value if known; leave it blank otherwise.

The **Equity overview** view shows option awards separately. **Estimated
intrinsic value** is the number vesting multiplied by the positive difference
between assumed share value and exercise price. It excludes taxes and time value;
it is not sale proceeds.
This describes this year's vesting, not the full multiyear grant or all vested
options. It does not change cash, taxes, or saving totals, and a share-value
estimate does not imply that the shares can be sold.

Use **ISO exercise and hold** separately for actual qualifying exercises held
through year end. NSO exercises and share sales are not supported.

## Delete an annual plan

In **Scenarios → Plans → Annual plans**, choose **Delete** on the scenario's card. Check the name
in the confirmation dialog, then choose **Delete annual plan** or **Cancel**.
Deletion removes the record from this session's database and clears its draft.
It cannot be undone. Download a backup first if you want to keep a copy.

A scenario changed since the dialog opened cannot be deleted until you refresh
and review it again. Deleted scenarios disappear from comparison selectors.
Other sessions and previously downloaded databases keep their own copies.

## Back up and recover

Use **Download database** to preserve all saved work together, then **Open
database** to recover that file. Individual JSON backups are useful when you want
to copy one record into a different database without replacing its other contents.

In the annual plan's **Plans → Backups** section, download the last calculated scenario as
JSON. To restore it, upload the file under **Backups** in Annual plans or the editor and choose
**Load backup as new draft**, then calculate and save. Imports are limited to
1 MB and the supported schema. Keep these files private: they contain household
financial inputs. Backups exclude edits made after the last calculation.

Backups include goal identities and names. If an imported identity has a different
current name, explicitly keep the shared goal or create a separate one. Version-one
backups require review of the proposed destinations before loading. Their old extra
saving becomes a fixed annual Retirement allocation; taxes are recalculated.

## Export an existing PostgreSQL database

The app no longer connects to PostgreSQL. To keep an existing current-format
household database, export it once to SQLite. Set `FINANCIAL_MODEL_DATABASE_URL`
to its connection string in your shell, then choose a private output location:

```bash
pixi run --locked -e postgres-export python scripts/export_postgres.py /private/path/financial-model.sqlite
```

The optional `postgres-export` environment supplies the PostgreSQL driver; the
app and its cloud environment do not need it. The exporter reads a consistent
snapshot without changing the source and refuses to overwrite an existing file.
It preserves saved identities, revisions, household defaults, goal relationships,
and frozen forecast results. It creates the local file with owner-only access.

Open the resulting file with **Open database** and review its saved libraries and
forecasts before continuing. Keep the original database and a private backup
until you have verified the exported file. The exporter accepts current-format
records only; it does not guess how to upgrade historical input formats. See
[historical conversion rules](#historical-conversions) if it reports an older schema.

## Prepare a private deployment

Streamlit Community Cloud can host the app without a separate database service.
Hosting is a separate setup step; running locally does not publish the app.
Uploaded databases are processed on the server, so restrict access before using
household data.

1. Generate deployment dependencies with `pixi run --locked export-cloud` and
   verify them with `pixi run --locked check-cloud`. Commit `environment.yml`.
2. Deploy the GitHub repository with entry point `streamlit_app.py` and Python
   3.12. Community Cloud installs the generated `environment.yml`.
3. Set app sharing to **Only specific people can view this app** and invite the
   household's email addresses. No database credentials or connection URL are
   required.
4. With a synthetic database, verify that invited users can sign in and an
   uninvited browser cannot use the app. In two sessions, check that opening the
   same file produces independent copies. Save a change, download the database,
   and reopen that download in a fresh session to check recovery.

Community Cloud supports one private app per workspace; invited viewers can
invite others. The host provides no durable copy of your database. Download
saved changes before closing or replacing the session, and keep the downloaded
file private. A separate hosted session must upload its own file to continue.

Sources: [private sharing](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app),
[dependency formats](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies),
[storage](https://docs.streamlit.io/develop/concepts/connections/connecting-to-data).

## Update tax rules intentionally

Change pinned dependencies through Pixi, regenerate `environment.yml`, and run
the tax reference suite. Review every changed expectation against the cited
primary source before accepting it. Update the engine constants, correction
revision when corrections change, and model revision when accounting changes.
Never update expected results solely to match a new engine output.

Run formatting, tests, type checks, and documentation build. Test the exported
Linux environment before redeploying. Recalculate existing scenarios to compare
them with the updated rules; saving remains explicit.
