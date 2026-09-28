# Using and Hosting the App

## Household and goals

**Household** holds names and ages for a specified year end, qualifying children's
ages, and the supported filing status. Save these defaults once. New scenarios copy
them; existing scenarios, duplicates, imports, and open drafts keep their own facts.
Changing defaults does not update a saved financial calculation.

**Goals** manages shared purposes. Add, rename, or delete unused goals here. Renaming
a goal changes its displayed name across the app, without changing allocations or
financial results. Saved scenario backups retain the names used when saved. A goal
referenced by a saved scenario or an open draft cannot be deleted from this page.
Spending is the automatic cash remainder, separate from editable goals.

## Save wealth snapshots

Open **Wealth** to keep assets and debts at a financial date. Choose **New
snapshot**, set the date, and label it **Actual** or **Hypothetical**. A name is
optional; otherwise the date becomes its title. Mark household coverage as
partial until all intended assets and debts are included, and describe exclusions.

Add accounts with their owner, account type, tax treatment, and reported value.
Within an account, use **Add holding** to record individual securities or groups
of assets. Holding type and asset class are separate: an ETF can contain equities,
fixed income, or a mixture. A 401(k) or HSA label does not identify its investments.
Owner choices suggest names from Household; you can enter another name. Saved
snapshots retain those labels when household defaults change.

Enter amounts in USD without commas or a currency symbol, such as `1250.00`.
Leave an unknown amount blank; enter `0` only for a known zero. You can save an
account total before identifying its holdings. The difference appears as
**Unitemized contents**, rather than assumed cash. A $100,000 account containing
$90,000 of identified holdings contributes $100,000 to assets, with $10,000
unitemized. If its total is unknown, only known holdings contribute to the partial
subtotal. Holdings exceeding a reported total must be reconciled before saving.

Add a home or other assets outside accounts under **Directly held assets**, and
amounts owed under **Debts**. Enter debt as a positive number; it is subtracted
once. Do not also enter an account holding as a direct asset. For an account with
borrowing, record gross assets and the borrowing separately. Combined Savings or
Credit amounts can be marked as not yet itemized; replace those rows when entering
individual accounts. Replace grouped holdings when adding their constituents.

**Net recorded position** is known assets less known debts. It does not claim
complete household net worth. Separate summaries show assets by account type and
asset class. **Missing information** identifies unknown values, owners, tax
details, and source dates. A snapshot's financial date does not establish when an
account last updated; use its optional valuation date and source notes.

**Save snapshot** saves without calculating taxes. **Edit** corrects the same
dated record. Its date is fixed after saving: use **Duplicate** in Wealth to
record a later date, review all copied balances, and save a new snapshot. A
duplicate remains unsaved until you save it. **Save as new snapshot** also keeps
an independent copy of current edits. Changing one snapshot never changes another.

Edits survive navigation between pages within the browser session. Return through
**Edit** or **Session drafts**. If someone else changed or deleted the record,
saving cannot overwrite their change. Save a new copy, or choose **Reload saved
snapshot → Discard edits and reload wealth**. **Refresh** updates the library.

Under **Wealth backups**, download the current valid contents as JSON, including
unsaved edits. This also works in local preview, where database saving is disabled.
Upload a wealth backup and choose **Load wealth backup as new draft** to restore
its contents without reusing its saved identity. Files are limited to 1 MB;
scenario backups and wealth backups have different readers. Keep exports private.

**Delete** opens a confirmation identifying the saved snapshot. Deletion removes
it for both users and clears its draft in the current session. A changed revision
cannot be deleted until you refresh and review it again. Revisions detect
competing edits; they do not provide an archive of every previous edit.

Wealth snapshots currently serve as a saved inventory. Annual scenarios and goal
allocations remain independent. Applying scenarios to starting wealth, assigning
existing assets to goals, investment growth, and retirement projections are future
work. Unvested awards, tax lots, and detailed tax splits can be described in notes;
do not include future vesting again in opening account balances.

## Create and edit scenarios

**Scenarios** is the home page. Each saved scenario shows its title,
location, tax year, each person's employer, annual cash surplus or deficit,
and cash available for spending after funding goals.

- Choose **New scenario** to start from scratch, or **Duplicate** to
  start from an existing scenario. A duplicate stays unsaved until you save it.
- Choose **Edit** to open a scenario. **Assumptions** contains the year, state,
  family facts, employment, compensation, and expenses. **Savings goals** sets
  additional savings and the intended use of retained shares and contributions. **Results** shows its calculated views.
- **Calculate** opens Results without saving. **Save** calculates and saves the
  current inputs and matching results, then opens Results.
- **Unsaved changes** identifies edits that are not in the shared database.
  A separate notice identifies results that need recalculation.

Leave **Custom label (optional)** blank to use the state as the heading.
Employment details appear beneath it, without an extra work-status category.

Enter a label such as “Higher rent” when useful. Labels need not be unique and
stay as written when inputs change. Clear a label to return to the automatic
title. Existing names remain custom labels until cleared. Duplicate and
**Save as new scenario** preserve the label, including a blank one.

Enter **Employer** within each person's section. Use a short name for table
headings; leave it blank if unknown. Select **Not working in this scenario** to
show that status explicitly. A blank employer or zero salary does not mean
someone is not working. Checking the box removes the current employer when you
calculate or save; it does not change any compensation amounts. Set those
separately. Employer details follow the same calculate/save process as other inputs.

Edits stay in your browser session when you switch pages or open another
scenario. Use a scenario's **Edit** button or the **Unsaved drafts** section to
return. Save before closing the browser.

Reopening a scenario loads a newer saved version when you have no unsaved edits.
If you do have edits, the editor keeps them and shows a warning about the newer
saved version.

If another person has changed or deleted a scenario, your save cannot overwrite
that change. Keep your version with **Save as new scenario**, or use
**Reload saved version → Discard edits and reload** when a saved version still
exists. **Refresh** updates the library without replacing your drafts.

## Set savings goals

In the editor's **Savings goals** section:

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

## Compare scenarios

Open **Compare** in the top navigation and check two to four scenarios in the
visible list. The order you check them sets the table's column order. Checked
scenarios show **Column 1**, **Column 2**, and so on; the checklist itself stays
in place. Use **Move left** or **Move right** under **Column order** to reorder
the results without changing your selections.

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

In each adult's **Stock option award** section, select **Include an option
award**. Enter ISO or NSO, the number vesting this year, and exercise price per
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

## Delete a scenario

In **Scenarios**, choose **Delete** on the scenario's card. Check the name
in the confirmation dialog, then choose **Delete scenario** or **Cancel**.
Deletion removes the shared record for both people and its draft in your current
session. It cannot be undone. Download a backup first if you want to keep a copy.

A scenario changed since the dialog opened cannot be deleted until you refresh
and review it again. Deleted scenarios disappear from comparison selectors.
An open draft in another session can only be saved as a new scenario.

## Back up and recover

In the editor's **Results → Backups** section, download the last calculated scenario as
JSON. To restore it, upload the file under **Backups** in Scenarios or the editor and choose
**Load backup as new draft**, then calculate and save. Imports are limited to
1 MB and the supported schema. Keep these files private: they contain household
financial inputs. Backups exclude edits made after the last calculation.

Backups include goal identities and names. If an imported identity has a different
current name, explicitly keep the shared goal or create a separate one. Version-one
backups require review of the proposed destinations before loading. Their old extra
saving becomes a fixed annual Retirement allocation; taxes are recalculated.

## Run the local database

The configured local database uses Docker Desktop and `compose.yaml`:

```bash
docker compose up -d --wait
pixi run --locked app
```

The database is `financial_model`, on `127.0.0.1:5433`. Its data lives in the
`financial-model-postgres` Docker volume. `docker compose stop` stops the
service without removing that data; `docker compose up -d --wait` starts it
again. Keep the named volume when recreating the container.

Local credentials are in the ignored `.env` and `.streamlit/secrets.toml` files.
The app uses a separate account with read/create/update/delete access to scenarios,
goals, scenario-goal references, household defaults, and wealth snapshots.
These files and the volume belong to this computer; cloning the repository does
not copy the database. Local access does not provide a shared hosted URL.

For a database backup, choose a private destination outside the application
repository and run:

```bash
docker compose exec -T db pg_dump -U financial_model_admin -d financial_model \
  --format=custom > /path/to/private/financial-model.dump
```

The dump contains financial inputs and results. Keep it private. Scenario-level
JSON backups are also available in the app.

## Connect another PostgreSQL database

Create a dedicated database, then apply `scripts/schema.sql` using its SQL
console or a PostgreSQL client. The app never creates or migrates tables on
startup. Give its runtime role `SELECT`, `INSERT`, `UPDATE`, and `DELETE` on
`scenarios`, `goals`, `scenario_goals`, `household`, and `wealth_snapshots`,
plus schema usage and database connection rights. Use a separate owner for
schema changes.

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`, then replace
the placeholder with the runtime role's connection string. Alternatively set
`FINANCIAL_MODEL_DATABASE_URL` in the server environment. Remote connections
require `sslmode=verify-full`; the system CA store is the default. Credentials
remain on the server and are not part of scenario exports.

Restart the app and save a synthetic scenario. Open another browser session and
verify that it can load it. Use a separate test database for automated tests;
never use the family database for testing.

For an existing version-two database, reapply `scripts/schema.sql` as its owner
to add `wealth_snapshots`, then grant those four privileges on the new table to
the runtime role. This is an additive setup step; it does not rewrite scenarios,
household defaults, or goals. If the new table or permissions are missing, Wealth
reports that it cannot read saved snapshots and retains session drafts. The app
does not apply schema changes automatically.

## Migrate a version-one database

Use an owner connection in `FINANCIAL_MODEL_DATABASE_URL`, with a private folder
outside this repository. The first command writes original backups and a preview;
review the destinations and totals before applying:

```bash
pixi run --locked python scripts/migrate_goal_allocations.py preview /private/path/review
pixi run --locked python scripts/migrate_goal_allocations.py apply /private/path/review
```

The migration preserves scenario IDs and verifies unchanged taxes and net additions.
It stops if records changed after the preview. Give the app role access to the new
`goals` and `scenario_goals` tables after migration. Restart the app once complete.

## Prepare a private deployment

The proposed host is private Streamlit Community Cloud with managed PostgreSQL.
Hosting is a separate setup step; running the app locally does not publish it.

1. Confirm that the household accepts both providers and that a private app slot
   is available. Configure the database as above.
2. Generate deployment dependencies with `pixi run --locked export-cloud` and
   verify them with `pixi run --locked check-cloud`. Commit `environment.yml`.
3. Deploy the private GitHub repository with entry point `streamlit_app.py` and
   Python 3.12. Community Cloud installs the generated `environment.yml`.
4. Put `database_url` in the host's secrets settings. Restrict app access to the
   household's two email addresses before entering family data.
5. Verify both people can sign in, an uninvited browser cannot access data, two
   sessions detect competing saves, and saved scenarios survive a restart and
   redeploy. Verify JSON recovery and acceptable calculation time with both
   sessions in use.

Community Cloud currently supports one private app per workspace; invited
viewers can invite others. Treat sharing as trusted-family access. Local files
on the host are not durable storage. Hosting capacity, authentication, and
restart behavior must pass these checks before the deployment is ready.

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
